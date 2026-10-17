# BGM・パリイ・会話・WebSocket統合ガイド

この資料は、`chambara-app`と外部統合アプリを接続し、1ゲームの開始から次の待機までを確認する手順です。WebSocketとJSONの基礎を個別に試したい場合は、先に`communication_test/README.md`を参照してください。

## 1. 全体構成

```text
外部統合アプリまたは模擬画面
        │ WebSocket + JSON
        ▼
chambara_app（進行状態を一元管理）
  ├─ chambara_talk（録音・認識・LLM・読み上げ）
  └─ chambara_bgm（BGM再生・パリイ判定）
```

本アプリがWebSocketサーバーです。既定では`ws://127.0.0.1:8765`で待ち受け、最初の1接続だけを制御元として受け付けます。`reference/`のROS/rosbridgeとは別の直接WebSocket仕様です。

## 2. 状態遷移

```text
standby
  └─ game_start → pre_interview
       ├─ インタビュー完了 → waiting_battle_start
       └─ battle_startで中断 ─────────┐
                                      ▼
waiting_battle_start ─ battle_start → battle（easy、残数3）
                                      ├─ 残数2 → normal
                                      ├─ 残数1 → hard
                                      ├─ 残数0 → 人間勝利 ─┐
                                      └─ battle_end → ロボット勝利
                                                            ▼
                                                    post_interview
                                                            │ 完了
                                                            ▼
                                                         standby
```

開始前インタビュー中の`battle_start`だけは例外的に受理します。進行中の録音と読み上げへキャンセルを通知し、音声認識・LLMが既に処理中なら返ってきた結果を破棄します。この場合、`pre_interview_finished`は送りません。

統合インタビューでは、質問の読み上げが終わると録音を自動で開始します。認識結果はターミナルへ表示しますが、Enter確認を待たずにLLMへ送信します。無音、録音エラー、認識エラー、空の認識結果では、1秒待ってから同じフェーズの録音を自動で再開します。

弱点残り回数は絶対値です。現在値以下の0〜3だけを受理します。同じ値は受付通知だけを返し、BGMを再開しません。3から1へ飛ばした場合は直接hardへ切り替わります。

## 3. 起動する

初回はPowerShellでメイン環境へ依存関係をインストールします。

