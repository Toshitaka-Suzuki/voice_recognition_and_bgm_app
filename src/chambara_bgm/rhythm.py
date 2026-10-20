"""統合版の4拍ガイド。音・表示・判定は同じ音声サンプル位置を使用する。"""
from dataclasses import dataclass, replace
from math import floor

from .config import Window

HALF_WINDOW_MS = 150


@dataclass(frozen=True)
class GuideCue:
    beats_ms: tuple[float, ...]

    @property
    def target_ms(self):
        return self.beats_ms[-1]


@dataclass(frozen=True)
class RhythmGuide:
    cues: tuple[GuideCue, ...]

    def occurrences(self, position_ms, duration_ms, armed_from_ms):
        """周回境界の前後も含め、現在付近の有効な攻撃を返す。"""
        lap = floor(position_ms / duration_ms)
        return [
            tuple(beat + cycle * duration_ms for beat in cue.beats_ms)
            for cycle in range(max(0, lap - 1), lap + 3)
            for cue in self.cues
            if (cue.beats_ms[0] + cycle * duration_ms >= armed_from_ms
                and cue.target_ms + cycle * duration_ms >= 0)
        ]


def with_rhythm_guide(track):
    """元の拍位置から4拍ごとの攻撃と予告音を作る。単独BGM版は変更しない。"""
    import numpy as np

    beats = tuple(sorted(set(track.beats_ms)))
    cues = tuple(
        GuideCue(beats[i:i + 4])
        for i in range(0, len(beats) - 3, 4)
        if beats[i + 3] + HALF_WINDOW_MS < track.duration_ms
    )
    if not cues:
        raise ValueError(f"{track.id}: 4拍ガイドには曲内の拍が4つ以上必要です")
    guide_samples = np.zeros(len(track.samples), dtype=np.float32)
    for cue in cues:
        for index, beat in enumerate(cue.beats_ms):
            target = index == 3
            length = int(track.sample_rate * (0.11 if target else 0.065))
            t = np.arange(length) / track.sample_rate
            frequency = 1320 if target else 660
            # 短い木音を3回、最後だけ高い金属音。BGMと同じ出力コールバックで鳴る。
            tone = np.sin(2 * np.pi * frequency * t)
            if target:
                tone += 0.35 * np.sin(2 * np.pi * frequency * 2.73 * t)
            tone *= np.minimum(t / 0.003, 1) * np.exp(-t * (40 if target else 65))
            start = round(beat * track.sample_rate / 1000)
            end = min(start + length, len(guide_samples))
            guide_samples[start:end] += tone[:end - start] * (0.25 if target else 0.19)
    return replace(
        track,
        windows=tuple(Window(c.target_ms - HALF_WINDOW_MS, c.target_ms + HALF_WINDOW_MS) for c in cues),
        guide=RhythmGuide(cues),
        guide_samples=guide_samples,
    )
