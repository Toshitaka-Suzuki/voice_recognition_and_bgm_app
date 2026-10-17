"""会話全体の進行を、外部機器を使わない偽物の部品で確認するテスト。"""

import unittest
from threading import Event
from unittest.mock import Mock, patch

from chambara_talk.controller import ConversationController, RecognitionConfirmation
from chambara_talk.errors import AudioError, SpeechRecognitionError
from chambara_talk.models import (
    AudioData,
    ConfirmationResult,
    ConversationExample,
    ConversationExamples,
    ConversationPhase,
    ConversationState,
    FeedbackOutcome,
    PhaseRunResult,
    TranscriptionResult,
)


class FakeRecorder:
    """呼ばれるたびに、発話済みの固定音声データを返す録音機の偽物。"""

    def __init__(self) -> None:
        """録音メソッドが呼ばれた回数を0で初期化する。"""

        self.calls = 0

    def record_until_silence(self, *, cancel_event=None) -> AudioData:
        """録音回数を増やし、テスト用の音声データを返す。"""

        if cancel_event is not None and cancel_event.is_set():
            from chambara_talk.errors import ConversationCancelled

            raise ConversationCancelled("cancelled")
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

    def generate(self, phase, transcript, examples, *, outcome=None) -> str:  # noqa: ANN001
        """受け取った引数を保存し、固定の侍風コメントを返す。"""

        self.calls.append((phase, transcript, tuple(examples), outcome))
        return "よい心意気でござる！"


