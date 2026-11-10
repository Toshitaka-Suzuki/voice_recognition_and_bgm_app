"""選択した1機会の音・表示・判定と、待ち拍数の抽選を実デバイスなしで検証。"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from chambara_bgm.config import Track, Window
from chambara_bgm.engine import Game
from chambara_bgm.player import AudioPlayer
from chambara_bgm.rhythm import with_rhythm_guide


def guided_track(stage="easy", beat_ms=500):
    return with_rhythm_guide(Track(
        stage, (Window(100, 200),), np.full((8000, 2), 0.1, dtype=np.float32), 1000,
        beats_ms=tuple(range(0, 8000, beat_ms)),
    ))


class ClockPlayer:
    def __init__(self, clock):
        self.clock = clock
        self.played = []
        self.cue = None
        self.scheduled = []

    def play(self, track):
        self.track = track
        self.started = self.clock()
        self.played.append(track.id)

    def position(self, timestamp):
        absolute = (timestamp - self.started) * 1000
        return int(absolute // self.track.duration_ms) + 1, absolute % self.track.duration_ms

    def set_guide_cue(self, beats_ms):
        self.cue = beats_ms
        self.scheduled.append(beats_ms)
        return True

    def stop(self):
        self.cue = None


class RhythmTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.rng = Mock()
        self.rng.choice.side_effect = [2, 4, 6, 2, 4, 6]
        self.player = ClockPlayer(lambda: self.now)
        self.game = Game(tuple(guided_track(s) for s in ("easy", "normal", "hard")), self.player,
                         clock=lambda: self.now, rng=self.rng)
        self.game.start()

    def test_only_selected_fourth_beat_has_symmetric_window(self):
        state = self.game.rhythm_state(0)
        self.assertEqual(state["beats_ms"], (1000, 1500, 2000, 2500))
        self.assertEqual(self.player.cue, state["beats_ms"])
        self.assertIsNone(self.game.status(1.5)[2])  # 旧固定配置の4拍目は無効
        self.assertIsNone(self.game.status(2.349)[2])
        self.assertEqual(self.game.status(2.35)[2], state["opportunity_id"])
        self.assertEqual(self.game.status(2.649)[2], state["opportunity_id"])
        event = self.game.submit_parry(2.5)
        self.assertEqual((event.kind, event.timing, event.delta_ms), ("parry_success", "on_time", 0))
        self.assertIsNone(self.player.cue)
        self.assertIsNone(self.game.submit_parry(2.5))

    def test_wait_is_drawn_once_per_opportunity_and_uses_two_four_six_beats(self):
        first = self.game.rhythm_state(0.1)
        for time in (0.2, 0.8, 1.5, 2.6):
            self.game.status(time)
            self.assertEqual(self.game.rhythm_state(time)["beats_ms"], first["beats_ms"])
        self.rng.choice.assert_called_once_with((2, 4, 6))
        second = self.game.rhythm_state(2.65)  # 見逃し、次の拍3000msから4拍待ち
        self.assertEqual(second["beats_ms"], (5000, 5500, 6000, 6500))
        self.assertEqual(second["wait_beats"], 4)
        third = self.game.rhythm_state(6.65)  # 次の拍7000msから6拍待ち、次の周回へ
        self.assertEqual(third["beats_ms"], (10000, 10500, 11000, 11500))
        self.assertEqual(third["wait_beats"], 6)
        self.assertEqual([first["opportunity_id"], second["opportunity_id"], third["opportunity_id"]], [1, 2, 3])
        self.assertEqual(self.rng.choice.call_count, 3)

    def test_health_status_alone_advances_missed_window(self):
        self.game.status(2.65)
        self.assertEqual(self.player.cue, (5000, 5500, 6000, 6500))
        self.assertIsNone(self.game.status(3.5)[2])
        self.assertEqual(self.game.submit_parry(6.5).kind, "parry_success")

    def test_external_parry_keeps_expired_opportunity_until_explicit_rearm(self):
        self.game.stop()
        self.rng.choice.side_effect = [2, 4]
        self.rng.choice.reset_mock()
        self.game.start(external_parry=True)
        first = self.game.rhythm_state(0)
        scheduled_count = len(self.player.scheduled)
        for timestamp in (2.65, 3.0, 10.0, 80.0):
            self.assertIsNone(self.game.status(timestamp)[2])
            state = self.game.rhythm_state(timestamp)
            self.assertTrue(state["ready"])
            self.assertEqual(state["beats_ms"], first["beats_ms"])
            self.assertEqual(state["opportunity_id"], first["opportunity_id"])
            self.assertAlmostEqual(state["position_ms"], timestamp * 1000)
        self.assertEqual(len(self.player.scheduled), scheduled_count)
        self.rng.choice.assert_called_once_with((2, 4, 6))
        # 遅着した成功通知を待っている間に、readyの重複で次の予告へ進まない。
        self.game.set_defense(True, 80.0)
        self.assertEqual(self.game.rhythm_state(80.0)["beats_ms"], first["beats_ms"])
        self.game.set_defense(False, 80.0)
        self.assertFalse(self.game.rhythm_state(80.0)["ready"])
        self.game.set_defense(True, 80.1)
        next_state = self.game.rhythm_state(80.1)
        self.assertEqual(next_state["beats_ms"], (82500, 83000, 83500, 84000))
        self.assertEqual(next_state["opportunity_id"], first["opportunity_id"] + 1)
        self.assertEqual(self.rng.choice.call_count, 2)

    def test_external_parry_does_not_rejudge_even_an_on_time_arrival(self):
        self.game.stop()
        self.rng.choice.side_effect = [2]
        self.game.start(external_parry=True)
        cue = self.player.cue
        self.assertIsNone(self.game.submit_parry(2.5))
        self.assertEqual(self.player.cue, cue)
        self.assertTrue(self.game.defense_active)
        self.assertEqual(self.game.successes, set())

    def test_new_normal_game_restores_automatic_next_cue_after_external_game(self):
        self.game.stop()
        self.rng.choice.side_effect = [2, 2, 4]
        self.game.start(external_parry=True)
        self.assertEqual(self.game.rhythm_state(3)["beats_ms"], (1000, 1500, 2000, 2500))
        self.game.stop()
        self.assertFalse(self.game.external_parry)
        self.game.start()
        self.assertFalse(self.game.external_parry)
        self.assertEqual(self.game.rhythm_state(2.65)["beats_ms"], (5000, 5500, 6000, 6500))

    def test_no_duplicate_reselection_when_health_and_display_poll_same_boundary(self):
        self.game.status(2.65)
        cue = self.player.cue
        for _ in range(5):
            self.game.status(2.65)
            self.game.rhythm_state(2.65)
        self.assertEqual(self.player.cue, cue)
        self.assertEqual(self.rng.choice.call_count, 2)

    def test_early_and_late_offsets_use_the_selected_opportunity(self):
        early = self.game.submit_parry(2.2)
        self.assertEqual((early.timing, early.delta_ms), ("early", -300))
        self.game.set_defense(True, 3.0)  # 4拍待ち -> 5000..6500
        late = self.game.submit_parry(6.7)
        self.assertEqual(late.timing, "late")
        self.assertAlmostEqual(late.delta_ms, 200)

    def test_pause_disables_sound_judgment_and_draws_until_rearmed(self):
        self.game.set_defense(False, 0.5)
        self.assertIsNone(self.player.cue)
        for time in (1.0, 10.0, 100.0):
            state = self.game.rhythm_state(time)
            self.assertFalse(state["active"])
            self.assertFalse(state["ready"])
            self.assertIsNone(self.game.status(time)[2])
            self.assertIsNone(self.game.submit_parry(time))
        self.assertEqual(self.rng.choice.call_count, 1)
        self.game.set_defense(True, 100.1)
        self.assertEqual(self.player.cue, (102500, 103000, 103500, 104000))
        self.game.set_defense(True, 100.2)
        self.assertEqual(self.rng.choice.call_count, 2)

    def test_four_beats_can_cross_bgm_loop_without_repeating_earlier_cues(self):
        self.game.set_defense(False, 0.5)
        self.rng.choice.side_effect = [2]
        self.game.set_defense(True, 6.1)
        state = self.game.rhythm_state(6.2)
        self.assertEqual(state["beats_ms"], (7500, 8000, 8500, 9000))
        self.assertEqual(self.game.rhythm_state(8.1)["beats_ms"], state["beats_ms"])
        self.assertEqual(self.game.submit_parry(9.0).kind, "parry_success")

    def test_long_polling_gap_resumes_with_a_full_future_sequence(self):
        state = self.game.rhythm_state(80.1)
        self.assertEqual(state["beats_ms"], (82500, 83000, 83500, 84000))
        self.assertEqual(self.rng.choice.call_count, 2)
        self.assertEqual(self.player.cue, state["beats_ms"])

    def test_unknown_audio_position_waits_without_changing_schedule(self):
        cue = self.player.cue
        with patch.object(self.player, "position", return_value=None):
            self.assertFalse(self.game.rhythm_state(0.5)["ready"])
            self.assertIsNone(self.game.submit_parry(0.5))
        self.assertEqual(self.game.rhythm_state(0.6)["beats_ms"], cue)
        self.assertEqual(self.rng.choice.call_count, 1)
        self.game.set_defense(False, 0.6)
        with patch.object(self.player, "position", return_value=None):
            self.game.set_defense(True, 3.0)
            self.assertFalse(self.game.rhythm_state(3.1)["ready"])
        self.assertIsNone(self.player.cue)
        self.assertEqual(self.rng.choice.call_count, 1)
        self.assertEqual(self.game.rhythm_state(3.2)["beats_ms"], (5500, 6000, 6500, 7000))

    def test_callback_reservation_race_retries_without_redrawing_wait(self):
        self.game.set_defense(False, 0.5)
        with patch.object(self.player, "set_guide_cue", return_value=False):
            self.game.set_defense(True, 3.0)
            self.assertFalse(self.game.rhythm_state(3.1)["ready"])
        self.assertEqual(self.rng.choice.call_count, 2)
        self.assertTrue(self.game.rhythm_state(3.2)["ready"])
        self.assertEqual(self.rng.choice.call_count, 2)

    def test_selected_difficulty_starts_directly_and_invalid_stage_is_rejected(self):
        player = ClockPlayer(lambda: 0)
        game = Game(tuple(guided_track(s) for s in ("easy", "normal", "hard")), player,
                    clock=lambda: 0, rng=Mock(choice=Mock(return_value=2)))
        game.start(stage="hard")
        self.assertEqual(player.played, ["hard"])
        self.assertEqual(game.rhythm_state(0)["stage"], "hard")
        with self.assertRaisesRegex(ValueError, "未定義"):
            game.start(stage="unknown")
        self.assertEqual(player.played, ["hard"])

    def test_track_change_during_weak_point_keeps_guide_paused(self):
        self.game.submit_parry(2.5)
        self.now = 3.0
        self.game.switch_stage("normal")
        self.assertIsNone(self.player.cue)
        self.assertFalse(self.game.rhythm_state(3.1)["active"])
        self.game.set_defense(True, 3.1)
        self.assertEqual(self.game.rhythm_state(3.2)["stage"], "normal")
        self.assertEqual(self.player.cue, (2500, 3000, 3500, 4000))

    def test_wait_follows_track_beats_instead_of_fixed_wall_time(self):
        slow = guided_track(beat_ms=500).guide.next_cue(0, 8000, 4)
        fast = guided_track(beat_ms=250).guide.next_cue(0, 8000, 4)
        self.assertEqual(slow, (2000, 2500, 3000, 3500))
        self.assertEqual(fast, (1000, 1250, 1500, 1750))

    def test_audio_callback_only_mixes_selected_cue_across_loop_boundary(self):
        track = guided_track()
        player = AudioPlayer()
        player.track = track
        beats = (7500, 8000, 8500, 9000)
        self.assertTrue(player.set_guide_cue(beats))
        actual = []
        for start, length in ((0, 7600), (7600, 600), (8200, 9800)):
            out = np.zeros((length, 2), dtype=np.float32)
            player._callback(out, length, SimpleNamespace(outputBufferDacTime=start / 1000), False)
            self.assertIsNone(player.error)
            actual.append(out)
        actual = np.concatenate(actual)
        expected = np.full_like(actual, 0.065)
        for index, beat in enumerate(beats):
            tone = track.guide.tones[index == 3]
            expected[beat:beat + len(tone)] += tone[:, None]
        np.testing.assert_allclose(actual, expected, atol=1e-7)
        self.assertGreater(float(np.max(np.abs(actual[7500:9200] - 0.065))), 0.05)
        np.testing.assert_allclose(actual[10000:], 0.065, atol=1e-7)

    def test_audio_pause_removes_future_cue_and_rejects_already_buffered_start(self):
        player = AudioPlayer()
        player.track = guided_track()
        player.set_guide_cue((1000, 1500, 2000, 2500))
        player.set_guide_cue(None)
        out = np.zeros((4000, 2), dtype=np.float32)
        player._callback(out, len(out), SimpleNamespace(outputBufferDacTime=0), False)
        np.testing.assert_allclose(out, 0.065, atol=1e-7)
        self.assertEqual(player.guide_schedule_floor_ms, 4000)
        self.assertFalse(player.set_guide_cue((3500, 4000, 4500, 5000)))
        self.assertTrue(player.set_guide_cue((4000, 4500, 5000, 5500)))

    def test_base_track_is_not_modified_and_guide_has_no_fixed_loop_waveform(self):
        base = Track("easy", (Window(100, 200),), np.zeros((4000, 1)), 1000,
                     beats_ms=tuple(range(0, 4000, 500)))
        guided = with_rhythm_guide(base)
        self.assertIsNone(base.guide)
        self.assertIsNone(base.guide_samples)
        self.assertEqual(base.windows, (Window(100, 200),))
        self.assertEqual(guided.guide.beats_ms, base.beats_ms)
        self.assertIsNone(guided.guide_samples)
        self.assertEqual(tuple(map(len, guided.guide.tones)), (65, 110))
