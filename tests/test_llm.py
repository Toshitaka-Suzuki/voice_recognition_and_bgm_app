import unittest

from chambara_talk.config import LLMSettings
from chambara_talk.llm import ChatBackend
from chambara_talk.models import ConversationExample, ConversationPhase


class ChatBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = ChatBackend(
            LLMSettings(
                host="http://localhost:11434",
                model="test-model",
                production_model="gpt-oss:20b",
                timeout_seconds=1.0,
                temperature=0.4,
                max_response_chars=80,
            ),
            ("チャンバラ", "一本"),
        )

    def test_all_current_phase_examples_are_included_in_order(self) -> None:
        examples = tuple(
            ConversationExample(user=f"作戦{i}", assistant=f"返答{i}")
            for i in range(25)
        )

        messages = self.backend.build_messages(
            ConversationPhase.STRATEGY,
            "本番の発話",
            examples,
        )

        self.assertEqual(len(messages), 1 + len(examples) * 2 + 1)
        self.assertEqual(messages[1], {"role": "user", "content": "作戦0"})
        self.assertEqual(messages[-2], {"role": "assistant", "content": "返答24"})
        self.assertEqual(messages[-1], {"role": "user", "content": "本番の発話"})

    def test_response_clipping_prefers_sentence_boundary(self) -> None:
        text = "短い一文でござる。" + "あ" * 100
        clipped = self.backend._clip_to_limit(text)
        self.assertLessEqual(len(clipped), 80)
        self.assertTrue(clipped.endswith("。") or clipped.endswith("…"))


if __name__ == "__main__":
    unittest.main()

