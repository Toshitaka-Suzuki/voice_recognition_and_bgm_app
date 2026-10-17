# チャンバラロボット会話アプリ プログラム構成解説書

この文書は、Pythonを学び始めた方が本アプリの構成と処理の流れを理解するための解説書です。Pythonの文法やライブラリの説明は、[Python学習ガイド](python-learning-guide.md)を参照してください。

## 1. アプリの目的

このアプリは、チャンバラロボットアトラクションの前後に利用者と音声で会話します。

- アトラクション前：作戦や意気込みを聞いて応援する
- アトラクション後：感想を聞いてコメントする
- 音声認識結果は画面に表示する。単独版はEnterで確定し、統合版は自動でLLMへ送る
- 録音した音声、認識文、返答をファイルへ保存しない

faster-whisperが音声を文章へ変換し、LLMが返答を作り、pyttsx3が返答を読み上げます。LLMはOllamaのローカル実行とGitHub Copilotのクラウド実行から選べます。音声認識と読み上げはどちらの場合もPC上で実行します。クラウド側やSDKの一時保存については後述します。

## 2. フォルダ構成

```text
voice_recognition_app/
├── README.md                         セットアップと実行方法
├── pyproject.toml                    パッケージ情報と依存ライブラリ
├── requirements.txt                 依存ライブラリ一覧
├── config/
│   └── settings.json                 録音・認識・LLM・音声の設定
├── data/
│   └── conversation_examples.json    固有用語と会話例
├── docs/
│   ├── program-guide.md              この構成解説書
│   └── python-learning-guide.md       Python学習ガイド
├── src/chambara_talk/                 アプリ本体
└── tests/                             単体テスト
```

### 本体モジュールの役割

| ファイル | 主な役割 |
| --- | --- |
| `__main__.py` | `python -m chambara_talk`で実行したときの入口 |
| `cli.py` | 設定を読み、各部品を作り、起動前チェックを行う |
| `config.py` | 2つのJSONファイルを読み、型や値を検証する |
| `models.py` | 音声・認識結果・会話例・状態を表すデータを定義する |
| `controller.py` | 質問、録音、認識、確認、LLM、読み上げの順序を管理する |
| `audio.py` | マイク録音、環境音測定、発話と無音の判定を行う |
| `speech.py` | faster-whisperで音声を日本語へ変換する |
| `llm.py` | 共通メッセージ・短縮処理、Ollama接続、接続先の選択 |
| `copilot_backend.py` | Copilot SDKの非同期通信とセッションの後片付け |
| `tts.py` | Windowsの日本語音声で質問とコメントを読み上げる |
| `errors.py` | 機能ごとのエラー型を定義する |

## 3. 起動から終了まで

```text
PowerShellから起動
        │
        ▼
__main__.py → cli.main()
        │
        ├─ settings.jsonを読み込む
        ├─ conversation_examples.jsonを読み込む
        ├─ 録音・認識・LLM・読み上げの部品を作る
        └─ マイク、音声、LLM、認識モデルを事前確認
        │
        ▼
ConversationController.run()
        │
        ├─ 作戦フェーズ
        ├─ アトラクション終了待ち
        ├─ 感想フェーズ
        └─ 正常終了
```

`cli.py`は部品を組み立てる場所です。個別の専門処理は各モジュールへ任せ、`ConversationController`には完成した部品を渡します。この分け方により、録音方法などを変更しても会話の進行処理への影響を小さくできます。

## 4. 1フェーズのデータフロー

作戦と感想は、質問文と使用する会話例が異なるだけで、同じ処理を通ります。次の図は単独版の手動操作です。統合版では2か所のEnter待機を飛ばし、質問の読み上げ直後に録音し、認識結果の表示直後にLLMへ進みます。

```text
質問を画面表示・読み上げ
        │
        ▼
Enterで録音開始 ───── q → 終了
        │
        ▼
環境音測定 → 発話検出 → 2秒の無音で終了
        │
        ├─ 発話なし・録音失敗 → 録音待機へ戻る
        ▼
faster-whisperで認識
        │
        ├─ 空・認識失敗 → 録音待機へ戻る
        ▼
認識結果を画面表示
        │
        ├─ q → LLMへ送らず終了
        └─ Enter → 確定
        │
        ▼
現在フェーズの全会話例と認識文を選択したLLMへ送る
        │
        ├─ LLM失敗 → 録音待機へ戻る
        ▼
コメントを画面表示・読み上げ
```

### 認識結果がLLMへ渡る場所

`controller.py`の`_run_phase()`は`manual_input_enabled`で操作方法を分けます。単独版では`RecognitionConfirmation.confirm()`が`CONFIRMED`を返した後にだけ`chat_backend.generate()`を呼びます。統合版では`RecognitionConfirmation.display()`で同じ形式の認識結果を表示し、外部キャンセルがないことを再確認してから自動で`generate()`を呼びます。

