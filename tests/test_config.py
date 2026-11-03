"""設定JSONと会話例JSONの読み込み・入力検証を確認するテスト。"""

import json
import tempfile
import unittest
from pathlib import Path

from chambara_talk.config import load_conversation_examples, load_settings, load_tts_settings
from chambara_talk.errors import ConfigurationError
from chambara_talk.models import ConversationPhase


# テストファイルから1階層上にあるプロジェクトルートを求めます。
ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    """正しい設定の読み込みと、不正な設定の拒否を検証する。"""

    def test_legacy_tts_settings_keep_windows_voice(self) -> None:
        """接続先を省略した既存設定は、声・速度・音量を変えず読み込む。"""

        tts = load_tts_settings({"voice_match": "Haruka", "rate": 175, "volume": 0.7})
        self.assertEqual(tts.provider, "pyttsx3")
        self.assertEqual(tts.voice_match, "Haruka")
        self.assertEqual(tts.rate, 175)
        self.assertEqual(tts.volume, 0.7)

    def test_voicevox_defaults_do_not_require_windows_voice_fields(self) -> None:
        tts = load_tts_settings({"provider": "voicevox", "volume": 1.0})
        self.assertEqual(tts.provider, "voicevox")
        self.assertEqual(tts.host, "http://127.0.0.1:50021")
        self.assertEqual(tts.speaker_id, 11)
        self.assertEqual(tts.speed_scale, 1.0)
        self.assertEqual(tts.pitch_scale, 0.0)
        self.assertEqual(tts.intonation_scale, 1.0)
        self.assertEqual(tts.timeout_seconds, 30.0)

    def test_voicevox_preset_uses_local_model_and_cpu_recognition(self) -> None:
        settings = load_settings(ROOT / "config/settings.voicevox.json")
        self.assertEqual(settings.tts.provider, "voicevox")
        self.assertEqual(settings.llm.provider, "ollama")
        self.assertEqual(settings.llm.model, "gemma4:12b")
        self.assertEqual(settings.speech_recognition.device, "cpu")
        self.assertEqual(settings.speech_recognition.compute_type, "int8")

    def test_voicevox_accepts_explicit_controls_and_normalizes_host(self) -> None:
        tts = load_tts_settings({
            "provider": "voicevox", "host": "http://localhost:50022/", "speaker_id": 0,
            "volume": 0.0, "speed_scale": 0.5, "pitch_scale": -0.15,
            "intonation_scale": 2.0, "timeout_seconds": 120.0,
        })
        self.assertEqual(tts.host, "http://localhost:50022")
        self.assertEqual(tts.speaker_id, 0)
        self.assertEqual(tts.volume, 0.0)
        self.assertEqual(tts.speed_scale, 0.5)
        self.assertEqual(tts.pitch_scale, -0.15)
        self.assertEqual(tts.intonation_scale, 2.0)
        self.assertEqual(tts.timeout_seconds, 120.0)

    def test_unknown_tts_provider_is_rejected(self) -> None:
        for provider in ("unknown", "", None, True, [], {}):
            with self.subTest(provider=provider), self.assertRaises(ConfigurationError):
                load_tts_settings({"provider": provider, "volume": 1.0})

    def test_voicevox_invalid_controls_are_rejected(self) -> None:
        invalid = {
            "speaker_id": (-1, True, 1.5, "11"),
            "volume": (-0.1, 1.1, True, "1", float("nan"), float("inf")),
            "speed_scale": (0.49, 2.01, True, "1", float("nan")),
            "pitch_scale": (-0.16, 0.16, float("inf")),
            "intonation_scale": (-0.01, 2.01, float("nan")),
            "timeout_seconds": (0, 121, float("inf")),
        }
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ConfigurationError):
                    load_tts_settings({"provider": "voicevox", "volume": 1.0, key: value})

    def test_voicevox_invalid_endpoints_are_rejected(self) -> None:
        for host in (
            "localhost:50021", "file:///tmp/engine", "http://", "http://localhost:0",
            "http://localhost:70000", "http://localhost:bad", "http://user:password@localhost:50021",
            "http://localhost:50021/api", "http://localhost:50021?x=1", "http://localhost:50021#x",
        ):
            with self.subTest(host=host), self.assertRaises(ConfigurationError):
                load_tts_settings({"provider": "voicevox", "volume": 1.0, "host": host})

    def test_copilot_settings_and_default_model(self) -> None:
        """CPU用設定を読み、Copilotのmodel省略時はautoになる。"""

        source = ROOT / "config/settings.copilot.json"
        settings = load_settings(source)
        self.assertEqual(settings.llm.provider, "copilot")
        self.assertEqual(settings.speech_recognition.device, "cpu")
        self.assertEqual(settings.speech_recognition.compute_type, "int8")
        payload = json.loads(source.read_text(encoding="utf-8"))
        del payload["llm"]["model"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(load_settings(path).llm.model, "auto")

    def test_unknown_provider_is_rejected(self) -> None:
        """接続先の誤記や不正な型を起動時に拒否する。"""

        payload = json.loads((ROOT / "config/settings.copilot.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for provider in ("unknown", "", None, 1, []):
                with self.subTest(provider=provider):
                    payload["llm"]["provider"] = provider
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(ConfigurationError):
                        load_settings(path)

    def test_project_configuration_loads(self) -> None:
        """プロジェクトに置かれた実際のJSONを読み込めることを確認する。"""

        settings = load_settings(ROOT / "config" / "settings.json")
        examples = load_conversation_examples(ROOT / "data" / "conversation_examples.json")

        self.assertEqual(settings.audio.silence_seconds, 2.0)
        self.assertEqual(settings.llm.model, "qwen2.5:3b")
        self.assertIn("勝利", settings.questions.feedback_human_win)
        self.assertIn("惜しい", settings.questions.feedback_robot_win)
        self.assertEqual(len(examples.phases[ConversationPhase.STRATEGY]), 2)

    def test_legacy_questions_fall_back_to_common_feedback(self) -> None:
        """勝敗別質問がない旧設定でも、従来の共通質問を使って読み込める。"""

        payload = json.loads((ROOT / "config/settings.json").read_text(encoding="utf-8"))
        del payload["questions"]["feedback_human_win"]
        del payload["questions"]["feedback_robot_win"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            questions = load_settings(path).questions
        self.assertEqual(questions.feedback_human_win, questions.feedback)
        self.assertEqual(questions.feedback_robot_win, questions.feedback)

    def test_unknown_phase_is_rejected(self) -> None:
        """仕様にないフェーズ名があるとエラーになることを確認する。"""

        payload = {
            "version": 1,
            "domain_terms": ["チャンバラ"],
            "phases": {"strategy": [], "feedback": [], "unknown": []},
        }
        # 一時フォルダはwithを抜けると自動削除され、テスト用JSONが残りません。
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "examples.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_conversation_examples(path)

    def test_empty_example_text_is_rejected(self) -> None:
        """空の発話例を起動時に拒否できることを確認する。"""

        payload = {
            "version": 1,
            "domain_terms": ["チャンバラ"],
            "phases": {
                "strategy": [{"user": "", "assistant": "返答"}],
                "feedback": [],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "examples.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_conversation_examples(path)


if __name__ == "__main__":
    # このファイルを直接実行した場合にもテストを開始します。
    unittest.main()
