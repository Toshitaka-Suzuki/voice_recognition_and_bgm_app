# このアプリで学ぶPython入門ガイド

このガイドでは、チャンバラロボット会話アプリで実際に使われているPythonの機能を基礎から説明します。アプリ全体の設計は[プログラム構成解説書](program-guide.md)を参照してください。

## 1. Git Bashで使うコマンド

コマンドはプロジェクトフォルダで実行します。

```bash
cd /d/Program_Folder/Codex_ws/voice_recognition_app
```

### 仮想環境

仮想環境は、このアプリ専用のPythonライブラリ置き場です。他のPythonアプリとライブラリの版が衝突するのを防ぎます。

```bash
# Python 3.11で.venvを作る（最初の1回だけ）
py -3.11 -m venv .venv

# Git Bashで仮想環境を有効にする
source .venv/Scripts/activate

# 仮想環境を終了する
deactivate
```

PowerShell用の`Activate.ps1`をGit Bashで直接実行することはできません。Git Bashでは`source .venv/Scripts/activate`を使います。

### インストールと実行

```bash
# pip自身を更新する
python -m pip install --upgrade pip

# このプロジェクトと依存ライブラリを編集可能形式で入れる
python -m pip install -e .

# Ollamaへ開発用モデルを取得する
ollama pull qwen2.5:3b

# アプリを起動する
python -m chambara_talk

# 単体テストを実行する
python -m unittest discover -s tests -v
```

`python -m 名前`は、Pythonに指定したモジュールを実行させる書き方です。`pip`や`unittest`も、使用中の仮想環境のPythonから確実に実行できます。

## 2. 値、変数、基本の型

変数は値へ名前を付ける仕組みです。

```python
model_name = "small"       # str（文字列）
sample_rate = 16000        # int（整数）
volume = 1.0               # float（小数）
speech_detected = True     # bool（真偽値）
input_device = None        # 値がないことを表す
```

このアプリでは、設定JSONから読んだ値や処理結果を変数へ保存します。文字列の前後の空白を除く`strip()`、大文字と小文字の違いをなくす`casefold()`も入力確認に使っています。

```python
choice = input("入力 > ").strip().casefold()
```

## 3. 条件分岐と繰り返し

### if

`if`は条件によって処理を分けます。

```python
if choice == "":
    return ConfirmationResult.CONFIRMED
if choice == "q":
    return ConfirmationResult.QUIT
```

`==`は左右が同じか比較します。`not`は真偽を逆にし、`and`と`or`で条件を組み合わせられます。

### for

`for`はリストなどの要素を順番に処理します。

```python
for example in examples:
    messages.append({"role": "user", "content": example.user})
```

### while

`while`は条件が真の間、処理を繰り返します。`while True`は、`return`や`break`まで続くループです。

```python
while True:
    choice = input("Enterまたはq > ")
    if choice == "q":
        break
```

録音の監視や、不正なキー入力後の再入力で使っています。

## 4. コレクション

複数の値をまとめる代表的な型です。

```python
terms = ["面", "胴", "小手"]                # list：追加・変更できる
roles = ("user", "assistant")              # tuple：基本的に変更しない
settings = {"model": "small", "beam": 5}  # dict：キーで値を探す
phases = {"strategy", "feedback"}          # set：重複しない集合
```

アプリでは、処理中に追加する音声ブロックは`list`、読み込んだ後に変えない会話例は`tuple`、フェーズから会話例を探す部分は`dict`を使います。`set`はフェーズ名の不足や余分な項目を比較するために使います。

### 内包表記

繰り返しから新しいリストや集合を短く作る書き方です。

```python
names = {self._model_name(model) for model in raw_models}
```

これは各モデルから名前を取り出し、重複のない集合を作っています。

## 5. 関数、引数、戻り値

関数は、名前を付けて再利用できる処理です。

```python
def _rms(chunk) -> float:
    return float(np.sqrt(np.mean(np.square(chunk))))
```

- `chunk`：関数へ渡す引数
- `-> float`：戻り値は小数という型ヒント
- `return`：呼び出し元へ結果を返す

