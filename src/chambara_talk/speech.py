from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from .config import SpeechRecognitionSettings
from .errors import SpeechRecognitionError
from .models import AudioData, TranscriptionResult


class SpeechRecognizer:
    def __init__(self, settings: SpeechRecognitionSettings, domain_terms: Sequence[str]) -> None:
        self.settings = settings
        self.domain_terms = tuple(domain_terms)
        self._model: Any = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.settings.model,
                device=self.settings.device,
                compute_type=self.settings.compute_type,
            )
        except Exception as exc:
            raise SpeechRecognitionError(
                f"faster-whisperモデル '{self.settings.model}' の読み込みに失敗しました: {exc}"
            ) from exc

    def transcribe(self, audio: AudioData) -> TranscriptionResult:
        if not audio.speech_detected or len(audio.samples) == 0:
            return TranscriptionResult("", 0.0, self.settings.model)
        self.load()
        initial_prompt = "、".join(self.domain_terms)
        started_at = time.perf_counter()
        try:
            segments, _info = self._model.transcribe(
                audio.samples,
                language=self.settings.language,
                beam_size=self.settings.beam_size,
                initial_prompt=initial_prompt,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            materialized = list(segments)
        except Exception as exc:
            raise SpeechRecognitionError(f"音声認識に失敗しました: {exc}") from exc

        text = "".join(segment.text for segment in materialized).strip()
        probabilities = [
            float(segment.avg_logprob)
            for segment in materialized
            if getattr(segment, "avg_logprob", None) is not None
        ]
        average = sum(probabilities) / len(probabilities) if probabilities else None
        return TranscriptionResult(
            text=text,
            processing_seconds=time.perf_counter() - started_at,
            model_name=self.settings.model,
            average_log_probability=average,
        )

