"""外部信号に依存しないゲーム処理。メソッドは同じ制御スレッドから呼ぶ。"""
from dataclasses import dataclass
from random import Random
from time import perf_counter
from .rhythm import HALF_WINDOW_MS, WAIT_BEATS


@dataclass(frozen=True)
class Event:
    kind: str
    stage: str
    loop: int | None = None
    position_ms: float | None = None
    window_id: int | None = None
    reason: str = ""
    timing: str | None = None
    delta_ms: float | None = None


class Game:
    def __init__(self, tracks, player, emit=lambda event: None, *, clock=None, rng=None):
        self.tracks, self.player, self.emit = tracks, player, emit
        self._clock = clock or (lambda: perf_counter())
        self._rng = rng if rng is not None else Random()
        self.index = 0
        self.running = False
        self.generation = 0
        self.accept_after = 0.0
        self.successes = set()
        self.last_loop = None
        self.defense_active = True
        self.external_parry = False
        self._cue = None
        self._previous_cue = None
        self._opportunity_id = 0
        self._wait_beats = None
        self._last_guide_position = 0.0

    @property
    def track(self):
        return self.tracks[self.index]

    def start(self, stage="easy", *, external_parry=False):
        target = next((index for index, track in enumerate(self.tracks) if track.id == stage), None)
        if target is None:
            raise ValueError(f"未定義のBGM段階です: {stage}")
        self.running = False
        self.index = target
        self.defense_active = True
        self.external_parry = external_parry
        self._play()

    def _play(self):
        self.running = False
        self.generation += 1
        self.successes.clear()
        self.last_loop = None
        self.player.play(self.track)
        self._cue = None
        self._previous_cue = None
        self._wait_beats = None
        self._last_guide_position = 0.0
        if self.track.guide is not None:
            self.player.set_guide_cue(None)
        self.accept_after = self._clock()
        self.running = True
        if self.track.guide is not None:
            self.status(self.accept_after)
        self.emit(Event("stage_changed", self.track.id,
                        reason=f"曲外の判定区間: {self.track.excluded_window_count}件除外"))

    def _accepts(self, timestamp, generation):
        return (self.running and timestamp >= self.accept_after
                and (generation is None or generation == self.generation))

    def status(self, timestamp):
        if not self.running:
            return None
        position = self.player.position(timestamp)
        if position is None:
            return None
        loop, ms = position
        if self.track.guide is not None:
            absolute = (loop - 1) * self.track.duration_ms + ms
            self._advance_guide(absolute)
            window = None
            if self.defense_active and self._cue is not None:
                target = self._cue[-1]
                if target - HALF_WINDOW_MS <= absolute < target + HALF_WINDOW_MS:
                    window = self._opportunity_id
            return loop, ms, window
        window = next((i for i, w in enumerate(self.track.windows)
                       if w.start_ms <= ms < w.end_ms), None)
        return loop, ms, window

    def _advance_guide(self, absolute):
        """通常は見逃しでも次へ進む。ESP連携では外部の終了処理まで予定を保持する。"""
        if not self.defense_active or absolute < self._last_guide_position:
            return
        self._last_guide_position = absolute
        if self._cue is not None and absolute >= self._cue[-1] + HALF_WINDOW_MS:
            if self.external_parry:
                # ESPの成功通知は有効時間後に届く場合がある。ここで次の機会に
                # 進まず、ブラウザーのdefense_pause → defense_readyを待つ。
                return
            self._previous_cue = self._cue
            self._cue = None
            self._wait_beats = None
            self.player.set_guide_cue(None)
        if self._cue is not None:
            return
        if self._wait_beats is None:
            self._wait_beats = self._rng.choice(WAIT_BEATS)
        # 既に音声デバイスへ渡したバッファより先へ予約し、最初の拍を欠かさない。
        floor_ms = max(absolute, getattr(self.player, "guide_schedule_floor_ms", absolute))
        beats = self.track.guide.next_cue(floor_ms, self.track.duration_ms, self._wait_beats)
        if self.player.set_guide_cue(beats) is False:
            return  # callbackが先に進んだ場合も待ち拍数は保持して次の確認で予約する。
        self._cue = beats
        self._opportunity_id += 1

    def set_defense(self, active, timestamp=None):
        """弱点中は音・判定を止め、復帰時に次の完全な4拍を1回だけ予約する。"""
        if (not self.running or self.track.guide is None
                or active == self.defense_active):
            return
        self.defense_active = active
        self._cue = None
        self._previous_cue = None
        self._wait_beats = None
        self.player.set_guide_cue(None)
        if not active:
            return
        self.status(self._clock() if timestamp is None else timestamp)

    def rhythm_state(self, timestamp):
        if not self.running or self.track.guide is None:
            return {"active": False, "ready": False}
        status = self.status(timestamp)
        base = {"generation": self.generation, "stage": self.track.id, "active": self.defense_active}
        if status is None or self._cue is None or not self.defense_active:
            return {**base, "ready": False}
        loop, ms, _ = status
        absolute = (loop - 1) * self.track.duration_ms + ms
        return {**base, "ready": True, "position_ms": absolute,
                "beats_ms": self._cue, "window_ms": HALF_WINDOW_MS * 2,
                "opportunity_id": self._opportunity_id, "wait_beats": self._wait_beats}

    def submit_parry(self, timestamp=None, generation=None):
        """入力時刻を判定し、通知したイベントを返す。判定不能なら``None``。"""

        timestamp = self._clock() if timestamp is None else timestamp
        if self.external_parry:
            return None  # ESPの結果をPythonへの到着時刻で判定し直さない。
        if not self._accepts(timestamp, generation):
            return None
        if self.track.guide is not None and not self.defense_active:
            return None
        status = self.status(timestamp)
        if status is None:
            return None
        if self.track.guide is not None and self._cue is None:
            return None
        loop, ms, window = status
        # 表示更新は成功済み状態を変更しない。遅着した旧周回信号も無視する。
        if self.last_loop is not None and loop < self.last_loop:
            return None
        if loop != self.last_loop:
            self.successes.clear()
            self.last_loop = loop
        success = window is not None and window not in self.successes
        if success:
            self.successes.add(window)
        timing, delta = None, None
        if self.track.guide is not None:
            absolute = (loop - 1) * self.track.duration_ms + ms
            candidates = [self._cue]
            if self._previous_cue is not None:
                candidates.append(self._previous_cue)
            nearest = min(candidates, key=lambda b: abs(b[-1] - absolute))
            delta = absolute - (self._cue[-1] if success else nearest[-1])
            timing = "on_time" if success else "early" if delta < 0 else "late"
            self.set_defense(False)
        event = Event("parry_success" if success else "parry_failure", self.track.id,
                      loop, ms, window, "" if success else "範囲外または成功済み", timing, delta)
        self.emit(event)
        return event

    def switch_stage(self, stage: str) -> bool:
        """実行中のBGMを指定段階へ切り替える。同じ段階なら何もしない。"""

        if not self.running:
            return False
        target = next((index for index, track in enumerate(self.tracks) if track.id == stage), None)
        if target is None:
            raise ValueError(f"未定義のBGM段階です: {stage}")
        if target == self.index:
            return False
        self.index = target
        self._play()
        return True

    def submit_life_decrement(self, timestamp=None, generation=None):
        timestamp = self._clock() if timestamp is None else timestamp
        if not self._accepts(timestamp, generation):
            return
        self.emit(Event("life_decrement", self.track.id))
        if self.index == len(self.tracks) - 1:
            self.stop("completed")
        else:
            self.index += 1
            self._play()

    def stop(self, reason="quit"):
        was_running = self.running
        self.running = False
        self.external_parry = False
        self.generation += 1
        self._cue = None
        self._previous_cue = None
        self._wait_beats = None
        try:
            self.player.stop()
        finally:
            if was_running:
                self.emit(Event("game_finished", self.track.id, reason=reason))
