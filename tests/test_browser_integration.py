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
    def test_parry_source_defaults_to_python_and_accepts_legacy_esp(self):
        message = {"type": "signal", "name": "game_start", "difficulty": "hard", "request_id": "start-1"}
        self.assertEqual(parse_signal(json.dumps(message)).parry_source, "python")
        for source in ("python", "esp_legacy"):
            with self.subTest(source=source):
                signal = parse_signal(json.dumps({**message, "parry_source": source}))
                self.assertEqual(signal, Signal("game_start", request_id="start-1", difficulty="hard", parry_source=source))
        for value in (None, True, 1, [], {}, "", "esp", "ESP_LEGACY"):
            with self.subTest(value=value), self.assertRaisesRegex(ProtocolError, "parry_source"):
                parse_signal(json.dumps({**message, "parry_source": value}))

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
            patch.object(cli, "create_synthesizer") as tts,
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

        self.send = send
        await self.controller.connect(send)

    async def asyncTearDown(self):
        await self.controller.close()

    async def battle(self, difficulty="easy"):
        await self.controller.handle(Signal("game_start", difficulty=difficulty))
        async with asyncio.timeout(2):
            while self.controller.state != AppState.WAITING_BATTLE_START:
                await asyncio.sleep(0.01)
        await self.controller.handle(Signal("battle_start"))

    async def test_start_confirmation_and_correlated_parry(self):
        self.assertEqual(self.messages[0]["protocol_version"], 3)
        self.assertEqual(self.messages[0]["conversation_mode"], "mock")
        self.assertTrue(self.messages[0]["esp_legacy"])
        await self.battle()
        self.assertEqual(self.messages[-1], {"type": "status", "value": "battle_started"})
        self.assertTrue(self.game.running)
        await self.controller.handle(Signal("parry", request_id="parry-42"))
        self.assertEqual(self.messages[-2]["request_id"], "parry-42")
        self.assertEqual(self.messages[-1], {
            "type": "parry_result", "value": "success", "request_id": "parry-42",
        })

    async def test_legacy_esp_start_uses_external_mode_and_rejects_arrival_judgment(self):
        start = self.game.start
        self.game.start = Mock(side_effect=lambda stage, external_parry=False: start(stage))
        self.game.submit_parry = Mock(side_effect=AssertionError("ESP result must not be rejudged"))
        await self.controller.handle(Signal("game_start", difficulty="hard", parry_source="esp_legacy"))
        await self.controller.handle(Signal("battle_start"))
        self.assertEqual(self.controller.state, AppState.BATTLE)
        self.assertEqual(self.controller.parry_source, "esp_legacy")
        self.game.start.assert_called_once_with(stage="hard", external_parry=True)
        await self.controller.handle(Signal("parry", request_id="unexpected-parry"), received_at=2.5)
        self.assertEqual(self.messages[-1]["type"], "error")
        self.assertIn("ESP", self.messages[-1]["text"])
        self.game.submit_parry.assert_not_called()
        self.assertFalse(any(m.get("type") == "parry_result" for m in self.messages))
        # ESPの結果で進んだ画面からのHP通知は従来どおり受け付ける。
        await self.controller.handle(Signal("weak_point_count", 2))
        self.assertEqual(self.controller.weak_point_count, 2)
        await self.controller.handle(Signal("game_cancel"))
        self.assertEqual(self.controller.parry_source, "python")
        self.game.start.reset_mock()
        await self.battle()
        self.game.start.assert_called_once_with(stage="easy")

    async def test_invalid_internal_parry_source_does_not_start_interview(self):
        await self.controller.handle(Signal("game_start", parry_source="unknown"))
        self.assertEqual(self.messages[-1]["type"], "error")
        self.assertEqual(self.controller.state, AppState.STANDBY)
        self.assertEqual(self.controller.parry_source, "python")
        self.assertEqual(self.controller.conversation.calls, [])

    async def test_external_mode_resets_after_feedback_and_disconnect(self):
        start = self.game.start
        self.game.start = Mock(side_effect=lambda stage, external_parry=False: start(stage))
        for finish in ("battle_end", "disconnect"):
            with self.subTest(finish=finish):
                await self.controller.handle(Signal("game_start", parry_source="esp_legacy"))
                await self.controller.handle(Signal("battle_start"))
                if finish == "disconnect":
                    await self.controller.disconnect(self.send)
                else:
                    await self.controller.handle(Signal(finish))
                    async with asyncio.timeout(2):
                        while self.controller.state != AppState.STANDBY:
                            await asyncio.sleep(0.01)
                self.assertEqual(self.controller.parry_source, "python")
                self.assertEqual(self.controller.state, AppState.STANDBY)

    async def test_failed_start_never_reports_battle_started(self):
        self.game.start = Mock(side_effect=RuntimeError("device unavailable"))
        await self.battle("hard")
        self.assertEqual(self.controller.state, AppState.STANDBY)
        self.assertEqual(self.controller.difficulty, "easy")
        self.assertFalse(any(m.get("value") == "battle_started" for m in self.messages))
        self.assertTrue(any(m["type"] == "error" for m in self.messages))

    async def test_each_selected_difficulty_stays_fixed_until_victory(self):
        for difficulty in ("easy", "normal", "hard"):
            with self.subTest(difficulty=difficulty):
                await self.battle(difficulty)
                self.assertEqual(self.controller.difficulty, difficulty)
                self.assertEqual(self.game.current_stage, difficulty)
                starts = list(self.game.stages)
                for count in (2, 2, 1):
                    await self.controller.handle(Signal("weak_point_count", count))
                    self.assertEqual(self.controller.weak_point_count, count)
                    self.assertEqual(self.game.stages, starts)
                    self.assertEqual(self.game.current_stage, difficulty)
                await self.controller.handle(Signal("weak_point_count", 0))
                async with asyncio.timeout(2):
                    while self.controller.state != AppState.STANDBY:
                        await asyncio.sleep(0.01)
                self.assertEqual(self.game.stops[-1], "human_win")
                self.assertEqual(self.controller.difficulty, "easy")

    async def test_selected_difficulty_is_used_when_pre_interview_is_skipped(self):
        self.controller.conversation = MockConversation(output=lambda _: None, seconds=1)
        await self.controller.handle(Signal("game_start", difficulty="hard"))
        await self.controller.handle(Signal("battle_start"))
        self.assertEqual(self.controller.state, AppState.BATTLE)
        self.assertEqual(self.game.stages, ["hard"])

    async def test_start_cannot_change_difficulty_during_battle(self):
        await self.battle("normal")
        await self.controller.handle(Signal("game_start", difficulty="hard"))
        self.assertEqual(self.messages[-1]["type"], "error")
        self.assertEqual(self.controller.difficulty, "normal")
        self.assertEqual(self.game.stages, ["normal"])

    async def test_invalid_internal_difficulty_does_not_start_interview(self):
        await self.controller.handle(Signal("game_start", difficulty="unknown"))
        self.assertEqual(self.messages[-1]["type"], "error")
        self.assertEqual(self.controller.state, AppState.STANDBY)
        self.assertEqual(self.controller.difficulty, "easy")
        self.assertEqual(self.controller.conversation.calls, [])

    async def test_disconnect_clears_difficulty_for_next_default_game(self):
        await self.battle("hard")
        await self.controller.disconnect(self.send)
        self.assertEqual(self.controller.difficulty, "easy")
        await self.controller.connect(self.send)
        await self.battle()
        self.assertEqual(self.game.stages, ["hard", "easy"])

    async def test_cancel_stops_bgm_without_starting_feedback(self):
        await self.battle("normal")
        await self.controller.handle(Signal("game_cancel", request_id="cancel-1"))
        self.assertEqual(self.controller.state, AppState.STANDBY)
        self.assertFalse(self.game.running)
        self.assertIsNone(self.controller.weak_point_count)
        self.assertEqual(self.controller.difficulty, "easy")
        self.assertEqual(self.messages[-1], {"type": "status", "value": "standby"})
        self.assertFalse(any(m.get("value") == "post_interview" for m in self.messages))

    async def test_cancel_invalidates_late_interview_completion(self):
        self.controller.conversation = MockConversation(output=lambda _: None, seconds=1)
        await self.controller.handle(Signal("game_start", difficulty="hard"))
        await self.controller.handle(Signal("game_cancel"))
        await asyncio.sleep(0.05)
        self.assertEqual(self.controller.state, AppState.STANDBY)
        self.assertEqual(self.controller.difficulty, "easy")
        self.assertFalse(any(m.get("value") == "pre_interview_finished" for m in self.messages))

    async def test_heartbeat_does_not_change_game_state(self):
        await self.battle()
        await self.controller.handle(Signal("heartbeat", request_id="ping-1"))
        self.assertEqual(self.controller.state, AppState.BATTLE)
        self.assertEqual(self.messages[-1]["request_id"], "ping-1")
