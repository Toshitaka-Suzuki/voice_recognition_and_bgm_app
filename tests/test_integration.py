"""実デバイスを使わず、統合状態機械と通信JSONの一連の流れを確認する。"""

import asyncio
import json
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chambara_app.orchestrator import AppState, IntegratedController
from chambara_app.protocol import ProtocolError, Signal, parse_signal
from chambara_talk.controller import ConversationController
from chambara_talk.errors import ConversationCancelled
from chambara_talk.models import (
    AudioData,
    ConversationExamples,
    ConversationPhase,
    FeedbackOutcome,
    PhaseRunResult,
    TranscriptionResult,
)


class FakeConversation:
    """会話の引数を記録し、すぐ完了するテスト用部品。"""

    def __init__(self) -> None:
        self.calls = []

    def run_phase(self, phase, *, outcome=None, cancel_event=None):  # noqa: ANN001
        self.calls.append((phase, outcome, cancel_event))
        return PhaseRunResult.CANCELLED if cancel_event.is_set() else PhaseRunResult.COMPLETED


class BlockingConversation(FakeConversation):
    """外部キャンセルが来るまで開始前インタビューを継続する偽物。"""

    def run_phase(self, phase, *, outcome=None, cancel_event=None):  # noqa: ANN001
        self.calls.append((phase, outcome, cancel_event))
        cancel_event.wait(2)
        return PhaseRunResult.CANCELLED


class FakeAutomaticSynthesizer:
    """統合会話の質問と返答を音声出力せず完了させる偽物。"""

    def speak(self, text, *, cancel_event=None):  # noqa: ANN001
        del text
        return cancel_event is None or not cancel_event.is_set()


class TrackingChatBackend:
    """自動会話からLLMが呼ばれた発話を記録する偽物。"""

    def __init__(self) -> None:
        self.calls = []

    def generate(self, phase, transcript, examples, *, outcome=None):  # noqa: ANN001
        self.calls.append((phase, transcript, examples, outcome))
        return "コメント"


def automatic_conversation(recorder, recognizer, chat_backend):  # noqa: ANN001
    """統合テスト用の完全自動ConversationControllerを作る。"""

    examples = ConversationExamples(
        version=1,
        domain_terms=(),
        phases={
            ConversationPhase.STRATEGY: (),
            ConversationPhase.FEEDBACK: (),
        },
    )
    return ConversationController(
        recorder=recorder,
        recognizer=recognizer,
        chat_backend=chat_backend,
        synthesizer=FakeAutomaticSynthesizer(),
        examples=examples,
        strategy_question="作戦",
        feedback_question="感想",
        input_function=lambda prompt: (_ for _ in ()).throw(AssertionError(prompt)),
        output=lambda text: None,
        manual_input_enabled=False,
    )


class FakePlayer:
    """BGM監視APIだけを持つ偽物。"""

    def __init__(self) -> None:
        self.waiting_for_first_callback = False
        self.synchronizing = False

    def take_warning(self, now):  # noqa: ANN001
        return None


class FakeGame:
    """再生段階、停止理由、パリイ結果を記録するBGMゲームの偽物。"""

    def __init__(self) -> None:
        self.player = FakePlayer()
        self.running = False
        self.stages = []
        self.stops = []
        self.parry_event = SimpleNamespace(kind="parry_success")
        self.current_stage = "easy"
        self.status_result = (1, 100, 0)

    @property
    def track(self):  # noqa: ANN201
        return SimpleNamespace(id=self.current_stage)

    def start(self, stage="easy") -> None:
        self.running = True
        self.current_stage = stage
        self.stages.append(stage)

    def switch_stage(self, stage: str) -> bool:
        self.current_stage = stage
        self.stages.append(stage)
        return True

    def submit_parry(self, timestamp=None):  # noqa: ANN001
        return self.parry_event

    def status(self, timestamp):  # noqa: ANN001
        return self.status_result if self.running else None

    def stop(self, reason="quit") -> None:
        if self.running:
            self.stops.append(reason)
        self.running = False


class FakeBgmDisplay:
    """統合コントローラーから渡されたBGM表示内容を記録する。"""

    def __init__(self) -> None:
        self.status_calls = []
        self.events = []
        self.clear_count = 0

    def status(self, stage, status, *, waiting=False, synchronizing=False):  # noqa: ANN001
        self.status_calls.append((stage, status, waiting, synchronizing))

    def event(self, event):  # noqa: ANN001
        self.events.append(event)

    def clear_status(self) -> None:
        self.clear_count += 1


