"""Bounded audio and control buffers shared by the call and phone transport."""

from collections import deque
from contextlib import contextmanager
from queue import Empty, Full, Queue
from threading import Event
from time import monotonic, sleep

import numpy as np

from .contracts import MicrophoneDropout, VoiceError
from .echo import FRAME_SAMPLES
from .output_audio import output_levels
from .playback import AudioPlayback
from .speech_input import SpeechInput


INPUT_RATE = 16_000
OUTPUT_RATE = 24_000
OUTPUT_SAMPLES = OUTPUT_RATE // 50
OUTPUT_BYTES = 2 * OUTPUT_SAMPLES
MAX_TALK_SAMPLES = 120 * INPUT_RATE


def empty(queue):
    while True:
        try:
            queue.get_nowait()
        except Empty:
            return


class PhoneAudio:
    def __init__(self, mode):
        self.mode = mode
        self.input = Queue(maxsize=300)
        self.output = Queue(maxsize=12)
        self.events = deque(maxlen=128)
        self.closed = Event()
        self.finished = Event()
        self.listening = Event()
        self.call_stop = None

    def bind_call(self, stop):
        self.call_stop = stop
        if self.closed.is_set():
            stop.set()

    def disconnect(self):
        self.closed.set()
        if self.call_stop is not None and not self.finished.is_set():
            self.call_stop.set()

    def event(self, kind, **values):
        self.events.append({"type": kind, **values})

    def say(self, text):
        self.event("status", text=text)

    def message(self, message):
        self.event("message", id=message.id, speaker=message.speaker, body=message.body, status=message.status)

    def finish(self, text):
        self.event("ended", text=text)
        self.finished.set()

    def feed(self, item):
        if not self.listening.is_set() or self.closed.is_set():
            return True
        try:
            self.input.put_nowait(item)
            return True
        except Full:
            self.say("Phone audio fell behind. The call was stopped.")
            self.disconnect()
            return False

    @contextmanager
    def open(self):
        self.listening.set()
        self.event("ready")
        try:
            yield
        finally:
            self.listening.clear()
            empty(self.input)

    def discard_buffered(self):
        if self.mode == "rtc":
            empty(self.input)

    def take(self, stop):
        while not self.closed.is_set() and not stop.is_set():
            try:
                return self.input.get(timeout=0.05)
            except Empty:
                continue
        return None

    def read(self, stop):
        item = self.take(stop)
        if item is not None:
            samples, at, missing = item
            return np.frombuffer(samples, dtype="<i2").reshape(FRAME_SAMPLES, 1), at, missing
        return None

    def microphone(self, meter):
        if self.mode == "rtc":
            return SpeechInput(self, BrowserEchoControl(), meter)
        return PushToTalk(self, meter)

    def playback(self, settings, meter):
        return AudioPlayback(settings, meter, device_factory=lambda rate, stop: PhoneOutput(self, rate, stop))


class BrowserEchoControl:
    def clean(self, samples, _at):
        return samples, None, False


class PushToTalk:
    def __init__(self, audio, meter):
        self.audio = audio
        self.meter = meter
        self.last_speech_at = None

    def open(self):
        return self.audio.open()

    def discard_buffered(self):
        pass

    def capture(self, settings, stop, stage, accept, *, speech_started, reply_pending):
        held = False
        samples = 0
        previous_meter = 0.0
        self.last_speech_at = None
        stage("Hold the phone’s talk button")
        while (item := self.audio.take(stop)) is not None:
            if isinstance(item, str):
                if item == "press":
                    held = settings.interrupt or not reply_pending()
                elif held and samples and item == "release":
                    return True
                elif samples:
                    raise MicrophoneDropout("Phone recording cancelled.")
                else:
                    held = False
                continue
            if not held:
                continue
            block = np.frombuffer(item, dtype="<i2").reshape(-1, 1)
            if not samples:
                speech_started()
            samples += len(block)
            if samples > MAX_TALK_SAMPLES:
                raise MicrophoneDropout("Hold to talk for up to two minutes.")
            self.last_speech_at = monotonic()
            if self.last_speech_at - previous_meter >= 0.1:
                previous_meter = self.last_speech_at
                self.meter(*output_levels((block.astype("<f4") / 32768).tobytes()))
            accept(block, True)
        return False


class PhoneOutput:
    def __init__(self, audio, rate, stop):
        if rate != OUTPUT_RATE:
            raise VoiceError("The phone audio sink expects 24 kHz speech.")
        self.audio, self.stop_token = audio, stop
        self.pending = bytearray()

    def start(self):
        pass

    def _send(self, packet):
        while not self.stop_token.is_set() and not self.audio.closed.is_set():
            try:
                self.audio.output.put(packet, timeout=0.04)
                return
            except Full:
                continue

    def write(self, block):
        samples = np.frombuffer(block, dtype="<f4")
        self.pending.extend((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        while len(self.pending) >= OUTPUT_BYTES:
            self._send(bytes(self.pending[:OUTPUT_BYTES]))
            del self.pending[:OUTPUT_BYTES]

    def stop(self):
        if self.pending:
            self._send(bytes(self.pending).ljust(OUTPUT_BYTES, b"\0"))
        while not self.audio.output.empty() and not self.stop_token.is_set() and not self.audio.closed.is_set():
            sleep(0.02)

    def abort(self):
        empty(self.audio.output)
        self.audio.event("flush")

    def close(self):
        self.pending.clear()
