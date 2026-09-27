"""録音データをfaster-whisperで日本語の文章へ変換するモジュール。"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from .config import SpeechRecognitionSettings
from .errors import SpeechRecognitionError
from .models import AudioData, TranscriptionResult


class SpeechRecognizer:
    """faster-whisperモデルを管理し、音声認識を実行する。"""

    def __init__(self, settings: SpeechRecognitionSettings, domain_terms: Sequence[str]) -> None:
        """認識設定と、認識の手掛かりにするチャンバラ用語を保持する。"""

        self.settings = settings
        self.domain_terms = tuple(domain_terms)
        self._model: Any = None

    def load(self) -> None:
        """モデルがまだなければ読み込む。

        大きなモデルを生成時ではなく必要な時に読み込む「遅延読み込み」です。
        読み込み失敗時は``SpeechRecognitionError``を発生させます。
        """

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
        """録音データを認識し、文章と処理時間などを返す。"""

        if not audio.speech_detected or len(audio.samples) == 0:
            return TranscriptionResult("", 0.0, self.settings.model)
        self.load()
        # 固有用語をプロンプトに含め、似た音の単語への誤認識を減らします。
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
            # segmentsは遅延評価されるため、list化した時点で実際の認識が進みます。
            materialized = list(segments)
        except Exception as exc:
            raise SpeechRecognitionError(f"音声認識に失敗しました: {exc}") from exc

        text = "".join(segment.text for segment in materialized).strip()
        # 各区間の対数確率が存在すれば、デバッグ用の平均値も計算します。
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
