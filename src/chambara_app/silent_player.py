"""無音の通信テスト用時計。実機の音声同期精度の確認には使用しない。"""

from time import perf_counter


class SilentAudioPlayer:
    """BGMエンジンの判定区間は使い、音声出力だけを省略する。"""

    waiting_for_first_callback = False
    synchronizing = False

    def __init__(self) -> None:
        self.track = None
        self.started_at = 0.0

    def play(self, track) -> None:
        self.track = track
        self.started_at = perf_counter()

    def stop(self) -> None:
        self.track = None

    def position(self, timestamp):
        if self.track is None or timestamp < self.started_at:
            return None
        elapsed = (timestamp - self.started_at) * 1000
        return int(elapsed // self.track.duration_ms) + 1, elapsed % self.track.duration_ms

    def take_warning(self, now):
        return None

    def set_guide_start(self, absolute_ms):
        pass
