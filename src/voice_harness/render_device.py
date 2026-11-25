"""Windows output with a short render queue, timestamps and a cancellation fade."""

from queue import Empty, Full, Queue
from threading import Event
import time

import numpy as np

from .contracts import VoiceError


class ReferencedOutput:
    def __init__(self, rate, reference, stop):
        import sounddevice as sd

        self._sd = sd
        self._rate = rate
        self._reference = reference
        self._stop = stop
        self._frames = Queue(maxsize=2)
        self._end = Event()
        self._finished = Event()
        self._samples = rate // 100
        self._size = self._samples * 4
        self._silence = bytes(self._size)
        self._last = 0.0
        self._ramp = np.linspace(1, 0, self._samples, dtype="<f4")
        self._device = sd.RawOutputStream(
            samplerate=rate, channels=1, dtype="float32", latency="low",
            blocksize=self._samples, callback=self._render, finished_callback=self._finished.set,
        )

    def _render(self, output, frames, timing, _status):
        if frames != self._samples:
            output[:] = bytes(frames * 4)
            raise self._sd.CallbackAbort
        try:
            block = self._frames.get_nowait()
        except Empty:
            block = None
        cancel = self._stop.is_set()
        if cancel:
            # Begin at the last submitted sample, even if synthesis is between segments.
            block = (self._last * self._ramp).tobytes()
        output[:] = block if block is not None else self._silence
        if block is not None:
            at = time.monotonic() + timing.outputBufferDacTime - timing.currentTime
            self._reference.add(block, self._rate, at)
            self._last = float(np.frombuffer(block, dtype="<f4")[-1])
        else:
            self._last = 0.0
        if cancel or (self._end.is_set() and block is None):
            raise self._sd.CallbackStop

    def start(self):
        self._device.start()

    def write(self, block):
        for offset in range(0, len(block), self._size):
            part = block[offset:offset + self._size].ljust(self._size, b"\0")
            while not self._stop.is_set():
                if self._finished.is_set():
                    raise VoiceError("The Windows output stream ended early.")
                try:
                    self._frames.put(part, timeout=0.02)
                    break
                except Full:
                    continue

    def stop(self):
        self._end.set()
        if not self._finished.wait(2):
            raise VoiceError("The Windows output stream did not finish.")

    def abort(self):
        if not self._stop.is_set() or not self._finished.wait(0.5):
            self._device.abort()

    def close(self):
        self._device.close()
