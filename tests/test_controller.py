"""会話全体の進行を、外部機器を使わない偽物の部品で確認するテスト。"""

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
    """呼ばれるたびに、発話済みの固定音声データを返す録音機の偽物。"""

    def __init__(self) -> None:
        """録音メソッドが呼ばれた回数を0で初期化する。"""

        self.calls = 0

    def record_until_silence(self) -> AudioData:
        """録音回数を増やし、テスト用の音声データを返す。"""

        self.calls += 1
        return AudioData(samples=[0.1], sample_rate=16000, speech_detected=True, stop_reason="silence")


class FakeRecognizer:
    """あらかじめ用意した文字列を順番に返す音声認識器の偽物。"""

    def __init__(self, texts: list[str]) -> None:
        """認識結果のリストを、順番に取り出せるiteratorへ変換する。"""

        self.texts = iter(texts)

    def transcribe(self, audio: AudioData) -> TranscriptionResult:
        """音声の内容によらず、次のテスト用認識結果を返す。"""

        del audio
        return TranscriptionResult(next(self.texts), 0.1, "fake-whisper")


class FakeChatBackend:
    """入力内容を記録し、固定コメントを返すLLMの偽物。"""

    def __init__(self) -> None:
        """LLM呼び出し内容を記録する空リストを用意する。"""

        self.calls = []

    def generate(self, phase, transcript, examples) -> str:  # noqa: ANN001
        """受け取った引数を保存し、固定の侍風コメントを返す。"""

        self.calls.append((phase, transcript, tuple(examples)))
        return "よい心意気でござる！"


class FakeSynthesizer:
    """音声を出さず、読み上げ対象の文章だけを記録するTTSの偽物。"""

    def __init__(self) -> None:
        """読み上げ文章を記録する空リストを用意する。"""

        self.spoken = []

    def speak(self, text: str) -> None:
        """文章を読み上げる代わりにリストへ追加する。"""

        self.spoken.append(text)


def make_examples() -> ConversationExamples:
    """件数制限の有無を調べるため、多めの会話例を作って返す。"""

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
    """認識確認、2フェーズの進行、入力の再試行を検証する。"""

    def build_controller(self, inputs: list[str], texts: list[str]):
        """指定したキー入力と認識文で動くテスト用Controller一式を返す。"""

        # input()の代わりに、リストの値を順番に返すlambdaを使います。
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
        """認識確認でqを押すと、LLMを呼ばず終了することを確認する。"""

        controller, chat, synthesizer, outputs = self.build_controller(
            ["", "q"], ["認識した発話"]
        )

        completed = controller.run()

        self.assertFalse(completed)
        self.assertEqual(chat.calls, [])
        self.assertEqual(synthesizer.spoken, ["作戦を教えてください"])
        self.assertIn("認識した発話", outputs)

    def test_two_phases_use_all_examples_without_sharing_history(self) -> None:
        """各フェーズの全例と4回の読み上げが正しい順序になることを確認する。"""

        controller, chat, synthesizer, outputs = self.build_controller(
            ["", "", "", "", ""],
            ["作戦の発話", "感想の発話"],
        )

        completed = controller.run()

        self.assertTrue(completed)
        self.assertEqual(controller.state, ConversationState.COMPLETE)
        # 2回の呼び出しを別々に調べ、例や発話が混ざっていないことを保証します。
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
        """Enterとq以外の入力では確認画面に留まることを確認する。"""

        inputs = iter(["x", ""])
        outputs = []
        confirmation = RecognitionConfirmation(lambda prompt: next(inputs), outputs.append)

        result = confirmation.confirm("認識結果")

        self.assertEqual(result, ConfirmationResult.CONFIRMED)
        self.assertIn("Enterまたはqを入力してください。", outputs)


if __name__ == "__main__":
    # このファイルを直接実行した場合にもテストを開始します。
    unittest.main()
