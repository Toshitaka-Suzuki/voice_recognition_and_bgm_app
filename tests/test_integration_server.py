"""通常のWindows環境で実際のローカルWebSocket接続を確認する。"""

import asyncio
import json
import os
import socket
import unittest

from chambara_app.orchestrator import IntegratedController
from chambara_app.server import run_server
from tests.test_integration import FakeConversation, FakeGame

try:
    from websockets.asyncio.client import connect
except ImportError:  # 依存導入前は、ほかの単体テストを継続します。
    connect = None


def unused_port() -> int:
    """テスト用に現在空いているローカルTCPポートを選ぶ。"""

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipIf(connect is None, "websocketsをインストールすると実通信テストを実行できます。")
@unittest.skipIf(
    os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED") == "1"
    and os.environ.get("CHAMBARA_FORCE_ASYNC_TESTS") != "1",
    "CodexのWindowsサンドボックスではローカルsocket通信を開始できません。",
)
class WebSocketIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """接続通知、単一接続制限、信号から状態出力までを実通信で確認する。"""

    async def test_single_client_and_game_start_sequence(self) -> None:
        controller = IntegratedController(FakeConversation(), FakeGame(), output=lambda text: None)
        port = unused_port()
        logs = []
        server_task = asyncio.create_task(
            run_server(controller, "127.0.0.1", port, log_output=logs.append)
        )

        async def open_connection():
            async with asyncio.timeout(3):
                while True:
                    if server_task.done():
                        await server_task
                    try:
                        return await connect(f"ws://127.0.0.1:{port}", open_timeout=1)
                    except OSError:
                        await asyncio.sleep(0.02)

        async def receive(connection):  # noqa: ANN001
            return json.loads(await asyncio.wait_for(connection.recv(), 3))

        try:
            first = await open_connection()
            async with first:
                self.assertEqual((await receive(first))["type"], "connected")
                self.assertEqual(await receive(first), {"type": "status", "value": "standby"})

                second = await open_connection()
                async with second:
                    rejection = await receive(second)
                    self.assertEqual(rejection["type"], "error")
                    await second.wait_closed()
                    self.assertEqual(second.close_code, 1008)

                await first.send(json.dumps({"type": "signal", "name": "game_start"}))
                self.assertEqual((await receive(first))["type"], "received")
                self.assertEqual(await receive(first), {"type": "status", "value": "pre_interview"})
                self.assertEqual(
                    await receive(first),
                    {"type": "status", "value": "pre_interview_finished"},
                )
        finally:
            controller.shutdown_event.set()
            await asyncio.wait_for(server_task, 3)

        self.assertTrue(any("待ち受け" in line for line in logs))
        self.assertTrue(any("受信 ← 外部アプリ" in line for line in logs))
        self.assertTrue(any("送信 → 外部アプリ" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
