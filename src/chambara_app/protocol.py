"""外部統合アプリと交換するWebSocket JSONを検証・作成する。"""

from __future__ import annotations

import json
from dataclasses import dataclass

MAX_SAFE_INTEGER = 9_007_199_254_740_991
DIFFICULTIES = ("easy", "normal", "hard")
PARRY_SOURCES = ("python", "esp_legacy")
SIGNAL_LABELS = {
    "game_start": "ゲームスタート",
    "battle_start": "戦闘開始",
    "parry": "パリイ",
    "weak_point_count": "弱点残り回数",
    "battle_end": "戦闘終了",
    "game_cancel": "ゲーム中断",
    "heartbeat": "接続確認",
    "rhythm_sync": "音楽位置確認",
    "defense_ready": "受けの準備完了",
    "defense_pause": "受けの予告停止",
}


class ProtocolError(ValueError):
    """受信JSONが通信仕様に合わない場合のエラー。"""


@dataclass(frozen=True)
class Signal:
    """検証済みの入力信号。省略時はeasy・Pythonによるパリィ判定。"""

    name: str
    count: int | None = None
    request_id: str | None = None
    difficulty: str = "easy"
    parry_source: str = "python"


def parse_signal(raw: str | bytes) -> Signal:
    """生のWebSocketメッセージを検証済み信号へ変換する。"""

    if not isinstance(raw, str):
        raise ProtocolError("文字列のJSONを送ってください。")
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("JSONの形式が正しくありません。") from exc
    if not isinstance(message, dict):
        raise ProtocolError("JSONオブジェクトを送ってください。")
    name = message.get("name")
    if message.get("type") != "signal" or not isinstance(name, str) or name not in SIGNAL_LABELS:
        raise ProtocolError("メッセージ形式または信号名が正しくありません。")
    request_id = message.get("request_id")
    if request_id is not None and (
        not isinstance(request_id, str) or not 1 <= len(request_id) <= 64
    ):
        raise ProtocolError("request_idは1〜64文字の文字列で指定してください。")
    if name == "game_start":
        difficulty = message.get("difficulty", "easy")
        if not isinstance(difficulty, str) or difficulty not in DIFFICULTIES:
            raise ProtocolError("difficultyはeasy、normal、hardのいずれかを指定してください。")
        parry_source = message.get("parry_source", "python")
        if not isinstance(parry_source, str) or parry_source not in PARRY_SOURCES:
            raise ProtocolError("parry_sourceはpythonまたはesp_legacyを指定してください。")
        return Signal(name, request_id=request_id, difficulty=difficulty, parry_source=parry_source)
    if name != "weak_point_count":
        return Signal(name, request_id=request_id)
    count = message.get("count")
    if type(count) is not int or not 0 <= count <= MAX_SAFE_INTEGER:
        raise ProtocolError(f"残り回数は0〜{MAX_SAFE_INTEGER}の整数で指定してください。")
    return Signal(name, count, request_id)


def received(signal: Signal) -> dict[str, object]:
    """有効な入力信号へ返す受付通知を作る。"""

    if signal.name == "weak_point_count":
        return {
            "type": "received",
            "name": signal.name,
            "text": f"弱点残り回数 {signal.count} を受信しました。",
            "count": signal.count,
            **({"request_id": signal.request_id} if signal.request_id is not None else {}),
        }
    return {
        "type": "received",
        "name": signal.name,
        "text": f"{SIGNAL_LABELS[signal.name]}を受信しました。",
        **({"request_id": signal.request_id} if signal.request_id is not None else {}),
    }


def error_message(text: str, state: str) -> dict[str, str]:
    """接続を維持したまま返す、現在状態付きエラーを作る。"""

    return {"type": "error", "text": text, "state": state}