`def load_settings(path: Path) -> AppSettings:`のように、入力と出力の型を示すと、何を渡す関数か理解しやすくなります。

`*`より後ろの引数は、名前を指定して渡す必要があります。

```python
def _number(data, key, label, *, minimum=0):
    ...

_number(tts, "volume", "tts", minimum=0)
```

## 6. クラスとインスタンス

クラスはデータと処理をまとめた設計図、インスタンスはその設計図から作った実物です。

```python
class SpeechRecognizer:
    def __init__(self, settings, domain_terms):
        self.settings = settings

    def load(self):
        ...

recognizer = SpeechRecognizer(settings, terms)
recognizer.load()
```

- `__init__`：インスタンス作成時の初期化
- `self`：現在のインスタンス自身
- メソッド：クラスの中に定義した関数

本アプリでは、録音、音声認識、LLM、読み上げを別々のクラスにしています。

## 7. import、モジュール、パッケージ

1つの`.py`ファイルをモジュール、複数のモジュールをまとめたフォルダをパッケージと呼びます。

```python
from pathlib import Path
from .errors import AudioError
```

1行目は標準ライブラリから`Path`を読み込みます。2行目の先頭の`.`は、同じ`chambara_talk`パッケージ内を表します。

`__main__.py`があるため、次の形式でパッケージを起動できます。

```bash
python -m chambara_talk
```

## 8. 型ヒントとAny

型ヒントは実行時の命令ではなく、値の種類を人や開発ツールへ伝える情報です。

```python
def confirm(self, text: str) -> ConfirmationResult:
    ...
```

よく出てくる型ヒントは次のとおりです。

| 書き方 | 意味 |
| --- | --- |
| `list[str]` | 文字列のリスト |
| `dict[str, Any]` | 文字列キーで、値はさまざまな型の辞書 |
| `tuple[str, ...]` | 文字列が0個以上入るタプル |
| `float | None` | 小数または値なし |
| `Callable[[str], None]` | 文字列を受け取り、値を返さない関数 |
| `Any` | 外部ライブラリなど、型を限定しにくい値 |

`from __future__ import annotations`は、型ヒントの評価を遅らせ、柔軟に記述できるようにします。

## 9. dataclass

`dataclass`は、データを入れるクラスを簡潔に定義する機能です。

```python
@dataclass(frozen=True)
class ConversationExample:
    user: str
    assistant: str
```

自動で`__init__`などが作られます。`frozen=True`は作成後の書き換えを禁止し、設定や処理結果が途中で変わる事故を減らします。

## 10. Enum

`Enum`は、選択肢を決められた名前へ限定します。

```python
class ConfirmationResult(Enum):
    CONFIRMED = auto()
    QUIT = auto()
```

文字列を直接比較するより、綴り間違いを減らせます。`auto()`は重複しない値を自動で付けます。

## 11. propertyとstaticmethod

### property

メソッドを値のように読めます。

```python
@property
def is_empty(self) -> bool:
    return not self.text.strip()

if result.is_empty:
    ...
```

### staticmethod

インスタンスの状態`self`を使わない、クラスに関連する処理を表します。

```python
@staticmethod
def _rms(chunk) -> float:
    ...
```

## 12. コールバックと関数の差し替え

コールバックは、後で呼んでもらうために渡す関数です。sounddeviceには、音声ブロックを受け取る`callback`を渡します。

```python
def callback(indata, frames, time_info, status):
    chunks.put(indata[:, 0].copy())
```

また、`output: Callable[[str], None] = print`のように関数を引数で受け取ると、通常は`print`、テストでは`list.append`へ差し替えられます。この方法により、表示内容を自動テストできます。

## 13. 例外処理

例外は、通常の戻り値では処理できない問題を伝える仕組みです。

```python
try:
    result = json.load(file)
except json.JSONDecodeError as exc:
    raise ConfigurationError("JSONの形式が正しくありません") from exc
```

- `try`：失敗する可能性がある処理
- `except`：特定の失敗を受け取る
- `raise`：例外を発生させる
- `from exc`：元の原因を保持したまま、分かりやすい例外へ変換する

このアプリ独自の例外はすべて`ChambaraTalkError`を継承するため、起動処理でまとめて扱えます。

