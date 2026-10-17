"""単一の外部統合アプリを受け付けるWebSocketサーバー。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from time import perf_counter
from typing import Any

from .protocol import ProtocolError, error_message, parse_signal


def print_log(message: str) -> None:
    """既定の通信ログを即時表示する。"""

    print(message, flush=True)


def log(direction: str, content: str, output: Callable[[str], None] = print_log) -> None:
    """通信方向と時刻を表示する。"""

    output(f"[{datetime.now():%H:%M:%S}] {direction} {content}")


async def run_server(
    controller: Any,
    host: str,
    port: int,
    log_output: Callable[[str], None] = print_log,
) -> None:
    """WebSocketを待ち受け、Ctrl+Cまたは内部終了まで処理を続ける。"""

    # 依存がない状態でも状態機械の単体テストを読めるよう、起動時に読み込みます。
    from websockets.asyncio.server import serve
    from websockets.exceptions import ConnectionClosed

    active_connection = None

    async def handle_connection(connection) -> None:  # noqa: ANN001
        nonlocal active_connection
        if active_connection is not None:
            message = error_message(
                "制御接続は1つだけです。先に接続した外部統合アプリを切断してください。",
                controller.state.value,
            )
            await connection.send(json.dumps(message, ensure_ascii=False))
            await connection.close(code=1008, reason="single control connection")
            return

        active_connection = connection

        async def send(message: dict[str, object]) -> None:
            text = json.dumps(message, ensure_ascii=False)
            await connection.send(text)
            log(f"送信 → 外部アプリ {connection.remote_address}", text, log_output)

        try:
            log("接続", str(connection.remote_address), log_output)
            await controller.connect(send)
            async for raw in connection:
                received_at = perf_counter()
                log(f"受信 ← 外部アプリ {connection.remote_address}", str(raw), log_output)
                try:
                    signal = parse_signal(raw)
                except ProtocolError as exc:
                    await send(error_message(str(exc), controller.state.value))
                    continue
                await controller.handle(signal, received_at)
        except ConnectionClosed:
            pass
        finally:
            if active_connection is connection:
                active_connection = None
                await controller.disconnect(send)
            log("接続終了", str(connection.remote_address), log_output)

    try:
        async with serve(handle_connection, host, port):
            log("待ち受け", f"ws://{host}:{port}（終了：Ctrl+C）", log_output)
            await controller.shutdown_event.wait()
    finally:
        await controller.close()
