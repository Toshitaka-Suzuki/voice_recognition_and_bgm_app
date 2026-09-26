from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class ConversationPhase(str, Enum):
    STRATEGY = "strategy"
    FEEDBACK = "feedback"


class ConversationState(Enum):
    STRATEGY_RECORDING = auto()
    STRATEGY_CONFIRMATION = auto()
    ATTRACTION_WAIT = auto()
    FEEDBACK_RECORDING = auto()
    FEEDBACK_CONFIRMATION = auto()
    COMPLETE = auto()


class ConfirmationResult(Enum):
    CONFIRMED = auto()
    QUIT = auto()


@dataclass(frozen=True)
class AudioData:
    samples: Any
    sample_rate: int
    speech_detected: bool
    stop_reason: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    processing_seconds: float
    model_name: str
    average_log_probability: float | None = None

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass(frozen=True)
class ConversationExample:
    user: str
    assistant: str


@dataclass(frozen=True)
class ConversationExamples:
    version: int
    domain_terms: tuple[str, ...]
    phases: dict[ConversationPhase, tuple[ConversationExample, ...]]

