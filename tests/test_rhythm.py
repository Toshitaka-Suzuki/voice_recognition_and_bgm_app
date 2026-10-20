"""4拍の音・判定・表示が同じ位置になり、再準備・周回・曲変更でずれないことを検証。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from chambara_bgm.config import Track, Window
from chambara_bgm.engine import Game
from chambara_bgm.player import AudioPlayer
from chambara_bgm.rhythm import with_rhythm_guide


def guided_track(stage="easy"):
    return with_rhythm_guide(Track(
        stage, (Window(100, 200),), np.full((8000, 2), 0.1, dtype=np.float32), 1000,
        beats_ms=tuple(range(0, 8000, 500)),
    ))


class ClockPlayer:
    def play(self, track):
        self.track = track

    def position(self, timestamp):
        absolute = timestamp * 1000
        return int(absolute // 8000) + 1, absolute % 8000

    def set_guide_start(self, absolute_ms):
        self.guide_start = absolute_ms

    def stop(self):
        pass


class RhythmTests(unittest.TestCase):
    def setUp(self):
        self.player = ClockPlayer()
        self.game = Game(tuple(guided_track(s) for s in ("easy", "normal", "hard")), self.player)
        with patch("chambara_bgm.engine.perf_counter", return_value=0):
            self.game.start()

    def test_only_fourth_beat_is_an_attack_and_has_symmetric_window(self):
        self.assertEqual(self.game.track.windows[0], Window(1350, 1650))
        self.assertIsNone(self.game.status(0.5)[2])
        self.assertEqual(self.game.status(1.35)[2], 0)
        self.assertEqual(self.game.status(1.5)[2], 0)
        self.assertIsNone(self.game.status(1.65)[2])
        event = self.game.submit_parry(1.5)
        self.assertEqual((event.kind, event.timing, event.delta_ms), ("parry_success", "on_time", 0))
        self.assertIsNone(self.player.guide_start)
        self.assertIsNone(self.game.submit_parry(1.5))

    def test_early_and_late_offsets_are_measured_from_the_same_target(self):
        early = self.game.submit_parry(1.2)
        self.assertEqual((early.timing, early.delta_ms), ("early", -300))
        self.game.set_defense(True, 1.8)
        late = self.game.submit_parry(3.7)
        self.assertEqual(late.timing, "late")
        self.assertAlmostEqual(late.delta_ms, 200)

    def test_rearming_waits_for_a_complete_sequence_and_survives_loop_boundary(self):
        self.game.submit_parry(1.5)
        self.game.set_defense(True, 2.1)
        self.assertEqual(self.player.guide_start, 4000)
        state = self.game.rhythm_state(2.2)
        self.assertEqual(state["beats_ms"], (4000, 4500, 5000, 5500))
        self.assertIsNone(self.game.status(3.5)[2])  # 途中から聞こえる古い攻撃は不採用
        event = self.game.submit_parry(5.5)
        self.assertEqual(event.kind, "parry_success")
        self.game.set_defense(True, 7.9)
        self.assertEqual(self.player.guide_start, 10000)  # 120msの予告準備余裕
        state = self.game.rhythm_state(8.1)
        self.assertEqual(state["beats_ms"], (10000, 10500, 11000, 11500))

    def test_track_change_does_not_restart_cues_during_attack(self):
        self.game.submit_parry(1.5)
        with patch("chambara_bgm.engine.perf_counter", return_value=0):
            self.game.switch_stage("normal")
        self.assertIsNone(self.player.guide_start)
        self.assertFalse(self.game.rhythm_state(0.1)["active"])
        self.game.set_defense(True, 0.1)
        self.assertEqual(self.game.rhythm_state(0.2)["stage"], "normal")
        self.assertEqual(self.player.guide_start, 2000)

    def test_audio_callback_mixes_cues_at_exact_samples_and_mutes_on_pause(self):
        track = guided_track()
        player = AudioPlayer()
        player.track = track
        player.set_guide_start(2000)
        out = np.zeros((4000, 2), dtype=np.float32)
        player._callback(out, len(out), SimpleNamespace(outputBufferDacTime=0), False)
        self.assertIsNone(player.error)
        np.testing.assert_allclose(out[:2000], 0.065, atol=1e-7)
        np.testing.assert_allclose(out[2000:, 0], 0.065 + track.guide_samples[2000:4000], atol=1e-7)
        self.assertGreater(float(np.max(np.abs(out[2000:] - 0.065))), 0.05)
        player.set_guide_start(None)
        player._callback(out, len(out), SimpleNamespace(outputBufferDacTime=4), False)
        np.testing.assert_allclose(out, 0.065, atol=1e-7)

    def test_base_track_is_not_modified(self):
        base = Track("easy", (Window(100, 200),), np.zeros((4000, 1)), 1000,
                     beats_ms=tuple(range(0, 4000, 500)))
        guided = with_rhythm_guide(base)
        self.assertIsNone(base.guide)
        self.assertIsNone(base.guide_samples)
        self.assertEqual(base.windows, (Window(100, 200),))
        self.assertEqual(len(guided.guide.cues), 2)

    def test_unknown_audio_position_waits_before_rearming(self):
        self.game.submit_parry(1.5)
        with patch.object(self.player, "position", return_value=None):
            self.game.set_defense(True, 2.1)
            self.assertIsNone(self.player.guide_start)
            self.assertFalse(self.game.rhythm_state(2.2)["ready"])
            self.assertIsNone(self.game.submit_parry(2.2))
        state = self.game.rhythm_state(3.1)
        self.assertTrue(state["ready"])
        self.assertEqual(state["beats_ms"], (4000, 4500, 5000, 5500))
