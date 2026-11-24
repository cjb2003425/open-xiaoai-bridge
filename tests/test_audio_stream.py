import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.services.audio.stream import MyStream, GlobalStream

FRAME = 512
FRAME_BYTES = FRAME * 2


class MyStreamOffsetTest(unittest.TestCase):
    def _new_stream(self) -> MyStream:
        return MyStream(
            rate=16000,
            channels=1,
            format=8,
            input=True,
            frames_per_buffer=FRAME,
            start=True,
        )

    def test_offset_read_returns_sequential_frames(self):
        stream = self._new_stream()
        stream.input(b"\x11" * (FRAME_BYTES * 3))

        self.assertEqual(FRAME_BYTES, len(stream.read(FRAME)))
        self.assertEqual(FRAME_BYTES, len(stream.read(FRAME)))
        self.assertEqual(2 * FRAME_BYTES, stream._read_offset)

    def test_clear_input_resets_read_cursor(self):
        stream = self._new_stream()
        stream.input(b"\x11" * (FRAME_BYTES * 3))
        stream.read(FRAME)
        stream.read(FRAME)
        self.assertNotEqual(0, stream._read_offset)

        stream.clear_input()

        self.assertEqual(0, stream._read_offset)
        self.assertEqual(0, len(stream.input_bytes))

    def test_fresh_audio_after_clear_is_read_from_start(self):
        """Regression: clearing the buffer must not make read() skip or drop
        freshly-arrived audio (the offset-based read bug from dbcbad9)."""
        stream = self._new_stream()
        stream.input(b"\x11" * (FRAME_BYTES * 3))
        stream.read(FRAME)
        stream.read(FRAME)  # advance _read_offset well past zero

        stream.clear_input()  # what VAD.resume() does between capture rounds

        fresh = b"\x22" * FRAME_BYTES
        stream.input(fresh)
        # Must return the fresh frame intact, not empty and not offset garbage.
        self.assertEqual(fresh, stream.read(FRAME))

    def test_stop_stream_also_resets_cursor(self):
        stream = self._new_stream()
        stream.input(b"\x11" * (FRAME_BYTES * 3))
        stream.read(FRAME)

        stream.stop_stream()

        self.assertEqual(0, stream._read_offset)
        self.assertEqual(0, len(stream.input_bytes))

    def test_preroll_preserves_recent_audio_once_in_order(self):
        stream = MyStream(rate=16000, channels=1, format=8, input=True, start=False)
        GlobalStream.clear_history()
        old = b"\x11\x00" * 1024
        new = b"\x22\x00" * 512
        GlobalStream.input(old)
        stream.start_stream(preroll_ms=64)
        GlobalStream.input(new)
        self.assertEqual(old, stream.read(1024))
        self.assertEqual(new, stream.read(512))
        stream.stop_stream()
        GlobalStream.clear_history()

    def test_preroll_rejects_stale_audio(self):
        stream = MyStream(rate=16000, channels=1, format=8, input=True, start=False)
        GlobalStream.clear_history()
        with mock.patch("core.services.audio.stream.time.monotonic", return_value=1.0):
            GlobalStream.input(b"\x11\x00" * 512)
        with mock.patch("core.services.audio.stream.time.monotonic", return_value=2.0):
            stream.start_stream(preroll_ms=600)
        self.assertEqual(b"", stream.read(512))
        stream.stop_stream()
        GlobalStream.clear_history()


if __name__ == "__main__":
    unittest.main()
