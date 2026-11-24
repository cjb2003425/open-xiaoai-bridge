from unittest import mock

from core.services.audio.stream import MyStream
from core.services.audio.vad import _VAD


def test_speech_to_silence_transition_keeps_unread_audio():
    vad = object.__new__(_VAD)
    vad.sample_rate = 16000
    vad.min_speech_duration = 0
    vad.min_silence_duration = 1200
    vad.speech_count = 0
    vad.silence_count = 0
    vad.speech_frames = []
    vad.silence_frames = []
    vad.target = "speech"
    vad.paused = False
    vad.stream = MyStream(rate=16000, channels=1, format=8, input=True)
    unread = b"\x24\x01" * 512
    vad.stream.input(unread)

    def on_speech(_buffer):
        vad.resume("silence", preserve_input=True)

    with mock.patch("core.services.audio.vad.EventManager.on_speech", on_speech):
        vad._handle_speech_frame(b"\x01\x00" * 512)

    assert vad.target == "silence"
    assert vad.stream.read(512) == unread
    vad.stream.stop_stream()
