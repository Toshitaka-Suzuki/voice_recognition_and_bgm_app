"""日本語コンソールでBGMと外部信号をデバッグする。"""
import argparse
import os
from pathlib import Path
from time import perf_counter, sleep

from .config import load_tracks
from .engine import Game
from .player import AudioPlayer

LABELS = {"easy": "簡単", "normal": "普通", "hard": "難しい"}


def show_event(event):
    labels = {"stage_changed": "曲切り替え", "parry_success": "パリイ成功",
              "parry_failure": "パリイ失敗", "life_decrement": "ライフ減少信号受付",
              "game_finished": "ゲーム終了"}
    position = "" if event.position_ms is None else f" {event.position_ms:.1f} ms / 周回{event.loop}"
    print(f"\r{labels[event.kind]}: {LABELS[event.stage]}{position} {event.reason}".ljust(100))


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="独立BGM・パリイ判定デバッグ")
    parser.add_argument("--config", type=Path, default=root / "config/bgm_settings.json")
    parser.add_argument("--audio-dir", type=Path, default=root / "data/bgm")
    args = parser.parse_args()
    if os.name != "nt":
        print("このコンソールアプリはWindows向けです。")
        return 1
    from .keyboard import Keyboard
    keyboard, game = None, None
    exit_reason = "quit"
    try:
        keyboard = Keyboard()
        print("Enter: 開始 / Space: パリイ / L: ライフ減少 / Q: 終了")
        next_display = 0.0
        while True:
            batch = keyboard.read()
            generation = game.generation if game else None
            for action, timestamp in batch:
                if action == "quit":
                    return 0
                if action == "start" and game is None:
                    tracks = load_tracks(args.config, args.audio_dir)
                    game = Game(tracks, AudioPlayer(), show_event)
                    game.start()
                    keyboard.discard()
                    break
                if game is None:
                    continue
                if action == "parry":
                    game.submit_parry(timestamp, generation)
                elif action == "life":
                    game.submit_life_decrement(timestamp, generation)
                    if not game.running:
                        return 0
                    keyboard.discard()
                    break
            now = perf_counter()
            if game:
                # 出力異常は表示周期に関係なく検知する。
                status = game.status(now)
                if now >= next_display:
                    if status is None:
                        message = "再生時刻確認中"
                    else:
                        loop, ms, window = status
                        message = f"{LABELS[game.track.id]} / 周回{loop} / {ms:9.1f} ms / パリイ判定: {'有効' if window is not None else '無効'}"
                    print("\r" + message.ljust(90), end="", flush=True)
                    next_display = now + 0.1
            sleep(0.002)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        exit_reason = "error"
        print(f"\nアプリを停止しました: {exc}")
        return 1
    finally:
        try:
            if game:
                game.stop(exit_reason)
        finally:
            if keyboard:
                keyboard.close()
            print()
