"""マイク・LLMなしで同じセリフをVOICEVOXの複数の声へ書き出す。"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import wave
from dataclasses import replace
from pathlib import Path

from .config import load_settings
from .errors import ChambaraTalkError
from .voicevox_tts import VoicevoxSynthesizer


COMPARISON_IDS = (11, 21, 42)
SAMPLE_TEXT = (
    "いざ、勝負でござる！ どんな作戦で挑むのか、聞かせてくだされ。"
    "見事な勝負でござった！ またの挑戦を待っておるぞ。"
)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="VOICEVOXの声をWAVで聴き比べる（マイク・LLM不要）")
    parser.add_argument("--settings", type=Path, default=root / "config/settings.voicevox.json")
    parser.add_argument("--list-voices", action="store_true", help="利用できる声とスタイルIDを表示")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--compare", action="store_true", help="玄野武宏・剣崎雌雄・ちび式じいを比較")
    selection.add_argument("--speaker", type=int, nargs="+", help="書き出すスタイルID（複数指定可）")
    parser.add_argument("--text", default=SAMPLE_TEXT, help="全員に読ませる同じ文章")
    parser.add_argument("--output-dir", type=Path, default=Path("output/voice-samples"))
    args = parser.parse_args(argv)
    if not args.text.strip():
        parser.error("--text は空にできません。")
    try:
        settings = load_settings(args.settings).tts
        if settings.provider != "voicevox":
            parser.error("VOICEVOXの設定ファイルを指定してください。")
        voices = VoicevoxSynthesizer(settings).list_voices()
        styles = {
            style["id"]: (voice["name"], style["name"])
            for voice in voices for style in voice["styles"]
        }
        if args.list_voices:
            for identifier, (name, style) in styles.items():
                print(f"{identifier:>3}: {name}（{style}）")
            return 0
        identifiers = list(dict.fromkeys(
            COMPARISON_IDS if args.compare else args.speaker or [settings.speaker_id]
        ))
        for identifier in identifiers:
            if identifier not in styles:
                parser.error(f"スタイルID {identifier} は見つかりません。--list-voices で確認してください。")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        credits = [f"VOICEVOX:{styles[identifier][0]}" for identifier in identifiers]
        (args.output_dir / "CREDITS.txt").write_text(
            "\n".join(credits) + "\n\n"
            "音声を展示・公開する際も、使用した声のクレジットを表示してください。\n"
            "https://voicevox.hiroshiba.jp/term/\n"
            "https://github.com/VOICEVOX/voicevox_resource/blob/main/engine/README.md\n",
            encoding="utf-8",
        )
        samples = []
        for identifier in identifiers:
            synthesizer = VoicevoxSynthesizer(replace(settings, speaker_id=identifier))
            started = time.perf_counter()
            wav = synthesizer.synthesize(args.text)
            elapsed = time.perf_counter() - started
            if wav is None:
                return 130
            filename = f"voice-{identifier}.wav"
            (args.output_dir / filename).write_bytes(wav)
            with wave.open(io.BytesIO(wav), "rb") as source:
                duration = source.getnframes() / source.getframerate()
            name, style = styles[identifier]
            samples.append(dict(
                speaker_id=identifier, name=name, style=style, file=filename,
                duration_seconds=round(duration, 2), synthesis_seconds=round(elapsed, 2),
                credit=f"VOICEVOX:{name}",
            ))
            print(f"{name}（{style}）: {args.output_dir / filename} ({duration:.1f}秒)")
        (args.output_dir / "samples.json").write_text(
            json.dumps(dict(text=args.text, samples=samples), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("WAVを開いて聴き比べ、選んだIDを設定の tts.speaker_id に指定してください。")
        return 0
    except (ChambaraTalkError, OSError) as exc:
        print(f"音声プレビューエラー: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
