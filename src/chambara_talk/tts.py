"""pyttsx3とWindows SAPI5を使って日本語文章を読み上げるモジュール。"""

from __future__ import annotations

from importlib import metadata
from collections.abc import Callable
from typing import Any

from .config import TTSSettings
from .errors import TTSError


class SpeechSynthesizer:
    """日本語音声エンジンを1つ保持し、文章を同期的に読み上げる。"""

    SUPPORTED_PYTTSX3_VERSION = "2.98"

    def __init__(
        self,
        settings: TTSSettings,
        output: Callable[[str], None] = print,
    ) -> None:
        """音声設定と、進行メッセージを表示する関数を受け取る。"""

        self.settings = settings
        self.output = output
        self._engine: Any = None
        self.voice_name = ""

    def load(self) -> None:
        """対応版のpyttsx3と指定されたWindows音声を初期化する。

        初期化済みなら同じエンジンを再利用します。バージョンや音声に問題が
        ある場合は、対処方法を含む``TTSError``を発生させます。
        """

        if self._engine is not None:
            return
        try:
            # 2.99はWindowsで2回目以降が無音になるため、動作確認版に限定します。
            installed_version = metadata.version("pyttsx3")
            if installed_version != self.SUPPORTED_PYTTSX3_VERSION:
                raise TTSError(
                    "pyttsx3 "
                    f"{installed_version} はWindowsで2回目以降の読み上げが無音になるため使用できません。"
                    "仮想環境を有効にして "
                    "'python -m pip install --force-reinstall pyttsx3==2.98' "
                    "を実行してください。"
                )
            import pyttsx3

            engine = pyttsx3.init()
            voices = engine.getProperty("voices")
            match = self.settings.voice_match.casefold()
            # 音声の表示名またはIDを、大文字・小文字を区別せず検索します。
            selected = next(
                (
                    voice
                    for voice in voices
                    if match in str(getattr(voice, "name", "")).casefold()
                    or match in str(getattr(voice, "id", "")).casefold()
                ),
                None,
            )
            if selected is None:
                available = ", ".join(str(getattr(v, "name", v.id)) for v in voices) or "なし"
                raise TTSError(
                    f"日本語音声 '{self.settings.voice_match}' が見つかりません。"
                    f"Windowsの音声設定で日本語音声を追加してください（検出済み: {available}）。"
                )
            engine.setProperty("voice", selected.id)
            engine.setProperty("rate", self.settings.rate)
            engine.setProperty("volume", self.settings.volume)
            self.voice_name = str(getattr(selected, "name", selected.id))
            self._engine = engine
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"音声合成を初期化できません: {exc}") from exc

    def speak(self, text: str) -> None:
        """文章を読み上げ、完了するまで待つ。

        ``runAndWait``が終わるまで戻らないため、次の画面へ進む前に読み上げが
        完了します。失敗した場合は``TTSError``を発生させます。
        """

        self.load()
        try:
            self.output(f"音声読み上げ開始: {text}")
            self._engine.say(text)
            self._engine.runAndWait()
            self.output("音声読み上げ完了")
        except Exception as exc:
            raise TTSError(f"音声の読み上げに失敗しました: {exc}") from exc