class FakeSynthesizer:
    """音声を出さず、読み上げ対象の文章だけを記録するTTSの偽物。"""

    def __init__(self) -> None:
        """読み上げ文章を記録する空リストを用意する。"""

        self.spoken = []

    def speak(self, text: str, *, cancel_event=None) -> bool:
        """文章を読み上げる代わりにリストへ追加する。"""

        if cancel_event is not None and cancel_event.is_set():
            return False
        self.spoken.append(text)
        return True


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

    def test_feedback_phase_uses_outcome_question_and_context(self) -> None:
        """人間勝利時は専用質問と勝敗情報をLLMへ渡す。"""

        inputs = iter(["", ""])
        chat = FakeChatBackend()
        synthesizer = FakeSynthesizer()
        controller = ConversationController(
            recorder=FakeRecorder(), recognizer=FakeRecognizer(["楽しかった"]),
            chat_backend=chat, synthesizer=synthesizer, examples=make_examples(),
            strategy_question="作戦", feedback_question="共通感想",
            feedback_human_win_question="勝利の感想", feedback_robot_win_question="敗北の感想",
            input_function=lambda prompt: next(inputs), output=lambda text: None,
        )

        result = controller.run_phase(
            ConversationPhase.FEEDBACK, outcome=FeedbackOutcome.HUMAN_WIN
        )

        self.assertEqual(result, PhaseRunResult.COMPLETED)
        self.assertEqual(synthesizer.spoken[0], "勝利の感想")
        self.assertEqual(chat.calls[0][3], FeedbackOutcome.HUMAN_WIN)

    def test_cancelled_phase_never_calls_llm(self) -> None:
        """開始前に外部キャンセル済みなら会話部品を動かさない。"""

        controller, chat, synthesizer, _outputs = self.build_controller([], [])
        cancel = Event()
        cancel.set()

        result = controller.run_phase(ConversationPhase.STRATEGY, cancel_event=cancel)

        self.assertEqual(result, PhaseRunResult.CANCELLED)
        self.assertEqual(chat.calls, [])
        self.assertEqual(synthesizer.spoken, [])

    def test_confirmation_repeats_after_unknown_key(self) -> None:
        """Enterとq以外の入力では確認画面に留まることを確認する。"""

        inputs = iter(["x", ""])
        outputs = []
        confirmation = RecognitionConfirmation(lambda prompt: next(inputs), outputs.append)

        result = confirmation.confirm("認識結果")

        self.assertEqual(result, ConfirmationResult.CONFIRMED)
        self.assertIn("Enterまたはqを入力してください。", outputs)

    def test_automatic_mode_records_displays_and_sends_without_input(self) -> None:
        """統合用自動モードはキー入力なしで認識文を表示してLLMへ渡す。"""

        outputs = []
        chat = FakeChatBackend()
        synthesizer = FakeSynthesizer()

        def unexpected_input(prompt: str) -> str:
            self.fail(f"自動モードで入力待機が呼ばれました: {prompt}")

        controller = ConversationController(
            recorder=FakeRecorder(),
            recognizer=FakeRecognizer(["自動送信する発話"]),
            chat_backend=chat,
            synthesizer=synthesizer,
            examples=make_examples(),
            strategy_question="作戦を教えてください",
            feedback_question="感想を教えてください",
            input_function=unexpected_input,
            output=outputs.append,
            manual_input_enabled=False,
        )

        result = controller.run_phase(ConversationPhase.STRATEGY, cancel_event=Event())

        self.assertEqual(result, PhaseRunResult.COMPLETED)
        self.assertEqual(chat.calls[0][1], "自動送信する発話")
        self.assertIn("自動送信する発話", outputs)
        self.assertIn("録音を自動で開始します。", outputs)
        self.assertEqual(
            synthesizer.spoken,
            ["作戦を教えてください", "よい心意気でござる！"],
        )

    def test_automatic_mode_retries_recording_and_recognition_failures(self) -> None:
        """自動モードは想定内エラーを表示し、1秒待機後の録音へ戻る。"""

        speech = AudioData([0.1], 16000, True, "silence")
        no_speech = AudioData([], 16000, False, "timeout")
        recorder = Mock()
        recorder.record_until_silence.side_effect = [
            AudioError("device"),
            no_speech,
            speech,
            speech,
            speech,
        ]
        recognizer = Mock()
        recognizer.transcribe.side_effect = [
            SpeechRecognitionError("recognition"),
            TranscriptionResult("", 0.1, "fake-whisper"),
            TranscriptionResult("再試行後の発話", 0.1, "fake-whisper"),
        ]
        outputs = []
        chat = FakeChatBackend()
        controller = ConversationController(
            recorder=recorder,
            recognizer=recognizer,
            chat_backend=chat,
            synthesizer=FakeSynthesizer(),
            examples=make_examples(),
            strategy_question="作戦",
            feedback_question="感想",
            input_function=lambda prompt: self.fail(prompt),
            output=outputs.append,
            manual_input_enabled=False,
        )

        with patch("chambara_talk.controller.AUTO_RETRY_DELAY_SECONDS", 0):
            result = controller.run_phase(
                ConversationPhase.STRATEGY, cancel_event=Event()
            )

        self.assertEqual(result, PhaseRunResult.COMPLETED)
        self.assertEqual(recorder.record_until_silence.call_count, 5)
        self.assertEqual(recognizer.transcribe.call_count, 3)
        self.assertEqual(chat.calls[0][1], "再試行後の発話")
        self.assertTrue(any("録音エラー" in line for line in outputs))
        self.assertTrue(any("発話を検出" in line for line in outputs))
        self.assertTrue(any("音声認識エラー" in line for line in outputs))
        self.assertTrue(any("認識結果が空" in line for line in outputs))
        self.assertEqual(outputs.count("1秒後に録音を再開します。"), 4)

    def test_automatic_retry_wait_is_cancelable(self) -> None:
        """自動再試行の1秒待機中に外部キャンセルを受理する。"""

        class CancelOnWaitEvent(Event):
            """waitが呼ばれた時点でキャンセル済みにするテスト用Event。"""

            def wait(self, timeout=None):  # noqa: ANN001
                del timeout
                self.set()
                return True

        recorder = Mock()
        recorder.record_until_silence.side_effect = AudioError("device")
        chat = FakeChatBackend()
        controller = ConversationController(
            recorder=recorder,
            recognizer=FakeRecognizer([]),
            chat_backend=chat,
            synthesizer=FakeSynthesizer(),
            examples=make_examples(),
            strategy_question="作戦",
            feedback_question="感想",
            input_function=lambda prompt: self.fail(prompt),
            output=lambda text: None,
            manual_input_enabled=False,
        )

        result = controller.run_phase(
            ConversationPhase.STRATEGY, cancel_event=CancelOnWaitEvent()
        )

        self.assertEqual(result, PhaseRunResult.CANCELLED)
        self.assertEqual(chat.calls, [])

    def test_automatic_mode_discards_transcript_cancelled_after_display(self) -> None:
        """認識結果表示直後の割り込みでは、その発話をLLMへ送らない。"""

        cancel = Event()
        chat = FakeChatBackend()
        synthesizer = FakeSynthesizer()

        def output(text: str) -> None:
            if text == "破棄する発話":
                cancel.set()

        controller = ConversationController(
            recorder=FakeRecorder(),
            recognizer=FakeRecognizer(["破棄する発話"]),
            chat_backend=chat,
            synthesizer=synthesizer,
            examples=make_examples(),
            strategy_question="作戦",
            feedback_question="感想",
            input_function=lambda prompt: self.fail(prompt),
            output=output,
            manual_input_enabled=False,
        )

        result = controller.run_phase(
            ConversationPhase.STRATEGY, cancel_event=cancel
        )

        self.assertEqual(result, PhaseRunResult.CANCELLED)
        self.assertEqual(chat.calls, [])
        self.assertEqual(synthesizer.spoken, ["作戦"])


if __name__ == "__main__":
    # このファイルを直接実行した場合にもテストを開始します。
    unittest.main()
