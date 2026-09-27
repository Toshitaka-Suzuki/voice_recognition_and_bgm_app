"""無音判定だけを切り出し、実際のマイクを使わず確認するテスト。"""

import unittest

from chambara_talk.audio import SilenceDetector


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
