"""WebSocketでJSONを送受信する学習用サーバー。"""

# [P1] 準備：asyncioは待ち時間を他の処理に譲る仕組み、jsonは文字列変換用です。
import asyncio
import json
from datetime import datetime

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

HOST = "127.0.0.1"  # 同じPCからだけ接続できます。
PORT = 8765


def log(direction, content):
    """方向と時刻を表示し、通信の順番を追いやすくします。"""
    print(f"[{datetime.now():%H:%M:%S}] {direction} {content}", flush=True)


# [P2] Pythonの送信：辞書をJSON文字列にして、接続相手へ送ります。
async def send_json(connection, message):
    # ensure_ascii=Falseにすると、日本語がログでもそのまま読めます。
    text = json.dumps(message, ensure_ascii=False)
    # awaitは送信完了を待ちます。その間、他の接続などの処理も進められます。
    await connection.send(text)
    log("送信 → ブラウザー", text)


# [P3] JSONの解析：受信データの型を確認してから、返信内容を作ります。
def make_reply(raw):
    if not isinstance(raw, str):
        return {"type": "error", "text": "文字列のJSONを送ってください。"}
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return {"type": "error", "text": "JSONの形式が正しくありません。"}
    # 配列や数値ではなくオブジェクトで、typeとtextが正しいことを確認します。
    if (not isinstance(message, dict)
            or message.get("type") != "message"
            or not isinstance(message.get("text"), str)):
        return {"type": "error", "text": 'typeが"message"、textが文字列のJSONを送ってください。'}
    return {"type": "reply", "text": f"受信しました：{message['text']}"}


# [P4] Pythonの受信：この関数は、ブラウザーが接続するたびに呼ばれます。
async def handle_connection(connection):
    log("接続", str(connection.remote_address))
    try:
        # 入力を待たずに送信します。サーバーからも通知を始められる例です。
        await send_json(connection, {"type": "connected", "text": "接続しました"})
        # async forは次のメッセージを待ち、届くたびに中の処理を実行します。
        # 通常の切断ではループが終了します。接続中に何回も送信できます。
        async for raw in connection:
            log("受信 ← ブラウザー", raw)
            reply = make_reply(raw)
            await send_json(connection, reply)
    except ConnectionClosed:
        # タブを閉じるなどの切断も、サーバー全体の終了にはしません。
        log("切断", "通信が終了しました。")
    finally:
        log("接続終了", str(connection.remote_address))


# [P5] 起動と終了：待ち受けを開始し、Ctrl+Cまでサーバーを動かします。
async def main():
    # async withを抜けると、ライブラリが接続と待ち受けを閉じます。
    async with serve(handle_connection, HOST, PORT) as server:
        log("待ち受け", f"ws://{HOST}:{PORT}（終了：Ctrl+C）")
        await server.serve_forever()


if __name__ == "__main__":
    # importされた場合には起動せず、このファイルを直接実行した場合だけ起動します。
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("終了", "サーバーを停止しました。")
    except OSError as error:
        log("起動失敗", f"ポート{PORT}の使用状況を確認してください：{error}")
        raise SystemExit(1)