## 14. withとコンテキストマネージャー

`with`は、処理の前後に必要な開始・終了を自動化します。

```python
with path.open("r", encoding="utf-8") as file:
    data = json.load(file)
```

ブロックを抜けるとファイルが閉じられます。`sounddevice.InputStream`も`with`を使うため、録音が終わるとストリームが安全に閉じられます。

## 15. Queue、deque、ジェネレーター

### queue.Queue

音声callbackと録音処理の間で、安全に音声ブロックを受け渡します。先に入れた値を先に取り出すFIFO構造です。

### collections.deque

`maxlen`を指定すると、古いデータを自動で捨てる両端キューになります。発話直前の短い音声だけを保持するpre-rollに適しています。

### ジェネレーター

値を必要になったときに順番に作る仕組みです。faster-whisperの`segments`はジェネレーターなので、次の行で全結果を実体化します。

```python
materialized = list(segments)
```

テストの`iter(texts)`と`next(self.texts)`も、値を順番に取り出すiteratorの例です。

## 16. 遅延読み込み

時間やメモリが必要な部品を、作成時ではなく最初に使うときに準備します。

```python
def load(self):
    if self._model is not None:
        return
    self._model = WhisperModel(...)
```

`None`なら読み込み、すでにモデルがあれば再利用します。Ollamaクライアントやpyttsx3エンジンも同じ考え方です。

## 17. 依存部品の差し替えとMock

`ConversationController`は録音機などを自分で作らず、外から受け取ります。この構造を依存性の注入と呼びます。

```python
controller = ConversationController(
    recorder=FakeRecorder(),
    recognizer=FakeRecognizer(texts),
    ...
)
```

テストでは`FakeRecorder`などの小さな偽物へ差し替え、マイクやGPUなしで会話の順序を確認します。

`unittest.mock.Mock`は、呼ばれた回数や引数を記録できる汎用の偽物です。`patch`はテスト中だけ関数の戻り値などを置き換えます。

## 18. 主な標準ライブラリ

| ライブラリ | このアプリでの用途 |
| --- | --- |
| `argparse` | `--settings`、`--examples`を解析する |
| `pathlib` | Windowsのパスを文字列操作せず扱う |
| `json` | 設定と会話例を読み込む |
| `queue` | callbackから音声ブロックを受け取る |
| `statistics` | 環境音RMSの中央値を求める |
| `time` | 待機時間、録音時間、認識時間を測る |
| `math` | pre-rollに必要なブロック数を切り上げる |
| `importlib.metadata` | 導入済みpyttsx3の版を調べる |
| `unittest` | テストケースと検証を記述する |
| `unittest.mock` | 外部機能を偽物へ置き換える |

## 19. 外部ライブラリ

### NumPy

数値配列を高速に扱います。音声ブロックの結合、RMS計算、float32形式への変換に使用します。

### sounddevice

PCのマイクとスピーカーを扱うライブラリです。このアプリでは入力設定の確認と、マイクストリームからの録音に使用します。

### faster-whisper

Whisperを高速に実行する音声認識ライブラリです。NumPy配列を受け取り、日本語の認識区間を返します。設定によりCPUまたはCUDA対応GPUを使用します。

### Ollama Pythonライブラリ

ローカルで動くOllamaへPythonから接続します。モデル一覧の取得と、`chat()`によるコメント生成に使用します。

### pyttsx3

OSの音声合成機能をPythonから利用します。WindowsではSAPI5を通じてHaruka音声を選びます。本アプリは連続読み上げを安定させるため2.98へ固定しています。

## 20. テスト結果の読み方

次のコマンドで`tests`内のテストを自動検出します。

```bash
python -m unittest discover -s tests -v
```

結果の主な意味は次のとおりです。

- `ok`：期待どおり
- `FAIL`：実行できたが結果が期待と違う
- `ERROR`：テスト中に予期しない例外が発生した
- `Ran 14 tests`：実行したテスト件数
- 最後の`OK`：すべて成功

テストを読むときは、`test_`で始まるメソッド名、準備、実行、`assert...`による確認の順に見ると理解しやすくなります。
