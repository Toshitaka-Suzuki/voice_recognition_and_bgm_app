"""設定JSONと会話例JSONの読み込み・入力検証を確認するテスト。"""

import json
import tempfile
import unittest
from pathlib import Path

from chambara_talk.config import load_conversation_examples, load_settings
from chambara_talk.errors import ConfigurationError
from chambara_talk.models import ConversationPhase


# テストファイルから1階層上にあるプロジェクトルートを求めます。
ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    """正しい設定の読み込みと、不正な設定の拒否を検証する。"""

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
        self.assertEqual(len(examples.phases[ConversationPhase.STRATEGY]), 2)

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