class ProtocolTests(unittest.TestCase):
    """通信サンプルから引き継いだ入力形式を検証する。"""

    def test_all_signals_and_weak_count(self) -> None:
        for name in ("game_start", "battle_start", "parry", "battle_end"):
            self.assertEqual(
                parse_signal(json.dumps({"type": "signal", "name": name})), Signal(name)
            )
        self.assertEqual(
            parse_signal('{"type":"signal","name":"weak_point_count","count":2}'),
            Signal("weak_point_count", 2),
        )

    def test_invalid_json_and_counts_are_rejected(self) -> None:
        for raw in ("not json", "[]", '{"type":"message"}', b"binary"):
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                parse_signal(raw)
        for count in (-1, 1.5, True, 9_007_199_254_740_992):
            with self.subTest(count=count), self.assertRaises(ProtocolError):
                parse_signal(json.dumps({"type": "signal", "name": "weak_point_count", "count": count}))

    def test_game_start_difficulty_defaults_and_preserves_request_id(self) -> None:
        self.assertEqual(parse_signal('{"type":"signal","name":"game_start"}').difficulty, "easy")
        for difficulty in ("easy", "normal", "hard"):
            with self.subTest(difficulty=difficulty):
                signal = parse_signal(json.dumps({
                    "type": "signal", "name": "game_start", "difficulty": difficulty,
                    "request_id": "start-1",
                }))
                self.assertEqual(signal, Signal("game_start", None, "start-1", difficulty))

    def test_invalid_start_difficulty_is_rejected(self) -> None:
        for difficulty in (None, "", "HARD", "expert", 1, True, [], {}):
            with self.subTest(difficulty=difficulty), self.assertRaises(ProtocolError):
                parse_signal(json.dumps({"type": "signal", "name": "game_start", "difficulty": difficulty}))


class IntegratedCliTests(unittest.TestCase):
    """統合CLIがキーボード待機なしの会話部品を組み立てることを確認する。"""

    def test_conversation_is_built_in_automatic_mode(self) -> None:
        """統合版では手動入力を無効化し、入力関数を渡さない。"""

        from chambara_app import cli

        settings = SimpleNamespace(
            audio=SimpleNamespace(),
            speech_recognition=SimpleNamespace(model="fake"),
            llm=SimpleNamespace(provider="ollama", model="fake"),
            tts=SimpleNamespace(),
            questions=SimpleNamespace(
                strategy="作戦",
                feedback="感想",
                feedback_human_win="勝利",
                feedback_robot_win="敗北",
            ),
        )
        examples = SimpleNamespace(domain_terms=())

        def close_coroutine(coroutine):  # noqa: ANN001
            """非同期サーバーを開始せず、生成したコルーチンだけを閉じる。"""

            coroutine.close()

        with (
            patch.object(cli.sys, "platform", "win32"),
            patch.object(cli, "load_settings", return_value=settings),
            patch.object(cli, "load_conversation_examples", return_value=examples),
            patch.object(cli, "load_tracks", return_value=[]),
            patch.object(cli, "AudioRecorder"),
            patch.object(cli, "SpeechRecognizer"),
            patch.object(cli, "create_chat_backend", return_value=Mock()),
            patch.object(cli, "create_synthesizer"),
            patch.object(cli, "ConversationController") as conversation_class,
            patch.object(cli, "AudioPlayer"),
            patch.object(cli, "ConsoleDisplay") as display_class,
            patch.object(cli, "Game") as game_class,
            patch.object(cli, "IntegratedController", return_value=Mock()) as integrated_class,
            patch.object(cli.asyncio, "run", side_effect=close_coroutine),
        ):
            code = cli.main([])

        self.assertEqual(code, 0)
        arguments = conversation_class.call_args.kwargs
        self.assertFalse(arguments["manual_input_enabled"])
        self.assertNotIn("input_function", arguments)
        display = display_class.return_value
        self.assertIs(game_class.call_args.args[2], display.event)
        integrated_arguments = integrated_class.call_args.kwargs
        self.assertIs(integrated_arguments["bgm_display"], display)
        arguments["output"]("会話ログ")
        display.line.assert_called_with("会話ログ", restore_status=True)
        display.close.assert_called_once()


