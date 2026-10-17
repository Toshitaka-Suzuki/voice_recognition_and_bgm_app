"""JSON設定を読み込み、内容を検証してPythonのデータへ変換するモジュール。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .models import ConversationExample, ConversationExamples, ConversationPhase


@dataclass(frozen=True)
class AudioSettings:
    """録音と無音判定に使用する設定値。"""

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
    """faster-whisperのモデルと認識方法に関する設定値。"""

    model: str
    device: str
    compute_type: str
    language: str
    beam_size: int


@dataclass(frozen=True)
class LLMSettings:
    """LLMの接続先と生成設定。provider省略時は既存のOllamaを使う。"""

    host: str
    model: str
    production_model: str
    timeout_seconds: float
    temperature: float
    max_response_chars: int
    provider: str = "ollama"


@dataclass(frozen=True)
class TTSSettings:
    """pyttsx3で使用する音声、速度、音量の設定値。"""

    voice_match: str
    rate: int
    volume: float


@dataclass(frozen=True)
class QuestionSettings:
    """作戦フェーズと、共通・勝敗別の感想フェーズで読み上げる質問文。"""

    strategy: str
    feedback: str
    feedback_human_win: str
    feedback_robot_win: str


@dataclass(frozen=True)
class AppSettings:
    """アプリ全体の設定を機能別にまとめたデータ。"""

    audio: AudioSettings
    speech_recognition: SpeechRecognitionSettings
    llm: LLMSettings
    tts: TTSSettings
    questions: QuestionSettings


def _read_json(path: Path) -> Any:
    """UTF-8のJSONファイルを読み込む。

    ファイルがない場合やJSON構文が不正な場合は、利用者向けの
    :class:`ConfigurationError`へ変換します。
    """

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
    """値がJSONオブジェクト、つまりPythonの辞書であることを確認する。"""

    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} はJSONオブジェクトで指定してください。")
    return value


def _string(data: dict[str, Any], key: str, label: str) -> str:
    """辞書から空でない文字列を取り出し、前後の空白を除いて返す。"""

    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{label}.{key} は空でない文字列で指定してください。")
    return value.strip()


def _number(data: dict[str, Any], key: str, label: str, *, minimum: float = 0) -> float:
    """辞書から下限以上の数値を取り出し、``float``として返す。"""

    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
        raise ConfigurationError(f"{label}.{key} は {minimum} 以上の数値で指定してください。")
    return float(value)


def _integer(data: dict[str, Any], key: str, label: str, *, minimum: int = 1) -> int:
    """辞書から下限以上の整数を取り出す。

    Pythonでは``bool``も整数の一種なので、先に明示的に除外しています。
    """

    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigurationError(f"{label}.{key} は {minimum} 以上の整数で指定してください。")
    return value


def load_settings(path: Path) -> AppSettings:
    """動作設定JSONを読み込み、検証済みの``AppSettings``を返す。

    必須項目の不足や値の型・範囲が不正な場合は``ConfigurationError``を
    発生させます。
    """

    # 最初に各大項目が辞書になっていることを確認します。
    root = _mapping(_read_json(path), "settings")
    audio = _mapping(root.get("audio"), "audio")
    speech = _mapping(root.get("speech_recognition"), "speech_recognition")
    llm = _mapping(root.get("llm"), "llm")
    tts = _mapping(root.get("tts"), "tts")
    questions = _mapping(root.get("questions"), "questions")

    # 既存JSONとの互換性を保ち、接続先の誤記は起動時に検出します。
    provider = llm.get("provider", "ollama")
    if provider not in ("ollama", "copilot"):
        raise ConfigurationError("llm.provider は ollama または copilot を指定してください。")

    # input_deviceだけは、番号・名前・既定値(null)の3形式を許可します。
    input_device = audio.get("input_device")
    if input_device is not None and (
        isinstance(input_device, bool) or not isinstance(input_device, (int, str))
    ):
        raise ConfigurationError("audio.input_device は整数、文字列、または null で指定してください。")

    # 音量は一般的なpyttsx3の範囲である0～1に限定します。
    volume = _number(tts, "volume", "tts", minimum=0)
    if volume > 1:
        raise ConfigurationError("tts.volume は0から1の範囲で指定してください。")

    # 検証済みの辞書を、用途ごとのdataclassへ変換します。
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
            host=_string(llm, "host", "llm") if provider == "ollama" else "",
            model=_string(llm, "model", "llm") if "model" in llm or provider == "ollama" else "auto",
            production_model=_string(llm, "production_model", "llm") if provider == "ollama" else "",
            timeout_seconds=_number(llm, "timeout_seconds", "llm", minimum=1),
            temperature=_number(llm, "temperature", "llm", minimum=0),
            max_response_chars=_integer(llm, "max_response_chars", "llm"),
            provider=provider,
        ),
        tts=TTSSettings(
            voice_match=_string(tts, "voice_match", "tts"),
            rate=_integer(tts, "rate", "tts"),
            volume=volume,
        ),
        questions=QuestionSettings(
            strategy=_string(questions, "strategy", "questions"),
            feedback=_string(questions, "feedback", "questions"),
            # 旧設定ファイルも単独会話アプリで使えるよう、未指定時は共通文へ戻します。
            feedback_human_win=(
                _string(questions, "feedback_human_win", "questions")
                if "feedback_human_win" in questions
                else _string(questions, "feedback", "questions")
            ),
            feedback_robot_win=(
                _string(questions, "feedback_robot_win", "questions")
                if "feedback_robot_win" in questions
                else _string(questions, "feedback", "questions")
            ),
        ),
    )


def load_conversation_examples(path: Path) -> ConversationExamples:
    """会話例JSONを読み込み、用語とフェーズ別の例を返す。

    JSONの版、必須フェーズ、各文字列を検証し、登録された会話例は
    件数を制限せず記載順のまま保持します。
    """

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

    # Enumから正しいフェーズ名の集合を作り、不足と未知項目を同時に調べます。
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

    # リストを不変のtupleへ変換し、JSONに書かれた順序を維持します。
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
