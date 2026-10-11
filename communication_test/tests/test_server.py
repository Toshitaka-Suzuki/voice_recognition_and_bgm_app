"""実際にローカル接続し、画面を使わずサーバーの送受信を確認します。"""
import asyncio
import json
import contextlib
import io
import unittest

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from server import handle_connection, parse_args, main, handle_key, CLIENTS


class ArgumentTests(unittest.TestCase):
    def test_defaults_and_custom_address(self):
        args = parse_args([])
        self.assertEqual((args.host, args.port), ("127.0.0.1", 8765))
        args = parse_args(["--host", "192.168.10.10", "--port", "9001"])
        self.assertEqual((args.host, args.port), ("192.168.10.10", 9001))
        for port in [1, 65535]:
            self.assertEqual(parse_args(["--port", str(port)]).port, port)

    def test_invalid_ports(self):
        for port in ["0", "65536", "-1", "abc", "1.5"]:
            with self.subTest(port=port), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parse_args(["--port", port])
                self.assertEqual(caught.exception.code, 2)


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

    async def test_all_signals_in_arbitrary_order_and_repeated(self):
        async with connect(self.url) as connection:
            await self.receive(connection)
            # 戦闘終了から始め、パリイを繰り返しても受付されることを確認します。
            for name in ["battle_end", "parry", "parry", "battle_start", "game_start"]:
                await connection.send(json.dumps({"type": "signal", "name": name}))
                reply = await self.receive(connection)
                self.assertEqual((reply["type"], reply["name"]), ("received", name))
            for count in [3, 0, 8, 9007199254740991]:
                await connection.send(json.dumps({"type": "signal", "name": "weak_point_count", "count": count}))
                reply = await self.receive(connection)
                self.assertEqual(reply["count"], count)
                self.assertEqual(reply["type"], "received")

    async def test_invalid_signals_and_counts_then_valid_signal(self):
        async with connect(self.url) as connection:
            await self.receive(connection)
            invalid = [{"type": "signal", "name": name} for name in ["other", [], None]]
            invalid += [{"type": "signal", "name": "weak_point_count", "count": count}
                        for count in [-1, 1.5, True, "3", None, 9007199254740992]]
            invalid += [{"type": "signal", "name": "weak_point_count"}]
            for message in invalid:
                await connection.send(json.dumps(message))
                self.assertEqual((await self.receive(connection))["type"], "error")
            await connection.send('{"type":"signal","name":"parry"}')
            self.assertEqual((await self.receive(connection))["type"], "received")

    async def test_all_outputs_broadcast_without_input(self):
        async with connect(self.url) as first, connect(self.url) as second:
            await self.receive(first)
            await self.receive(second)
            expected = [
                ("5", "status", "post_interview_finished"),
                ("1", "status", "standby"), ("3", "status", "pre_interview_finished"),
                ("2", "status", "pre_interview"), ("4", "status", "post_interview"),
                ("7", "parry_result", "failure"), ("6", "parry_result", "success"),
                ("6", "parry_result", "success"),
            ]
            for key, kind, value in expected:
                self.assertTrue(await handle_key(key, CLIENTS))
                for connection in [first, second]:
                    self.assertEqual(await self.receive(connection), {"type": kind, "value": value})
            await second.close()
            await asyncio.sleep(0)  # 切断の処理に実行を譲ります。
            await handle_key("1", CLIENTS)
            self.assertEqual((await self.receive(first))["value"], "standby")

    async def test_no_saved_output_on_connect_or_reconnect(self):
        await handle_key("6", CLIENTS)  # 未接続で押したキーは保存されません。
        for _ in range(2):
            async with connect(self.url) as connection:
                self.assertEqual((await self.receive(connection))["type"], "connected")
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(connection.recv(), 0.1)
                await handle_key("7", CLIENTS)
                self.assertEqual((await self.receive(connection))["value"], "failure")

    async def test_keyboard_help_and_quit(self):
        # キーの制御結果を確認します。実際のWindowsコンソールは手動確認の対象です。
        self.assertTrue(await handle_key("H", CLIENTS))
        self.assertTrue(await handle_key("x", CLIENTS))
        self.assertFalse(await handle_key("Q", CLIENTS))

    async def test_main_uses_specified_address_and_port(self):
        # 既存テストの空きポートを解放し、実際の起動関数へ引き渡します。
        port = self.server.sockets[0].getsockname()[1]
        self.server.close()
        await self.server.wait_closed()
        task = asyncio.create_task(main("127.0.0.1", port))
        try:
            async with asyncio.timeout(3):
                while True:
                    if task.done():
                        await task  # 起動失敗をそのままテストの失敗として扱います。
                    try:
                        connection = await connect(self.url, open_timeout=1)
                        break
                    except OSError:
                        await asyncio.sleep(0.02)
                async with connection:
                    self.assertEqual((await self.receive(connection))["type"], "connected")
                    await connection.send('{"type":"message","text":"指定ポート"}')
                    self.assertEqual((await self.receive(connection))["type"], "reply")
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    unittest.main()
