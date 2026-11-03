"""会話の表示順序と、キャンセル・通信切断後の通知破棄を実機なしで確認する。"""

import asyncio
import threading
import unittest
from unittest.mock import Mock, patch

from chambara_app.orchestrator import AppState, IntegratedController
from chambara_app.protocol import Signal
from chambara_talk.controller import ConversationController
from chambara_talk.errors import AudioError, LLMError, SpeechRecognitionError, TTSError
from chambara_talk.models import AudioData, ConversationPhase, PhaseRunResult, TranscriptionResult
from tests.test_controller import FakeChatBackend, FakeRecorder, FakeRecognizer, FakeSynthesizer, make_examples
from tests.test_integration import FakeGame


class ReadyRecorder(FakeRecorder):
    supports_ready_callback = True

    def record_until_silence(self, *, cancel_event=None, on_ready=None):
        if on_ready is not None:
            on_ready()
        return super().record_until_silence(cancel_event=cancel_event)


def make_controller():
    return ConversationController(
        recorder=ReadyRecorder(), recognizer=FakeRecognizer(["頑張ります"] * 3),
        chat_backend=FakeChatBackend(), synthesizer=FakeSynthesizer(), examples=make_examples(),
        strategy_question="どんな作戦で挑むでござる？", feedback_question="感想を聞かせてくだされ。",
        manual_input_enabled=False, output=lambda _: None,
    )


class ConversationProgressTests(unittest.TestCase):
    def test_stages_and_text_arrive_before_their_work(self):
        controller = make_controller()
        events = []
        original_speak = controller.synthesizer.speak
        original_generate = controller.chat_backend.generate

        def speak(text, **kwargs):
            self.assertIn(events[-1]["stage"], ("question", "speaking"))
            self.assertEqual(events[-1]["text"], text)
            return original_speak(text, **kwargs)

        def generate(*args, **kwargs):
            self.assertEqual(events[-1], {"stage": "thinking", "text": "頑張ります"})
            return original_generate(*args, **kwargs)

        controller.synthesizer.speak = speak
        controller.chat_backend.generate = generate
        result = controller.run_phase(ConversationPhase.STRATEGY, on_progress=events.append)
        self.assertEqual(result, PhaseRunResult.COMPLETED)
        self.assertEqual([event["stage"] for event in events], [
            "question", "preparing", "listening", "recognizing", "thinking", "speaking",
        ])
        self.assertEqual(events[-1]["text"], "よい心意気でござる！")

    def test_recoverable_failures_report_safe_reasons_then_resume(self):
        audio = AudioData([0.1], 16000, True, "silence")
        transcript = TranscriptionResult("頑張ります", 0.1, "fake")
        for reason in ("recording_failed", "no_speech", "recognition_failed", "empty_transcript", "reply_failed"):
            with self.subTest(reason=reason):
                controller = make_controller()
                events = []
                if reason in ("recording_failed", "no_speech"):
                    first = AudioError("private device details") if reason == "recording_failed" else AudioData([], 16000, False, "no_speech")
                    controller.recorder.record_until_silence = Mock(side_effect=[first, audio])
                elif reason in ("recognition_failed", "empty_transcript"):
                    first = SpeechRecognitionError("private model details") if reason == "recognition_failed" else TranscriptionResult("", 0.1, "fake")
                    controller.recognizer.transcribe = Mock(side_effect=[first, transcript])
                else:
                    controller.chat_backend.generate = Mock(side_effect=[LLMError("private backend details"), "応援するでござる！"])
                with patch.object(controller, "_wait_before_retry", return_value=True):
                    self.assertEqual(controller.run_phase(ConversationPhase.STRATEGY, on_progress=events.append), PhaseRunResult.COMPLETED)
                retry = {"stage": "retry", "reason": reason}
                self.assertIn(retry, events)
                index = events.index(retry)
                self.assertEqual(events[index + 1], {"stage": "preparing"})
                self.assertEqual(events[-1]["stage"], "speaking")
                self.assertNotIn("private", str(events))

    def test_speech_failure_keeps_text_and_existing_continue_behavior(self):
        controller = make_controller()
        controller.synthesizer.speak = Mock(side_effect=[TTSError("device failed"), True])
        events = []
        result = controller.run_phase(ConversationPhase.STRATEGY, on_progress=events.append)
        self.assertEqual(result, PhaseRunResult.COMPLETED)
        self.assertEqual(events[0]["stage"], "question")
        self.assertEqual(events[1], {"stage": "retry", "reason": "speech_failed"})
        self.assertEqual(events[-1]["stage"], "speaking")

    def test_notification_exception_does_not_interrupt_conversation(self):
        controller = make_controller()
        callback = Mock(side_effect=RuntimeError("display unavailable"))
        self.assertEqual(controller.run_phase(ConversationPhase.STRATEGY, on_progress=callback), PhaseRunResult.COMPLETED)
        self.assertEqual(len(controller.chat_backend.calls), 1)

    def test_cancel_after_recognition_does_not_emit_thinking_or_call_llm(self):
        controller = make_controller()
        cancel = threading.Event()
        events = []

        def recognize(_audio):
            cancel.set()
            return TranscriptionResult("遅れて届いた発話", 0.1, "fake")

        controller.recognizer.transcribe = recognize
        result = controller.run_phase(ConversationPhase.STRATEGY, cancel_event=cancel, on_progress=events.append)
        self.assertEqual(result, PhaseRunResult.CANCELLED)
        self.assertEqual(events[-1]["stage"], "recognizing")
        self.assertEqual(controller.chat_backend.calls, [])

    def test_legacy_recorder_without_ready_callback_remains_usable(self):
        controller = make_controller()
        controller.recorder = FakeRecorder()
        events = []
        self.assertEqual(controller.run_phase(ConversationPhase.STRATEGY, on_progress=events.append), PhaseRunResult.COMPLETED)
        self.assertNotIn("listening", [event["stage"] for event in events])


