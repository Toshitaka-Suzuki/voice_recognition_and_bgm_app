# チャンバラロボット会話アプリ

6軸ロボットアームを使ったチャンバラアトラクション向けの音声会話プロトタイプです。音声認識結果をコンソールで確認し、Enterで確定した場合だけLLMへ送ります。LLMはローカルのOllama、またはクラウドのGitHub Copilotを選べます。

## 会話の流れ

1. アプリが「作戦・意気込み」を読み上げます。
2. Enterで録音を開始します。発話後に2秒間の無音が続くと終了します。
3. faster-whisperの認識結果が表示されます。
4. Enterで確定するか、`q` で終了します。
5. LLMのコメントを表示・読み上げます。
6. アトラクション終了後にEnterを押し、「感想」について同じ処理を行います。

アプリ独自の音声、認識文、返答、ログはファイルへ保存しません。Copilot SDK側の一時保存については、下記のクラウド利用時の説明を確認してください。

## プログラムを理解したい方へ

Python初心者向けに、アプリの構成とコードで使われているPython機能を別々の資料へまとめています。

- [Python学習ガイド](docs/python-learning-guide.md)：Pythonの基礎、PowerShellのコマンド、標準機能、外部ライブラリを説明します。
- [プログラム構成解説書](docs/program-guide.md)：各ファイルの役割、処理の流れ、設定、エラー処理、テスト構成を説明します。
- [統合動作確認ガイド](docs/integration-guide.md)：会話、BGM、パリイ判定、WebSocket通信を一連で動かす方法とJSON仕様を説明します。

次の順番で読むと理解しやすくなります。

1. このREADMEでセットアップと実行方法を確認する
2. Python学習ガイドで基礎用語を確認する
3. プログラム構成解説書で処理全体を理解する
4. `src/chambara_talk/cli.py`から実際のコードを読み始める
5. `tests`のテストコードで各部品の期待動作を確認する

## 必要環境

BGM再生とパリイ判定だけを試す独立アプリもあります。
起動・設定・操作は[BGMデバッグアプリの説明](docs/bgm-guide.md)を参照してください。
`python -m chambara_bgm`で起動し、Spaceでパリイ、Lでライフ減少信号を模擬できます。
パリイ成功では曲を変えず、ライフ減少信号で難易度を進めます。

全機能をまとめて外部統合アプリまたはブラウザー模擬画面から操作する場合は、`python -m chambara_app`または`chambara-app`を起動します。統合版では質問の読み上げ後に録音を自動開始し、認識結果を表示してからEnter確認なしでLLMへ送ります。戦闘中はBGMの種類、再生時間、判定状態、周回とパリイ結果をターミナルへ表示します。終了操作はCtrl+Cです。詳しい順序と模擬画面の起動方法は[統合動作確認ガイド](docs/integration-guide.md)を参照してください。

