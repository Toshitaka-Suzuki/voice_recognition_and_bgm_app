"""BGMの拍位置から、その都度1回分だけ4拍ガイドを予約する。"""
from bisect import bisect_left
from dataclasses import dataclass, replace
from math import floor

HALF_WINDOW_MS = 150
WAIT_BEATS = (2, 4, 6)


@dataclass(frozen=True)
class RhythmGuide:
    beats_ms: tuple[float, ...]
    tones: tuple[object, object]

    def next_cue(self, after_ms: float, duration_ms: float, wait_beats: int):
        """次のBGM拍を基準に待ち拍数を空け、周回をまたぐ4拍を返す。"""
        cycle = floor(after_ms / duration_ms)
        index = cycle * len(self.beats_ms) + bisect_left(
            self.beats_ms, after_ms - cycle * duration_ms,
        ) + wait_beats

        def absolute_beat(i):
            lap, beat = divmod(i, len(self.beats_ms))
            return lap * duration_ms + self.beats_ms[beat]

        return tuple(absolute_beat(index + offset) for offset in range(4))


def with_rhythm_guide(track):
    """固定の予告配置は作らず、拍一覧と短い2種類の予告音だけを用意する。"""
    import numpy as np

    beats = tuple(sorted(set(beat for beat in track.beats_ms if 0 <= beat < track.duration_ms)))
    if len(beats) < 4:
        raise ValueError(f"{track.id}: 4拍ガイドには曲内の拍が4つ以上必要です")
    tones = []
    for target in (False, True):
        length = max(1, round(track.sample_rate * (0.11 if target else 0.065)))
        t = np.arange(length) / track.sample_rate
        frequency = 1320 if target else 660
        tone = np.sin(2 * np.pi * frequency * t)
        if target:
            tone += 0.35 * np.sin(2 * np.pi * frequency * 2.73 * t)
        tone *= np.minimum(t / 0.003, 1) * np.exp(-t * (40 if target else 65))
        tones.append((tone * (0.25 if target else 0.19)).astype(np.float32))
    # 判定窓はGameが予約した1機会から計算する。元の単独BGM用Trackは変更しない。
    return replace(track, windows=(), guide=RhythmGuide(beats, tuple(tones)), guide_samples=None)
