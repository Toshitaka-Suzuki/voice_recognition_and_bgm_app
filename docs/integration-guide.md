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

`cobopro-chambara`の「プレイ画面」→「会話・BGM連携」から接続できます。
画面がHP・制限時間・弱点表示を管理し、本アプリが会話・BGM・パリイ判定を担当します。
接続時に通信仕様v2と会話モードを通知します。旧来の信号（request_idなし）も引き続き受理します。

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

### 会話の進行と発話の表示

実際の会話では、通信仕様v2に追加の `conversation_progress` 通知を送ります。既存の状態通知とゲーム開始・終了の順序は変わりません。模擬会話や古い会話部品はこの通知を送らない場合があります。

```json
{"type":"conversation_progress","phase":"strategy","stage":"thinking","text":"頑張ります"}
```

`phase` は `strategy`（作戦）または `feedback`（感想）です。

| stage | 意味 | 追加項目 |
| --- | --- | --- |
| `question` | 質問を表示して読み上げる | `text`: ロボットの質問 |
| `preparing` | 録音を準備し、環境音を測定する | なし |
| `listening` | 環境音測定が終わり、話しかけられる | なし |
| `recognizing` | 録音した音声を文字へ変換する | なし |
| `thinking` | 認識した発言を表示し、返答を作る | `text`: 認識した参加者の発言 |
| `speaking` | 返答を表示して読み上げる | `text`: ロボットの返答 |
| `retry` | 再試行、または読み上げ失敗を案内する | `reason`: 下記の理由 |

`retry.reason` は `no_speech`（発話なし）、`empty_transcript`（認識文なし）、`recording_failed`（録音失敗）、`recognition_failed`（音声認識失敗）、`reply_failed`（返答生成失敗）、`speech_failed`（読み上げ失敗）のいずれかです。読み上げ失敗時は文章の表示を残して従来どおり先へ進み、それ以外は録音から再試行します。画面へ内部のエラー詳細は送りません。

`thinking` は返答生成中という状態であり、LLMの内部の思考内容は送りません。通知は発生順に送り、会話完了通知より前に送信します。中断・対戦へのスキップ・通信切断後に届く古い会話の通知は破棄します。表示通知の送信失敗によって録音や会話処理を止めることはありません。

弱点残り回数は絶対値です。現在値以下の0〜3だけを受理します。同じ値は受付通知だけを返し、BGMを再開しません。3から1へ飛ばした場合は直接hardへ切り替わります。

## 3. 起動する

初回はPowerShellでメイン環境へ依存関係をインストールします。

```powershell
Set-Location "D:\Program_Folder\Codex_ws\voice_recognition_app"
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

`Activate.ps1`の実行がポリシーで拒否された場合は、そのPowerShellで`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`を実行してから再度有効化します。

従来のHaruka設定ではOllama、マイク、Windows日本語音声を使用可能にしてから、統合アプリを起動します。MacまたはVOICEVOXの声を使う場合は[VOICEVOXガイド](voicevox-guide.md)の設定・起動手順を使ってください。

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
| `--mock-conversation` | 無効 | 会話を約3秒で完了する模擬処理に置換 |
| `--silent-audio` | 無効 | 模擬会話モードで音声出力を省略 |

統合アプリは会話中にEnterや`q`を受け付けません。アプリ全体を終了するときはCtrl+Cを押します。終了時はBGM、会話処理、WebSocket接続を片付けます。

### LLMなしでブラウザー連携を確認する

既存の環境では`python -m chambara_app --mock-conversation`で起動できます。
このモードはマイク・認識モデル・LLM・TTSの設定読込と起動チェックを行いません。
会話を模擬しても、BGMの音声出力と判定区間は通常の設定を使用します。
Windowsに加えてMacでも起動できます。実際の会話はHaruka設定ではWindows向けです。Macでは[VOICEVOX設定](voicevox-guide.md)を使います。

新しい環境で、LLMなどの依存を入れずに確認する場合は、Python 3.11または3.12で仮想環境を作り、次の最小依存だけをインストールしてください。

```sh
python -m pip install -r requirements-integration-test.txt
```

パッケージ全体をインストールしていない場合は、プロジェクト直下で`src`を指定して起動します。

Mac / Linux:

```sh
PYTHONPATH=src python -m chambara_app --mock-conversation
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m chambara_app --mock-conversation
```

音声デバイスも使わず確認するときは、上記に`--silent-audio`を追加します。
この場合も楽曲の長さと設定された判定区間を使用しますが、再生位置は単調時計で模擬します。
音は出ず、音声出力と接触の同期精度は確認できません。画面にも模擬モードを表示します。
`--silent-audio`を通常の会話と組み合わせることはできません。

開始前の模擬会話→カウントダウン→BGM開始通知→受け・命中→感想の模擬会話→待機まで確認できます。
画面の入力方法を「ゲームテスト（モック）」にするとSpaceで受け、1〜3で対応する弱点への命中、Bで紙風船破損を模擬します。
無音版でもパリイが必ず成功するわけではありません。通常版と同じ判定区間を使います。

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
{"type":"signal","name":"game_cancel"}
{"type":"signal","name":"heartbeat","request_id":"ping-1"}
```

