"""Bounded Windows microphone stream with timestamps in the host clock."""

from collections import deque
from contextlib import ExitStack, contextmanager
from threading import Condition
import math
import time

from .contracts import PRE_ROLL_SECONDS, VoiceError
from .echo import FRAME_SAMPLES, FRAME_SECONDS, RATE


class WindowsMicrophone:
    def __init__(self):
        self._frames = deque(maxlen=round(PRE_ROLL_SECONDS / FRAME_SECONDS))
        self._condition = Condition()
        self._sequence = 0
        self._last_read = 0
        self._last_adc = None
        self._ended = False

    def _capture(self, data, _frame_count, timing, status):
        adc = timing.inputBufferAdcTime
        at = time.monotonic() + adc - timing.currentTime
        with self._condition:
            self._sequence += 1
            self._frames.append((self._sequence, data.copy(), at, adc, status.input_overflow))
            self._condition.notify()

    def _finished(self):
        with self._condition:
            self._ended = True
            self._condition.notify()

    def discard_buffered(self):
        with self._condition:
            self._frames.clear()
            self._last_read = self._sequence
            self._last_adc = None

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
                    sequence, samples, at, adc, overflow = self._frames.popleft()
                    missing = sequence - self._last_read - 1
                    if overflow:
                        # The driver reports a loss without a size. Estimate it from its ADC
                        # clock only when it flags overflow. Queue losses have an exact count.
                        if self._last_adc is not None:
                            elapsed = adc - self._last_adc
                            if math.isfinite(elapsed) and elapsed > 0:
                                missing = max(missing, round(elapsed / FRAME_SECONDS) - 1)
                        missing = max(1, missing)
                    self._last_read = sequence
                    self._last_adc = adc
                    return samples, at, missing
                if self._ended:
                    raise VoiceError("The Windows microphone stopped before the turn finished.")
                self._condition.wait(timeout=0.05)
        return None
