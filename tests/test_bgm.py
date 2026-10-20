"""実デバイスを使わず設定、判定、DAC時刻、リソース解放を検証。"""
import json
import io
import os
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import numpy as np

from chambara_bgm.config import Track, Window, load_tracks
from chambara_bgm.engine import Game
from chambara_bgm.player import AudioPlayer


def tracks():
    return tuple(Track(name, (Window(100, 200), Window(300, 400)),
                       np.zeros((1000, 1), dtype=np.float32), 1000)
                 for name in ("easy", "normal", "hard"))


class FakePlayer:
    waiting_for_first_callback = True
    synchronizing = False

    def take_warning(self, now):
        return None
    def __init__(self):
        self.current = (1, 0)
        self.played = []
        self.stops = 0

    def play(self, track):
        self.played.append(track.id)
        self.current = (1, 0)

    def position(self, timestamp):
        return self.current

    def stop(self):
        self.stops += 1


class GameTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.player = FakePlayer()
        self.game = Game(tracks(), self.player, self.events.append)
        with patch("chambara_bgm.engine.perf_counter", return_value=0):
            self.game.start()

    def parry_at(self, ms, loop=1):
        self.player.current = loop, ms
        self.game.submit_parry(timestamp=1)
        return self.events[-1]

    def test_boundaries_and_multiple_windows(self):
        for ms, kind in [(99.9, "parry_failure"), (100, "parry_success"),
                         (199.9, "parry_failure"), (200, "parry_failure"),
                         (300, "parry_success"), (400, "parry_failure")]:
            self.assertEqual(self.parry_at(ms).kind, kind)
        self.assertEqual(self.player.played, ["easy"])

    def test_parry_returns_the_emitted_event(self):
        """統合処理が通信結果へ変換できるよう、判定イベント自体を返す。"""

        self.player.current = (1, 100)
        event = self.game.submit_parry(timestamp=1)
        self.assertIs(event, self.events[-1])
        self.assertEqual(event.kind, "parry_success")

    def test_absolute_stage_switch_is_idempotent(self):
        """絶対段階で切り替え、同じ段階の再送では曲を再開しない。"""

        with patch("chambara_bgm.engine.perf_counter", return_value=0):
            self.assertTrue(self.game.switch_stage("normal"))
            self.assertFalse(self.game.switch_stage("normal"))
            self.assertTrue(self.game.switch_stage("hard"))
        self.assertEqual(self.player.played, ["easy", "normal", "hard"])

    def test_end_exclusive_without_previous_success(self):
        self.assertEqual(self.parry_at(200).kind, "parry_failure")
        self.assertEqual(self.parry_at(199.999).kind, "parry_success")

    def test_loop_resets_success(self):
        self.assertEqual(self.parry_at(100).kind, "parry_success")
        self.assertEqual(self.parry_at(150).kind, "parry_failure")
        self.assertEqual(self.parry_at(100, loop=2).kind, "parry_success")

    def test_display_does_not_reset_success_history(self):
        self.parry_at(100)
        self.player.current = (2, 0)
        self.game.status(1)
        self.assertEqual(self.parry_at(100).kind, "parry_failure")
        self.assertEqual(self.parry_at(100, loop=2).kind, "parry_success")
        count = len(self.events)
        self.parry_at(100, loop=1)
        self.assertEqual(len(self.events), count)

    def test_life_independent_and_hard_finishes(self):
        with patch("chambara_bgm.engine.perf_counter", return_value=0):
            self.game.submit_life_decrement(timestamp=1)
            self.assertEqual(self.game.track.id, "normal")
            self.game.submit_life_decrement(timestamp=1)
            self.assertEqual(self.game.track.id, "hard")
            self.parry_at(100)
            self.assertTrue(self.game.running)
            self.game.submit_life_decrement(timestamp=1)
        self.assertFalse(self.game.running)
        self.assertEqual(self.player.stops, 1)
        self.assertEqual(self.events[-1].reason, "completed")
        self.assertEqual(self.player.played, ["easy", "normal", "hard"])

    def test_old_generation_and_transition_timestamp_discarded(self):
        generation = self.game.generation
        with patch("chambara_bgm.engine.perf_counter", return_value=5):
            self.game.submit_life_decrement(timestamp=1, generation=generation)
        event_count = len(self.events)
        self.game.submit_parry(timestamp=6, generation=generation)
        self.game.submit_life_decrement(timestamp=6, generation=generation)
        self.game.submit_life_decrement(timestamp=4)
        self.assertEqual(len(self.events), event_count)
        self.assertEqual(self.game.track.id, "normal")
        self.assertEqual(self.player.current, (1, 0))

    def test_no_signals_before_start_or_after_stop(self):
        self.game.stop()
        count = len(self.events)
        self.game.submit_life_decrement(timestamp=1)
        self.game.submit_parry(timestamp=1)
        self.assertEqual(len(self.events), count)

    def test_life_accepted_but_parry_ignored_while_waiting(self):
        self.player.current = None
        count = len(self.events)
        self.game.submit_parry(timestamp=1)
        self.assertEqual(len(self.events), count)
        with patch("chambara_bgm.engine.perf_counter", return_value=0):
            self.game.submit_life_decrement(timestamp=1)
        self.assertEqual(self.game.track.id, "normal")

    def test_status_updates_do_not_emit_parry(self):
        count = len(self.events)
        for ms in range(1000):
            self.player.current = (1, ms)
            self.game.status(1)
        self.assertEqual(len(self.events), count)

    def test_stage_start_and_switch_report_excluded_count(self):
        self.game.tracks = tuple(replace(t, excluded_window_count=i + 1)
                                 for i, t in enumerate(self.game.tracks))
        with patch("chambara_bgm.engine.perf_counter", return_value=0):
            self.game.start()
            self.assertEqual(self.events[-1].reason, "曲外の判定区間: 1件除外")
            self.game.submit_life_decrement(timestamp=1)
            self.assertEqual(self.events[-1].reason, "曲外の判定区間: 2件除外")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "settings.json"
        self.data = {"version": 1, "stages": [
            {"id": name, "audio_file": name + ".wav",
             "parry_windows": [{"start_ms": 100, "end_ms": 200}]}
            for name in ("easy", "normal", "hard")]}
        for name in ("easy", "normal", "hard"):
            with wave.open(str(self.root / (name + ".wav")), "wb") as wav:
                wav.setnchannels(2)
                wav.setsampwidth(2)
                wav.setframerate(1000)
                wav.writeframes(b"\0" * 4000)

    def load(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        return load_tracks(self.path, self.root)

    def use_version2(self):
        self.data["version"] = 2
        for stage in self.data["stages"]:
            stage["parry_offset_ms"] = 0
            stage["parry_duration_ms"] = 100
            stage["parry_windows"] = [{"start_ms": w["start_ms"]} for w in stage["parry_windows"]]

    def test_version2_matches_version1_with_same_duration(self):
        old = self.load()
        self.use_version2()
        new = self.load()
        self.assertEqual([t.windows for t in old], [t.windows for t in new])
        self.assertEqual([t.excluded_window_count for t in new], [0, 0, 0])

    def test_version2_per_stage_offsets_and_durations(self):
        self.use_version2()
        for stage, offset, duration in zip(self.data["stages"], (50, -50, 0), (200, 150, 300)):
            stage["parry_offset_ms"] = offset
            stage["parry_duration_ms"] = duration
        self.assertEqual([t.windows for t in self.load()],
                         [(Window(150, 350),), (Window(50, 200),), (Window(100, 400),)])

    def test_version2_excludes_whole_windows_at_edges(self):
        self.use_version2()
        stage = self.data["stages"][0]
        stage["parry_duration_ms"] = 200
        stage["parry_windows"] = [{"start_ms": n} for n in (0, 400, 800)]
        stage["parry_offset_ms"] = -50
        track = self.load()[0]
        self.assertEqual(track.windows, (Window(350, 550), Window(750, 950)))
        self.assertEqual(track.excluded_window_count, 1)
        stage["parry_offset_ms"] = 50
        track = self.load()[0]
        self.assertEqual(track.windows, (Window(50, 250), Window(450, 650)))
        self.assertEqual(track.excluded_window_count, 1)
        stage["parry_offset_ms"] = 0
        self.assertEqual(self.load()[0].windows[-1], Window(800, 1000))
        self.assertEqual(self.load()[0].excluded_window_count, 0)

    def test_version2_invalid_offset_and_duration(self):
        self.use_version2()
        stage = self.data["stages"][0]
        for field, values in [("parry_offset_ms", [True, 1.5, "50", None]),
                              ("parry_duration_ms", [True, 0, -1, 1.5, "200", None])]:
            original = stage[field]
            for value in values:
                with self.subTest(field=field, value=value):
                    stage[field] = value
                    with self.assertRaisesRegex(ValueError, field):
                        self.load()
            stage[field] = original

    def test_version2_rejects_end_ms_even_on_excluded_window(self):
        self.use_version2()
        stage = self.data["stages"][0]
        stage["parry_offset_ms"] = -500
        stage["parry_windows"][0]["end_ms"] = 200
        with self.assertRaisesRegex(ValueError, "end_ms"):
            self.load()

    def test_version2_rejects_all_excluded_and_overlaps(self):
        self.use_version2()
        stage = self.data["stages"][0]
        stage["parry_offset_ms"] = -1000
        with self.assertRaisesRegex(ValueError, "すべて.*除外"):
            self.load()
        stage["parry_offset_ms"] = 1000
        with self.assertRaisesRegex(ValueError, "すべて.*除外"):
            self.load()
        stage["parry_offset_ms"] = 0
        stage["parry_duration_ms"] = 200
        stage["parry_windows"] = [{"start_ms": 0}, {"start_ms": 100}]
        with self.assertRaisesRegex(ValueError, "重複"):
            self.load()

    def test_version2_missing_fields_and_bad_starts(self):
        self.use_version2()
        stage = self.data["stages"][0]
        for field in ("parry_offset_ms", "parry_duration_ms"):
            value = stage.pop(field)
            with self.assertRaises(ValueError):
                self.load()
            stage[field] = value
        for value in (-1, True, 1.5, "100"):
            stage["parry_windows"] = [{"start_ms": value}]
            with self.assertRaisesRegex(ValueError, "start_ms"):
                self.load()

    def test_load_three_tracks(self):
        result = self.load()
        self.assertEqual([t.id for t in result], ["easy", "normal", "hard"])
        self.assertEqual(result[0].samples.shape, (1000, 2))
        self.assertEqual(result[0].duration_ms, 1000)

    def test_invalid_windows(self):
        cases = [[], [{"start_ms": -1, "end_ms": 2}],
                 [{"start_ms": 10, "end_ms": 10}],
                 [{"start_ms": 200, "end_ms": 100}],
                 [{"start_ms": 0, "end_ms": 1001}],
                 [{"start_ms": True, "end_ms": 100}],
                 [{"start_ms": 1.5, "end_ms": 100}],
                 [{"start_ms": 0, "end_ms": 200}, {"start_ms": 100, "end_ms": 300}]]
        for windows in cases:
            with self.subTest(windows=windows):
                self.data["stages"][0]["parry_windows"] = windows
                with self.assertRaises(ValueError):
                    self.load()

    def test_adjacent_windows_and_sort(self):
        self.data["stages"][0]["parry_windows"] = [
            {"start_ms": 200, "end_ms": 300}, {"start_ms": 100, "end_ms": 200}]
        self.assertEqual(self.load()[0].windows[0], Window(100, 200))

    def test_missing_file_and_escape(self):
        for filename in ("missing.wav", "../escape.wav", "easy.mp3"):
            with self.subTest(filename=filename):
                self.data["stages"][0]["audio_file"] = filename
                with self.assertRaises(ValueError):
                    self.load()

    def test_bad_order_and_version(self):
        self.data["stages"].reverse()
        with self.assertRaises(ValueError):
            self.load()
        self.data["stages"].reverse()
        self.data["version"] = True
        with self.assertRaises(ValueError):
            self.load()

    def test_bad_wav_format_and_truncation(self):
        with wave.open(str(self.root / "easy.wav"), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(1)
            wav.setframerate(1000)
            wav.writeframes(b"\0" * 1000)
        with self.assertRaises(ValueError):
            self.load()
        (self.root / "easy.wav").write_bytes(b"not a wave")
        with self.assertRaises(ValueError):
            self.load()

    def write_ogg(self, stage=0, channels=1):
        import soundfile as sf

        name = self.data["stages"][stage]["id"] + ".ogg"
        self.data["stages"][stage]["audio_file"] = name
        rate = 16000
        signal = (0.2 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate)).astype(np.float32)
        samples = np.column_stack([signal] * channels)
        sf.write(str(self.root / name), samples, rate, format="OGG", subtype="VORBIS")
        return samples

    def test_ogg_mono_stereo_and_mixed_wav(self):
        for channels in (1, 2):
            with self.subTest(channels=channels):
                expected = self.write_ogg(channels=channels)
                result = self.load()
                self.assertEqual(result[0].sample_rate, 16000)
                self.assertEqual(result[0].samples.dtype, np.float32)
                self.assertEqual(result[0].samples.shape, (16000, channels))
                self.assertEqual(result[0].duration_ms, 1000)
                self.assertLess(np.mean((result[0].samples - expected) ** 2), 0.0001)
                self.assertEqual(result[1].samples.shape, (1000, 2))

    def test_all_ogg_and_duration_validation(self):
        for stage in range(3):
            self.write_ogg(stage=stage)
        self.assertEqual([t.duration_ms for t in self.load()], [1000] * 3)
        self.data["stages"][0]["parry_windows"] = [{"start_ms": 0, "end_ms": 1001}]
        with self.assertRaisesRegex(ValueError, "曲の長さ"):
            self.load()

    def test_bad_missing_empty_and_disguised_ogg(self):
        import soundfile as sf

        self.data["stages"][0]["audio_file"] = "easy.ogg"
        path = self.root / "easy.ogg"
        with self.assertRaisesRegex(ValueError, "easy.ogg"):
            self.load()
        for content in (b"", b"invalid ogg"):
            path.write_bytes(content)
            with self.assertRaisesRegex(ValueError, "easy.ogg"):
                self.load()
        # 拡張子だけOGGへ変更したWAVを受け付けない。
        path.write_bytes((self.root / "easy.wav").read_bytes())
        with self.assertRaisesRegex(ValueError, "Ogg Vorbis"):
            self.load()
        sf.write(str(path), np.empty((0, 1), dtype=np.float32), 16000,
                 format="OGG", subtype="VORBIS")
        with self.assertRaisesRegex(ValueError, "easy.ogg"):
            self.load()

    def test_unsupported_ogg_channels_and_opus(self):
        self.write_ogg(channels=3)
        with self.assertRaisesRegex(ValueError, "モノラルまたはステレオ"):
            self.load()
        import soundfile as sf

        audio = SimpleNamespace(format="OGG", subtype="OPUS")
        with patch.object(sf, "SoundFile") as opened:
            opened.return_value.__enter__.return_value = audio
            with self.assertRaisesRegex(ValueError, "Opusは未対応"):
                self.load()

    def test_missing_soundfile_message_and_wav_independence(self):
        with patch.dict("sys.modules", {"soundfile": None}):
            self.assertEqual(len(self.load()), 3)
            self.data["stages"][0]["audio_file"] = "easy.ogg"
            with self.assertRaisesRegex(ValueError, "easy.ogg: .*pip install"):
                self.load()


class FakeStream:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.time = 10.0
        self.blocksize = kwargs.get("blocksize")
        self.active = False
        self.aborted = self.closed = False

    def start(self):
        self.active = True

    def abort(self):
        self.aborted = True
        self.active = False

    def close(self):
        self.closed = True


class PlayerTests(unittest.TestCase):
    def setUp(self):
        clock = patch("chambara_bgm.player.perf_counter", return_value=100)
        clock.start()
        self.addCleanup(clock.stop)
        self.player = AudioPlayer(FakeStream)
        self.addCleanup(self.player.stop)
        self.track = Track("easy", (), np.arange(10, dtype=np.float32).reshape(-1, 1), 1000)
        self.player.play(self.track)

    def callback(self, frames=16, status=""):
        output = np.empty((frames, 1), dtype=np.float32)
        with patch("chambara_bgm.player.perf_counter", return_value=100):
            self.player._callback(output, frames,
                                  SimpleNamespace(outputBufferDacTime=10.05, currentTime=10), status)
        return output

    def test_sample_loop_and_dac_latency(self):
        output = self.callback()
        np.testing.assert_array_equal(output[:, 0], np.tile(np.arange(10), 2)[:16])
        self.assertIsNone(self.player.position(100.04))
        loop, ms = self.player.position(100.055)
        self.assertEqual(loop, 1)
        self.assertAlmostEqual(ms, 5)
        loop, ms = self.player.position(100.062)
        self.assertEqual(loop, 2)
        self.assertAlmostEqual(ms, 2)

    def test_restart_clears_clock_and_closes_old_stream(self):
        self.callback()
        old = self.player.stream
        self.player.play(self.track)
        self.assertTrue(old.aborted and old.closed)
        self.assertIsNone(self.player.position(100.055))
        self.assertEqual(self.player.frames_sent, 0)
        self.assertTrue(self.player.waiting_for_first_callback)

    def test_output_error_stale_clock_and_inactive(self):
        self.callback()
        with patch("chambara_bgm.player.perf_counter", return_value=101):
            with self.assertRaisesRegex(RuntimeError, "更新停止時間.*ブロック長.*音声デバイス"):
                self.player.position(101)
        self.callback(status="underflow")
        with self.assertRaisesRegex(RuntimeError, "underflow"):
            self.player.position(100.055)
        self.player.error = None
        self.player.stream.active = False
        with self.assertRaises(RuntimeError):
            self.player.position(100.055)

    def test_start_failure_closes_device(self):
        stream = FakeStream(callback=None)
        stream.start = lambda: (_ for _ in ()).throw(RuntimeError("start failed"))
        player = AudioPlayer(lambda **kwargs: stream)
        with self.assertRaises(RuntimeError):
            player.play(self.track)
        self.assertTrue(stream.closed)
        self.assertIsNone(player.stream)

    def test_abort_failure_still_closes(self):
        stream = self.player.stream
        stream.abort = lambda: (_ for _ in ()).throw(RuntimeError("abort failed"))
        with self.assertRaises(RuntimeError):
            self.player.stop()
        self.assertTrue(stream.closed)

    def test_no_callback_timeout(self):
        for now in (100.5, 104.999):
            with patch("chambara_bgm.player.perf_counter", return_value=now):
                self.assertIsNone(self.player.position(now))
        with patch("chambara_bgm.player.perf_counter", return_value=105):
            with self.assertRaisesRegex(RuntimeError, "初回音声コールバック.*未受信"):
                self.player.position(105)

    def test_slow_device_preparation_does_not_consume_initial_timeout(self):
        def factory(**kwargs):
            self.clock_value = 100.6
            return FakeStream(**kwargs)
        self.clock_value = 100
        with patch("chambara_bgm.player.perf_counter", side_effect=lambda: self.clock_value):
            player = AudioPlayer(factory)
            self.addCleanup(player.stop)
            player.play(self.track)
            self.assertEqual(player.started_at, 100.6)
            self.assertIsNone(player.position(100.6))

    def test_callback_during_start_preserved(self):
        def factory(**kwargs):
            stream = FakeStream(**kwargs)
            def start():
                stream.active = True
                stream.callback(np.empty((16, 1), dtype=np.float32), 16,
                                SimpleNamespace(outputBufferDacTime=10.05), "")
            stream.start = start
            return stream
        player = AudioPlayer(factory)
        self.addCleanup(player.stop)
        player.play(self.track)
        self.assertFalse(player.waiting_for_first_callback)
        self.assertEqual(len(player.anchors), 1)
        self.assertIsNotNone(player.position(100.055))

    def test_late_initial_callback_then_running_timeout(self):
        with patch("chambara_bgm.player.perf_counter", return_value=100.8):
            self.player.stream.time = 10.8
            self.player._callback(np.empty((16, 1), dtype=np.float32), 16,
                                  SimpleNamespace(outputBufferDacTime=10.8), "")
            self.assertIsNotNone(self.player.position(100.8))
        with patch("chambara_bgm.player.perf_counter", return_value=101.3):
            with self.assertRaisesRegex(RuntimeError, "更新が停止.*受信済み"):
                self.player.position(101.3)

    def test_error_during_initial_wait_is_immediate(self):
        self.player.error = "音声出力異常: underflow"
        with self.assertRaisesRegex(RuntimeError, "underflow.*初回受信: 未受信"):
            self.player.position(100)
        self.player.error = None
        self.player.stream.active = False
        with self.assertRaisesRegex(RuntimeError, "ストリームが停止"):
            self.player.position(100)

    def test_grace_boundary_and_unknown_position(self):
        self.callback()
        self.assertEqual(self.player.stream.blocksize, 0)
        self.assertIsNotNone(self.player.position(100.066))
        self.assertIsNotNone(self.player.position(100.086))
        self.assertIsNone(self.player.position(100.0861))
        self.assertIsNone(self.player.position(100.2))

    def test_delayed_callback_and_variable_blocks(self):
        self.callback()
        output = np.empty((7, 1), dtype=np.float32)
        with patch("chambara_bgm.player.perf_counter", return_value=100.08):
            self.player._callback(output, 7,
                                  SimpleNamespace(outputBufferDacTime=10.066, currentTime=10.016), "")
        # Python側で遅れてcallbackが実行されてもDAC時刻はずれない。
        loop, ms = self.player.position(100.069)
        self.assertEqual(loop, 2)
        self.assertAlmostEqual(ms, 9)
        np.testing.assert_array_equal(output[:, 0], [6, 7, 8, 9, 0, 1, 2])

    def test_acquired_input_time_is_preserved(self):
        self.callback()
        self.player.stream.time = 10.08
        with patch("chambara_bgm.player.perf_counter", return_value=100.08):
            loop, ms = self.player.position(100.055)
        self.assertEqual(loop, 1)
        self.assertAlmostEqual(ms, 5)

    def test_recovery_after_short_clock_gap(self):
        self.callback()
        self.assertIsNone(self.player.position(100.09))
        output = np.empty((8, 1), dtype=np.float32)
        self.player._callback(output, 8,
                              SimpleNamespace(outputBufferDacTime=10.09, currentTime=10.08), "")
        self.assertIsNotNone(self.player.position(100.091))

    @staticmethod
    def underflow_flags():
        import sounddevice as sd
        flags = sd.CallbackFlags()
        flags.output_underflow = True
        return flags

    def test_underflow_fills_audio_and_recovers_on_next_dac_block(self):
        self.callback()
        flags = self.underflow_flags()
        output = self.callback(status=flags)
        np.testing.assert_array_equal(output[:, 0], (np.arange(16) + 16) % 10)
        self.assertIsNone(self.player.error)
        self.assertEqual(self.player.frames_sent, 32)
        self.assertEqual(self.player.underflow_count, 1)
        self.assertTrue(self.player.synchronizing)
        self.assertIsNone(self.player.position(100.055))
        self.player._callback(np.empty((8, 1), dtype=np.float32), 8,
                              SimpleNamespace(outputBufferDacTime=10.2), "")
        self.assertIsNone(self.player.position(100.199))
        self.assertTrue(self.player.synchronizing)
        loop, ms = self.player.position(100.2)
        self.assertEqual(loop, 4)
        self.assertAlmostEqual(ms, 2)
        self.assertFalse(self.player.synchronizing)

    def test_repeated_underflows_throttle_warnings_and_reset_on_switch(self):
        flags = self.underflow_flags()
        with patch("builtins.print") as printed:
            self.callback(status=flags)
            self.assertIn("1回", self.player.take_warning(100))
            self.callback(status=flags)
            self.assertIsNone(self.player.take_warning(100.9))
            self.assertIn("2回", self.player.take_warning(101))
            self.assertIsNone(self.player.take_warning(102))
            printed.assert_not_called()
        self.assertEqual(self.player.frames_sent, 32)
        self.assertIsNone(self.player.position(100.055))
        self.player.play(self.track)
        self.assertEqual(self.player.underflow_count, 0)
        self.assertFalse(self.player.synchronizing)
        self.assertIsNone(self.player.take_warning(103))

    def test_mixed_and_other_flags_remain_fatal(self):
        for mixed in (False, True):
            flags = self.underflow_flags()
            flags.output_underflow = mixed
            flags.output_overflow = True
            self.player.play(self.track)
            self.callback(status=flags)
            with self.assertRaisesRegex(RuntimeError, "output overflow"):
                self.player.position(100.055)
            self.assertEqual(self.player.frames_sent, 0)

    def test_recovery_does_not_repeat_successful_window(self):
        track = tracks()[0]
        self.player.play(track)
        events = []
        game = Game((track,) * 3, self.player, events.append)
        with patch("chambara_bgm.engine.perf_counter", return_value=100):
            game.start()
        self.player._callback(np.empty((150, 1), dtype=np.float32), 150,
                              SimpleNamespace(outputBufferDacTime=10), "")
        game.submit_parry(100.12)
        self.assertEqual(events[-1].kind, "parry_success")
        self.player._callback(np.empty((20, 1), dtype=np.float32), 20,
                              SimpleNamespace(outputBufferDacTime=10.15), self.underflow_flags())
        count = len(events)
        game.submit_parry(100.16)
        self.assertEqual(len(events), count)
        self.player._callback(np.empty((20, 1), dtype=np.float32), 20,
                              SimpleNamespace(outputBufferDacTime=10.17), "")
        game.submit_parry(100.18)
        self.assertEqual(events[-1].kind, "parry_failure")

    def test_underflow_does_not_disable_callback_timeout(self):
        self.callback(status=self.underflow_flags())
        with patch("chambara_bgm.player.perf_counter", return_value=100.5):
            with self.assertRaisesRegex(RuntimeError, "更新が停止.*アンダーフロー回数: 1"):
                self.player.position(100.5)

    def test_callback_exception_remains_fatal(self):
        self.player.track = Track("easy", (), np.empty((0, 1), dtype=np.float32), 1000)
        output = self.callback()
        np.testing.assert_array_equal(output, np.zeros((16, 1)))
        with self.assertRaisesRegex(RuntimeError, "音声再生に失敗"):
            self.player.position(100.055)


class KeyboardTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windowsのコンソール構造体の配置を実OSで確認する。")
    def test_windows_console_record_layout(self):
        import ctypes
        from chambara_bgm.keyboard import _Record

        self.assertEqual(ctypes.sizeof(_Record), 20)

    def test_repeat_release_and_discard(self):
        from chambara_bgm.keyboard import Keyboard

        keyboard = Keyboard.__new__(Keyboard)
        keyboard.held = set()
        keyboard.handle = None
        pending = []

        def count_events(handle, count):
            count._obj.value = len(pending)
            return 1

        def read_events(handle, records, count, received):
            received._obj.value = len(pending)
            for i, (vk, down, repeat) in enumerate(pending):
                records[i].kind = 1
                records[i].payload.key.vk = vk
                records[i].payload.key.down = down
                records[i].payload.key.repeat = repeat
            pending.clear()
            return 1

        keyboard.api = SimpleNamespace(GetNumberOfConsoleInputEvents=count_events,
                                       ReadConsoleInputW=read_events)
        pending.extend([(76, True, 1), (76, True, 20), (76, True, 1)])
        self.assertEqual([a for a, _ in keyboard.read()], ["life"])
        pending.extend([(76, True, 1)])
        keyboard.discard()
        pending.extend([(76, True, 1)])
        self.assertEqual(keyboard.read(), [])
        pending.extend([(76, False, 1), (76, True, 1), (32, True, 1)])
        self.assertEqual([a for a, _ in keyboard.read()], ["life", "parry"])


class CliTests(unittest.TestCase):
    def run_cli(self, batches, player):
        from chambara_bgm.cli import main
        keyboard = SimpleNamespace(read=lambda: batches.pop(0), discard=lambda: None,
                                   close=lambda: None)
        with patch("sys.argv", ["chambara-bgm"]), \
             patch("chambara_bgm.cli.os", SimpleNamespace(name="nt")), \
             patch("chambara_bgm.keyboard.Keyboard", return_value=keyboard), \
             patch("chambara_bgm.cli.load_tracks", return_value=tracks()), \
             patch("chambara_bgm.cli.AudioPlayer", return_value=player), \
             patch("chambara_bgm.cli.sleep"), \
             patch("chambara_bgm.cli.perf_counter", return_value=1), \
             patch("chambara_bgm.engine.perf_counter", return_value=0), \
             patch("sys.stdout", new_callable=io.StringIO), \
             patch.object(keyboard, "close") as close:
            result = main()
        close.assert_called_once()
        return result

    def test_final_life_exits_and_discards_old_batch(self):
        player = FakePlayer()
        batches = [[("start", 1)], [("life", 1), ("life", 1), ("parry", 1)],
                   [("life", 1)], [("life", 1)]]
        self.assertEqual(self.run_cli(batches, player), 0)
        self.assertEqual(player.played, ["easy", "normal", "hard"])
        self.assertGreaterEqual(player.stops, 1)

    def test_error_and_quit_release_player(self):
        for failing in (False, True):
            with self.subTest(failing=failing):
                player = FakePlayer()
                if failing:
                    player.position = lambda t: (_ for _ in ()).throw(RuntimeError("device lost"))
                batches = [[("start", 1)], [("quit", 1)]]
                self.assertEqual(self.run_cli(batches, player), 1 if failing else 0)
                self.assertEqual(player.stops, 1)


if __name__ == "__main__":
    unittest.main()
