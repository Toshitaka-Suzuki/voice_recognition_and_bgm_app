"""HTTPと音声出力を置き換え、VOICEVOXの合成・再生・中断を検証する。"""

import io
import json
import unittest
import wave
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from chambara_talk.errors import TTSError
from chambara_talk.tts import SpeechSynthesizer, create_synthesizer
from chambara_talk.voicevox_tts import VoicevoxSynthesizer


def settings(**changes):
    values = dict(
        provider="voicevox", voice_match="Haruka", rate=175, volume=0.8,
        host="http://127.0.0.1:50021", speaker_id=11, speed_scale=1.1,
        pitch_scale=-0.02, intonation_scale=1.2, timeout_seconds=30.0,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def wav_data():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(24000)
        target.writeframes(b"\x01\x00\x02\x00\x03\x00")
    return buffer.getvalue()


VOICES = [{"name": "玄野武宏", "styles": [{"id": 11, "name": "ノーマル"}]}]


class CallbackStop(Exception):
    pass


class CallbackAbort(Exception):
    pass


class FakeAudio:
    """コールバックへ小さなバッファを渡す、外部依存のない音声出力。"""

    CallbackStop = CallbackStop
    CallbackAbort = CallbackAbort

    def __init__(self, *, cancel=None, start_error=False, stalled=False, status=False):
        self.cancel = cancel
        self.start_error = start_error
        self.stalled = stalled
        self.status = status
        self.streams = []
        self.buffers = []

    def RawOutputStream(self, **kwargs):
        stream = Mock()
        self.streams.append(stream)

        def start():
            if self.start_error:
                raise OSError("device unavailable")
            if self.cancel:
                self.cancel.set()
                return
            if self.stalled:
                return
            for _ in range(10):
                data = bytearray(4)
                try:
                    kwargs["callback"](data, 2, None, self.status)
                except (CallbackStop, CallbackAbort):
                    self.buffers.append(bytes(data))
                    kwargs["finished_callback"]()
                    return
                self.buffers.append(bytes(data))
            raise AssertionError("callback did not finish")

        stream.start.side_effect = start
        return stream


class VoicevoxTests(unittest.TestCase):
    def make_synth(self):
        return VoicevoxSynthesizer(settings(), output=lambda _: None)

    def test_factory_preserves_legacy_and_selects_voicevox(self):
        self.assertIsInstance(create_synthesizer(settings(provider="pyttsx3")), SpeechSynthesizer)
        self.assertIsInstance(create_synthesizer(settings()), VoicevoxSynthesizer)
        with self.assertRaises(TTSError):
            create_synthesizer(settings(provider="unknown"))

    def test_http_requests_generate_wav_with_selected_voice_and_settings(self):
        responses = [json.dumps(VOICES).encode(), b'{"accent_phrases":[]}', wav_data()]
        requests = []

        def open_request(request, timeout):
            requests.append((request, timeout))
            response = Mock()
            response.__enter__ = Mock(return_value=io.BytesIO(responses.pop(0)))
            response.__exit__ = Mock(return_value=False)
            return response

        synth = self.make_synth()
        with patch("chambara_talk.voicevox_tts.urlopen", side_effect=open_request):
            self.assertEqual(synth.synthesize("いざ、勝負！"), wav_data())
        self.assertEqual(synth.voice_name, "VOICEVOX: 玄野武宏（ノーマル）")
        self.assertEqual([item[0].get_method() for item in requests], ["GET", "POST", "POST"])
        self.assertEqual([item[1] for item in requests], [30.0, 30.0, 30.0])
        self.assertEqual(urlsplit(requests[0][0].full_url).path, "/speakers")
        query_request = urlsplit(requests[1][0].full_url)
        self.assertEqual(query_request.path, "/audio_query")
        self.assertEqual(parse_qs(query_request.query), {"text": ["いざ、勝負！"], "speaker": ["11"]})
        synthesis_request = requests[2][0]
        self.assertEqual(urlsplit(synthesis_request.full_url).path, "/synthesis")
        self.assertEqual(parse_qs(urlsplit(synthesis_request.full_url).query), {"speaker": ["11"]})
        payload = json.loads(synthesis_request.data)
        self.assertEqual(payload["accent_phrases"], [])
        self.assertEqual(payload["speedScale"], 1.1)
        self.assertEqual(payload["pitchScale"], -0.02)
        self.assertEqual(payload["intonationScale"], 1.2)
        self.assertEqual(payload["volumeScale"], 0.8)
        self.assertFalse(payload["outputStereo"])

    def test_list_voices_does_not_require_selected_voice(self):
        synth = VoicevoxSynthesizer(settings(speaker_id=999))
        with patch.object(synth, "_request", return_value=json.dumps(VOICES).encode()):
            self.assertEqual(synth.list_voices(), VOICES)
            with self.assertRaisesRegex(TTSError, "999"):
                synth.load()

    def test_invalid_speaker_responses_are_rejected(self):
        for response in (b"invalid JSON", b"{}", b'[{}]', b'[{"name":"x","styles":[{}]}]'):
            with self.subTest(response=response):
                synth = self.make_synth()
                with patch.object(synth, "_request", return_value=response), self.assertRaises(TTSError):
                    synth.list_voices()

    def test_request_errors_are_actionable(self):
        errors = [
            URLError("connection refused"), TimeoutError("timed out"),
            HTTPError("http://localhost", 422, "unprocessable", {}, None),
        ]
        for error in errors:
            with self.subTest(error=error):
                with patch("chambara_talk.voicevox_tts.urlopen", side_effect=error):
                    with self.assertRaisesRegex(TTSError, "VOICEVOX"):
                        self.make_synth().load()

    def test_invalid_and_truncated_wav_are_rejected(self):
        for response in (b"not a WAV", wav_data()[:-2]):
            with self.subTest(response=response):
                synth = self.make_synth()
                synth._loaded = True
                with patch.object(synth, "_request", side_effect=[b"{}", response]):
                    with self.assertRaisesRegex(TTSError, "WAV"):
                        synth.synthesize("質問")

    def test_cancel_before_synthesis_makes_no_request(self):
        synth = self.make_synth()
        cancel = Event()
        cancel.set()
        with patch.object(synth, "_request") as request:
            self.assertIsNone(synth.synthesize("質問", cancel_event=cancel))
        request.assert_not_called()

    def test_cancel_after_query_prevents_synthesis(self):
        synth = self.make_synth()
        synth._loaded = True
        cancel = Event()

        def query(*args, **kwargs):
            cancel.set()
            return b"{}"

        with patch.object(synth, "_request", side_effect=query) as request:
            self.assertIsNone(synth.synthesize("質問", cancel_event=cancel))
        self.assertEqual(request.call_count, 1)

    def test_cancel_after_synthesis_discards_audio_before_playback(self):
        synth = self.make_synth()
        synth._loaded = True
        cancel = Event()

        def request(path, **kwargs):
            if path == "/audio_query":
                return b"{}"
            cancel.set()
            return wav_data()

        with patch.object(synth, "_request", side_effect=request), patch.object(synth, "_play") as play:
            self.assertFalse(synth.speak("質問", cancel_event=cancel))
        play.assert_not_called()

    def test_four_utterances_each_finish_and_close_own_stream(self):
        synth = self.make_synth()
        audio = FakeAudio()
        with patch.object(synth, "synthesize", return_value=wav_data()), patch.dict("sys.modules", sounddevice=audio):
            for phrase in ("作戦質問", "作戦コメント", "感想質問", "感想コメント"):
                self.assertTrue(synth.speak(phrase))
        self.assertEqual(len(audio.streams), 4)
        self.assertEqual(audio.buffers[:2], [b"\x01\x00\x02\x00", b"\x03\x00\x00\x00"])
        for stream in audio.streams:
            stream.close.assert_called_once()

    def test_cancel_during_playback_aborts_only_owned_stream(self):
        synth = self.make_synth()
        cancel = Event()
        audio = FakeAudio(cancel=cancel)
        with patch.object(synth, "synthesize", return_value=wav_data()), patch.dict("sys.modules", sounddevice=audio):
            self.assertFalse(synth.speak("質問", cancel_event=cancel))
        audio.streams[0].abort.assert_called_once()
        audio.streams[0].close.assert_called_once()

    def test_start_failure_closes_stream_and_reports_error(self):
        synth = self.make_synth()
        audio = FakeAudio(start_error=True)
        with patch.object(synth, "synthesize", return_value=wav_data()), patch.dict("sys.modules", sounddevice=audio):
            with self.assertRaisesRegex(TTSError, "device unavailable"):
                synth.speak("質問")
        audio.streams[0].close.assert_called_once()

    def test_callback_error_reports_failure_and_closes_stream(self):
        synth = self.make_synth()
        audio = FakeAudio(status="underflow")
        with patch.object(synth, "synthesize", return_value=wav_data()), patch.dict("sys.modules", sounddevice=audio):
            with self.assertRaisesRegex(TTSError, "underflow"):
                synth.speak("質問")
        audio.streams[0].close.assert_called_once()

    def test_stalled_output_has_bounded_wait(self):
        synth = self.make_synth()
        audio = FakeAudio(stalled=True)
        with (
            patch.object(synth, "synthesize", return_value=wav_data()),
            patch.dict("sys.modules", sounddevice=audio),
            patch("chambara_talk.voicevox_tts.time.monotonic", side_effect=[0.0, 6.0]),
        ):
            with self.assertRaisesRegex(TTSError, "再生完了"):
                synth.speak("質問")
        audio.streams[0].abort.assert_called_once()
        audio.streams[0].close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
