"""コマンドライン引数を受け取り、各部品を組み立ててアプリを起動する。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audio import AudioRecorder
from .config import load_conversation_examples, load_settings
from .controller import ConversationController
from .errors import ChambaraTalkError
from .llm import create_chat_backend
from .speech import SpeechRecognizer
from .tts import create_synthesizer


def project_root() -> Path:
    """このファイルの位置を基準にプロジェクトのルートを返す。"""

    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    """設定ファイルと会話例ファイルを指定できる引数解析器を作る。"""

    root = project_root()
    parser = argparse.ArgumentParser(description="チャンバラロボット会話アプリ")
    parser.add_argument(
        "--settings",
        type=Path,
        default=root / "config" / "settings.json",
        help="設定JSONのパス",
    )
    parser.add_argument(
        "--examples",
        type=Path,
        default=root / "data" / "conversation_examples.json",
        help="会話例JSONのパス",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """アプリを初期化して実行し、OSへ返す終了コードを返す。

    ``argv``を省略すると実際のコマンドライン引数を使います。起動時の想定内
    エラーは表示して終了コード1、Ctrl+Cは130、それ以外は0を返します。
    """

    args = build_parser().parse_args(argv)
    chat_backend = None
    try:
        settings = load_settings(args.settings.resolve())
        examples = load_conversation_examples(args.examples.resolve())

        # 各専門部品を作り、設定や用語一覧を渡します。
        recorder = AudioRecorder(settings.audio)
        recognizer = SpeechRecognizer(settings.speech_recognition, examples.domain_terms)
        chat_backend = create_chat_backend(settings.llm, examples.domain_terms)
        synthesizer = create_synthesizer(settings.tts)

        # 会話を始める前に、マイク・音声・LLM・認識モデルを順番に確認します。
        if settings.llm.provider == "copilot":
            print("クラウドLLM: GitHub Copilot（インターネット接続と利用枠を使用）")
            print("送信対象: Enterで確定した認識文、現在フェーズの会話例、用語一覧、システム指示。録音音声は送信しません。")
            print("SDKの一時保存は終了時に削除します。GitHub側の保持と異常終了時の残存は保証できません。")
        print("起動前チェックを実行しています...")
        microphone = recorder.check_device()
        print(f"マイク: {microphone}")
        synthesizer.load()
        print(f"音声: {synthesizer.voice_name}")
        chat_backend.check_model()
        print(f"LLM: {settings.llm.provider} / {settings.llm.model}")
        print(f"音声認識モデルを読み込んでいます: {settings.speech_recognition.model}")
        recognizer.load()
        print("起動前チェックが完了しました。")

        # Controllerは専門部品の呼び出し順だけを担当します。
        controller = ConversationController(
            recorder=recorder,
            recognizer=recognizer,
            chat_backend=chat_backend,
            synthesizer=synthesizer,
            examples=examples,
            strategy_question=settings.questions.strategy,
            feedback_question=settings.questions.feedback,
        )
        controller.run()
        return 0
    except ChambaraTalkError as exc:
        print(f"起動エラー: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n終了します。")
        return 130
    finally:
        # 起動チェックの失敗、q、Ctrl+Cでも、SDKプロセスと会話データを解放します。
        if chat_backend is not None:
            try:
                chat_backend.close()
            except ChambaraTalkError as exc:
                print(f"LLM終了処理エラー: {exc}", file=sys.stderr)


if __name__ == "__main__":
    # cli.pyを直接実行した場合にも、戻り値を終了コードとして利用します。
    raise SystemExit(main())
