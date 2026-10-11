"""WebSocketでJSONを送受信する学習用サーバー。"""

# [P1] 準備：asyncioは待ち時間を他の処理に譲る仕組み、jsonは文字列変換用です。
import argparse
import asyncio
import json
import sys
from datetime import datetime

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

HOST = "127.0.0.1"  # 同じPCからだけ接続できます。
PORT = 8765

# 信号名と表示名を一か所にまとめ、送受信で同じ意味を使います。
SIGNALS = {
    "game_start": "ゲームスタート", "battle_start": "戦闘開始",
    "parry": "パリイ", "weak_point_count": "弱点残り回数", "battle_end": "戦闘終了",
}
KEY_MESSAGES = {
    "1": {"type": "status", "value": "standby"},
    "2": {"type": "status", "value": "pre_interview"},
    "3": {"type": "status", "value": "pre_interview_finished"},
    "4": {"type": "status", "value": "post_interview"},
    "5": {"type": "status", "value": "post_interview_finished"},
    "6": {"type": "parry_result", "value": "success"},
    "7": {"type": "parry_result", "value": "failure"},
}
# 同じPC内のテストと従来のhandle_connection呼び出しで使う接続一覧です。
CLIENTS = set()


# [P6] 起動引数：コードを書き換えずに、待ち受け先を選べるようにします。
def parse_args(argv=None):
    def port_number(value):
        try:
            port = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("ポートは1〜65535の整数で指定してください。")
        if not 1 <= port <= 65535:
            raise argparse.ArgumentTypeError("ポートは1〜65535の整数で指定してください。")
        return port

    parser = argparse.ArgumentParser(description="JSON送受信用WebSocketサーバー")
    parser.add_argument("--host", default=HOST, help="待ち受けIPアドレス（既定：127.0.0.1）")
    parser.add_argument("--port", type=port_number, default=PORT, help="待ち受けポート（既定：8765）")
    return parser.parse_args(argv)


def log(direction, content):
    """方向と時刻を表示し、通信の順番を追いやすくします。"""
    print(f"[{datetime.now():%H:%M:%S}] {direction} {content}", flush=True)


# [P2] Pythonの送信：辞書をJSON文字列にして、接続相手へ送ります。
async def send_json(connection, message):
    # ensure_ascii=Falseにすると、日本語がログでもそのまま読めます。
    text = json.dumps(message, ensure_ascii=False)
    # awaitは送信完了を待ちます。その間、他の接続などの処理も進められます。
    await connection.send(text)
    log(f"送信 → ブラウザー {connection.remote_address}", text)


# [P3] JSONの解析：受信データの型を確認してから、返信内容を作ります。
def make_reply(raw):
    if not isinstance(raw, str):
        return {"type": "error", "text": "文字列のJSONを送ってください。"}
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return {"type": "error", "text": "JSONの形式が正しくありません。"}
    if not isinstance(message, dict):
        return {"type": "error", "text": "JSONオブジェクトを送ってください。"}
    if message.get("type") == "message" and isinstance(message.get("text"), str):
        return {"type": "reply", "text": f"受信しました：{message['text']}"}
    # [P7] 信号の受付：ゲームの順序は判定せず、信号名と値だけを検証します。
    name = message.get("name")
    if message.get("type") != "signal" or not isinstance(name, str) or name not in SIGNALS:
        return {"type": "error", "text": "メッセージ形式または信号名が正しくありません。"}
    reply = {"type": "received", "name": name, "text": f"{SIGNALS[name]}を受信しました。"}
    if name == "weak_point_count":
        count = message.get("count")
        # boolもPythonでは整数の仲間なので、typeで明示的に除外します。
        # JavaScriptで正確に扱える整数の範囲を、両側で共通にします。
        if type(count) is not int or not 0 <= count <= 9007199254740991:
            return {"type": "error", "text": "残り回数は0〜9007199254740991の整数で指定してください。"}
        reply["count"] = count
        reply["text"] = f"弱点残り回数 {count} を受信しました。"
    return reply


