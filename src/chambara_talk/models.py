"""各機能の間で受け渡すデータと、会話の状態を定義するモジュール。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class ConversationPhase(str, Enum):
    """会話が作戦と感想のどちらのフェーズにいるかを表す。"""

    STRATEGY = "strategy"
    FEEDBACK = "feedback"


class ConversationState(Enum):
    """アプリの進行状態を表す。

    ``auto()``を使うと、比較に使う一意な値をPythonが自動で割り当てます。
    """

    STRATEGY_RECORDING = auto()
    STRATEGY_CONFIRMATION = auto()
    ATTRACTION_WAIT = auto()
    FEEDBACK_RECORDING = auto()
    FEEDBACK_CONFIRMATION = auto()
    COMPLETE = auto()


class ConfirmationResult(Enum):
    """Enterで確定したか、qで終了したかを表す。"""

    CONFIRMED = auto()
    QUIT = auto()


@dataclass(frozen=True)
class AudioData:
    """録音結果を音声認識機能へ渡すためのデータ。

    ``frozen=True``のため、作成後に各フィールドを書き換えることはできません。
    ``samples``にはNumPy配列が入り、テストでは簡単なリストも使います。
    """

    samples: Any
    sample_rate: int
    speech_detected: bool
    stop_reason: str


@dataclass(frozen=True)
class TranscriptionResult:
    """音声認識で得た文章と処理情報をまとめたデータ。"""

    text: str
    processing_seconds: float
    model_name: str
    average_log_probability: float | None = None

    @property
    def is_empty(self) -> bool:
        """認識文が空白だけ、または空文字なら``True``を返す。"""

        return not self.text.strip()


@dataclass(frozen=True)
class ConversationExample:
    """LLMへ見本として渡す、利用者の発話と返答の1組。"""

    user: str
    assistant: str


@dataclass(frozen=True)
class ConversationExamples:
    """JSONから読み込んだ用語一覧と、フェーズ別の会話例。"""

    version: int
    domain_terms: tuple[str, ...]
    phases: dict[ConversationPhase, tuple[ConversationExample, ...]]
