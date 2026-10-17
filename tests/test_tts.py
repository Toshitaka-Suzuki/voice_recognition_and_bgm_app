"""音声エンジンをモックへ差し替え、読み上げ制御を確認するテスト。"""

import unittest
from threading import Event
from unittest.mock import Mock, patch

from chambara_talk.config import TTSSettings
from chambara_talk.errors import TTSError
from chambara_talk.tts import SpeechSynthesizer


def settings() -> TTSSettings:
    """各テストで使用する共通の音声設定を返す。"""

    return TTSSettings(voice_match="Haruka", rate=175, volume=1.0)


class SpeechSynthesizerTests(unittest.TestCase):
    """実際に音を出さず、pyttsx3へ正しく命令することを検証する。"""

    def test_four_consecutive_utterances_are_processed(self) -> None:
        """質問とコメントの合計4回がすべて読み上げ処理されることを確認する。"""

        # Mockは呼ばれたメソッド、引数、回数を記録する偽物のエンジンです。
        outputs = []
        engine = Mock()
        synthesizer = SpeechSynthesizer(settings(), output=outputs.append)
        synthesizer._engine = engine

        utterances = ["作戦質問", "作戦コメント", "感想質問", "感想コメント"]
        for utterance in utterances:
            synthesizer.speak(utterance)

        self.assertEqual(
            [call.args[0] for call in engine.say.call_args_list],
            utterances,
        )
        self.assertEqual(engine.runAndWait.call_count, 4)
        self.assertEqual(outputs.count("音声読み上げ完了"), 4)

    @patch("chambara_talk.tts.metadata.version", return_value="2.99")
    def test_unsupported_pyttsx3_version_is_rejected(self, version: Mock) -> None:
        """問題のあるpyttsx3 2.99を起動時に拒否することを確認する。"""

        del version
        synthesizer = SpeechSynthesizer(settings())

        with self.assertRaisesRegex(TTSError, "pyttsx3==2.98"):
            synthesizer.load()

    def test_engine_error_is_reported_as_tts_error(self) -> None:
        """音声エンジンの失敗が利用者向けTTSErrorになることを確認する。"""

        engine = Mock()
        engine.runAndWait.side_effect = RuntimeError("SAPI failure")
        synthesizer = SpeechSynthesizer(settings(), output=lambda message: None)
        synthesizer._engine = engine

        with self.assertRaisesRegex(TTSError, "SAPI failure"):
            synthesizer.speak("読み上げ")

    @patch("chambara_talk.tts.time.sleep")
    def test_cancelable_speech_stops_current_utterance(self, sleep: Mock) -> None:
        """戦闘開始のキャンセルを同じSAPIループ内で検出して停止する。"""

        cancel = Event()
        engine = Mock()
        engine.isBusy.side_effect = [True, True]
        engine.iterate.side_effect = cancel.set
        synthesizer = SpeechSynthesizer(settings(), output=lambda message: None)
        synthesizer._engine = engine

        self.assertFalse(synthesizer.speak("開始前質問", cancel_event=cancel))
        engine.startLoop.assert_called_once_with(False)
        engine.stop.assert_called_once()
        engine.endLoop.assert_called_once()
        sleep.assert_called_once()


if __name__ == "__main__":
    # このファイルを直接実行した場合にもテストを開始します。
    unittest.main()