@unittest.skipIf(
    os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED") == "1"
    and os.environ.get("CHAMBARA_FORCE_ASYNC_TESTS") != "1",
    "CodexのWindowsサンドボックスではasyncioの自己通信用socketを作成できません。",
)
class IntegratedControllerTests(unittest.IsolatedAsyncioTestCase):
    """状態遷移、勝敗、割り込み、エラー応答を非同期で確認する。"""

    async def asyncSetUp(self) -> None:
        self.messages = []
        self.conversation = FakeConversation()
        self.game = FakeGame()
        self.controller = IntegratedController(
            self.conversation, self.game, output=lambda message: None
        )

        async def send(message):  # noqa: ANN001
            self.messages.append(message)

        self.send = send
        await self.controller.connect(self.send)

    async def asyncTearDown(self) -> None:
        await self.controller.close()

    async def wait_for_state(self, state: AppState) -> None:
        async with asyncio.timeout(2):
            while self.controller.state != state:
                await asyncio.sleep(0.01)

    async def test_human_win_full_flow_and_second_standby(self) -> None:
        await self.controller.handle(Signal("game_start"))
        await self.wait_for_state(AppState.WAITING_BATTLE_START)
        await self.controller.handle(Signal("battle_start"))
        await self.controller.handle(Signal("weak_point_count", 2))
        await self.controller.handle(Signal("weak_point_count", 2))
        await self.controller.handle(Signal("weak_point_count", 1))
        await self.controller.handle(Signal("parry"), received_at=1.0)
        await self.controller.handle(Signal("weak_point_count", 0))
        await self.wait_for_state(AppState.STANDBY)

        self.assertEqual(self.game.stages, ["easy"])
        self.assertIn("human_win", self.game.stops)
        self.assertEqual(self.conversation.calls[-1][1], FeedbackOutcome.HUMAN_WIN)
        statuses = [m["value"] for m in self.messages if m["type"] == "status"]
        self.assertEqual(
            statuses,
            [
                "standby",
                "pre_interview",
                "pre_interview_finished",
                "battle_started",
                "post_interview",
                "post_interview_finished",
                "standby",
            ],
        )
        self.assertIn({"type": "parry_result", "value": "success"}, self.messages)

    async def test_robot_win_and_increasing_count_error(self) -> None:
        await self.controller.handle(Signal("game_start"))
        await self.wait_for_state(AppState.WAITING_BATTLE_START)
        await self.controller.handle(Signal("battle_start"))
        await self.controller.handle(Signal("weak_point_count", 1))
        before = list(self.game.stages)
        await self.controller.handle(Signal("weak_point_count", 2))
        self.assertEqual(self.game.stages, before)
        self.assertEqual(self.messages[-1]["type"], "error")
        self.assertEqual(self.messages[-1]["state"], "battle")
        await self.controller.handle(Signal("battle_end"))
        await self.wait_for_state(AppState.STANDBY)
        self.assertEqual(self.conversation.calls[-1][1], FeedbackOutcome.ROBOT_WIN)

    async def test_parry_not_ready_always_returns_result(self) -> None:
        await self.controller.handle(Signal("game_start"))
        await self.wait_for_state(AppState.WAITING_BATTLE_START)
        await self.controller.handle(Signal("battle_start"))
        display = FakeBgmDisplay()
        self.controller.bgm_display = display
        self.game.parry_event = None
        await self.controller.handle(Signal("parry"))
        self.assertEqual(
            self.messages[-1],
            {"type": "parry_result", "value": "failure", "reason": "not_ready"},
        )
        self.assertEqual(len(display.events), 1)
        self.assertEqual(display.events[0].kind, "parry_failure")
        self.assertEqual(display.events[0].stage, "easy")
        self.assertIn("判定準備中", display.events[0].reason)

    async def test_bgm_debug_status_tracks_stage_position_and_sync_state(self) -> None:
        """100ms監視が曲、位置、判定、周回と準備状態を表示へ渡す。"""

        display = FakeBgmDisplay()
        self.controller.bgm_display = display
        await self.controller.handle(Signal("game_start"))
        await self.wait_for_state(AppState.WAITING_BATTLE_START)
        await self.controller.handle(Signal("battle_start"))

        self.game.status_result = (3, 1234.5, 0)
        self.controller._display_bgm_status(1.0)
        await self.controller.handle(Signal("weak_point_count", 2))
        self.game.status_result = (4, 2000.0, None)
        self.controller._display_bgm_status(2.0)
        await self.controller.handle(Signal("weak_point_count", 1))
        self.game.status_result = None
        self.game.player.waiting_for_first_callback = True
        self.controller._display_bgm_status(3.0)
        self.game.player.waiting_for_first_callback = False
        self.game.player.synchronizing = True
        self.controller._display_bgm_status(4.0)
        self.game.player.synchronizing = False
        self.controller._display_bgm_status(5.0)

        self.assertEqual(
            display.status_calls,
            [
                ("easy", (3, 1234.5, 0), False, False),
                ("easy", (4, 2000.0, None), False, False),
                ("easy", None, True, False),
                ("easy", None, False, True),
                ("easy", None, False, False),
            ],
        )

        await self.controller.handle(Signal("battle_end"))
        self.assertGreaterEqual(display.clear_count, 1)

    async def test_disconnect_resets_and_new_connection_gets_standby(self) -> None:
        await self.controller.handle(Signal("game_start"))
        await self.wait_for_state(AppState.WAITING_BATTLE_START)
        await self.controller.handle(Signal("battle_start"))
        await self.controller.disconnect(self.send)
        self.assertEqual(self.controller.state, AppState.STANDBY)
        self.assertFalse(self.game.running)
        self.messages.clear()
        await self.controller.connect(self.send)
        self.assertEqual(
            self.messages,
            [
                {"type": "connected", "text": "接続しました", "protocol_version": 3, "conversation_mode": "live", "esp_legacy": True},
                {"type": "status", "value": "standby"},
            ],
        )

    async def test_battle_start_interrupts_pre_interview(self) -> None:
        await self.controller.close()
        self.messages.clear()
        self.conversation = BlockingConversation()
        self.game = FakeGame()
        self.controller = IntegratedController(
            self.conversation, self.game, output=lambda message: None
        )
        await self.controller.connect(self.send)
        await self.controller.handle(Signal("game_start"))
        async with asyncio.timeout(2):
            while not self.conversation.calls:
                await asyncio.sleep(0.01)
        await self.controller.handle(Signal("battle_start"))
        self.assertEqual(self.controller.state, AppState.BATTLE)
        statuses = [m["value"] for m in self.messages if m["type"] == "status"]
        self.assertNotIn("pre_interview_finished", statuses)
        self.assertTrue(self.conversation.calls[0][2].is_set())

    async def test_battle_start_interrupts_automatic_recording(self) -> None:
        """自動録音中でもbattle_startを先に処理し、会話を中断する。"""

        await self.controller.close()
        self.messages.clear()
        started = threading.Event()

        class BlockingRecorder:
            def record_until_silence(self, *, cancel_event=None):  # noqa: ANN001
                started.set()
                cancel_event.wait(2)
                raise ConversationCancelled("cancelled")

        chat = TrackingChatBackend()
        conversation = automatic_conversation(
            BlockingRecorder(), Mock(), chat
        )
        self.controller = IntegratedController(
            conversation, self.game, output=lambda message: None
        )
        await self.controller.connect(self.send)
        await self.controller.handle(Signal("game_start"))
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0.01)

        await self.controller.handle(Signal("battle_start"))

        self.assertEqual(self.controller.state, AppState.BATTLE)
        self.assertEqual(chat.calls, [])

    async def test_battle_start_discards_automatic_recognition_result(self) -> None:
        """認識中のbattle_start後に返った文章をLLMへ送らない。"""

        await self.controller.close()
        self.messages.clear()
        recognition_started = threading.Event()
        release_recognition = threading.Event()

        class ImmediateRecorder:
            def record_until_silence(self, *, cancel_event=None):  # noqa: ANN001
                return AudioData([0.1], 16000, True, "silence")

        class BlockingRecognizer:
            def transcribe(self, audio):  # noqa: ANN001
                del audio
                recognition_started.set()
                release_recognition.wait(2)
                return TranscriptionResult("遅れて返る発話", 0.1, "fake")

        chat = TrackingChatBackend()
        conversation = automatic_conversation(
            ImmediateRecorder(), BlockingRecognizer(), chat
        )
        self.controller = IntegratedController(
            conversation, self.game, output=lambda message: None
        )
        await self.controller.connect(self.send)
        await self.controller.handle(Signal("game_start"))
        async with asyncio.timeout(2):
            while not recognition_started.is_set():
                await asyncio.sleep(0.01)

        await self.controller.handle(Signal("battle_start"))
        release_recognition.set()
        async with asyncio.timeout(2):
            while self.controller._phase_tasks:  # テストでは会話終了まで待つ。
                await asyncio.sleep(0.01)

        self.assertEqual(self.controller.state, AppState.BATTLE)
        self.assertEqual(chat.calls, [])

    async def test_prepare_conversation_and_cleanup_share_one_thread(self) -> None:
        """SAPI5とCopilotを初期化・使用・終了するスレッドを固定する。"""

        await self.controller.close()
        thread_ids = []

        class ThreadRecordingConversation(FakeConversation):
            def run_phase(self, phase, *, outcome=None, cancel_event=None):  # noqa: ANN001
                thread_ids.append(threading.get_ident())
                return PhaseRunResult.COMPLETED

        controller = IntegratedController(
            ThreadRecordingConversation(),
            FakeGame(),
            output=lambda message: None,
            prepare=lambda: thread_ids.append(threading.get_ident()),
            cleanup=lambda: thread_ids.append(threading.get_ident()),
        )
        await controller.prepare()
        await controller.connect(self.send)
        await controller.handle(Signal("game_start"))
        async with asyncio.timeout(2):
            while controller.state != AppState.WAITING_BATTLE_START:
                await asyncio.sleep(0.01)
        await controller.close()
        self.controller = controller

        self.assertEqual(len(thread_ids), 3)
        self.assertEqual(len(set(thread_ids)), 1)


if __name__ == "__main__":
    unittest.main()
