"""BGM設定とWAV・Ogg Vorbisをゲーム開始前に読み込む。"""
from dataclasses import dataclass
import json
from pathlib import Path
import wave


@dataclass(frozen=True)
class Window:
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class Track:
    id: str
    windows: tuple[Window, ...]
    samples: object
    sample_rate: int

    @property
    def duration_ms(self):
        return len(self.samples) * 1000 / self.sample_rate


def _load_wav(path: Path):
    """従来どおり非圧縮16bit PCM WAVを読み込む。"""
    import numpy as np

    with wave.open(str(path), "rb") as wav:
        channels, rate, frames = wav.getnchannels(), wav.getframerate(), wav.getnframes()
        if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2 or channels not in (1, 2):
            raise ValueError("16bit PCM・モノラル/ステレオが必要です")
        raw = wav.readframes(frames)
        if not frames or len(raw) != frames * channels * 2:
            raise ValueError("音声が空または破損しています")
    samples = np.frombuffer(raw, dtype="<i2").reshape(-1, channels).astype(np.float32) / 32768
    return samples, rate


def _load_ogg(path: Path):
    """Vorbisをfloat32の2次元配列へデコードする。"""
    try:
        import soundfile as sf
    except (ImportError, OSError) as exc:
        raise ValueError(
            'SoundFileを読み込めません。使用する仮想環境で '
            'python -m pip install "soundfile>=0.13.1,<1" を実行してください。'
        ) from exc
    try:
        with sf.SoundFile(str(path)) as audio:
            if audio.format != "OGG" or audio.subtype != "VORBIS":
                raise ValueError("Ogg Vorbisが必要です（Opusは未対応）")
            if audio.channels not in (1, 2):
                raise ValueError("モノラルまたはステレオが必要です")
            samples = audio.read(dtype="float32", always_2d=True)
            rate = audio.samplerate
            if not len(samples) or len(samples) != audio.frames:
                raise ValueError("音声が空または破損しています")
    except sf.SoundFileError as exc:
        raise ValueError(f"OGGのデコードに失敗しました: {exc}") from exc
    return samples, rate


def _load_audio(path: Path):
    try:
        return _load_ogg(path) if path.suffix.lower() == ".ogg" else _load_wav(path)
    except (OSError, ValueError, wave.Error, EOFError) as exc:
        raise ValueError(f"{path.name}: {exc}") from exc


def load_tracks(config_path: Path, audio_dir: Path) -> tuple[Track, ...]:
    """設定を検証し全曲をメモリへ展開する。パスはBGMフォルダ内に限定。"""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("versionは1を指定してください")
        stages = data["stages"]
        if not isinstance(stages, list) or [s["id"] for s in stages] != ["easy", "normal", "hard"]:
            raise ValueError("stagesはeasy、normal、hardの順に3件指定してください")
        tracks = []
        root = audio_dir.resolve()
        for stage in stages:
            path = (root / stage["audio_file"]).resolve()
            if not path.is_relative_to(root) or path.suffix.lower() not in (".wav", ".ogg"):
                raise ValueError("音声はBGMフォルダ内のWAVまたはOGGを指定してください")
            samples, rate = _load_audio(path)
            windows = []
            for item in stage["parry_windows"]:
                start, end = item["start_ms"], item["end_ms"]
                if type(start) is not int or type(end) is not int or not 0 <= start < end:
                    raise ValueError(f"{stage['id']}: 区間は非負の整数で開始 < 終了にしてください")
                if end > len(samples) * 1000 / rate:
                    raise ValueError(f"{stage['id']}: 判定区間が曲の長さを超えています")
                windows.append(Window(start, end))
            if not windows:
                raise ValueError(f"{stage['id']}: 判定区間を1件以上指定してください")
            windows.sort(key=lambda w: w.start_ms)
            if any(a.end_ms > b.start_ms for a, b in zip(windows, windows[1:])):
                raise ValueError(f"{stage['id']}: 判定区間が重複しています")
            tracks.append(Track(stage["id"], tuple(windows), samples, rate))
        return tuple(tracks)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, wave.Error, EOFError) as exc:
        raise ValueError(f"BGM設定の読み込みに失敗しました: {exc}") from exc