有効な信号には最初に受付通知を返します。弱点信号では`count`も返します。

```json
{"type":"received","name":"parry","text":"パリイを受信しました。"}
{"type":"received","name":"weak_point_count","text":"弱点残り回数 2 を受信しました。","count":2}
```

### 本アプリから外部統合アプリ

```json
{"type":"connected","text":"接続しました","protocol_version":2,"conversation_mode":"live"}
{"type":"status","value":"standby"}
{"type":"status","value":"pre_interview"}
{"type":"status","value":"pre_interview_finished"}
{"type":"status","value":"battle_started"}
{"type":"status","value":"post_interview"}
{"type":"status","value":"post_interview_finished"}
{"type":"parry_result","value":"success"}
{"type":"parry_result","value":"failure","reason":"outside_window_or_already_successful"}
{"type":"parry_result","value":"failure","reason":"not_ready"}
```

`not_ready`は、BGM開始直後や音声出力の同期を確認中で再生位置を決められない場合です。通常の失敗は、判定範囲外または同じ周回の同じ判定区間ですでに成功した場合です。

`conversation_mode`は`live`、`mock`、`mock_silent`のいずれかです。
`battle_start`の`received`は受付だけを表します。`game.start()`成功後に`battle_started`を送ります。
音声出力開始直後はまだ`not_ready`になり得ます。開始失敗では`error`→`standby`になり、`battle_started`は送りません。

すべての信号に省略可能な1〜64文字の`request_id`を付けられます。
`received`に同じIDを返し、`parry_result`にも元の`parry`のIDを付けます。
IDなしの従来の信号にはIDを追加しません。これは応答の対応付け用で、重複要求の排除や再送を保証するものではありません。
パリイの判定時刻は従来どおりサーバー受信時刻です。

```json
{"type":"signal","name":"parry","request_id":"parry-42"}
{"type":"received","name":"parry","text":"パリイを受信しました。","request_id":"parry-42"}
{"type":"parry_result","value":"success","request_id":"parry-42"}
```

`game_cancel`はすべての状態で受理し、BGM停止・会話キャンセル・進行破棄の後、`standby`を送ります。
中断では感想の会話を始めません。`heartbeat`は状態を変えず`received`だけを返します。
ブラウザーは5秒ごとに接続を確認し、開始・終了処理や信号応答が10秒途切れた場合は接続を閉じます。
会話そのものはこの10秒制限の対象ではなく、会話中もheartbeatを継続します。

画面のEsc・準備への移動・センサ切断はゲーム中断になります。途中からの再開はしません。
紙風船破損と時間切れは`battle_end`でロボット勝利として扱います。
ブラウザー内の仮BGMは連携中に止め、効果音だけを使用します。画面の効果音OFFはPython側の音声をミュートしません。
ROSや実物のライト制御、認識文・返答本文の画面への配信はこの通信には含みません。

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

## 8. 4拍ガイドによるブラウザー連携

統合版は既定で4拍ガイドを有効にします。`--mock-conversation`でも実際のBGMと予告音を再生できます。
「タン・タン・タン・カン！」の最後の高い音で受けます。`config/bgm_settings.json`の基準位置を4個ずつまとめ、4個目を目標にします。
元の曲と設定ファイルは変更せず、統合版の起動時に判定区間と予告音レイヤーを作ります。単独の`chambara_bgm`は従来どおりです。
ガイド中は全難易度で目標の前後150ms、終端を含まない300ms区間を使用します。曲末で区間がはみ出る4拍は使用しません。
`--no-rhythm-guide`を付けると、従来の判定幅・毎拍判定へ戻せます。旧ブラウザー・既存通信サンプルを使う場合もこのオプションを指定してください。

### 追加の通信仕様（v2の任意拡張）

- `connected`に`rhythm_guide: true`がある場合だけ、ブラウザーは下記のガイド信号を送ります。
- `rhythm_sync`への応答は`rhythm_state`。同じ`request_id`を返し、`ready`、`active`、`generation`、`stage`、`position_ms`、4個の`beats_ms`、`window_ms`を含みます。時刻は曲の周回を含む絶対再生位置（ms）で、別PCの時計を直接比較しません。再生位置が不明なら`ready: false`です。
- `defense_pause`は予告音と受け判定を止めます。BGMは継続します。成功・失敗を確定した時点でもPython側で自動停止します。
- `defense_ready`は次の受けを準備します。現在位置から120ms以上先の完全な4拍を選び、その1拍目から予告音を再開します。以前の攻撃区間は受け付けません。
- `parry_result`には`timing: "on_time" | "early" | "late"`と`delta_ms`（負は早い、正は遅い）を追加します。ガイド中の最寄りの有効な目標を基準にします。
- 音声の準備中は予告も判定も確定しません。画面は古い同期情報を使用し続けず、再同期を待ちます。

予告音はBGMと同じ出力コールバックでサンプル位置を合わせて混ぜます。画面は200msごとに音声位置を問い合わせ、往復時間の半分を補正して補間します。
これはローカルでの体験確認用です。実際のロボット動作とは未連携で、入力のネットワーク到着遅延・画面遅延・USB遅延の厳密な補正はしていません。