## 5. 録音と無音判定

`AudioRecorder.record_until_silence()`が録音を担当します。

1. `sounddevice.InputStream`が短い音声ブロックを繰り返し取得する
2. callbackが音声ブロックを`queue.Queue`へ入れる
3. 最初の環境音のRMS音量を測る
4. 「固定下限」と「環境音の中央値×倍率」の大きい方を発話しきい値にする
5. `SilenceDetector`が発話開始後の無音時間を数える
6. 無音時間または最大録音時間に達したら終了する
7. 小さな音声ブロックをNumPyで1本の配列へ結合する

RMSは音の大きさを表す値です。環境音に応じてしきい値を変えるため、静かな場所と少し騒がしい場所の両方へ対応しやすくしています。

`deque`へ発話直前の音声を一時保存するpre-rollもあります。発話を検出した瞬間より少し前から残すことで、最初の一音が欠けるのを防ぎます。

## 6. 音声認識

`SpeechRecognizer`は、最初に必要になった時点でfaster-whisperモデルを読み込みます。これを遅延読み込みと呼びます。同じインスタンスでは2回目以降も同じモデルを再利用します。

認識時には次の情報を渡します。

- 録音したNumPy配列
- 言語`ja`
- beam size
- `domain_terms`を連結した認識用プロンプト
- VADフィルター

faster-whisperの認識結果はジェネレーターです。`list(segments)`で全区間を取り出した時点で認識処理が完了します。その後、各区間の文章を結合して`TranscriptionResult`を作ります。

## 7. LLMコメント生成

`ChatBackend.build_messages()`は、両接続先で共通のメッセージを次の順番で作ります。

1. 侍風、文字数、安全性などを指示するシステムメッセージ
2. 現在のフェーズに登録された会話例すべて
3. 今回確定された利用者の発話

作戦フェーズには`strategy`の例だけ、感想フェーズには`feedback`の例だけを渡します。前のフェーズの発話や返答は履歴として引き継ぎません。

返答が最大文字数を超えた場合は、LLMへ一度だけ短縮を依頼します。それでも長い場合や短縮に失敗した場合は、句点を優先して機械的に切り詰めます。

## 8. 音声読み上げ

`SpeechSynthesizer`は起動時に以下を確認します。

- pyttsx3が動作確認済みの2.98であること
- `voice_match`に合うWindows音声が存在すること

見つけたエンジンは作戦質問、作戦コメント、感想質問、感想コメントで再利用します。`say()`で文章を登録し、`runAndWait()`で読み上げ終了まで待ってから次の処理へ進みます。

## 9. 会話状態

`ConversationState`は現在位置を表します。

```text
STRATEGY_RECORDING
        ↓
STRATEGY_CONFIRMATION
        ↓
ATTRACTION_WAIT
        ↓
FEEDBACK_RECORDING
        ↓
FEEDBACK_CONFIRMATION
        ↓
COMPLETE
```

録音や認識をやり直す場合は、現在フェーズの録音状態へ戻ります。単独版のq・入力終了、または共通のCtrl+Cでは完了状態へ進まず終了します。

## 10. 設定ファイル

JSONにはコメントを書けないため、項目の意味をここで説明します。

### `config/settings.json`

| 項目 | 意味 |
| --- | --- |
| `audio.sample_rate` | 1秒あたりの音声サンプル数 |
| `audio.channels` | 音声チャンネル数。現在はモノラルの1 |
| `audio.silence_seconds` | 発話後、録音を終了する連続無音時間 |
| `audio.speech_wait_timeout_seconds` | 発話開始を待つ最大時間 |
| `audio.max_recording_seconds` | 発話開始後の最大録音時間 |
| `audio.calibration_seconds` | 環境音を測る時間 |
| `audio.block_seconds` | 1つの音声ブロックの長さ |
| `audio.minimum_rms_threshold` | 発話判定しきい値の固定下限 |
| `audio.noise_multiplier` | 環境音へ掛ける倍率 |
| `audio.pre_roll_seconds` | 発話開始前も残す時間 |
| `audio.input_device` | マイク番号・名前。`null`は既定マイク |
| `speech_recognition.*` | モデル、実行装置、量子化、言語、beam size |
| `llm.host` | Ollamaの接続先 |
| `llm.provider` | `ollama`または`copilot`。省略時は`ollama` |
| `llm.model` | 実際に使うモデル。Copilotの既定値は`auto` |
| `llm.production_model` | 本番用として記録しているモデル名 |
| `llm.timeout_seconds` | LLM確認・応答の待ち時間 |
| `llm.temperature` | Ollamaの返答の変化量を調整する値（Copilotでは未使用） |
| `llm.max_response_chars` | 返答の最大文字数 |
| `tts.voice_match` | Windows音声名を探す文字列 |
| `tts.rate` / `tts.volume` | 読み上げ速度と音量 |
| `questions.strategy` / `feedback` | 各フェーズの質問文 |

