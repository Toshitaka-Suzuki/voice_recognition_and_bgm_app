"""外部信号に依存しないゲーム処理。メソッドは同じ制御スレッドから呼ぶ。"""
from dataclasses import dataclass
from time import perf_counter
from .rhythm import HALF_WINDOW_MS


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
    def __init__(self, tracks, player, emit=lambda event: None):
        self.tracks, self.player, self.emit = tracks, player, emit
        self.index = 0
        self.running = False
        self.generation = 0
        self.accept_after = 0.0
        self.successes = set()
        self.last_loop = None
        self.defense_active = True
        self.guide_from_ms = 0.0
        self._guide_arm_pending = False

    @property
    def track(self):
        return self.tracks[self.index]

    def start(self):
        self.running = False
        self.index = 0
        self.defense_active = True
        self._play()

    def _play(self):
        self.running = False
        self.generation += 1
        self.successes.clear()
        self.last_loop = None
        self.player.play(self.track)
        self.guide_from_ms = 0.0
        self._guide_arm_pending = False
        if self.track.guide is not None:
            self.player.set_guide_start(0 if self.defense_active else None)
        self.accept_after = perf_counter()
        self.running = True
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
        window = next((i for i, w in enumerate(self.track.windows)
                       if w.start_ms <= ms < w.end_ms), None)
        if self.track.guide is not None and (
            not self.defense_active or self._guide_arm_pending or
            (window is not None and self.track.guide.cues[window].beats_ms[0]
             + (loop - 1) * self.track.duration_ms < self.guide_from_ms)
        ):
            window = None
        return loop, ms, window

    def set_defense(self, active, timestamp=None):
        """途中の拍から予告を再開せず、次の4拍を丸ごと案内する。"""
        if (not self.running or self.track.guide is None
                or (active == self.defense_active and not self._guide_arm_pending)):
            return
        self.defense_active = active
        if not active:
            self._guide_arm_pending = False
            self.player.set_guide_start(None)
            return
        position = self.player.position(perf_counter() if timestamp is None else timestamp)
        if position is None:
            self._guide_arm_pending = True
            self.player.set_guide_start(None)
            return
        self._guide_arm_pending = False
        absolute = (position[0] - 1) * self.track.duration_ms + position[1]
        candidates = self.track.guide.occurrences(absolute, self.track.duration_ms, absolute + 120)
        self.guide_from_ms = candidates[0][0]
        self.player.set_guide_start(self.guide_from_ms)

    def rhythm_state(self, timestamp):
        if not self.running or self.track.guide is None:
            return {"active": False, "ready": False}
        if self._guide_arm_pending:
            self.set_defense(True, timestamp)
        status = self.status(timestamp)
        base = {"generation": self.generation, "stage": self.track.id, "active": self.defense_active}
        if status is None:
            return {**base, "ready": False}
        loop, ms, _ = status
        absolute = (loop - 1) * self.track.duration_ms + ms
        candidates = self.track.guide.occurrences(absolute, self.track.duration_ms, self.guide_from_ms)
        beats = next(b for b in candidates if b[-1] + HALF_WINDOW_MS > absolute)
        return {**base, "ready": True, "position_ms": absolute,
                "beats_ms": beats, "window_ms": HALF_WINDOW_MS * 2}

    def submit_parry(self, timestamp=None, generation=None):
        """入力時刻を判定し、通知したイベントを返す。判定不能なら``None``。"""

        timestamp = perf_counter() if timestamp is None else timestamp
        if not self._accepts(timestamp, generation):
            return None
        if self.track.guide is not None and (not self.defense_active or self._guide_arm_pending):
            return None
        status = self.status(timestamp)
        if status is None:
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
            candidates = self.track.guide.occurrences(absolute, self.track.duration_ms, self.guide_from_ms)
            nearest = min(candidates, key=lambda b: abs(b[-1] - absolute))
            delta = absolute - nearest[-1]
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
        timestamp = perf_counter() if timestamp is None else timestamp
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
        self.generation += 1
        try:
            self.player.stop()
        finally:
            if was_running:
                self.emit(Event("game_finished", self.track.id, reason=reason))
