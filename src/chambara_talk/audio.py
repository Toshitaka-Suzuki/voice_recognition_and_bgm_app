from __future__ import annotations

import math
import queue
import statistics
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .config import AudioSettings
from .errors import AudioError
from .models import AudioData


@dataclass
class SilenceDetector:
    threshold: float
    silence_seconds: float
    speech_started: bool = False
    silent_seconds: float = 0.0

    def update(self, rms: float, duration: float) -> str:
        if rms >= self.threshold:
            was_started = self.speech_started
            self.speech_started = True
            self.silent_seconds = 0.0
            return "speaking" if was_started else "speech_started"
        if not self.speech_started:
            return "waiting"
        self.silent_seconds += duration
        if self.silent_seconds >= self.silence_seconds:
            return "finished"
        return "silence"


class AudioRecorder:
    def __init__(
        self,
        settings: AudioSettings,
        output: Callable[[str], None] = print,
    ) -> None:
        self.settings = settings
        self.output = output

    def check_device(self) -> str:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise AudioError("sounddeviceを読み込めません。依存関係をインストールしてください。") from exc
        try:
            sd.check_input_settings(
                device=self.settings.input_device,
                channels=self.settings.channels,
                dtype="float32",
                samplerate=self.settings.sample_rate,
            )
            info = sd.query_devices(self.settings.input_device, "input")
            return str(info.get("name", "既定のマイク"))
        except Exception as exc:
            raise AudioError(f"利用可能な入力マイクを確認できません: {exc}") from exc

    def record_until_silence(self) -> AudioData:
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as exc:
            raise AudioError("録音ライブラリを読み込めません。依存関係をインストールしてください。") from exc

        chunks: queue.Queue = queue.Queue()
        stream_statuses: list[str] = []

        def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            del frames, time_info
            if status:
                stream_statuses.append(str(status))
            chunks.put(indata[:, 0].copy())

        block_size = max(1, round(self.settings.sample_rate * self.settings.block_seconds))
        calibration_chunks: list = []
        captured_chunks: list = []
        pre_roll_size = max(1, math.ceil(self.settings.pre_roll_seconds / self.settings.block_seconds))
        pre_roll: deque = deque(maxlen=pre_roll_size)

        try:
            with sd.InputStream(
                samplerate=self.settings.sample_rate,
                channels=self.settings.channels,
                dtype="float32",
                blocksize=block_size,
                device=self.settings.input_device,
                callback=callback,
            ):
                self.output("環境音を測定しています。静かにお待ちください...")
                calibration_duration = 0.0
                while calibration_duration < self.settings.calibration_seconds:
                    chunk = chunks.get(timeout=2.0)
                    calibration_chunks.append(chunk)
                    calibration_duration += len(chunk) / self.settings.sample_rate

                rms_values = [self._rms(chunk) for chunk in calibration_chunks]
                ambient_rms = statistics.median(rms_values) if rms_values else 0.0
                threshold = max(
                    self.settings.minimum_rms_threshold,
                    ambient_rms * self.settings.noise_multiplier,
                )
                detector = SilenceDetector(threshold, self.settings.silence_seconds)
                self.output(f"録音中です。話してください（検出しきい値: {threshold:.4f}）")

                wait_started_at = time.monotonic()
                recorded_seconds = 0.0
                stop_reason = "no_speech"

                while True:
                    if (
                        not detector.speech_started
                        and time.monotonic() - wait_started_at
                        >= self.settings.speech_wait_timeout_seconds
                    ):
                        break

                    try:
                        chunk = chunks.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    duration = len(chunk) / self.settings.sample_rate
                    event = detector.update(self._rms(chunk), duration)
                    if not detector.speech_started:
                        pre_roll.append(chunk)
                        continue

                    if event == "speech_started":
                        captured_chunks.extend(pre_roll)
                        pre_roll.clear()
                    captured_chunks.append(chunk)
                    recorded_seconds += duration

                    if event == "finished":
                        stop_reason = "silence"
                        break
                    if recorded_seconds >= self.settings.max_recording_seconds:
                        stop_reason = "max_duration"
                        break
        except queue.Empty as exc:
            raise AudioError("マイクから音声データを受信できませんでした。") from exc
        except Exception as exc:
            raise AudioError(f"録音に失敗しました: {exc}") from exc

        if stream_statuses:
            self.output(f"録音デバイスからの通知: {stream_statuses[-1]}")
        if not detector.speech_started or not captured_chunks:
            return AudioData(
                samples=np.empty(0, dtype=np.float32),
                sample_rate=self.settings.sample_rate,
                speech_detected=False,
                stop_reason="no_speech",
            )

        samples = np.concatenate(captured_chunks).astype(np.float32, copy=False)
        return AudioData(
            samples=samples,
            sample_rate=self.settings.sample_rate,
            speech_detected=True,
            stop_reason=stop_reason,
        )

    @staticmethod
    def _rms(chunk) -> float:  # noqa: ANN001
        try:
            import numpy as np
        except ImportError as exc:
            raise AudioError("NumPyを読み込めません。") from exc
        if len(chunk) == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
