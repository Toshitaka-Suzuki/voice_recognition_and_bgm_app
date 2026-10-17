"""外部信号に依存しないゲーム処理。メソッドは同じ制御スレッドから呼ぶ。"""
from dataclasses import dataclass
from time import perf_counter


@dataclass(frozen=True)
class Event:
    kind: str
    stage: str
    loop: int | None = None
    position_ms: float | None = None
    window_id: int | None = None
    reason: str = ""


class Game:
    def __init__(self, tracks, player, emit=lambda event: None):
        self.tracks, self.player, self.emit = tracks, player, emit
        self.index = 0
        self.running = False
        self.generation = 0
        self.accept_after = 0.0
        self.successes = set()
        self.last_loop = None

    @property
    def track(self):
        return self.tracks[self.index]

    def start(self):
        self.running = False
        self.index = 0
        self._play()

    def _play(self):
        self.running = False
        self.generation += 1
        self.successes.clear()
        self.last_loop = None
        self.player.play(self.track)
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
        return loop, ms, window

    def submit_parry(self, timestamp=None, generation=None):
        """入力時刻を判定し、通知したイベントを返す。判定不能なら``None``。"""

        timestamp = perf_counter() if timestamp is None else timestamp
        if not self._accepts(timestamp, generation):
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
        event = Event("parry_success" if success else "parry_failure", self.track.id,
                      loop, ms, window, "" if success else "範囲外または成功済み")
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
