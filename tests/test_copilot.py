"""Copilot通信をモックし、送信内容・異常系・セッション解放を確認する。"""

import asyncio
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from chambara_talk.config import load_conversation_examples, load_settings
from chambara_talk.controller import ConversationController
from chambara_talk.copilot_backend import CopilotChatBackend
from chambara_talk.errors import LLMError
from chambara_talk.llm import ChatBackend, create_chat_backend
from chambara_talk.models import AudioData, ConversationExample, ConversationPhase, TranscriptionResult


ROOT = Path(__file__).resolve().parents[1]


class CopilotTests(unittest.TestCase):
    """実サービスや利用枠を使用せず、クラウド接続の契約を検証する。"""

    def setUp(self) -> None:
        """非同期メソッドを持つ偽クライアントとセッションを用意する。"""

        settings = load_settings(ROOT / "config/settings.copilot.json").llm
        self.backend = CopilotChatBackend(settings, ("面", "胴"))
        self.client = Mock()
        self.client.start = AsyncMock()
        self.client.stop = AsyncMock()
        self.client.force_stop = AsyncMock()
        self.client.delete_session = AsyncMock()
        self.client.get_auth_status = AsyncMock(return_value=SimpleNamespace(isAuthenticated=True))
        self.client.list_models = AsyncMock(return_value=[SimpleNamespace(id="auto"), SimpleNamespace(id="test")])
        self.sessions = []

        async def create_session(**kwargs):
            """新しい会話を作るたび、別の偽セッションを返す。"""

            session = Mock(session_id=kwargs["session_id"])
            session.send_and_wait = AsyncMock(return_value=SimpleNamespace(data=SimpleNamespace(content="よい心意気でござる！")))
            session.abort = AsyncMock()
            session.disconnect = AsyncMock()
            self.sessions.append(session)
            return session

        self.client.create_session = AsyncMock(side_effect=create_session)
        self.backend._client = self.client
        self.backend._directory = Mock(name="temporary_directory")
        self.backend._directory.name = "test-empty-working-directory"
        self.addCleanup(self.backend.close)

    def test_factory_and_legacy_settings(self) -> None:
        """省略時はOllamaで、Copilot選択時には別バックエンドが作られる。"""

        local = load_settings(ROOT / "config/settings.json").llm
        self.assertEqual(local.provider, "ollama")
        self.assertIs(type(create_chat_backend(local, ())), ChatBackend)
        cloud = create_chat_backend(self.backend.settings, ())
        self.assertIsInstance(cloud, CopilotChatBackend)
        cloud.close()

    def test_all_examples_order_isolation_and_disabled_tools(self) -> None:
        """例の全件・順序・フェーズ独立性と、ツール無効化を検証する。"""

        examples = tuple(ConversationExample(f"作戦{i}", f"応援{i}") for i in range(30))
        self.backend.generate(ConversationPhase.STRATEGY, "作戦発話", examples)
        self.backend.generate(ConversationPhase.FEEDBACK, "感想発話", ())
        first = json.loads(self.sessions[0].send_and_wait.call_args.args[0])
        second = json.loads(self.sessions[1].send_and_wait.call_args.args[0])
        self.assertEqual(len(first), 61)
        self.assertEqual(first[0]["content"], "作戦0")
        self.assertEqual(first[-2]["content"], "応援29")
        self.assertEqual(first[-1]["content"], "作戦発話")
        self.assertEqual(second, [{"role": "user", "content": "感想発話"}])
        calls = self.client.create_session.call_args_list
        self.assertNotEqual(calls[0].kwargs["session_id"], calls[1].kwargs["session_id"])
        for call in calls:
            self.assertEqual(call.kwargs["available_tools"], [])
            self.assertFalse(call.kwargs["enable_config_discovery"])
            self.assertTrue(call.kwargs["skip_custom_instructions"])
            self.assertFalse(call.kwargs["enable_session_store"])
        self.assertEqual(self.client.delete_session.await_count, 2)
        self.assertEqual(self.backend._session_ids, set())

    def test_authentication_and_model_errors(self) -> None:
        """未ログインと未知モデルを起動時に日本語エラーへ変換する。"""

        self.client.get_auth_status.return_value = SimpleNamespace(isAuthenticated=False)
        with self.assertRaisesRegex(LLMError, "copilot login"):
            self.backend.check_model()
        self.client.get_auth_status.return_value = SimpleNamespace(isAuthenticated=True)
        self.backend.settings = replace(self.backend.settings, model="unknown")
        with self.assertRaisesRegex(LLMError, "利用可能モデル"):
            self.backend.check_model()

    def test_service_error_categories(self) -> None:
        """認証・利用枠・通信エラーの案内を確認し、生エラーを表示しない。"""

        for error, expected in [("401 token", "認証"), ("429 quota", "利用枠"), ("network offline", "ネットワーク")]:
            with self.subTest(error=error):
                self.client.get_auth_status.side_effect = RuntimeError(error)
                with self.assertRaisesRegex(LLMError, expected):
                    self.backend.check_model()

    def test_empty_response_is_error_and_session_is_deleted(self) -> None:
        """空応答を成功扱いせず、失敗した会話も削除する。"""

        original = self.client.create_session.side_effect

        async def empty_session(**kwargs):
            """空応答を返すセッションを作る。"""

            session = await original(**kwargs)
            session.send_and_wait.return_value = None
            return session

        self.client.create_session.side_effect = empty_session
        with self.assertRaisesRegex(LLMError, "空の返答"):
            self.backend.generate(ConversationPhase.STRATEGY, "発話", ())
        self.sessions[0].abort.assert_awaited_once()
        self.client.delete_session.assert_awaited_once()

    def test_timeout_aborts_and_deletes_session(self) -> None:
        """期限切れで実際の生成中断とセッション削除が行われる。"""

        original = self.client.create_session.side_effect

        async def stalled_session(**kwargs):
            """タイムアウトする非同期メソッドを設定する。"""

            session = await original(**kwargs)

            async def stall(*args, **options):
                """キャンセルされるまで応答を待つ。"""

                await asyncio.sleep(60)

            session.send_and_wait.side_effect = stall
            return session

        self.backend.settings = replace(self.backend.settings, timeout_seconds=0.02)
        self.client.create_session.side_effect = stalled_session
        with self.assertRaisesRegex(LLMError, "タイムアウト"):
            self.backend.generate(ConversationPhase.STRATEGY, "発話", ())
        self.sessions[0].abort.assert_awaited_once()
        self.sessions[0].disconnect.assert_awaited_once()
        self.client.delete_session.assert_awaited_once()

    def test_long_response_shortens_once_then_clips(self) -> None:
        """短縮を一度だけ追加依頼し、それでも長ければ文字数内へ収める。"""

        original = self.client.create_session.side_effect

        async def long_session(**kwargs):
            """上限を超える応答を返す。"""

            session = await original(**kwargs)
            session.send_and_wait.return_value.data.content = "あ" * 120
            return session

        self.client.create_session.side_effect = long_session
        response = self.backend.generate(ConversationPhase.STRATEGY, "発話", ())
        self.assertEqual(len(response), 80)
        self.assertEqual(len(self.sessions), 2)

    def test_confirmation_gate_in_both_phases(self) -> None:
        """Enter確定前の待機と両フェーズのqで、Copilot送信が起きない。"""

        examples = load_conversation_examples(ROOT / "data/conversation_examples.json")
        for feedback in (False, True):
            with self.subTest(feedback=feedback):
                self.client.create_session.reset_mock()
                # strategy開始・確定・待機・feedback開始・q、またはstrategy開始・q。
                keys = iter(["", "", "", "", "q"] if feedback else ["", "x", "q"])
                recorder = Mock()
                recorder.record_until_silence.return_value = AudioData([0.1], 16000, True, "silence")
                recognizer = Mock()
                recognizer.transcribe.return_value = TranscriptionResult("発話", 0.1, "fake")

                def read_key(prompt):
                    """確認画面の入力時点では、そのフェーズは未送信であることを確認する。"""

                    if prompt.startswith("Enter: この内容で確定"):
                        self.assertEqual(self.client.create_session.call_count, 1 if feedback and controller.state.name.startswith("FEEDBACK") else 0)
                    return next(keys)

                controller = ConversationController(recorder=recorder, recognizer=recognizer, chat_backend=self.backend, synthesizer=Mock(), examples=examples, strategy_question="作戦", feedback_question="感想", input_function=read_key, output=lambda text: None)
                self.assertFalse(controller.run())
                self.assertEqual(self.client.create_session.call_count, 1 if feedback else 0)

    def test_close_stops_once_and_cleans_working_directory(self) -> None:
        """終了処理を複数回呼んでも、停止と一時フォルダ削除は1回だけ。"""

        directory = self.backend._directory
        self.backend.close()
        self.backend.close()
        self.client.stop.assert_awaited_once()
        directory.cleanup.assert_called_once()

    def test_stop_failure_uses_force_stop(self) -> None:
        """正常停止が失敗した場合も、SDKの強制停止を呼ぶ。"""

        self.client.stop.side_effect = RuntimeError("process failure")
        self.backend.close()
        self.client.force_stop.assert_awaited_once()

    def test_permission_requests_are_rejected(self) -> None:
        """SDKが任意依存でも、権限ハンドラーが拒否を返すことを検証する。"""

        decision = SimpleNamespace(kind="reject")
        constructor = Mock(return_value=decision)
        module = SimpleNamespace(PermissionDecisionReject=constructor)
        with patch.dict("sys.modules", {"copilot.generated.rpc": module}):
            result = self.backend._deny_permission(None, None)
        self.assertEqual(result.kind, "reject")
        constructor.assert_called_once()

    def test_delete_failure_is_retried_on_close(self) -> None:
        """応答後に削除できなかったセッションは終了処理で再試行する。"""

        self.client.delete_session.side_effect = [RuntimeError("offline"), None]
        with patch("builtins.print"):
            self.backend.generate(ConversationPhase.STRATEGY, "発話", ())
        self.backend.close()
        self.assertEqual(self.client.delete_session.await_count, 2)
        self.assertEqual(self.backend._session_ids, set())


if __name__ == "__main__":
    unittest.main()
