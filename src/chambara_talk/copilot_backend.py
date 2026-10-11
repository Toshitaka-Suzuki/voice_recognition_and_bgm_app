"""公式Copilot SDKを同期会話アプリから使うための接続アダプター。

ChatBackendからプロンプトと文字数調整を継承し、通信部分だけを置き換えます。
SDKは非同期ですが、専用Runner内で動かすためControllerの書き方は同じです。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from collections.abc import Sequence
from typing import Any

from .config import LLMSettings
from .errors import LLMError
from .llm import ChatBackend


class CopilotChatBackend(ChatBackend):
    """Copilotの新規セッションでコメントを生成し、使用後に削除する。"""

    def __init__(self, settings: LLMSettings, domain_terms: Sequence[str]) -> None:
        """設定を保持する。SDKとランタイムは必要になるまで起動しない。"""

        super().__init__(settings, domain_terms)
        self._runner = asyncio.Runner()
        self._directory: tempfile.TemporaryDirectory | None = None
        self._session_ids: set[str] = set()
        self._closed = False

    @staticmethod
    def _deny_permission(request: Any, invocation: Any) -> Any:
        """ツールの権限要求はすべて拒否する（待機状態にしない）。"""

        from copilot.generated.rpc import PermissionDecisionReject

        return PermissionDecisionReject(feedback="このアプリは文章生成のみを許可します。")

    async def _ensure_client(self) -> Any:
        """標準ログインを使い、SDKに互換ランタイムを管理させる。"""

        if self._client is None:
            try:
                from copilot import CopilotClient
            except ImportError as exc:
                raise LLMError('Copilot SDKがありません。python -m pip install -e ".[copilot]" を実行してください。') from exc

            # 空の作業フォルダを使い、プロジェクト内のファイルを参照させません。
            # 認証は通常のCopilotログインを再利用するため、COPILOT_HOMEは変更しません。
            self._directory = tempfile.TemporaryDirectory(prefix="chambara-copilot-")
            self._client = CopilotClient(
                working_directory=self._directory.name,
                log_level="none",
                use_logged_in_user=True,
                enable_remote_sessions=False,
            )
            await self._client.start()
        return self._client

    def _run(self, operation: Any) -> Any:
        """同じ専用イベントループでSDK操作を実行し、エラーを日本語へ変換する。"""

        if self._closed:
            operation.close()
            raise LLMError("Copilot接続は終了済みです。アプリを再起動してください。")
        try:
            return self._runner.run(asyncio.wait_for(operation, self.settings.timeout_seconds))
        except LLMError:
            raise
        except TimeoutError as exc:
            raise LLMError("Copilotの応答がタイムアウトしました。接続状態を確認して再録音してください。") from exc
        except Exception as exc:
            # サービスの生エラーには認識文や認証情報が混ざる可能性があるため、表示しません。
            detail = str(exc).casefold()
            if any(word in detail for word in ("401", "403", "auth", "login", "token")):
                message = "Copilotの認証または利用権限を確認できません。copilot login を実行し、契約・組織設定を確認してください。"
            elif any(word in detail for word in ("429", "quota", "rate limit", "credits", "billing")):
                message = "Copilotの利用枠またはレート制限に達しました。利用状況を確認し、時間をおいて再試行してください。"
            elif "model" in detail:
                message = "Copilotのモデルを利用できません。llm.modelをautoまたは利用可能なモデルへ変更してください。"
            else:
                message = "Copilotへ接続またはコメント生成できませんでした。ネットワーク、SDKランタイム、ログイン状態を確認してください。"
            raise LLMError(message) from exc

    async def _check_model(self) -> None:
        """ログインと利用可能モデルを確認する。会話文は送信しない。"""

        client = await self._ensure_client()
        auth = await client.get_auth_status()
        if not auth.isAuthenticated:
            raise LLMError("Copilotにログインしていません。Git Bashで copilot login を実行してください。")
        models = await client.list_models()
        names = {model.id for model in models}
        if not names:
            raise LLMError("Copilotの利用可能モデルがありません。契約・組織の利用許可を確認してください。")
        if self.settings.model != "auto" and self.settings.model not in names:
            raise LLMError("指定したCopilotモデルを利用できません。利用可能モデル: " + ", ".join(sorted(names)))

    def check_model(self) -> None:
        """同期APIとして起動前確認を行う。"""

        self._run(self._check_model())

    async def _release_session(self, session: Any) -> None:
        """自分が作ったセッションだけを切断・削除する。既存の会話は扱わない。"""

        try:
            await asyncio.wait_for(session.disconnect(), 5)
        finally:
            await asyncio.wait_for(self._client.delete_session(session.session_id), 5)
            self._session_ids.discard(session.session_id)

    async def _chat_async(self, messages: list[dict[str, str]]) -> str:
        """履歴のないセッションで生成し、失敗・キャンセル時にも中断と削除を行う。"""

        client = await self._ensure_client()
        session_id = str(uuid.uuid4())
        self._session_ids.add(session_id)
        session = None
        try:
            session = await client.create_session(
                session_id=session_id,
                model=self.settings.model,
                system_message={
                    "mode": "replace",
                    "content": messages[0]["content"] + "\n次の入力は会話例と今回の発話を表すJSONです。会話例は参考で、最後のuser発話だけにコメントしてください。",
                },
                available_tools=[],
                tools=[],
                on_permission_request=self._deny_permission,
                skip_custom_instructions=True,
                enable_config_discovery=False,
                enable_on_demand_instruction_discovery=False,
                enable_skills=False,
                enable_file_hooks=False,
                enable_host_git_operations=False,
                enable_session_store=False,
                enable_session_telemetry=False,
                enable_file_change_tracking=False,
                skip_embedding_retrieval=True,
                embedding_cache_storage="in-memory",
                mcp_servers={},
                memory={"enabled": False},
                infinite_sessions={"enabled": False},
                working_directory=self._directory.name,
                streaming=False,
            )
            # 全会話例はJSON配列として順序とroleを保ち、件数制限せず送ります。
            prompt = json.dumps(messages[1:], ensure_ascii=False)
            response = await session.send_and_wait(prompt, timeout=self.settings.timeout_seconds)
            content = getattr(getattr(response, "data", None), "content", None)
            if not isinstance(content, str) or not content.strip():
                raise LLMError("Copilotから空の返答が返されました。もう一度録音してください。")
            return content.strip()
        except BaseException:
            # タイムアウトやCtrl+Cで待機をやめるだけでは、サーバーの生成は止まりません。
            if session is not None:
                try:
                    await asyncio.wait_for(session.abort(), 5)
                except Exception:
                    pass  # 接続断でもfinallyの削除とcloseのプロセス停止へ進みます。
            raise
        finally:
            if session is not None:
                try:
                    await self._release_session(session)
                except Exception:
                    print("Copilotセッションの削除に失敗しました。終了処理で再試行します。")

    def _chat(self, messages: list[dict[str, str]]) -> str:
        """共通generate/短縮処理から呼ばれる同期の通信メソッド。"""

        return self._run(self._chat_async(messages))

    async def _shutdown(self) -> None:
        """削除できなかった所有セッションを再試行し、ランタイムを停止する。"""

        if self._client is None:
            return
        try:
            for session_id in tuple(self._session_ids):
                try:
                    await asyncio.wait_for(self._client.delete_session(session_id), 5)
                    self._session_ids.discard(session_id)
                except Exception:
                    print("Copilotの一時会話データが残る可能性があります。")
        finally:
            try:
                await asyncio.wait_for(self._client.stop(), 15)
            except Exception:
                await asyncio.wait_for(self._client.force_stop(), 5)

    def close(self) -> None:
        """正常終了・起動失敗・Ctrl+C後にプロセスと専用ループを解放する。"""

        if self._closed:
            return
        try:
            self._runner.run(self._shutdown())
        except Exception as exc:
            raise LLMError("Copilotランタイムの終了処理に失敗しました。") from exc
        finally:
            self._closed = True
            self._runner.close()
            if self._directory is not None:
                self._directory.cleanup()
            self._client = None
