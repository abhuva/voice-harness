"""Timestamped playback reference and WebRTC near-end speech processing."""

from collections import deque
from threading import Lock

import numpy as np


RATE = 16_000
FRAME_SECONDS = 0.01
FRAME_SAMPLES = 160
_ECHO_TAIL = 0.25


class PlaybackReference:
    """Store a short, call-local history of PCM sent to the output device."""

    def __init__(self):
        self._frames = deque(maxlen=100)
        self._lock = Lock()

    def add(self, pcm, rate, at):
        with self._lock:
            self._frames.append((pcm, rate, at))

    def at(self, capture_at):
        with self._lock:
            while self._frames and self._frames[0][2] < capture_at - _ECHO_TAIL:
                self._frames.popleft()
            frames = tuple(self._frames)
        aligned = np.zeros(FRAME_SAMPLES, dtype=np.float32)
        recent = False
        for pcm, rate, at in frames:
            if at >= capture_at + FRAME_SECONDS:
                break
            recent = True
            offset = round((at - capture_at) * RATE)
            if offset <= -FRAME_SAMPLES:
                continue
            render = np.frombuffer(pcm, dtype="<f4")
            count = min(round(len(render) * RATE / rate), FRAME_SAMPLES - offset)
            start = max(0, -offset)
            if count > start:
                index = np.arange(start, count) * (rate / RATE)
                aligned[offset + start:offset + count] = np.interp(index, np.arange(len(render)), render)
        return aligned, recent


class EchoProcessor:
    def __init__(self, reference):
        self.reference = reference
        try:
            from pywebrtc_audio import AudioProcessor

            self.processor = AudioProcessor(
                sample_rate=RATE, echo_cancellation=True, noise_suppression=True,
                auto_gain_control=False, stream_delay_ms=0,
            )
        except ImportError:
            self.processor = None

    @property
    def available(self):
        return self.processor is not None

    def clean(self, samples, at):
        far, recent = self.reference.at(at)
        if self.processor is None:
            return samples, None, recent
        near = samples.reshape(-1).astype(np.float32) / 32768
        clean = self.processor.process(near, far)
        pcm = np.clip(clean * 32768, -32768, 32767).astype(np.int16).reshape(-1, 1)
        return pcm, self.processor.speech_probability, recent
