"""ローカルのVOICEVOX ENGINEで合成し、専用ストリームで読み上げる。"""

from __future__ import annotations

import io
import json
import time
import wave
from collections.abc import Callable
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import TTSSettings
from .errors import TTSError


class VoicevoxSynthesizer:
    """VOICEVOXの話者確認、WAV生成、キャンセル可能な再生を担当する。"""

    def __init__(self, settings: TTSSettings, output: Callable[[str], None] = print) -> None:
        self.settings = settings
        self.output = output
        self.voice_name = ""
        self._loaded = False

    def _request(self, path: str, *, params=None, payload=None, method="GET") -> bytes:
        url = self.settings.host.rstrip("/") + path
        if params:
            url += "?" + urlencode(params)
        data = None if method == "GET" else b""
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                return response.read()
        except HTTPError as exc:
            raise TTSError(
                f"VOICEVOXがHTTP {exc.code}を返しました ({path})。"
                "話者IDとエンジンの状態を確認してください。"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise TTSError(
                f"VOICEVOXへ接続できないか、応答がタイムアウトしました ({self.settings.host})。"
                "VOICEVOXを起動し、tts.host・tts.timeout_secondsを確認してください。"
            ) from exc

    @staticmethod
    def _json(data: bytes):
        try:
            return json.loads(data)
        except (UnicodeError, ValueError) as exc:
            raise TTSError("VOICEVOXの応答が正しいJSONではありません。接続先を確認してください。") from exc

    def list_voices(self) -> list[dict]:
        """話者名と各スタイルのIDを含む、エンジンの話者一覧を返す。"""

        voices = self._json(self._request("/speakers"))
        if not isinstance(voices, list) or any(
            not isinstance(voice, dict)
            or not isinstance(voice.get("name"), str)
            or not isinstance(voice.get("styles"), list)
            or any(
                not isinstance(style, dict)
                or not isinstance(style.get("name"), str)
                or type(style.get("id")) is not int
                for style in voice.get("styles", [])
            )
            for voice in voices
        ):
            raise TTSError("VOICEVOXの話者一覧を読み取れません。接続先を確認してください。")
        return voices

    def load(self) -> None:
        if self._loaded:
            return
        for voice in self.list_voices():
            for style in voice["styles"]:
                if style["id"] == self.settings.speaker_id:
                    self.voice_name = f"VOICEVOX: {voice['name']}（{style['name']}）"
                    self._loaded = True
                    return
        raise TTSError(
            f"VOICEVOXの話者ID {self.settings.speaker_id} が見つかりません。"
            "音声プレビューの --list-voices で確認し、tts.speaker_idを変更してください。"
        )

    @staticmethod
    def _cancelled(cancel_event: Event | None) -> bool:
        return cancel_event is not None and cancel_event.is_set()

    @staticmethod
    def _read_wav(data: bytes) -> tuple[bytes, int, int]:
        try:
            with wave.open(io.BytesIO(data), "rb") as source:
                channels, rate = source.getnchannels(), source.getframerate()
                frames, width = source.getnframes(), source.getsampwidth()
                samples = source.readframes(frames)
                if (
                    width != 2 or channels not in (1, 2) or rate <= 0 or frames <= 0
                    or len(samples) != frames * channels * width
                ):
                    raise ValueError("empty, incomplete, or unsupported WAV")
                return samples, rate, channels
        except (wave.Error, EOFError, OSError, ValueError) as exc:
            raise TTSError("VOICEVOXから有効な16bit PCM WAVを取得できませんでした。エンジンを確認してください。") from exc

    def synthesize(self, text: str, *, cancel_event: Event | None = None) -> bytes | None:
        """再生せずWAVを生成する。中断時は後続の合成・再生を行わずNoneを返す。"""

        if self._cancelled(cancel_event):
            return None
        self.load()
        if self._cancelled(cancel_event):
            return None
        raw_query = self._request(
            "/audio_query", method="POST", params={"text": text, "speaker": self.settings.speaker_id}
        )
        if self._cancelled(cancel_event):
            return None
        query = self._json(raw_query)
        if not isinstance(query, dict):
            raise TTSError("VOICEVOXの音声クエリが不正です。エンジンを確認してください。")
        query.update(
            speedScale=self.settings.speed_scale,
            pitchScale=self.settings.pitch_scale,
            intonationScale=self.settings.intonation_scale,
            volumeScale=self.settings.volume,
            outputStereo=False,
        )
        wav = self._request(
            "/synthesis", method="POST", params={"speaker": self.settings.speaker_id}, payload=query
        )
        if self._cancelled(cancel_event):
            return None
        self._read_wav(wav)
        return wav

    def _play(self, wav: bytes, cancel_event: Event | None) -> bool:
        if self._cancelled(cancel_event):
            return False
        samples, rate, channels = self._read_wav(wav)
        import sounddevice as sd

        finished = Event()
        position = 0
        callback_errors = []
        frame_bytes = channels * 2

        def callback(outdata, frames, _time_info, status):
            nonlocal position
            try:
                outdata[:] = b"\x00" * len(outdata)
                if self._cancelled(cancel_event):
                    raise sd.CallbackAbort
                if status:
                    callback_errors.append(f"音声出力異常: {status}")
                    raise sd.CallbackAbort
                chunk = samples[position:position + frames * frame_bytes]
                outdata[:len(chunk)] = chunk
                position += len(chunk)
                if position >= len(samples):
                    # 最後のバッファがスピーカーへ出力されてからfinished_callbackが呼ばれる。
                    raise sd.CallbackStop
            except (sd.CallbackAbort, sd.CallbackStop):
                raise
            except Exception as exc:
                callback_errors.append(str(exc))
                raise sd.CallbackAbort from exc

        stream = sd.RawOutputStream(
            samplerate=rate, channels=channels, dtype="int16",
            callback=callback, finished_callback=finished.set,
        )
        try:
            if self._cancelled(cancel_event):
                return False
            stream.start()
            deadline = time.monotonic() + len(samples) / (rate * frame_bytes) + 5.0
            while not finished.wait(0.02):
                if self._cancelled(cancel_event):
                    stream.abort()
                    return False
                if time.monotonic() > deadline:
                    stream.abort()
                    raise TTSError("音声の再生完了を確認できません。音声出力デバイスを確認してください。")
            if callback_errors:
                raise TTSError(callback_errors[0])
            return not self._cancelled(cancel_event)
        finally:
            stream.close()

    def speak(self, text: str, *, cancel_event: Event | None = None) -> bool:
        try:
            wav = self.synthesize(text, cancel_event=cancel_event)
            if wav is None:
                return False
            self.output(f"音声読み上げ開始: {text}")
            if not self._play(wav, cancel_event):
                return False
            self.output("音声読み上げ完了")
            return True
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"VOICEVOX音声の読み上げに失敗しました。音声出力デバイスを確認してください: {exc}") from exc
