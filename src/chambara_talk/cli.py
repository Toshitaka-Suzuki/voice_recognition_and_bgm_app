"""コマンドライン引数を受け取り、各部品を組み立ててアプリを起動する。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audio import AudioRecorder
from .config import load_conversation_examples, load_settings
from .controller import ConversationController
from .errors import ChambaraTalkError
from .llm import ChatBackend
from .speech import SpeechRecognizer
from .tts import SpeechSynthesizer


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
    try:
        settings = load_settings(args.settings.resolve())
        examples = load_conversation_examples(args.examples.resolve())

        # 各専門部品を作り、設定や用語一覧を渡します。
        recorder = AudioRecorder(settings.audio)
        recognizer = SpeechRecognizer(settings.speech_recognition, examples.domain_terms)
        chat_backend = ChatBackend(settings.llm, examples.domain_terms)
        synthesizer = SpeechSynthesizer(settings.tts)

        # 会話を始める前に、マイク・音声・LLM・認識モデルを順番に確認します。
        print("起動前チェックを実行しています...")
        microphone = recorder.check_device()
        print(f"マイク: {microphone}")
        synthesizer.load()
        print(f"音声: {synthesizer.voice_name}")
        chat_backend.check_model()
        print(f"LLM: {settings.llm.model}")
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


if __name__ == "__main__":
    # cli.pyを直接実行した場合にも、戻り値を終了コードとして利用します。
    raise SystemExit(main())
