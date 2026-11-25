"""Bounded Windows microphone stream with timestamps in the host clock."""

from collections import deque
from contextlib import ExitStack, contextmanager
from threading import Condition
import time

from .contracts import VoiceError
from .echo import FRAME_SAMPLES, RATE


class WindowsMicrophone:
    def __init__(self):
        self._frames = deque(maxlen=100)
        self._condition = Condition()
        self._sequence = 0
        self._last_read = 0
        self._ended = False

    def _capture(self, data, frames, timing, status):
        at = time.monotonic() + timing.inputBufferAdcTime - timing.currentTime
        with self._condition:
            self._sequence += 1
            overflow = status.input_overflow or frames != FRAME_SAMPLES
            self._frames.append((self._sequence, data.copy(), at, overflow))
            self._condition.notify()

    def _finished(self):
        with self._condition:
            self._ended = True
            self._condition.notify()

    def discard_buffered(self):
        with self._condition:
            self._frames.clear()
            self._last_read = self._sequence

    @contextmanager
    def open(self):
        import sounddevice as sd

        with ExitStack() as stack:
            try:
                device = sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                                        blocksize=FRAME_SAMPLES, callback=self._capture,
                                        finished_callback=self._finished)
                stack.callback(device.close)
                device.start()
            except (OSError, sd.PortAudioError):
                raise VoiceError("Could not record. Check the Windows microphone and its permissions.") from None
            yield

    def read(self, stop):
        with self._condition:
            while not stop.is_set():
                if self._frames:
                    sequence, samples, at, overflow = self._frames.popleft()
                    dropped = sequence != self._last_read + 1
                    self._last_read = sequence
                    return samples, at, overflow or dropped
                if self._ended:
                    raise VoiceError("The Windows microphone stopped before the turn finished.")
                self._condition.wait(timeout=0.05)
        return None
