"""統合アプリの設定読込、部品作成、WebSocket起動を行う。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from chambara_bgm.config import load_tracks
from chambara_bgm.display import ConsoleDisplay
from chambara_bgm.engine import Game
from chambara_bgm.player import AudioPlayer
from chambara_bgm.rhythm import with_rhythm_guide
from chambara_talk.audio import AudioRecorder
from chambara_talk.config import load_conversation_examples, load_settings
from chambara_talk.controller import ConversationController
from chambara_talk.errors import ChambaraTalkError
from chambara_talk.llm import create_chat_backend
from chambara_talk.speech import SpeechRecognizer
from chambara_talk.tts import SpeechSynthesizer

from .orchestrator import IntegratedController
from .server import run_server
from .mock_conversation import MockConversation
from .silent_player import SilentAudioPlayer


def project_root() -> Path:
    """インストール元のプロジェクトルートを返す。"""

    return Path(__file__).resolve().parents[2]


def port_number(value: str) -> int:
    """コマンドラインのポート番号を検証する。"""

    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ポートは1〜65535の整数で指定してください。") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("ポートは1〜65535の整数で指定してください。")
    return port


def build_parser() -> argparse.ArgumentParser:
    """通信・会話・BGMのファイルを指定できる引数解析器を作る。"""

    root = project_root()
    parser = argparse.ArgumentParser(description="チャンバラ統合動作確認アプリ")
    parser.add_argument("--host", default="127.0.0.1", help="WebSocket待ち受けIP")
    parser.add_argument("--port", type=port_number, default=8765, help="WebSocket待ち受けポート")
    parser.add_argument("--settings", type=Path, default=root / "config/settings.json")
    parser.add_argument("--examples", type=Path, default=root / "data/conversation_examples.json")
    parser.add_argument("--bgm-config", type=Path, default=root / "config/bgm_settings.json")
    parser.add_argument("--audio-dir", type=Path, default=root / "data/bgm")
    parser.add_argument("--mock-conversation", action="store_true",
                        help="会話を3秒の模擬処理へ置換（LLM・マイク・音声認識・TTS不要）")
    parser.add_argument("--silent-audio", action="store_true",
                        help="模擬会話モードで音声出力を省略し、通信と判定を確認")
    parser.add_argument("--no-rhythm-guide", action="store_true",
                        help="4拍の予告を使わず、従来のBGM判定設定で動作")
    return parser


def main(argv: list[str] | None = None) -> int:
    """全機能を初期化し、統合WebSocketサーバーを起動する。"""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.silent_audio and not args.mock_conversation:
        parser.error("--silent-audioは--mock-conversationと一緒に指定してください。")
    if sys.platform != "win32" and not args.mock_conversation:
        print("実際の会話はWindows向けです。連携確認には--mock-conversationを指定してください。",
              file=sys.stderr)
        return 1
    chat_backend = None
    game = None
    controller = None
    display = ConsoleDisplay()

    def terminal_output(message: str) -> None:
        """通常ログを表示し、戦闘中なら直後にBGM状態行を復元する。"""

        display.line(str(message), restore_status=True)

    try:
        tracks = load_tracks(args.bgm_config.resolve(), args.audio_dir.resolve())
        if not args.no_rhythm_guide:
            tracks = tuple(with_rhythm_guide(track) for track in tracks)
            terminal_output("4拍ガイド: タン・タン・タン・カン！ 最後の音に合わせて受けます。")
        if args.mock_conversation:
            conversation = MockConversation(terminal_output)
            terminal_output("会話は模擬モードです。録音・音声認識・LLM・読み上げを使用しません。")
            if args.silent_audio:
                terminal_output("BGMは無音テストです。実機の音声同期は確認できません。")
        else:
            settings = load_settings(args.settings.resolve())
            examples = load_conversation_examples(args.examples.resolve())
            recorder = AudioRecorder(settings.audio)
            recognizer = SpeechRecognizer(settings.speech_recognition, examples.domain_terms)
            chat_backend = create_chat_backend(settings.llm, examples.domain_terms)
            synthesizer = SpeechSynthesizer(settings.tts)
            conversation = ConversationController(
                recorder=recorder,
                recognizer=recognizer,
                chat_backend=chat_backend,
                synthesizer=synthesizer,
                examples=examples,
                strategy_question=settings.questions.strategy,
                feedback_question=settings.questions.feedback,
                feedback_human_win_question=settings.questions.feedback_human_win,
                feedback_robot_win_question=settings.questions.feedback_robot_win,
                output=terminal_output,
                manual_input_enabled=False,
            )
        game = Game(
            tracks,
            SilentAudioPlayer() if args.silent_audio else AudioPlayer(),
            display.event,
        )

        def prepare() -> None:
            """スレッド依存部品を、実際の会話と同じ専用スレッドで初期化する。"""

            terminal_output("起動前チェックを実行しています...")
            terminal_output(f"マイク: {recorder.check_device()}")
            synthesizer.load()
            terminal_output(f"音声: {synthesizer.voice_name}")
            chat_backend.check_model()
            terminal_output(f"LLM: {settings.llm.provider} / {settings.llm.model}")
            recognizer.load()
            terminal_output(f"音声認識: {settings.speech_recognition.model}")
            terminal_output("起動前チェックが完了しました。")

        controller = IntegratedController(
            conversation,
            game,
            output=terminal_output,
            prepare=None if args.mock_conversation else prepare,
            cleanup=chat_backend.close if chat_backend is not None else None,
            bgm_display=display,
            conversation_mode="mock_silent" if args.silent_audio else "mock" if args.mock_conversation else "live",
        )

        async def run_application() -> None:
            """起動確認に失敗した場合も、専用スレッドと部品を片付ける。"""

            try:
                await controller.prepare()
                await run_server(
                    controller,
                    args.host,
                    args.port,
                    log_output=terminal_output,
                )
            finally:
                await controller.close()

        asyncio.run(run_application())
        return 0
    except ChambaraTalkError as exc:
        print(f"起動エラー: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"通信起動エラー: 待ち受けIPとポートを確認してください: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n終了します。")
        return 130
    except Exception as exc:
        print(f"統合アプリを開始できませんでした: {exc}", file=sys.stderr)
        return 1
    finally:
        if game is not None:
            try:
                game.stop("shutdown")
            except Exception:
                pass
        if chat_backend is not None and controller is None:
            try:
                chat_backend.close()
            except ChambaraTalkError as exc:
                print(f"LLM終了処理エラー: {exc}", file=sys.stderr)
        display.close()


if __name__ == "__main__":
    raise SystemExit(main())