### `data/conversation_examples.json`

| 項目 | 意味 |
| --- | --- |
| `version` | データ形式の版。現在は1 |
| `domain_terms` | 認識や聞き間違い補正に使う用語 |
| `phases.strategy` | 作戦フェーズの利用者発話と返答例 |
| `phases.feedback` | 感想フェーズの利用者発話と返答例 |

## 11. エラー処理

`errors.py`の各エラーはすべて`ChambaraTalkError`を継承しています。起動中の設定、マイク、モデル、音声の問題は`cli.py`がまとめて表示して終了します。

会話中は、`ConversationController`が次のように回復します。

| 問題 | 動作 |
| --- | --- |
| 録音・音声認識・LLMの失敗 | エラーを表示する。単独版は録音待機、統合版は1秒後の自動録音へ戻る |
| 読み上げの失敗 | エラーを表示し、画面上の会話は続ける |
| Ctrl+Cまたは入力終了 | 「終了します」と表示して安全に終了する |

## 12. テストの構成

単体テストは`unittest`を使用します。

| テスト | 確認内容 |
| --- | --- |
| `test_audio.py` | 発話・無音の時間判定 |
| `test_config.py` | 正常なJSONと不正なJSONの拒否 |
| `test_controller.py` | 手動のq・Enter、自動録音、自動送信、再試行、キャンセル |
| `test_llm.py` | メッセージ順序と文字数制限 |
| `test_tts.py` | pyttsx3の呼び出し回数、版確認、エラー変換 |

`FakeRecorder`などの偽物や`Mock`を使うため、マイク、GPU、Ollama、実際のスピーカーがなくても制御処理を確認できます。実機テストは、外部機器やモデルを含む最終確認として別に行います。

## 13. おすすめのコード読解順

1. `models.py`で扱うデータと状態を知る
2. `cli.py`で部品がどのように作られるか確認する
3. `controller.py`で会話全体を追う
4. `audio.py`、`speech.py`、`llm.py`、`tts.py`で専門処理を読む
5. `config.py`で外部設定がデータになる過程を読む
6. `tests`で期待される動作を確認する

## 14. よく変更する場所

| 変更したい内容 | 変更場所 |
| --- | --- |
| 質問文 | `config/settings.json`の`questions` |
| 会話例 | `data/conversation_examples.json`の各フェーズ |
| チャンバラ用語 | 同ファイルの`domain_terms` |
| Whisperモデル・GPU設定 | `settings.json`の`speech_recognition` |
| Ollamaモデル | `settings.json`の`llm.model` |
| 無音時間・マイク | `settings.json`の`audio` |
| 読み上げ音声・速度・音量 | `settings.json`の`tts` |

変更後は、JSONのカンマや引用符が正しいことを確認し、単体テストを実行してください。

## 15. Copilotバックエンドの仕組み

`create_chat_backend()`は`llm.provider`を見て、従来の`ChatBackend`または`CopilotChatBackend`を作ります。Copilot側は共通のプロンプト・生成・短縮処理を継承し、起動確認と通信メソッドを置き換えます。Controllerは接続先の違いを意識せず`generate()`を呼べます。

```text
Controller（同期）
    ↓ generate()
共通メッセージ・短縮処理
    ↓ _chat()
Copilot専用asyncio.Runner
    ↓ SDK管理のローカルランタイム
GitHub Copilot（クラウドで生成）
```

各生成で新しいUUIDのセッションを作り、システム指示を設定します。会話例と今回の発話はrole付きJSON配列として全件を順番に送ります。応答後はセッションを切断・削除します。短縮依頼も独立したセッションです。

ファイル操作などのツール、設定探索、カスタム指示、履歴検索、メモリは無効です。権限要求が届いても拒否します。SDKの標準認証を使うため普段のCopilotログインを利用できますが、既存のCopilot会話を変更することはありません。

タイムアウト時は生成を中断してからセッションを削除します。`cli.main()`の`finally`は、単独版のq、共通のCtrl+C、起動失敗を含むすべての経路で`close()`を呼び、残った所有セッションの削除を再試行し、ランタイムと専用ループを停止します。

クラウドへは認識後の文章とプロンプトだけを送り、録音音声は送りません。単独版ではEnter確定後、統合版では認識結果の画面表示後に自動送信します。アプリ独自のログは作成しませんが、SDK/CLIの一時保存・ログやGitHub側の保持が完全にないとは保証できません。CPU用設定は`config/settings.copilot.json`です。起動手順はREADMEを参照してください。

`test_copilot.py`はSDKを偽物へ差し替えて異常系を検証し、`test_talk_cli.py`は終了時の解放を検証します。実サービスを呼ぶ単体テストはありません。
