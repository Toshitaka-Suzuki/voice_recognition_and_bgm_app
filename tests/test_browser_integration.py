"""ブラウザー連携の開始確認、応答対応付け、中断、LLM不要起動を検証する。"""

import asyncio
import json
import os
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chambara_app.mock_conversation import MockConversation
from chambara_app.orchestrator import AppState, IntegratedController
from chambara_app.protocol import ProtocolError, Signal, parse_signal, received
from chambara_app.silent_player import SilentAudioPlayer
from chambara_talk.models import ConversationPhase, PhaseRunResult
from tests.test_integration import FakeConversation, FakeGame


class BrowserProtocolTests(unittest.TestCase):
    def test_optional_request_id_is_validated_and_echoed(self):
        signal = parse_signal(json.dumps({
            "type": "signal", "name": "weak_point_count", "count": 2, "request_id": "hit-1",
        }))
        self.assertEqual(signal, Signal("weak_point_count", 2, "hit-1"))
        self.assertEqual(received(signal)["request_id"], "hit-1")
        for value in ("", "x" * 65, 1, True, []):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                parse_signal(json.dumps({"type": "signal", "name": "parry", "request_id": value}))
        self.assertNotIn("request_id", received(Signal("parry")))

    def test_mock_conversation_completes_or_cancels_without_devices(self):
        conversation = MockConversation(output=lambda _: None, seconds=0)
        self.assertEqual(conversation.run_phase(ConversationPhase.STRATEGY), PhaseRunResult.COMPLETED)
        cancel = Event()
        cancel.set()
        self.assertEqual(
            conversation.run_phase(ConversationPhase.FEEDBACK, cancel_event=cancel),
            PhaseRunResult.CANCELLED,
        )

    def test_silent_clock_loops_and_stops(self):
        player = SilentAudioPlayer()
        with patch("chambara_app.silent_player.perf_counter", return_value=10):
            player.play(SimpleNamespace(duration_ms=1000))
        self.assertEqual(player.position(12.25), (3, 250))
        self.assertIsNone(player.position(9))
        player.stop()
        self.assertIsNone(player.position(12.25))

    def test_mock_cli_does_not_load_conversation_dependencies(self):
        from chambara_app import cli
        with (
            patch.object(cli, "load_tracks", return_value=[]),
            patch.object(cli, "load_settings") as settings,
            patch.object(cli, "load_conversation_examples") as examples,
            patch.object(cli, "create_chat_backend") as llm,
            patch.object(cli, "AudioRecorder") as recorder,
            patch.object(cli, "SpeechRecognizer") as recognizer,
            patch.object(cli, "SpeechSynthesizer") as tts,
            patch.object(cli, "AudioPlayer") as player,
            patch.object(cli, "Game"),
            patch.object(cli, "ConsoleDisplay"),
            patch.object(cli, "IntegratedController", return_value=Mock()) as controller,
            patch.object(cli.asyncio, "run", side_effect=lambda coroutine: coroutine.close()),
        ):
            self.assertEqual(cli.main(["--mock-conversation", "--silent-audio"]), 0)
            for dependency in (settings, examples, llm, recorder, recognizer, tts, player):
                dependency.assert_not_called()
            self.assertIsNone(controller.call_args.kwargs["prepare"])
            self.assertIsNone(controller.call_args.kwargs["cleanup"])
            self.assertEqual(controller.call_args.kwargs["conversation_mode"], "mock_silent")


@unittest.skipIf(
    os.name == "nt"
    and os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED") == "1"
    and os.environ.get("CHAMBARA_FORCE_ASYNC_TESTS") != "1",
    "Windowsサンドボックスではasyncioの自己通信用socketを作成できません。",
)
class BrowserControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.messages = []
        self.game = FakeGame()
        self.controller = IntegratedController(
            FakeConversation(), self.game, output=lambda _: None, conversation_mode="mock",
        )

        async def send(message):
            self.messages.append(message)

        await self.controller.connect(send)

    async def asyncTearDown(self):
        await self.controller.close()

    async def battle(self):
        await self.controller.handle(Signal("game_start"))
        async with asyncio.timeout(2):
            while self.controller.state != AppState.WAITING_BATTLE_START:
                await asyncio.sleep(0.01)
        await self.controller.handle(Signal("battle_start"))

    async def test_start_confirmation_and_correlated_parry(self):
        self.assertEqual(self.messages[0]["protocol_version"], 2)
        self.assertEqual(self.messages[0]["conversation_mode"], "mock")
        await self.battle()
        self.assertEqual(self.messages[-1], {"type": "status", "value": "battle_started"})
        self.assertTrue(self.game.running)
        await self.controller.handle(Signal("parry", request_id="parry-42"))
        self.assertEqual(self.messages[-2]["request_id"], "parry-42")
        self.assertEqual(self.messages[-1], {
            "type": "parry_result", "value": "success", "request_id": "parry-42",
        })

    async def test_failed_start_never_reports_battle_started(self):
        self.game.start = Mock(side_effect=RuntimeError("device unavailable"))
        await self.battle()
        self.assertEqual(self.controller.state, AppState.STANDBY)
        self.assertFalse(any(m.get("value") == "battle_started" for m in self.messages))
        self.assertTrue(any(m["type"] == "error" for m in self.messages))

    async def test_cancel_stops_bgm_without_starting_feedback(self):
        await self.battle()
        await self.controller.handle(Signal("game_cancel", request_id="cancel-1"))
        self.assertEqual(self.controller.state, AppState.STANDBY)
        self.assertFalse(self.game.running)
        self.assertIsNone(self.controller.weak_point_count)
        self.assertEqual(self.messages[-1], {"type": "status", "value": "standby"})
        self.assertFalse(any(m.get("value") == "post_interview" for m in self.messages))

    async def test_cancel_invalidates_late_interview_completion(self):
        self.controller.conversation = MockConversation(output=lambda _: None, seconds=1)
        await self.controller.handle(Signal("game_start"))
        await self.controller.handle(Signal("game_cancel"))
        await asyncio.sleep(0.05)
        self.assertEqual(self.controller.state, AppState.STANDBY)
        self.assertFalse(any(m.get("value") == "pre_interview_finished" for m in self.messages))

    async def test_heartbeat_does_not_change_game_state(self):
        await self.battle()
        await self.controller.handle(Signal("heartbeat", request_id="ping-1"))
        self.assertEqual(self.controller.state, AppState.BATTLE)
        self.assertEqual(self.messages[-1]["request_id"], "ping-1")
