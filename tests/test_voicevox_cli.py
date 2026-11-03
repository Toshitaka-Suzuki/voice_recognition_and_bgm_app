"""実機を使わず、Macの会話起動でVOICEVOX設定が選べることを確認する。"""

import io
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from chambara_app import cli


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def fake_devices():
    """設定は実ファイルから読み、外部接続とサーバー起動のみを置き換える。"""

    with ExitStack() as stack:
        stack.enter_context(patch.object(cli.sys, "platform", "darwin"))
        stack.enter_context(patch.object(cli, "load_tracks", return_value=[]))
        stack.enter_context(patch.object(cli.asyncio, "run", side_effect=lambda coroutine: coroutine.close()))
        mocks = {}
        for name in (
            "AudioRecorder", "SpeechRecognizer", "create_chat_backend", "create_synthesizer",
            "ConversationController", "AudioPlayer", "Game", "ConsoleDisplay", "IntegratedController",
        ):
            mocks[name] = stack.enter_context(patch.object(cli, name, return_value=Mock()))
        yield mocks


class VoicevoxCliTests(unittest.TestCase):
    def test_mac_live_voicevox_builds_conversation_and_runs_startup_checks(self):
        with fake_devices() as mocks:
            result = cli.main(["--settings", str(ROOT / "config/settings.voicevox.json")])
            self.assertEqual(result, 0)
            tts_settings = mocks["create_synthesizer"].call_args.args[0]
            self.assertEqual(tts_settings.provider, "voicevox")
            recognizer_settings = mocks["SpeechRecognizer"].call_args.args[0]
            self.assertEqual(recognizer_settings.device, "cpu")
            self.assertEqual(recognizer_settings.compute_type, "int8")
            conversation = mocks["ConversationController"].call_args.kwargs
            self.assertFalse(conversation["manual_input_enabled"])
            self.assertIs(conversation["synthesizer"], mocks["create_synthesizer"].return_value)
            integrated = mocks["IntegratedController"].call_args.kwargs
            self.assertEqual(integrated["conversation_mode"], "live")
            integrated["prepare"]()
            mocks["create_synthesizer"].return_value.load.assert_called_once()
            mocks["AudioRecorder"].return_value.check_device.assert_called_once()
            mocks["create_chat_backend"].return_value.check_model.assert_called_once()
            mocks["SpeechRecognizer"].return_value.load.assert_called_once()

    def test_mac_legacy_voice_is_rejected_before_opening_microphone_or_llm(self):
        with fake_devices() as mocks, patch("sys.stderr", new_callable=io.StringIO) as error:
            result = cli.main(["--settings", str(ROOT / "config/settings.json")])
            self.assertEqual(result, 1)
            self.assertIn("settings.voicevox.json", error.getvalue())
            for name in ("AudioRecorder", "SpeechRecognizer", "create_chat_backend", "create_synthesizer"):
                mocks[name].assert_not_called()
            mocks["IntegratedController"].assert_not_called()

    def test_mac_mock_does_not_read_voice_settings_or_create_audio_dependencies(self):
        with fake_devices() as mocks, patch.object(cli, "load_settings") as load_settings:
            result = cli.main(["--mock-conversation", "--silent-audio"])
            self.assertEqual(result, 0)
            load_settings.assert_not_called()
            for name in ("AudioRecorder", "SpeechRecognizer", "create_chat_backend", "create_synthesizer", "AudioPlayer"):
                mocks[name].assert_not_called()
            integrated = mocks["IntegratedController"].call_args.kwargs
            self.assertEqual(integrated["conversation_mode"], "mock_silent")
            self.assertIsNone(integrated["prepare"])


if __name__ == "__main__":
    unittest.main()
