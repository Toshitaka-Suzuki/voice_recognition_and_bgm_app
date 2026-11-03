# VOICEVOXで声を聴き比べる

[VOICEVOX公式サイト](https://voicevox.hiroshiba.jp/)からOSに合うアプリをインストールして起動します。起動したアプリのエンジンへ、このPythonアプリから接続します。標準の接続先は `http://127.0.0.1:50021` です。MacはCPUで合成します。

## 最初にVOICEVOXを起動する

VOICEVOXは会話アプリとは別に動く音声合成サーバーです。会話アプリや試聴コマンドからは自動起動しないため、先にVOICEVOXを起動し、使用中は開いたままにします。通常は、公式サイトからインストールしたVOICEVOXアプリを開く方法をおすすめします。

このMacで用意したエンジン単体を使う場合は、ターミナルで次を実行し、そのターミナルを開いたままにします。

```sh
"$HOME/.local/share/chambara/voicevox-engine/run" --host 127.0.0.1 --port 50021 --disable_mutable_api
```

このパスのエンジンはこのMacに別途用意したものです。リポジトリには含まれず、Gitでcloneしてもインストールされません。別のPCでは公式アプリをインストールしてください。公式アプリとエンジン単体は、どちらか一方だけを起動します。

Macでは別のターミナルで接続を確認できます。

```sh
curl http://127.0.0.1:50021/version
```

バージョン番号が返れば準備完了です。接続エラーになる場合は、VOICEVOXが起動しているか、起動が完了しているかを確認します。その後に、以下の試聴コマンドまたは会話アプリを起動してください。

## Pythonを準備する（Mac）

Python 3.11または3.12が必要です。Mac標準の `python3` が3.9の場合は対応していません。`voice_recognition_and_bgm_app` のリポジトリ直下で作業します。

既に `.venv/bin/python --version` が3.11または3.12を表示する場合は、その環境を使い、作成手順を省略してください。`python3.12` が使える場合は `python3.12 -m venv .venv` で作成できます（3.11を使う場合は `python3.11`）。

`python3.11` と `python3.12` の両方が見つからない場合は、次の公式uvインストーラーとコマンドでPython 3.12・pip入りの仮想環境を用意できます。uvが導入済みなら1行目は不要です。

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
"$HOME/.local/bin/uv" venv --python 3.12 --seed .venv
.venv/bin/python --version
```

公式手順: [uvのインストール](https://docs.astral.sh/uv/getting-started/installation/)、[Pythonの取得](https://docs.astral.sh/uv/guides/install-python/)。以降は `.venv/bin/python` を使うため、`python3.11` を呼び出す必要はありません。

## まず声だけを比較する

この手順はマイク・Ollama・音声認識モデルを起動せず、音声をWAVファイルに書き出します。標準ライブラリだけで書き出せるため、試聴だけなら会話用の依存インストールは不要です。

Mac（リポジトリ直下）:

```sh
PYTHONPATH=src .venv/bin/python -m chambara_talk.voice_preview --compare
```

Windows / PowerShell:

```powershell
$env:PYTHONPATH = "src"
py -3.12 -m chambara_talk.voice_preview --compare
```

Windowsで3.11を使う場合は `py -3.12` を `py -3.11` に置き換えてください。`output/voice-samples/` に同じチャンバラのセリフを読んだ次のファイルができます。ファイルを開いて聴き比べてください。

| 声 | スタイルID | ファイル |
| --- | --- | --- |
| 玄野武宏・ノーマル | 11 | `voice-11.wav` |
| 剣崎雌雄・ノーマル | 21 | `voice-21.wav` |
| ちび式じい・ノーマル | 42 | `voice-42.wav` |

セリフを変える場合は `--text "いざ、勝負でござる！"`、ほかの声を使う場合は `--speaker 11 21` を指定します。`--compare` と `--speaker` は同時に指定できません。利用できる声とIDは `--list-voices` で表示できます。再実行すると同じ出力先の試聴ファイルを更新します。

## 選んだ声で会話する

`config/settings.voicevox.json` の `tts.speaker_id` を選んだIDへ変更します。初期値21は剣崎雌雄・ノーマルです。`speed_scale` は話速、`pitch_scale` は高さ、`intonation_scale` は抑揚、`volume` は音量です。最初は標準値で声を比較し、その後に少しずつ調整してください。pyttsx3の `rate` はVOICEVOXには使いません。

同じファイルの `llm.model` を `ollama list` に出るモデル名へ合わせます。このMac向けの初期値は `gemma4:12b` です。音声認識は `device: cpu` / `compute_type: int8` で、初回は認識モデルのダウンロードが発生します。

会話には `requirements.txt` の依存が必要です。Macでは上の手順で準備した仮想環境へインストールします。

```sh
.venv/bin/python -m pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m chambara_app --settings config/settings.voicevox.json
```

Windows / PowerShell（Python 3.11または3.12の仮想環境が既にあれば、作成の1行目は省略）:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m chambara_app --settings config/settings.voicevox.json
```

既存の仮想環境があれば再作成は不要です。

VOICEVOXとOllamaを起動したまま、ブラウザーの「会話・BGM連携」から接続します。単独の会話確認には `chambara_app` を `chambara_talk` へ置き換えます。実際の会話では `--mock-conversation` を付けません。

## 運用時の確認

- 起動時にVOICEVOXの接続と話者IDを確認します。起動していない場合は接続エラーになります。
- 中断後は生成済みの音声を再生せず、再生中ならその音声だけ停止します。合成APIの応答待ちはキャンセルで即座に打ち切れず、最大で `tts.timeout_seconds` 程度待つことがあります。
- 生成速度・聞こえ方・マイクへの回り込みは使用するPCとスピーカーで確認してください。音声認識・LLM・会話全体の実機検証は、声だけの書き出し確認とは別です。
- 展示では使用する声をクレジット表記します。比較用フォルダーには `CREDITS.txt` を同梱します。例: `VOICEVOX:玄野武宏`。選択した音声ライブラリの規約に従ってください。

公式資料: [エンジン/API](https://github.com/VOICEVOX/voicevox_engine)、[利用規約](https://voicevox.hiroshiba.jp/term/)、[音声ライブラリの規約](https://github.com/VOICEVOX/voicevox_resource/blob/main/engine/README.md)。
