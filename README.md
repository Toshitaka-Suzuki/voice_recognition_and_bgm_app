# チャンバラロボット会話アプリ

6軸ロボットアームを使ったチャンバラアトラクション向けの、ローカル音声会話プロトタイプです。音声認識結果をコンソールで確認し、Enterで確定した場合だけローカルLLMへ送ります。

## 会話の流れ

1. アプリが「作戦・意気込み」を読み上げます。
2. Enterで録音を開始します。発話後に2秒間の無音が続くと終了します。
3. faster-whisperの認識結果が表示されます。
4. Enterで確定するか、`q` で終了します。
5. LLMのコメントを表示・読み上げます。
6. アトラクション終了後にEnterを押し、「感想」について同じ処理を行います。

音声、認識文、返答、ログはファイルへ保存しません。

## プログラムを理解したい方へ

Python初心者向けに、アプリの構成とコードで使われているPython機能を別々の資料へまとめています。

- [Python学習ガイド](docs/python-learning-guide.md)：Pythonの基礎、Git Bashのコマンド、標準機能、外部ライブラリを説明します。
- [プログラム構成解説書](docs/program-guide.md)：各ファイルの役割、処理の流れ、設定、エラー処理、テスト構成を説明します。

次の順番で読むと理解しやすくなります。

1. このREADMEでセットアップと実行方法を確認する
2. Python学習ガイドで基礎用語を確認する
3. プログラム構成解説書で処理全体を理解する
4. `src/chambara_talk/cli.py`から実際のコードを読み始める
5. `tests`のテストコードで各部品の期待動作を確認する

## 必要環境

- Windows 10または11（64bit）
- Python 3.11（64bit）
- 入力マイク
- [Ollama](https://ollama.com/) とローカルLLM
- Windowsの日本語音声 `Microsoft Haruka Desktop - Japanese`

現在の開発PC向け既定モデルは `qwen2.5:3b`、高性能な本番PC向けモデルは `gpt-oss:20b` です。

## セットアップ

Git Bashを開き、プロジェクトフォルダへ移動して次を実行します。Windowsの `D:\Program_Folder\Codex_ws\voice_recognition_app` は、Git Bashでは `/d/Program_Folder/Codex_ws/voice_recognition_app` と表記します。

```bash
cd /d/Program_Folder/Codex_ws/voice_recognition_app
py -3.11 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -e .
ollama pull qwen2.5:3b
```

仮想環境が有効になると、Git Bashのプロンプト先頭に `(.venv)` と表示されます。

別のターミナルでOllamaが起動していることを確認してから、アプリを実行します。

```bash
python -m chambara_talk
```

インストール時に作られるコマンドでも起動できます。

```bash
chambara-talk
```

初回はfaster-whisperモデルの取得が発生します。モデル取得後の会話処理はローカルで完結します。

### pyttsx3 2.99が導入済みの場合

Windows版pyttsx3 2.99には、最初の読み上げ以降が無音になる不具合があります。このアプリでは動作確認済みの2.98へ固定しています。既存環境を更新した場合は、仮想環境を有効にして次を実行してください。

```bash
python -m pip install --force-reinstall "pyttsx3==2.98"
```

サンドボックス検証環境も使用する場合は、環境を切り替えて同じ更新を行います。

```bash
deactivate
source .sandbox-venv/Scripts/activate
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
- `llm.model`: 実際にOllamaで使用するモデル
- `tts.voice_match`: Windows音声名から検索する文字列

高性能PCでgpt-oss-20bを使用する場合は、次を実行します。

```bash
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

```bash
python -m unittest discover -s tests -v
```

単体テストでは外部サービス、マイク、音声合成をモックします。実際のマイク、faster-whisper、Ollama、Windows音声を使う確認は実機で行ってください。

## トラブルシューティング

- Ollamaへ接続できない場合は、Ollamaが起動しているか確認してください。
- モデルがない場合は、表示された `ollama pull ...` コマンドを実行してください。
- 日本語音声がない場合は、Windowsの「時刻と言語」から日本語の音声機能を追加してください。
- 騒音で録音が早く終わる、または始まらない場合は、`minimum_rms_threshold` と `noise_multiplier` を調整してください。
