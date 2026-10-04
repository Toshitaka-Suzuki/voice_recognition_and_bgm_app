"""DAC出力時刻とサンプル位置に同期したループ再生。"""
from collections import deque
from threading import Lock
from time import perf_counter

POSITION_GRACE_SECONDS = 0.020
CALLBACK_TIMEOUT_SECONDS = 0.500


class AudioPlayer:
    def __init__(self, stream_factory=None):
        self.stream_factory = stream_factory
        self.stream = None
        self.lock = Lock()
        self.anchors = deque(maxlen=512)
        self.error = None
        self.track = None
        self.frames_sent = 0
        self.started_at = None
        self.last_callback_at = None
        self.device_name = "不明"

    def play(self, track):
        self.stop()
        self.track = track
        self.frames_sent = 0
        self.started_at = perf_counter()
        self.error = None
        with self.lock:
            self.anchors.clear()
            self.last_callback_at = None
        if self.stream_factory is None:
            import sounddevice as sd
            factory = sd.OutputStream
        else:
            factory = self.stream_factory
        try:
            self.stream = factory(samplerate=track.sample_rate,
                                  channels=track.samples.shape[1], dtype="float32",
                                  blocksize=0, callback=self._callback)
            self.device_name = str(getattr(self.stream, "device", "不明"))
            if self.stream_factory is None:
                try:
                    self.device_name = sd.query_devices(self.stream.device, "output")["name"]
                except Exception:
                    pass
            self.stream.start()
        except Exception:
            self.stop()
            raise

    def _callback(self, outdata, frames, time_info, status):
        with self.lock:
            self.last_callback_at = perf_counter()
        try:
            if status:
                self.error = f"音声出力異常: {status}"
                outdata.fill(0)
                return
            audible_at = time_info.outputBufferDacTime
            with self.lock:
                self.anchors.append((audible_at, self.frames_sent, frames))
            offset = 0
            while offset < frames:
                source = self.frames_sent % len(self.track.samples)
                count = min(frames - offset, len(self.track.samples) - source)
                outdata[offset:offset + count] = self.track.samples[source:source + count]
                offset += count
                self.frames_sent += count
        except Exception as exc:
            self.error = f"音声再生に失敗しました: {exc}"
            outdata.fill(0)

    def position(self, timestamp):
        """取得時刻の(周回, ms)。未出力・時刻不明ならNone。"""
        if self.error:
            raise RuntimeError(self._diagnostic(self.error))
        if self.stream is None:
            return None
        if not self.stream.active:
            raise RuntimeError(self._diagnostic("音声出力ストリームが停止しました"))
        # stream.timeの取得をPython時計で挟み、入力の取得時刻を同じ時計へ変換。
        before = perf_counter()
        stream_now = self.stream.time
        now = perf_counter()
        target = stream_now + timestamp - (before + now) / 2
        with self.lock:
            last_callback = self.last_callback_at
            anchor = next((a for a in reversed(self.anchors) if a[0] <= target), None)
        updated_at = self.started_at if last_callback is None else last_callback
        if now - updated_at >= CALLBACK_TIMEOUT_SECONDS:
            raise RuntimeError(self._diagnostic("音声コールバックの更新が停止しました", now))
        if anchor is None:
            return None
        started, sample, frames = anchor
        elapsed = (target - started) * self.track.sample_rate
        if target - (started + frames / self.track.sample_rate) > POSITION_GRACE_SECONDS + 1e-9:
            return None
        absolute = sample + max(0, elapsed)
        length = len(self.track.samples)
        return int(absolute // length) + 1, (absolute % length) * 1000 / self.track.sample_rate

    def _diagnostic(self, message, now=None):
        now = perf_counter() if now is None else now
        with self.lock:
            updated_at = self.last_callback_at
            frames = self.anchors[-1][2] if self.anchors else 0
        updated_at = self.started_at if updated_at is None else updated_at
        gap_ms = max(0, now - updated_at) * 1000
        block_ms = frames * 1000 / self.track.sample_rate
        return (f"{message} / 更新停止時間: {gap_ms:.1f} ms / "
                f"ブロック長: {frames} samples ({block_ms:.1f} ms) / "
                f"音声デバイス: {self.device_name}")

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()