```powershell
Set-Location "D:\Program_Folder\Codex_ws\voice_recognition_app"
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

`Activate.ps1`の実行がポリシーで拒否された場合は、そのPowerShellで`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`を実行してから再度有効化します。

Ollama、マイク、Windows日本語音声を使用可能にしてから、統合アプリを起動します。

```powershell
python -m chambara_app
```

インストール時に作られる次のコマンドでも同じです。

```powershell
chambara-app
```

別PCから接続する場合は、サーバーPC自身の有線LAN IPv4を明示します。

```powershell
chambara-app --host 192.168.10.10 --port 8765
```

指定できる主な引数は次のとおりです。

| 引数 | 既定値 | 意味 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | WebSocketの待ち受けIP |
| `--port` | `8765` | WebSocketの待ち受けポート |
| `--settings` | `config/settings.json` | 会話・音声設定 |
| `--examples` | `data/conversation_examples.json` | 会話例 |
| `--bgm-config` | `config/bgm_settings.json` | 楽曲と判定区間 |
| `--audio-dir` | `data/bgm` | BGMファイルのフォルダー |

統合アプリは会話中にEnterや`q`を受け付けません。アプリ全体を終了するときはCtrl+Cを押します。終了時はBGM、会話処理、WebSocket接続を片付けます。

### 戦闘中のBGM・パリイ表示

戦闘中は、PowerShellなどの対話端末でBGM状態を100msごとに同じ1行へ更新します。追加の起動引数は不要です。

```text
簡単 | 1234.5 ms | 判定:有効 | 周回1
普通 | 2000.0 ms | 判定:無効 | 周回2
```

曲の読み込み直後や音声同期の復帰中は、「音声出力開始待ち」「音声同期確認中」「再生時刻確認中」のいずれかを表示します。パリイ信号を受けると、通信ログとは別の行へ結果を表示してから現在の状態行を復元します。

```text
パリイ成功: 簡単 1234.5 ms / 周回1
パリイ失敗: 簡単 1450.0 ms / 周回1 範囲外または成功済み
パリイ失敗: 簡単 判定準備中（再生位置を取得できません）
```

PowerShellでCopilot設定を使う場合の起動例です。

```powershell
Set-Location "D:\Program_Folder\Codex_ws\voice_recognition_app"
.\.venv\Scripts\Activate.ps1
chambara-app --settings .\config\settings.copilot.json
```

出力をファイルへリダイレクトした場合は時刻を100msごとに出さず、難易度、判定状態、周回、待機状態が変わったときだけ改行して記録します。パリイ結果などのイベントはすべて記録します。

## 4. 模擬画面を開く

統合アプリとは別のPowerShellを開きます。

```powershell
Set-Location "D:\Program_Folder\Codex_ws\voice_recognition_app"
.\.venv\Scripts\Activate.ps1
python -m http.server 8000 --bind 127.0.0.1 --directory integration_test
```

ブラウザーで[統合動作確認画面](http://127.0.0.1:8000/client.html)を開き、「接続」を押します。最初に`connected`、続けて`standby`が表示されれば準備完了です。

## 5. JSON通信仕様

### 外部統合アプリから本アプリ

```json
{"type":"signal","name":"game_start"}
{"type":"signal","name":"battle_start"}
{"type":"signal","name":"parry"}
{"type":"signal","name":"weak_point_count","count":2}
{"type":"signal","name":"battle_end"}
```

有効な信号には最初に受付通知を返します。弱点信号では`count`も返します。

```json
{"type":"received","name":"parry","text":"パリイを受信しました。"}
{"type":"received","name":"weak_point_count","text":"弱点残り回数 2 を受信しました。","count":2}
```

### 本アプリから外部統合アプリ

```json
{"type":"connected","text":"接続しました"}
{"type":"status","value":"standby"}
{"type":"status","value":"pre_interview"}
{"type":"status","value":"pre_interview_finished"}
{"type":"status","value":"post_interview"}
{"type":"status","value":"post_interview_finished"}
{"type":"parry_result","value":"success"}
{"type":"parry_result","value":"failure","reason":"outside_window_or_already_successful"}
{"type":"parry_result","value":"failure","reason":"not_ready"}
```

`not_ready`は、BGM開始直後や音声出力の同期を確認中で再生位置を決められない場合です。通常の失敗は、判定範囲外または同じ周回の同じ判定区間ですでに成功した場合です。

不正JSON、現在状態に合わない信号、弱点残数の増加には、現在状態付きのエラーを返します。接続は閉じません。

```json
{"type":"error","text":"パリイは戦闘中だけ受け付けます。","state":"standby"}
```

## 6. 手動確認シナリオ

### 人間勝利

1. 接続直後に`standby`を受信する。
2. 「ゲームスタート」を送り、`received`と`pre_interview`を受信する。
3. 質問の読み上げ後に自動録音が始まり、認識結果の表示後に自動でコメントが生成されることを確認する。Enterは押さず、`pre_interview_finished`を受信する。
4. 「戦闘開始」を送り、easyの再生と「簡単」の状態行を確認する。
5. 「パリイ」を複数回送り、毎回`received`の後に`parry_result`が届き、ターミナルにも「パリイ成功」または「パリイ失敗」が1回表示されることを確認する。
6. 残数2でnormalと「普通」、残数1でhardと「難しい」へ変わることを確認する。
7. 残数0を送り、BGM停止、`post_interview`、人間勝利用質問を確認する。
8. 終了インタビューも自動録音で完了し、`post_interview_finished`、`standby`を受信する。

### ロボット勝利と連続ゲーム

1. 次の`game_start`から戦闘開始まで進める。
2. 残数0より前に「戦闘終了」を送る。
3. BGM停止、`post_interview`、ロボット勝利用質問を確認する。
4. 終了後にstandbyへ戻り、もう一度`game_start`を受理できることを確認する。

### 例外経路

- 開始前インタビュー中に`battle_start`を送り、質問や録音が中断され、`pre_interview_finished`なしでBGMが始まることを確認する。
- 戦闘外で`parry`を送り、状態付き`error`を確認する。
- 残数1の後に2を送り、BGMが戻らず`error`になることを確認する。
- 戦闘中に切断し、BGMが停止することを確認する。再接続後は新しい`standby`から始める。
- 2つ目のブラウザーを接続し、エラー後にコード1008で切断されることを確認する。

## 7. 自動テストと実機確認の境界

```powershell
python -m unittest discover -s tests -v
```

単体テストは偽のマイク、LLM、TTS、BGMを使い、状態遷移とJSONを確認します。CodexのWindowsサンドボックスでは`asyncio`の自己通信用socketを作れないため、非同期統合テストを明示的にスキップします。通常のWindows環境では実行されます。

実際のBGM出力時刻、100ms状態表示、パリイ同期精度、マイク、連続TTS、別PCのLAN経路、外部統合アプリとの接続は、上記の模擬画面または実機で別に確認してください。テスト成功だけで実機確認済みとは扱いません。
