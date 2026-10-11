"""実際にローカル接続し、画面を使わずサーバーの送受信を確認します。"""
import asyncio
import json
import unittest

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from server import handle_connection


class CommunicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # port=0で空きポートを選び、通常起動の8765と衝突しないようにします。
        self.server = await serve(handle_connection, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}"

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def receive(self, connection):
        # 不具合があっても無期限に待たず、3秒でテストを失敗させます。
        return json.loads(await asyncio.wait_for(connection.recv(), timeout=3))

    async def test_connection_notification_and_japanese_round_trip(self):
        async with connect(self.url) as connection:
            self.assertEqual(await self.receive(connection),
                             {"type": "connected", "text": "接続しました"})
            for text in ["こんにちは", "もう一度", "<script>alert(1)</script>", ""]:
                await connection.send(json.dumps({"type": "message", "text": text}, ensure_ascii=False))
                self.assertEqual(await self.receive(connection),
                                 {"type": "reply", "text": "受信しました：" + text})

    async def test_invalid_messages_do_not_end_connection(self):
        async with connect(self.url) as connection:
            await self.receive(connection)
            for invalid in ['not JSON', 'null', '[]', '42', '{}',
                            '{"type":"other","text":"test"}',
                            '{"type":"message","text":1}', b'binary']:
                await connection.send(invalid)
                reply = await self.receive(connection)
                self.assertEqual(reply["type"], "error")
                self.assertIsInstance(reply["text"], str)
            await connection.send('{"type":"message","text":"回復"}')
            self.assertEqual((await self.receive(connection))["text"], "受信しました：回復")

    async def test_new_connection_after_disconnect(self):
        for _ in range(2):
            async with connect(self.url) as connection:
                self.assertEqual((await self.receive(connection))["type"], "connected")
                await connection.send('{"type":"message","text":"再接続"}')
                self.assertEqual((await self.receive(connection))["type"], "reply")


if __name__ == "__main__":
    unittest.main()
