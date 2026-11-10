"""DAC出力時刻とサンプル位置に同期したループ再生。"""
from collections import deque
from threading import Lock
from time import perf_counter

POSITION_GRACE_SECONDS = 0.020
CALLBACK_TIMEOUT_SECONDS = 0.500
INITIAL_CALLBACK_TIMEOUT_SECONDS = 5.0
WARNING_INTERVAL_SECONDS = 1.0


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
        self.device_id = "不明"
        self.host_api = "不明"
        self.output_latency = None
        self.underflow_count = 0
        self._synchronizing = False
        self._clock_generation = 0
        self._last_warning_at = None
        self._reported_underflows = 0
        self.last_block_frames = 0
        self._guide_cue = ()
        self._guide_reserved_until_frame = 0

    @property
    def guide_schedule_floor_ms(self):
        """既に出力準備済みのバッファを避けるための最早予約位置。"""
        with self.lock:
            return max(self.frames_sent, self._guide_reserved_until_frame) * 1000 / self.track.sample_rate

    def set_guide_cue(self, beats_ms):
        """選択済みの4拍だけを予約する。Noneは以後の予告音を停止する。"""
        if beats_ms is None:
            with self.lock:
                self._guide_cue = ()
            return True
        cue = tuple(
            (round(beat * self.track.sample_rate / 1000), self.track.guide.tones[index == 3])
            for index, beat in enumerate(beats_ms)
        )
        with self.lock:
            if cue[0][0] < max(self.frames_sent, self._guide_reserved_until_frame):
                return False
            self._guide_cue = cue
        return True

    @property
    def synchronizing(self):
        with self.lock:
            return self._synchronizing

    def take_warning(self, now):
        """メイン処理から警告を取り出す。callbackでは表示しない。"""
        with self.lock:
            if self.underflow_count == self._reported_underflows:
                return None
            if self._last_warning_at is not None and now - self._last_warning_at < WARNING_INTERVAL_SECONDS:
                return None
            self._last_warning_at = now
            self._reported_underflows = self.underflow_count
            count, pending = self.underflow_count, self._synchronizing
        state = "音声同期確認中" if pending else "音声同期復帰済み"
        return f"音声警告: output underflow / 今回の曲で{count}回 / {state}"

    @property
    def waiting_for_first_callback(self):
        with self.lock:
            return self.last_callback_at is None

    def play(self, track):
        self.stop()
        self.track = track
        self.frames_sent = 0
        self._guide_cue = ()
        self._guide_reserved_until_frame = 0
        self.started_at = None
        self.device_name = self.device_id = self.host_api = "不明"
        self.output_latency = None
        self.error = None
        with self.lock:
            self.anchors.clear()
            self.last_callback_at = None
            self.underflow_count = 0
            self._synchronizing = False
            self._clock_generation += 1
            self._last_warning_at = None
            self._reported_underflows = 0
            self.last_block_frames = 0
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
            self.device_id = getattr(self.stream, "device", "不明")
            self.output_latency = getattr(self.stream, "latency", None)
            if self.stream_factory is None:
                try:
                    info = sd.query_devices(self.stream.device, "output")
                    self.device_name = info["name"]
                    self.host_api = sd.query_hostapis(info["hostapi"])["name"]
                except Exception:
                    pass
            self.stream.start()
            # デバイス準備を初回待機へ算入しない。開始中の受信状態は保持する。
            self.started_at = perf_counter()
        except Exception:
            self.stop()
            raise

    def _callback(self, outdata, frames, time_info, status):
        with self.lock:
            self.last_callback_at = perf_counter()
            self.last_block_frames = frames
            self._guide_reserved_until_frame = self.frames_sent + frames
            guide_cue = self._guide_cue
        block_start = self.frames_sent
        try:
            underflow_only = (getattr(status, "output_underflow", False)
                              and not any(getattr(status, flag, False) for flag in
                                          ("input_underflow", "input_overflow", "output_overflow", "priming_output")))
            if status and not underflow_only:
                self.error = f"音声出力異常: {status}"
                outdata.fill(0)
                return
            audible_at = time_info.outputBufferDacTime
            with self.lock:
                if underflow_only:
                    self.underflow_count += 1
                    self._synchronizing = True
                    self._clock_generation += 1
                    self.anchors.clear()
                else:
                    self.anchors.append((audible_at, self.frames_sent, frames))
            offset = 0
            while offset < frames:
                source = self.frames_sent % len(self.track.samples)
                count = min(frames - offset, len(self.track.samples) - source)
                outdata[offset:offset + count] = self.track.samples[source:source + count]
                if self.track.guide is not None:
                    outdata[offset:offset + count] *= 0.65
                offset += count
                self.frames_sent += count
            # BGMの周回位置とは独立した絶対サンプル番号。選択していない拍は鳴らさない。
            for start, tone in guide_cue:
                first = max(block_start, start)
                last = min(block_start + frames, start + len(tone))
                if first < last:
                    outdata[first - block_start:last - block_start] += tone[first - start:last - start, None]
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
        now = perf_counter()
        with self.lock:
            last_callback = self.last_callback_at
        if last_callback is None:
            if self.started_at is not None and now - self.started_at >= INITIAL_CALLBACK_TIMEOUT_SECONDS:
                raise RuntimeError(self._diagnostic("初回音声コールバックを受信できません", now))
            return None
        if now - last_callback >= CALLBACK_TIMEOUT_SECONDS:
            raise RuntimeError(self._diagnostic("音声コールバックの更新が停止しました", now))
        # stream.timeの取得をPython時計で挟み、入力の取得時刻を同じ時計へ変換。
        before = perf_counter()
        stream_now = self.stream.time
        now = perf_counter()
        target = stream_now + (timestamp - (before + now) / 2)
        with self.lock:
            anchors = tuple(self.anchors)
            generation = self._clock_generation
        anchor = next((a for a in reversed(anchors) if a[0] <= target), None)
        if anchor is None:
            return None
        started, sample, frames = anchor
        elapsed = (target - started) * self.track.sample_rate
        if target - (started + frames / self.track.sample_rate) > POSITION_GRACE_SECONDS + 1e-9:
            return None
        absolute = sample + max(0, elapsed)
        length = len(self.track.samples)
        with self.lock:
            if generation != self._clock_generation:
                return None
            self._synchronizing = False
        return int(absolute // length) + 1, (absolute % length) * 1000 / self.track.sample_rate

    def _diagnostic(self, message, now=None):
        now = perf_counter() if now is None else now
        with self.lock:
            updated_at = self.last_callback_at
            frames = self.last_block_frames
            underflows = self.underflow_count
        updated_at = self.started_at if updated_at is None else updated_at
        gap_ms = max(0, now - updated_at) * 1000 if updated_at is not None else 0
        block_ms = frames * 1000 / self.track.sample_rate
        latency = "不明" if self.output_latency is None else f"{self.output_latency * 1000:.1f} ms"
        return (f"{message} / 更新停止時間: {gap_ms:.1f} ms / "
                f"ブロック長: {frames} samples ({block_ms:.1f} ms) / "
                f"初回受信: {'未受信' if self.waiting_for_first_callback else '受信済み'} / "
                f"音声デバイス: {self.device_id} {self.device_name} / "
                f"ホストAPI: {self.host_api} / サンプリング周波数: {self.track.sample_rate} Hz / "
                f"出力レイテンシ: {latency} / アンダーフロー回数: {underflows}")

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()
