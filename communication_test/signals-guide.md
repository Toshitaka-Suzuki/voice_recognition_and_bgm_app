# 信号の模擬送受信ガイド

ブラウザーから5種類の入力信号を送り、Pythonのターミナルから状態通知とパリイ結果を送ります。各信号は独立しており、任意の順番で何回でも送れます。文字の送受信も同じ接続で利用できます。

## 1. 送信と受信の役割

```text
ブラウザー                                 Pythonターミナル
信号ボタン・残り回数 ── signalのJSON ──────→ 受信・形式確認
受付通知を表示       ←─ receivedのJSON ──── 受付通知を返す
状態・判定結果を表示 ←─ status / parry_result ─ 数字キーで送信
```

ブラウザーからパリイを送っても、Pythonは自動で成功・失敗を決めません。Pythonの6・7キーはパリイ入力前でも使えます。状態通知も自動遷移せず、1〜5キーで選びます。

## 2. 起動する

初回は [READMEの準備手順](README.md#3-準備初回のみ) で `communication_test/.venv` と依存ライブラリを用意します。

### 同じPCで使う

**Python側はWindows TerminalのPowerShell** を開き、以下を実行します。仮想環境のPythonを直接指定するので、PowerShellの有効化スクリプトは不要です。

```powershell
Set-Location "D:\Program_Folder\Codex_ws\voice_recognition_app\communication_test"
.\.venv\Scripts\python.exe server.py
```

待ち受けとキー操作一覧が出ます。このターミナルを開いたままにしてください。キー操作には、このターミナルにフォーカスを置きます。Enterは押しません。IDEの出力欄や標準入力をリダイレクトした環境ではWindowsコンソール入力を使えない場合があるため、上記の起動方法を使います。

画面配信用にはもう1つのPowerShellを開きます。

```powershell
Set-Location "D:\Program_Folder\Codex_ws\voice_recognition_app\communication_test"
.\.venv\Scripts\python.exe -m http.server 8000 --bind 127.0.0.1 --directory .
```

ブラウザーで [サンプル画面](http://127.0.0.1:8000/client.html) を開き、接続先 `ws://127.0.0.1:8765` のまま「接続」を押します。ブラウザーの接続ボタンは、キー送信を開始する前に押してください。

### 有線LANの別PCで使う

PC AはPython、PC Bはブラウザーを動かします。IPアドレス確認とファイアウォールの手順は [READMEのLAN手順](README.md#41-別pc有線lanでの起動と操作) にあります。

PC AのPowerShellで、**PC A自身の有線LANのIPv4** を指定します。

```powershell
Set-Location "D:\Program_Folder\Codex_ws\voice_recognition_app\communication_test"
.\.venv\Scripts\python.exe server.py --host 192.168.10.10 --port 8765
```

上のIPは例です。実際のPC AのIPへ置き換えます。フォルダの場所も実際の配置先へ置き換えてください。

PC Bには `client.html` をコピーし、Python標準のHTTPサーバーで画面を配信します。`websockets` の導入は不要です。

```powershell
Set-Location "D:\websocket_client"
python -m http.server 8000 --bind 127.0.0.1 --directory .
```

PC Bのブラウザーで `http://127.0.0.1:8000/client.html` を開き、WebSocket接続先を `ws://192.168.10.10:8765` に変更します。こちらも実際のPC AのIPを使います。PC Bでの `127.0.0.1` はPC B自身を指します。

## 3. ブラウザーから入力信号を送る

| 操作 | 送信するJSON |
| --- | --- |
| ゲームスタート | `{"type":"signal","name":"game_start"}` |
| 戦闘開始 | `{"type":"signal","name":"battle_start"}` |
| パリイ | `{"type":"signal","name":"parry"}` |
| 残り回数を送信（例：3） | `{"type":"signal","name":"weak_point_count","count":3}` |
| 戦闘終了 | `{"type":"signal","name":"battle_end"}` |

`type` はメッセージの種類、`name` は信号名、`count` は弱点の残り回数です。残り回数は0以上の整数で、JavaScriptで正確に表せる上限 `9007199254740991` まで使えます。空欄・負数・小数は送れません。Pythonも同じ検証を行います。

Pythonは受信した信号に対して、その送信元へ受付通知を返します。

```json
{"type":"received","name":"parry","text":"パリイを受信しました。"}
```

残り回数の場合は値も返します。

```json
{"type":"received","name":"weak_point_count","text":"弱点残り回数 3 を受信しました。","count":3}
```

受付通知は、信号を受け取り形式を確認したことを表します。ゲーム処理が完了したことを表すものではありません。残り回数は自動で減らず、0を送っても戦闘終了信号は自動で出ません。

## 4. Pythonから出力信号を送る

Pythonサーバーのターミナルで次のキーを押します。接続中のすべてのブラウザーに同じJSONを送ります。

| キー | 内容 | 送信するJSON |
| --- | --- | --- |
| 1 | スタンバイ | `{"type":"status","value":"standby"}` |
| 2 | 開始前インタビュー | `{"type":"status","value":"pre_interview"}` |
| 3 | 開始前インタビュー終了 | `{"type":"status","value":"pre_interview_finished"}` |
| 4 | 終了インタビュー | `{"type":"status","value":"post_interview"}` |
| 5 | 終了インタビュー終了 | `{"type":"status","value":"post_interview_finished"}` |
| 6 | パリイ成功 | `{"type":"parry_result","value":"success"}` |
| 7 | パリイ失敗 | `{"type":"parry_result","value":"failure"}` |

`value` は状態または結果の値です。ブラウザーはJSONを受信し、日本語へ変換して「最後に受信した状態」「最後に受信したパリイ結果」を更新します。状態と結果はそれぞれ独立して表示します。

`H` は操作一覧の再表示、`Q` はサーバーの終了です。小文字も使えます。Ctrl+Cでも終了できます。1〜7以外のキーには操作案内を表示します。矢印などの特殊キーは無視します。

## 5. 任意の順番で試す

1. ブラウザーで接続します。まず接続通知だけが届き、状態と判定結果は「未受信」です。
2. ブラウザーで「戦闘終了」を先に押します。順序違反にはならず、受付通知が返ります。
3. 「パリイ」を2回押します。両方とも受け付けられ、パリイ結果はまだ「未受信」です。
4. Pythonのターミナルで `7` を押します。ブラウザーに「パリイ失敗」が出ます。
5. Pythonで `5`、`1`、`2` を押します。状態通知がそれぞれ届き、最新表示は「開始前インタビュー」になります。
6. 残り回数を3、0、5の順に送ります。各値の受付通知を確認します。
7. 「ゲームスタート」「戦闘開始」も送信して受付を確認します。
8. Pythonで `6` を2回押します。入力の有無とは関係なく、成功結果が2回届きます。
9. 必要なら別のブラウザータブでも接続し、同じ出力信号が両方に届くことを確認します。受付通知は信号を送ったタブだけに返ります。

## 6. ログの見比べ方

ブラウザーの「送信 → Python」とPythonの「受信 ← ブラウザー」を対応させます。Pythonの「送信 → ブラウザー」とブラウザーの「受信 ← Python」も、同じJSONになります。Pythonのログは接続相手のアドレスも表示し、複数接続を区別できます。

```text
ブラウザー：送信 → Python (...) {"type":"signal","name":"battle_end"}
Python：   受信 ← ブラウザー (...) {"type":"signal","name":"battle_end"}
Python：   送信 → ブラウザー (...) {"type":"received","name":"battle_end",...}
ブラウザー：受信 ← Python {"type":"received","name":"battle_end",...}
```

履歴には生のJSONも表示します。複数PCの時計が違う場合は時刻だけで比較せず、方向・信号名・値も確認してください。

## 7. 不正入力・切断・再接続

- 未接続ではブラウザーの送信ボタンが無効になります。Pythonで1〜7を押すと「未送信」と表示し、その信号を保存しません。
- 不正なJSON、未定義の信号名、不正な残り回数には `{"type":"error","text":"説明"}` を返します。次のメッセージは引き続き受け付けます。
- 切断すると最新表示を「未受信」に戻します。通信履歴はページを再読み込みするまで残ります。
- 再接続時は接続通知だけを送ります。過去の状態・判定結果・入力信号は再送しません。自動再接続もしません。
- 1つのブラウザーが切断しても、ほかの接続の通信とサーバーは継続します。
- 操作キーが反応しない場合は、Pythonを動かしているWindows TerminalのPowerShellにフォーカスがあるか確認してください。コンソールを使わない起動方法では、受信機能だけが動き、キー操作の案内が表示されます。

## 8. コードを読む

| 目印 | 処理 |
| --- | --- |
| P7（server.py） | 入力信号の名前・値を確認し、受付通知を作る |
| P8（server.py） | キーを出力信号へ変換して全接続へ送る |
| P9（server.py） | キー入力を短い間隔で確認し、通信処理と並行して動かす |
| B9（client.html） | 状態・パリイ結果・受付通知を受け取り表示する |
| B10（client.html） | 文字と信号で共通のJSON送信を使う |
| B11（client.html） | ボタンと残り回数から入力信号を作る |

`msvcrt.kbhit()` は入力があるかを確認し、あるときだけ `getwch()` で読みます。待ち続ける `input()` を使わないので、キーを押さなくても受信は進みます。`asyncio.sleep(0.05)` は処理を50ms休ませ、その間にWebSocketの接続・受信・送信を進められるようにします。

## 9. 終了とテスト

Python側は `Q` またはCtrl+C、HTTPサーバー側はCtrl+Cで終了します。

```powershell
Set-Location "D:\Program_Folder\Codex_ws\voice_recognition_app\communication_test"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

自動テストは実際のローカルWebSocket接続を使います。ターミナルへの物理的なキー入力と2台のPCのLAN経路は自動テストに含まれません。
