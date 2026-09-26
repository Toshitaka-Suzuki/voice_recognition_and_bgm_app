import unittest

from chambara_talk.audio import SilenceDetector


class SilenceDetectorTests(unittest.TestCase):
    def test_finishes_after_configured_continuous_silence(self) -> None:
        detector = SilenceDetector(threshold=0.1, silence_seconds=2.0)

        self.assertEqual(detector.update(0.2, 0.1), "speech_started")
        for _ in range(19):
            self.assertNotEqual(detector.update(0.01, 0.1), "finished")
        self.assertEqual(detector.update(0.01, 0.1), "finished")

    def test_speech_resets_accumulated_silence(self) -> None:
        detector = SilenceDetector(threshold=0.1, silence_seconds=2.0)

        detector.update(0.2, 0.1)
        detector.update(0.01, 1.5)
        detector.update(0.2, 0.1)

        self.assertEqual(detector.silent_seconds, 0.0)
        self.assertEqual(detector.update(0.01, 1.0), "silence")

    def test_silence_before_speech_does_not_finish(self) -> None:
        detector = SilenceDetector(threshold=0.1, silence_seconds=2.0)
        self.assertEqual(detector.update(0.01, 5.0), "waiting")


if __name__ == "__main__":
    unittest.main()

