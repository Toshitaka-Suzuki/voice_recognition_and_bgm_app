"""無音判定だけを切り出し、実際のマイクを使わず確認するテスト。"""

import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chambara_talk.audio import AudioRecorder, SilenceDetector
from chambara_talk.config import load_settings
from chambara_talk.errors import ConversationCancelled


class RecorderReadyTests(unittest.TestCase):
    """マイクを開かず、環境音測定後にだけ準備完了になることを確認する。"""

    def setUp(self):
        import numpy as np

        settings = load_settings(Path(__file__).resolve().parents[1] / "config/settings.json").audio
        self.recorder = AudioRecorder(replace(
            settings, sample_rate=100, block_seconds=0.1, calibration_seconds=0.2,
            silence_seconds=0.2,
        ), output=lambda _: None)
        self.entered = False
        self.closed = False

        def input_stream(**kwargs):
            owner = self

            class Stream:
                def __enter__(self):
                    owner.entered = True
                    for level in (0.0, 0.0, 0.5, 0.0, 0.0):
                        kwargs["callback"](np.full((10, 1), level), 10, None, False)
                    return self

                def __exit__(self, *_args):
                    owner.closed = True

            return Stream()

        self.audio_module = SimpleNamespace(InputStream=input_stream)

    def test_ready_callback_follows_calibration_with_open_stream(self):
        ready = []
        with patch.dict("sys.modules", sounddevice=self.audio_module), patch.object(self.recorder, "_rms", wraps=self.recorder._rms) as rms:
            def on_ready():
                self.assertTrue(self.entered)
                self.assertFalse(self.closed)
                self.assertEqual(rms.call_count, 2)
                ready.append(True)

            result = self.recorder.record_until_silence(on_ready=on_ready)
        self.assertTrue(result.speech_detected)
        self.assertEqual(ready, [True])
        self.assertTrue(self.closed)

    def test_cancelled_calibration_never_announces_ready(self):
        cancel = Event()
        cancel.set()
        ready = Mock()
        with patch.dict("sys.modules", sounddevice=self.audio_module), self.assertRaises(ConversationCancelled):
            self.recorder.record_until_silence(cancel_event=cancel, on_ready=ready)
        ready.assert_not_called()
        self.assertTrue(self.closed)

    def test_ready_notification_failure_does_not_break_recording(self):
        with patch.dict("sys.modules", sounddevice=self.audio_module):
            result = self.recorder.record_until_silence(on_ready=Mock(side_effect=RuntimeError("display failed")))
        self.assertTrue(result.speech_detected)
        self.assertTrue(self.closed)


class SilenceDetectorTests(unittest.TestCase):
    """RMS音量の並びに対する``SilenceDetector``の状態変化を検証する。"""

    def test_finishes_after_configured_continuous_silence(self) -> None:
        """発話後に2秒の無音が連続すると終了になることを確認する。"""

        detector = SilenceDetector(threshold=0.1, silence_seconds=2.0)

        self.assertEqual(detector.update(0.2, 0.1), "speech_started")
        for _ in range(19):
            self.assertNotEqual(detector.update(0.01, 0.1), "finished")
        self.assertEqual(detector.update(0.01, 0.1), "finished")

    def test_speech_resets_accumulated_silence(self) -> None:
        """無音の途中で再び話すと、無音時間が0へ戻ることを確認する。"""

        detector = SilenceDetector(threshold=0.1, silence_seconds=2.0)

        detector.update(0.2, 0.1)
        detector.update(0.01, 1.5)
        detector.update(0.2, 0.1)

        self.assertEqual(detector.silent_seconds, 0.0)
        self.assertEqual(detector.update(0.01, 1.0), "silence")

    def test_silence_before_speech_does_not_finish(self) -> None:
        """発話前の無音だけでは録音終了にならないことを確認する。"""

        detector = SilenceDetector(threshold=0.1, silence_seconds=2.0)
        self.assertEqual(detector.update(0.01, 5.0), "waiting")


if __name__ == "__main__":
    # このファイルを直接実行した場合にもテストを開始します。
    unittest.main()
