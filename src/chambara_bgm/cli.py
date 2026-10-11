"""日本語コンソールでBGMと外部信号をデバッグする。"""
import argparse
import os
from pathlib import Path
from time import perf_counter, sleep

from .config import load_tracks
from .engine import Game
from .player import AudioPlayer
from .display import ConsoleDisplay


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
    display = ConsoleDisplay()
    exit_reason = "quit"
    try:
        keyboard = Keyboard()
        display.line("Enter: 開始 / Space: パリイ / L: ライフ減少 / Q: 終了")
        next_display = 0.0
        while True:
            batch = keyboard.read()
            generation = game.generation if game else None
            for action, timestamp in batch:
                if action == "quit":
                    return 0
                if action == "start" and game is None:
                    tracks = load_tracks(args.config, args.audio_dir)
                    game = Game(tracks, AudioPlayer(), display.event)
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
                warning = game.player.take_warning(now)
                if warning:
                    display.line(warning, restore_status=True)
                if now >= next_display:
                    display.status(game.track.id, status,
                                   waiting=game.player.waiting_for_first_callback,
                                   synchronizing=game.player.synchronizing)
                    next_display = now + 0.1
            sleep(0.002)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        exit_reason = "error"
        display.line(f"アプリを停止しました: {exc}")
        return 1
    finally:
        try:
            if game:
                game.stop(exit_reason)
        finally:
            try:
                if keyboard:
                    keyboard.close()
            finally:
                display.close()
