"""マイク入力を録音し、発話後の連続した無音で録音を止めるモジュール。"""

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
    """音量の推移から「待機・発話・無音・終了」を判定する。

    ``update``へ音声ブロックごとのRMS音量と長さを渡すと、現在の状態を
    文字列で返します。発話開始前の無音は終了判定へ数えません。
    """

    threshold: float
    silence_seconds: float
    speech_started: bool = False
    silent_seconds: float = 0.0

    def update(self, rms: float, duration: float) -> str:
        """1ブロック分の音量を反映し、判定イベント名を返す。"""

        # しきい値以上なら発話中とみなし、途中まで数えた無音時間を戻します。
        if rms >= self.threshold:
            was_started = self.speech_started
            self.speech_started = True
            self.silent_seconds = 0.0
            return "speaking" if was_started else "speech_started"
        # 発話が一度も始まっていなければ、無音でも録音終了にはしません。
        if not self.speech_started:
            return "waiting"
        self.silent_seconds += duration
        if self.silent_seconds >= self.silence_seconds:
            return "finished"
        return "silence"


class AudioRecorder:
    """sounddeviceを使い、メモリ上へモノラル音声を録音する。"""

    def __init__(
        self,
        settings: AudioSettings,
        output: Callable[[str], None] = print,
    ) -> None:
        """録音設定と、進行メッセージを表示する関数を受け取る。"""

        self.settings = settings
        self.output = output

    def check_device(self) -> str:
        """設定されたマイクが使用可能か調べ、デバイス名を返す。

        ライブラリ不足やマイク設定の問題は``AudioError``として通知します。
        """

        # 起動時だけ必要な外部ライブラリは、ここで遅延importします。
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
        """発話を録音し、無音または最大時間で終了した結果を返す。

        音声はファイルへ保存せずNumPy配列として返します。マイクからデータを
        取得できない場合や録音に失敗した場合は``AudioError``を発生させます。
        """

        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as exc:
            raise AudioError("録音ライブラリを読み込めません。依存関係をインストールしてください。") from exc

        # マイクのcallbackとメイン処理は別のタイミングで動くため、スレッド間で
        # 安全にデータを渡せるQueueへ小さな音声ブロックを積みます。
        chunks: queue.Queue = queue.Queue()
        stream_statuses: list[str] = []

        def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            """sounddeviceから呼ばれ、受信した音声をQueueへコピーする。"""

            del frames, time_info
            if status:
                stream_statuses.append(str(status))
            chunks.put(indata[:, 0].copy())

        # 秒単位の設定を、sounddeviceが要求するサンプル数へ変換します。
        block_size = max(1, round(self.settings.sample_rate * self.settings.block_seconds))
        calibration_chunks: list = []
        captured_chunks: list = []
        # 発話開始直前の音声も残すことで、最初の一音が欠けにくくなります。
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
                # 録音開始直後の環境音を測り、その場所に合うしきい値を決めます。
                self.output("環境音を測定しています。静かにお待ちください...")
                calibration_duration = 0.0
                while calibration_duration < self.settings.calibration_seconds:
                    chunk = chunks.get(timeout=2.0)
                    calibration_chunks.append(chunk)
                    calibration_duration += len(chunk) / self.settings.sample_rate

                # 外れ値の影響を受けにくい中央値と固定下限の大きい方を採用します。
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

                # 発話待機、発話開始、終了後の無音を同じループで監視します。
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
                    # 発話前はpre-rollだけを更新し、本録音の長さには含めません。
                    if not detector.speech_started:
                        pre_roll.append(chunk)
                        continue

                    # 発話検出時に直前の音声を本録音の先頭へ追加します。
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

        # callback内では表示せず、録音終了後に最後のデバイス通知を表示します。
        if stream_statuses:
            self.output(f"録音デバイスからの通知: {stream_statuses[-1]}")
        if not detector.speech_started or not captured_chunks:
            return AudioData(
                samples=np.empty(0, dtype=np.float32),
                sample_rate=self.settings.sample_rate,
                speech_detected=False,
                stop_reason="no_speech",
            )

        # 複数の小さなブロックを、faster-whisperへ渡す1本の配列に結合します。
        samples = np.concatenate(captured_chunks).astype(np.float32, copy=False)
        return AudioData(
            samples=samples,
            sample_rate=self.settings.sample_rate,
            speech_detected=True,
            stop_reason=stop_reason,
        )

    @staticmethod
    def _rms(chunk) -> float:  # noqa: ANN001
        """音声ブロックのRMS（二乗平均平方根）音量を計算する。"""

        try:
            import numpy as np
        except ImportError as exc:
            raise AudioError("NumPyを読み込めません。") from exc
        if len(chunk) == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
