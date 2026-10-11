"""会話アプリの終了経路すべてでLLMが解放されることを確認する。"""

import unittest
from unittest.mock import Mock, patch

from chambara_talk.cli import main
from chambara_talk.errors import LLMError


class TalkCliTests(unittest.TestCase):
    """実機を使わず起動失敗、正常終了、q、割り込みの後片付けを検証する。"""

    def test_all_exit_paths_close_backend(self) -> None:
        """finallyが起動チェック失敗と会話中の終了の両方を扱う。"""

        for reason in ("complete", "quit", "interrupt", "startup_error"):
            with self.subTest(reason=reason), patch("chambara_talk.cli.AudioRecorder"), patch("chambara_talk.cli.SpeechRecognizer"), patch("chambara_talk.cli.SpeechSynthesizer"), patch("chambara_talk.cli.create_chat_backend") as factory, patch("chambara_talk.cli.ConversationController") as controller, patch("builtins.print"):
                backend = Mock()
                factory.return_value = backend
                if reason == "startup_error":
                    backend.check_model.side_effect = LLMError("起動失敗")
                elif reason == "interrupt":
                    controller.return_value.run.side_effect = KeyboardInterrupt()
                else:
                    controller.return_value.run.return_value = reason == "complete"
                code = main([])
                self.assertEqual(code, 1 if reason == "startup_error" else 130 if reason == "interrupt" else 0)
                backend.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
