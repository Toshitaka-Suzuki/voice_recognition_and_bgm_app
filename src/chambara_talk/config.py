from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .models import ConversationExample, ConversationExamples, ConversationPhase


@dataclass(frozen=True)
class AudioSettings:
    sample_rate: int
    channels: int
    silence_seconds: float
    speech_wait_timeout_seconds: float
    max_recording_seconds: float
    calibration_seconds: float
    block_seconds: float
    minimum_rms_threshold: float
    noise_multiplier: float
    pre_roll_seconds: float
    input_device: int | str | None


@dataclass(frozen=True)
class SpeechRecognitionSettings:
    model: str
    device: str
    compute_type: str
    language: str
    beam_size: int


@dataclass(frozen=True)
class LLMSettings:
    host: str
    model: str
    production_model: str
    timeout_seconds: float
    temperature: float
    max_response_chars: int


@dataclass(frozen=True)
class TTSSettings:
    voice_match: str
    rate: int
    volume: float


@dataclass(frozen=True)
class QuestionSettings:
    strategy: str
    feedback: str


@dataclass(frozen=True)
class AppSettings:
    audio: AudioSettings
    speech_recognition: SpeechRecognitionSettings
    llm: LLMSettings
    tts: TTSSettings
    questions: QuestionSettings


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"設定ファイルが見つかりません: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"JSONの形式が正しくありません: {path} ({exc.msg}, {exc.lineno}行目)"
        ) from exc


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} はJSONオブジェクトで指定してください。")
    return value


def _string(data: dict[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{label}.{key} は空でない文字列で指定してください。")
    return value.strip()


def _number(data: dict[str, Any], key: str, label: str, *, minimum: float = 0) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ConfigurationError(f"{label}.{key} は {minimum} 以上の数値で指定してください。")
    return float(value)


def _integer(data: dict[str, Any], key: str, label: str, *, minimum: int = 1) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigurationError(f"{label}.{key} は {minimum} 以上の整数で指定してください。")
    return value


def load_settings(path: Path) -> AppSettings:
    root = _mapping(_read_json(path), "settings")
    audio = _mapping(root.get("audio"), "audio")
    speech = _mapping(root.get("speech_recognition"), "speech_recognition")
    llm = _mapping(root.get("llm"), "llm")
    tts = _mapping(root.get("tts"), "tts")
    questions = _mapping(root.get("questions"), "questions")

    input_device = audio.get("input_device")
    if input_device is not None and (
        isinstance(input_device, bool) or not isinstance(input_device, (int, str))
    ):
        raise ConfigurationError("audio.input_device は整数、文字列、または null で指定してください。")

    volume = _number(tts, "volume", "tts", minimum=0)
    if volume > 1:
        raise ConfigurationError("tts.volume は0から1の範囲で指定してください。")

    return AppSettings(
        audio=AudioSettings(
            sample_rate=_integer(audio, "sample_rate", "audio"),
            channels=_integer(audio, "channels", "audio"),
            silence_seconds=_number(audio, "silence_seconds", "audio", minimum=0.1),
            speech_wait_timeout_seconds=_number(
                audio, "speech_wait_timeout_seconds", "audio", minimum=0.1
            ),
            max_recording_seconds=_number(
                audio, "max_recording_seconds", "audio", minimum=0.1
            ),
            calibration_seconds=_number(audio, "calibration_seconds", "audio", minimum=0.1),
            block_seconds=_number(audio, "block_seconds", "audio", minimum=0.01),
            minimum_rms_threshold=_number(
                audio, "minimum_rms_threshold", "audio", minimum=0.000001
            ),
            noise_multiplier=_number(audio, "noise_multiplier", "audio", minimum=1),
            pre_roll_seconds=_number(audio, "pre_roll_seconds", "audio", minimum=0),
            input_device=input_device,
        ),
        speech_recognition=SpeechRecognitionSettings(
            model=_string(speech, "model", "speech_recognition"),
            device=_string(speech, "device", "speech_recognition"),
            compute_type=_string(speech, "compute_type", "speech_recognition"),
            language=_string(speech, "language", "speech_recognition"),
            beam_size=_integer(speech, "beam_size", "speech_recognition"),
        ),
        llm=LLMSettings(
            host=_string(llm, "host", "llm"),
            model=_string(llm, "model", "llm"),
            production_model=_string(llm, "production_model", "llm"),
            timeout_seconds=_number(llm, "timeout_seconds", "llm", minimum=1),
            temperature=_number(llm, "temperature", "llm", minimum=0),
            max_response_chars=_integer(llm, "max_response_chars", "llm"),
        ),
        tts=TTSSettings(
            voice_match=_string(tts, "voice_match", "tts"),
            rate=_integer(tts, "rate", "tts"),
            volume=volume,
        ),
        questions=QuestionSettings(
            strategy=_string(questions, "strategy", "questions"),
            feedback=_string(questions, "feedback", "questions"),
        ),
    )


def load_conversation_examples(path: Path) -> ConversationExamples:
    root = _mapping(_read_json(path), "conversation_examples")
    version = root.get("version")
    if version != 1:
        raise ConfigurationError("conversation_examples.version は 1 である必要があります。")

    raw_terms = root.get("domain_terms")
    if not isinstance(raw_terms, list) or not raw_terms:
        raise ConfigurationError("domain_terms は1件以上の文字列配列で指定してください。")
    terms: list[str] = []
    for index, term in enumerate(raw_terms):
        if not isinstance(term, str) or not term.strip():
            raise ConfigurationError(f"domain_terms[{index}] は空でない文字列にしてください。")
        terms.append(term.strip())

    raw_phases = _mapping(root.get("phases"), "phases")
    expected = {phase.value for phase in ConversationPhase}
    actual = set(raw_phases)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"不足: {', '.join(missing)}")
        if unknown:
            details.append(f"未知: {', '.join(unknown)}")
        raise ConfigurationError(f"phases の項目が正しくありません ({'; '.join(details)})。")

    parsed: dict[ConversationPhase, tuple[ConversationExample, ...]] = {}
    for phase in ConversationPhase:
        raw_examples = raw_phases[phase.value]
        if not isinstance(raw_examples, list):
            raise ConfigurationError(f"phases.{phase.value} は配列で指定してください。")
        examples: list[ConversationExample] = []
        for index, raw_example in enumerate(raw_examples):
            item = _mapping(raw_example, f"phases.{phase.value}[{index}]")
            examples.append(
                ConversationExample(
                    user=_string(item, "user", f"phases.{phase.value}[{index}]"),
                    assistant=_string(item, "assistant", f"phases.{phase.value}[{index}]"),
                )
            )
        parsed[phase] = tuple(examples)

    return ConversationExamples(version=version, domain_terms=tuple(terms), phases=parsed)