class ProgressConversation:
    supports_progress = True

    def run_phase(self, phase, *, outcome=None, cancel_event=None, on_progress=None):
        for stage, text in (("question", "質問"), ("thinking", "頑張ります"), ("speaking", "応援するでござる！")):
            on_progress({"stage": stage, "text": text})
        return PhaseRunResult.COMPLETED


class DelayedProgressConversation:
    supports_progress = True

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.callback = None

    def run_phase(self, phase, *, outcome=None, cancel_event=None, on_progress=None):
        self.callback = on_progress
        on_progress({"stage": "question", "text": "質問"})
        self.started.set()
        self.release.wait(2)
        # 中断を尊重しない部品から届く結果も、統合側で破棄する必要があります。
        on_progress({"stage": "speaking", "text": "遅れた返答"})
        return PhaseRunResult.COMPLETED


class IntegratedProgressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.messages = []
        self.conversation = ProgressConversation()
        self.controller = IntegratedController(self.conversation, FakeGame(), output=lambda _: None)
        self.loop_thread = threading.get_ident()

        async def send(message):
            self.assertEqual(threading.get_ident(), self.loop_thread)
            self.messages.append(message)

        self.send = send
        await self.controller.connect(send)

    async def asyncTearDown(self):
        if isinstance(self.controller.conversation, DelayedProgressConversation):
            self.controller.conversation.release.set()
        await self.controller.close()

    async def wait_for(self, predicate):
        async with asyncio.timeout(2):
            while not predicate():
                await asyncio.sleep(0.005)

    async def test_progress_is_ordered_before_phase_completion_on_event_loop_thread(self):
        await self.controller.handle(Signal("game_start"))
        await self.wait_for(lambda: self.controller.state == AppState.WAITING_BATTLE_START)
        progress = [m for m in self.messages if m["type"] == "conversation_progress"]
        self.assertEqual([m["stage"] for m in progress], ["question", "thinking", "speaking"])
        self.assertTrue(all(m["phase"] == "strategy" for m in progress))
        self.assertEqual(self.messages[-1], {"type": "status", "value": "pre_interview_finished"})
        await self.controller.handle(Signal("battle_start"))
        await self.controller.handle(Signal("battle_end"))
        await self.wait_for(lambda: self.controller.state == AppState.STANDBY)
        self.assertEqual([m["stage"] for m in self.messages if m.get("phase") == "feedback"], ["question", "thinking", "speaking"])

    async def test_progress_send_failure_does_not_fail_phase(self):
        async def unreliable_send(message):
            if message["type"] == "conversation_progress":
                raise OSError("socket gone")
            self.messages.append(message)

        await self.controller.connect(unreliable_send)
        await self.controller.handle(Signal("game_start"))
        await self.wait_for(lambda: self.controller.state == AppState.WAITING_BATTLE_START)
        self.assertFalse(any(m["type"] == "error" for m in self.messages))

    async def test_recognized_text_is_sent_while_llm_is_still_working(self):
        conversation = make_controller()
        release = threading.Event()

        def delayed_reply(*_args, **_kwargs):
            release.wait(2)
            return "応援するでござる！"

        conversation.chat_backend.generate = delayed_reply
        self.controller.conversation = conversation
        try:
            await self.controller.handle(Signal("game_start"))
            await self.wait_for(lambda: any(m.get("stage") == "thinking" for m in self.messages))
            thought = next(m for m in self.messages if m.get("stage") == "thinking")
            self.assertEqual(thought["text"], "頑張ります")
            self.assertEqual(self.controller.state, AppState.PRE_INTERVIEW)
            self.assertFalse(any(m.get("stage") == "speaking" for m in self.messages))
        finally:
            release.set()
        await self.wait_for(lambda: self.controller.state == AppState.WAITING_BATTLE_START)

    async def check_stale_after(self, action):
        delayed = DelayedProgressConversation()
        self.controller.conversation = delayed
        await self.controller.handle(Signal("game_start"))
        await self.wait_for(lambda: any(m["type"] == "conversation_progress" for m in self.messages))
        # キューへ通知した直後、送信処理がロックを取る前に中断します。
        delayed.callback({"stage": "thinking", "text": "古い発話"})
        if action == "disconnect":
            await self.controller.disconnect(self.send)
            await self.controller.connect(self.send)
        else:
            await self.controller.handle(Signal(action))
        checkpoint = len(self.messages)
        delayed.release.set()
        await self.wait_for(lambda: not self.controller._phase_tasks)
        await asyncio.sleep(0)
        self.assertFalse(any(m["type"] == "conversation_progress" for m in self.messages[checkpoint:]))
        self.assertFalse(any(m.get("text") == "古い発話" for m in self.messages))

    async def test_queued_and_late_progress_discarded_after_cancel(self):
        await self.check_stale_after("game_cancel")

    async def test_queued_and_late_progress_discarded_after_skip_to_battle(self):
        await self.check_stale_after("battle_start")

    async def test_queued_and_late_progress_discarded_after_reconnect(self):
        await self.check_stale_after("disconnect")


if __name__ == "__main__":
    unittest.main()
