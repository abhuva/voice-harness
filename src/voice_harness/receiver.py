"""Buffered playback endpoint for one paired receiver connection."""

from collections import deque
from queue import Queue
from threading import Event, Lock
from uuid import UUID

from .phone_audio import PhoneOutput
from .playback import AudioPlayback


def receiver_id(value):
    if isinstance(value, str) and len(value) == 36:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return None


class PlaybackReceiver:
    def __init__(self, ident, name, address):
        self.ident, self.name, self.address = ident, name, address
        self.output = Queue(maxsize=12)
        self.events = deque(maxlen=128)
        self.closed = Event()
        self._lock = Lock()
        self._stop = None

    def bind_call(self, stop):
        with self._lock:
            self._stop = stop
            if self.closed.is_set():
                stop.set()

    def release_call(self):
        with self._lock:
            self._stop = None

    def disconnect(self):
        with self._lock:
            self.closed.set()
            if self._stop is not None:
                self._stop.set()

    def event(self, kind):
        self.events.append({"type": kind})

    def playback(self, settings, meter):
        return AudioPlayback(settings, meter, device_factory=lambda rate, stop: PhoneOutput(self, rate, stop))