# [P4] Pythonの受信：この関数は、ブラウザーが接続するたびに呼ばれます。
async def handle_connection(connection, clients=None):
    if clients is None:
        clients = CLIENTS
    log("接続", str(connection.remote_address))
    try:
        # 入力を待たずに送信します。サーバーからも通知を始められる例です。
        await send_json(connection, {"type": "connected", "text": "接続しました"})
        # 接続通知の送信後に登録し、最初に届くJSONが必ず接続通知になるようにします。
        clients.add(connection)
        # async forは次のメッセージを待ち、届くたびに中の処理を実行します。
        # 通常の切断ではループが終了します。接続中に何回も送信できます。
        async for raw in connection:
            log(f"受信 ← ブラウザー {connection.remote_address}", raw)
            reply = make_reply(raw)
            await send_json(connection, reply)
    except ConnectionClosed:
        # タブを閉じるなどの切断も、サーバー全体の終了にはしません。
        log("切断", "通信が終了しました。")
    finally:
        clients.discard(connection)
        log("接続終了", str(connection.remote_address))


def print_help():
    log("操作", "1:スタンバイ / 2:開始前インタビュー / 3:開始前インタビュー終了")
    log("操作", "4:終了インタビュー / 5:終了インタビュー終了 / 6:パリイ成功 / 7:パリイ失敗")
    log("操作", "H:操作一覧 / Q:終了（Enter不要。任意の順序で繰り返し送信できます）")


# [P8] キーからの送信：受信処理とは独立して、全接続へ同じ信号を送ります。
async def handle_key(key, clients):
    key = key.lower()
    if key == "q":
        return False  # 呼び出し元へ「終了」を伝え、接続を片付けてもらいます。
    if key == "h":
        print_help()
        return True
    message = KEY_MESSAGES.get(key)
    if message is None:
        log("操作", f"未対応のキーです：{key!r}（Hで操作一覧）")
        return True
    if not clients:
        log("未送信", "接続中のブラウザーがありません。信号は保存しません。")
        return True

    async def send_one(connection):
        try:
            await send_json(connection, message)
        except ConnectionClosed:
            # 送信中に1つの接続が切れても、ほかの接続への送信は続けます。
            clients.discard(connection)
            log("未送信", f"接続が切れました：{connection.remote_address}")

    # 接続一覧のコピーを使い、送信中の接続・切断で反復処理を壊さないようにします。
    await asyncio.gather(*(send_one(connection) for connection in tuple(clients)))
    return True


# [P9] Windowsのキー入力：入力があるときだけ読み、通信を止めないようにします。
class WindowsKeyboard:
    def __init__(self):
        import msvcrt
        self.console = msvcrt
        self.special_key = False

    def read(self):
        if not self.console.kbhit():
            return None
        key = self.console.getwch()
        # 矢印キーなどは2文字で届きます。信号キーと誤認しないよう両方を無視します。
        if self.special_key:
            self.special_key = False
            return None
        if key in ("\x00", "\xe0"):
            self.special_key = True
            return None
        return key


async def keyboard_loop(read_key, clients):
    print_help()
    while True:
        key = read_key()
        if key is not None and not await handle_key(key, clients):
            break
        # 入力がなくても、50msごとに通信処理へ実行を譲ります。
        await asyncio.sleep(0.05)


# [P5] 起動と終了：待ち受けを開始し、Ctrl+Cまでサーバーを動かします。
async def main(host=HOST, port=PORT, read_key=None):
    clients = set()  # 起動ごとに接続一覧を作り、以前の接続や信号を持ち越しません。
    # async withを抜けると、ライブラリが接続と待ち受けを閉じます。
    async with serve(lambda connection: handle_connection(connection, clients), host, port) as server:
        log("待ち受け", f"ws://{host}:{port}（終了：Ctrl+C）")
        if read_key is None:
            await server.serve_forever()
        else:
            # WebSocketはイベントループ内で受信を続け、キー処理も同時に動きます。
            await keyboard_loop(read_key, clients)


if __name__ == "__main__":
    # importされた場合には起動せず、このファイルを直接実行した場合だけ起動します。
    args = parse_args()
    try:
        if sys.platform == "win32" and sys.stdin.isatty():
            reader = WindowsKeyboard().read
        else:
            log("案内", "キー操作にはWindows TerminalのPowerShellを使用してください。受信は継続します。")
            reader = None
        asyncio.run(main(args.host, args.port, reader))
        log("終了", "サーバーを停止しました。")
    except KeyboardInterrupt:
        log("終了", "サーバーを停止しました。")
    except OSError as error:
        log("起動失敗", f"待ち受けIP {args.host} とポート{args.port}を確認してください：{error}")
        raise SystemExit(1)
