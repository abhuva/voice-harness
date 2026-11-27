"""Low-volume call timings and module activity. Contains no conversation text."""

from collections import Counter, OrderedDict
from contextlib import contextmanager, nullcontext
from datetime import datetime
from threading import Lock
from time import monotonic


class TurnTiming:
    def __init__(self):
        self._marks = {}
        self._lock = Lock()
        self.started = None

    def mark(self, name, at=None):
        with self._lock:
            self._marks.setdefault(name, monotonic() if at is None else at)

    def seconds(self):
        def interval(first, last):
            if first in self._marks and last in self._marks:
                return max(0, self._marks[last] - self._marks[first])
            return None

        with self._lock:
            return (
                interval("voice_end", "transcript_ready"),
                interval("send", "first_text"),
                interval("first_text", "first_audio"),
            )


class Telemetry:
    def __init__(self):
        self._lock = Lock()
        self._turns = OrderedDict()
        self._modules = Counter()

    def add_turn(self, scope, timing):
        with self._lock:
            timing.started = datetime.now()
            self._turns[scope] = timing
            if len(self._turns) > 100:
                self._turns.popitem(last=False)

    @contextmanager
    def online(self, *names):
        with self._lock:
            self._modules.update(names)
        try:
            yield
        finally:
            with self._lock:
                self._modules.subtract(names)

    def snapshot(self):
        with self._lock:
            return frozenset(name for name, count in self._modules.items() if count), tuple(self._turns.items())


def online(telemetry, *names):
    return telemetry.online(*names) if telemetry is not None else nullcontext()
