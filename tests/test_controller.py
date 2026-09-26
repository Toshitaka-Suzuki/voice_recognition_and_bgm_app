import unittest

from chambara_talk.controller import ConversationController, RecognitionConfirmation
from chambara_talk.models import (
    AudioData,
    ConfirmationResult,
    ConversationExample,
    ConversationExamples,
    ConversationPhase,
    ConversationState,
    TranscriptionResult,
)


class FakeRecorder:
    def __init__(self) -> None:
        self.calls = 0

    def record_until_silence(self) -> AudioData:
        self.calls += 1
        return AudioData(samples=[0.1], sample_rate=16000, speech_detected=True, stop_reason="silence")


class FakeRecognizer:
    def __init__(self, texts: list[str]) -> None:
        self.texts = iter(texts)

    def transcribe(self, audio: AudioData) -> TranscriptionResult:
        del audio
        return TranscriptionResult(next(self.texts), 0.1, "fake-whisper")


class FakeChatBackend:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, phase, transcript, examples) -> str:  # noqa: ANN001
        self.calls.append((phase, transcript, tuple(examples)))
        return "よい心意気でござる！"


class FakeSynthesizer:
    def __init__(self) -> None:
        self.spoken = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)


def make_examples() -> ConversationExamples:
    return ConversationExamples(
        version=1,
        domain_terms=("チャンバラ",),
        phases={
            ConversationPhase.STRATEGY: tuple(
                ConversationExample(f"作戦{i}", f"応援{i}") for i in range(12)
            ),
            ConversationPhase.FEEDBACK: tuple(
                ConversationExample(f"感想{i}", f"返答{i}") for i in range(9)
            ),
        },
    )


class ControllerTests(unittest.TestCase):
    def build_controller(self, inputs: list[str], texts: list[str]):
        iterator = iter(inputs)
        outputs = []
        chat = FakeChatBackend()
        synthesizer = FakeSynthesizer()
        controller = ConversationController(
            recorder=FakeRecorder(),
            recognizer=FakeRecognizer(texts),
            chat_backend=chat,
            synthesizer=synthesizer,
            examples=make_examples(),
            strategy_question="作戦を教えてください",
            feedback_question="感想を教えてください",
            input_function=lambda prompt: next(iterator),
            output=outputs.append,
        )
        return controller, chat, synthesizer, outputs

    def test_q_at_recognition_confirmation_does_not_call_llm(self) -> None:
        controller, chat, synthesizer, outputs = self.build_controller(
            ["", "q"], ["認識した発話"]
        )

        completed = controller.run()

        self.assertFalse(completed)
        self.assertEqual(chat.calls, [])
        self.assertEqual(synthesizer.spoken, ["作戦を教えてください"])
        self.assertIn("認識した発話", outputs)

    def test_two_phases_use_all_examples_without_sharing_history(self) -> None:
        controller, chat, synthesizer, outputs = self.build_controller(
            ["", "", "", "", ""],
            ["作戦の発話", "感想の発話"],
        )

        completed = controller.run()

        self.assertTrue(completed)
        self.assertEqual(controller.state, ConversationState.COMPLETE)
        self.assertEqual(len(chat.calls), 2)
        self.assertEqual(chat.calls[0][0], ConversationPhase.STRATEGY)
        self.assertEqual(chat.calls[0][1], "作戦の発話")
        self.assertEqual(len(chat.calls[0][2]), 12)
        self.assertEqual(chat.calls[1][0], ConversationPhase.FEEDBACK)
        self.assertEqual(chat.calls[1][1], "感想の発話")
        self.assertEqual(len(chat.calls[1][2]), 9)
        self.assertEqual(
            synthesizer.spoken,
            [
                "作戦を教えてください",
                "よい心意気でござる！",
                "感想を教えてください",
                "よい心意気でござる！",
            ],
        )
        self.assertTrue(any("12件" in line for line in outputs))
        self.assertTrue(any("9件" in line for line in outputs))

    def test_confirmation_repeats_after_unknown_key(self) -> None:
        inputs = iter(["x", ""])
        outputs = []
        confirmation = RecognitionConfirmation(lambda prompt: next(inputs), outputs.append)

        result = confirmation.confirm("認識結果")

        self.assertEqual(result, ConfirmationResult.CONFIRMED)
        self.assertIn("Enterまたはqを入力してください。", outputs)


if __name__ == "__main__":
    unittest.main()