- Windows 10または11（64bit）
- Python 3.11（64bit）
- 入力マイク
- [Ollama](https://ollama.com/) とローカルLLM、またはGitHub Copilotの利用権限とインターネット接続
- Windowsの日本語音声 `Microsoft Haruka Desktop - Japanese`

現在の開発PC向け既定モデルは `qwen2.5:3b`、高性能な本番PC向けモデルは `gpt-oss:20b` です。

## セットアップ

PowerShellを開き、プロジェクトフォルダへ移動して次を実行します。

```powershell
Set-Location "D:\Program_Folder\Codex_ws\voice_recognition_app"
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
ollama pull qwen2.5:3b
```

仮想環境が有効になると、PowerShellのプロンプト先頭に `(.venv)` と表示されます。`Activate.ps1`の実行がポリシーで拒否された場合は、そのPowerShellで`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`を実行してから、もう一度有効化してください。この設定は現在のPowerShellプロセスだけに適用されます。

別のターミナルでOllamaが起動していることを確認してから、アプリを実行します。

```powershell
python -m chambara_talk
```

インストール時に作られるコマンドでも起動できます。

```powershell
chambara-talk
```

初回はfaster-whisperモデルの取得が発生します。Ollamaモードではモデル取得後の会話処理はローカルで完結します。

### GPUなしのPCでGitHub Copilotを使う

公式Python SDKを使い、LLM生成をCopilot側で実行します。VS Codeの拡張機能への入力操作は不要です。Ollamaの起動・モデル取得も不要です。

PowerShellで、アプリ用仮想環境を有効にして追加依存をインストールします。

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[copilot]"
python -m copilot download-runtime
copilot login
python -m chambara_talk --settings .\config\settings.copilot.json
```

`copilot login`は導入済みCopilot CLIで行います。ログイン済みの場合は再ログイン不要です。SDKは互換性のあるランタイムを管理します。CLIと同じWindowsユーザーで起動してください。SDK 1.0.19へ固定しています。

サンドボックス検証環境を使用する場合は、上記の有効化行を`.\.sandbox-venv\Scripts\Activate.ps1`へ変更してください。

`settings.copilot.json`は音声認識を`small`・`cpu`・`int8`にしているためCUDAは不要です。録音と読み上げはこれまでどおりPC上で実行します。CPUでは認識に時間がかかる場合があります。

- `llm.provider`：`ollama`または`copilot`。省略時は`ollama`
- `llm.model`：Copilotでは既定値`auto`。明示モデルは起動時に利用可能か確認
- `llm.timeout_seconds`：起動確認・各生成の待ち時間。既定値120秒
- `llm.max_response_chars`：共通の返答文字数上限。既定値80文字
- `llm.temperature`：Ollama用。Copilot SDKは同じ生成設定を公開していないためCopilotでは使用しない

Copilotが失敗した場合は日本語エラーを表示し、録音待機へ戻ります。自動でOllamaへ切り替えることはありません。従来のローカル方式へ戻す場合は、`--settings`を省略して起動します。

クラウドへ送信するのは、認識文、現在フェーズの会話例全件、用語一覧、システム指示です。単独版ではEnterで確定した認識文、統合版では画面表示後に自動確定した認識文を送ります。録音音声は送信しません。通常は各フェーズ1回ずつ生成し、80文字超の応答では短縮のため1回追加生成します。これらはCopilotの利用枠を消費します。

SDKの履歴検索・メモリ・ツール・設定探索を無効化し、各生成後にアプリが作ったセッションをSDKから削除します。既存のCopilot会話やログイン情報は削除しません。空の一時作業フォルダは終了時に削除します。SDK/CLIが生成するログや異常終了時のデータ残存、GitHub側の保持は完全には保証できません。クラウド方式はこの前提を許容するデバッグ用です。

詳しくは[公式SDK](https://github.com/github/copilot-sdk)と[認証説明](https://docs.github.com/en/copilot/how-tos/copilot-sdk/auth/authenticate)を参照してください。

### pyttsx3 2.99が導入済みの場合

Windows版pyttsx3 2.99には、最初の読み上げ以降が無音になる不具合があります。このアプリでは動作確認済みの2.98へ固定しています。既存環境を更新した場合は、仮想環境を有効にして次を実行してください。

```powershell
python -m pip install --force-reinstall "pyttsx3==2.98"
```

サンドボックス検証環境も使用する場合は、環境を切り替えて同じ更新を行います。

```powershell
deactivate
.\.sandbox-venv\Scripts\Activate.ps1
python -m pip install --force-reinstall "pyttsx3==2.98"
```

## 設定

主な動作設定は [`config/settings.json`](config/settings.json) にあります。

- `audio.silence_seconds`: 発話終了と判定する無音時間（既定値 `2.0`）
- `audio.minimum_rms_threshold`: 発話検出の最小音量しきい値
- `audio.noise_multiplier`: 測定した環境音に掛ける倍率
- `audio.input_device`: マイク番号または名前。`null` はWindowsの既定マイク
- `speech_recognition.model`: faster-whisperモデル（既定値 `small`）
- `speech_recognition.device`: `auto`、`cpu`、`cuda` など
- `llm.provider`: LLMの接続先。省略時は`ollama`
- `llm.model`: 選択した接続先で使用するモデル
- `tts.voice_match`: Windows音声名から検索する文字列

高性能PCでgpt-oss-20bを使用する場合は、次を実行します。

```powershell
ollama pull gpt-oss:20b
```

その後、`settings.json` の `llm.model` を `gpt-oss:20b` に変更します。

## 会話例の追加

[`data/conversation_examples.json`](data/conversation_examples.json) の `strategy` または `feedback` 配列へ、次の形式で追加します。

```json
{
  "user": "利用者の発話例",
  "assistant": "明るい侍風のコメント例"
}
```

現在のフェーズに登録されている例は、件数を制限せず、記載順にすべてLLMへ渡されます。チャンバラ固有の単語は `domain_terms` に追加すると、faster-whisperの認識用プロンプトにも反映されます。

## テスト

依存関係のインストール後、次を実行します。

```powershell
python -m unittest discover -s tests -v
```

単体テストでは外部サービス、マイク、音声合成をモックします。実際のマイク、faster-whisper、Ollama、Windows音声を使う確認は実機で行ってください。

## トラブルシューティング

- Ollamaへ接続できない場合は、Ollamaが起動しているか確認してください。
- Copilot SDKがない場合は、仮想環境で`python -m pip install -e ".[copilot]"`を実行してください。
- Copilot未認証の場合は`copilot login`を実行してください。SDK起動時にも認証を確認します。
- Copilotのモデル不適合では、表示された利用可能モデルを指定するか`llm.model`を`auto`へ戻してください。
- Copilotの利用枠・権限エラーはGitHubの契約や組織ポリシー、タイムアウトは通信状態を確認してください。
- モデルがない場合は、表示された `ollama pull ...` コマンドを実行してください。
- 日本語音声がない場合は、Windowsの「時刻と言語」から日本語の音声機能を追加してください。
- 騒音で録音が早く終わる、または始まらない場合は、`minimum_rms_threshold` と `noise_multiplier` を調整してください。
