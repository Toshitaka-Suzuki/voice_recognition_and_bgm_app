import unittest
from unittest.mock import Mock, patch

from chambara_talk.config import TTSSettings
from chambara_talk.errors import TTSError
from chambara_talk.tts import SpeechSynthesizer


def settings() -> TTSSettings:
    return TTSSettings(voice_match="Haruka", rate=175, volume=1.0)


class SpeechSynthesizerTests(unittest.TestCase):
    def test_four_consecutive_utterances_are_processed(self) -> None:
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
        del version
        synthesizer = SpeechSynthesizer(settings())

        with self.assertRaisesRegex(TTSError, "pyttsx3==2.98"):
            synthesizer.load()

    def test_engine_error_is_reported_as_tts_error(self) -> None:
        engine = Mock()
        engine.runAndWait.side_effect = RuntimeError("SAPI failure")
        synthesizer = SpeechSynthesizer(settings(), output=lambda message: None)
        synthesizer._engine = engine

        with self.assertRaisesRegex(TTSError, "SAPI failure"):
            synthesizer.speak("読み上げ")


if __name__ == "__main__":
    unittest.main()
