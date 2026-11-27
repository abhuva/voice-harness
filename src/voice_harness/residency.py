"""Own one warm model per capability and lend it to foreground jobs."""

from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from threading import Condition, Timer
from time import monotonic
from typing import Literal
from uuid import uuid4

from .contracts import VoiceError
from .events import Event, ModelNotice
from .resources import working_set


@dataclass(frozen=True)
class ModelKey:
    kind: Literal["stt", "tts", "decision"]
    configuration: tuple


@dataclass(frozen=True)
class Resident:
    id: str
    key: ModelKey
    model: object
    memory: int | None


@dataclass(frozen=True)
class ModelObservation:
    key: ModelKey
    state: str
    memory: int | None
    id: str | None = None
    pid: int | None = None


_LABEL = {"stt": "speech recognition", "tts": "Pocket TTS", "decision": "Jevos v5"}


class ModelResidency:
    def __init__(self, events, *, measure=working_set):
        self.events = events
        self.measure = measure
        self._entries = {}
        self._claimed = set()
        self._loading = {}
        self._lock = Condition()
        self._evicting = set()
        self._timers = {}
        self._stt_load_seconds = None
        self._stt_released_bytes = None

    def snapshot(self):
        return tuple((row.key, row.state, row.memory) for row in self.observations())

    def observations(self):
        with self._lock:
            rows = [ModelObservation(entry.key, "In use" if kind in self._claimed else "Warm", entry.memory,
                                     entry.id, getattr(entry.model, "process_id", None))
                    for kind, entry in self._entries.items() if entry.model.ready]
            rows.extend(ModelObservation(entry.key, "Loading", None, entry.id, getattr(entry.model, "process_id", None))
                        for entry in self._loading.values())
            return tuple(rows)

    def recognition_relief(self):
        with self._lock:
            return self._stt_load_seconds, self._stt_released_bytes

    def set_recognition_idle(self, seconds, *, scope=None):
        """Cancel expiry in warm mode or start a timer if dynamic mode has no deadline yet."""
        with self._lock:
            if seconds is None:
                self._cancel_timer("stt")
            elif "stt" in self._entries and "stt" not in self._claimed and "stt" not in self._timers:
                self._expire_at("stt", monotonic() + seconds, scope)

    def credit(self, keys):
        """Measured resident bytes already reflected in host available memory."""
        with self._lock:
            return sum(entry.memory or 0 for entry in self._entries.values()
                       if entry.key in keys and entry.model.ready)

    def _notify(self, status, entry, scope):
        self.events.publish(Event(f"model.{status}", scope,
                                  model=ModelNotice(entry.id, entry.key.kind, entry.memory)))

    def _dispose(self, entry, scope, *, measure_release=False):
        pid = getattr(entry.model, "process_id", None)
        before = self.measure(pid) if measure_release else None
        entry.model.close()
        if measure_release:
            after = 0 if pid else self.measure(None)
            with self._lock:
                self._stt_released_bytes = None if None in (before, after) else before - after
        self._notify("unloaded", entry, scope)

    def evict_idle(self, *, keep=(), only=None, scope=None):
        """Drop idle models outside the requested configuration set."""
        with self._lock:
            kinds = [kind for kind, entry in self._entries.items()
                     if kind not in self._claimed and entry.key not in keep and (only is None or kind == only)]
            entries = [self._take_idle(kind) for kind in kinds]
        with ExitStack() as cleanup:
            for entry in entries:
                cleanup.callback(self._finish_eviction, entry, scope)
        return bool(entries)

    def _cancel_timer(self, kind):
        timer = self._timers.pop(kind, None)
        if timer is not None:
            timer.cancel()

    def _take_idle(self, kind):
        self._cancel_timer(kind)
        self._evicting.add(kind)
        return self._entries.pop(kind)

    def _finish_eviction(self, entry, scope, *, measure_release=False):
        try:
            self._dispose(entry, scope, measure_release=measure_release)
        finally:
            with self._lock:
                self._evicting.remove(entry.key.kind)
                self._lock.notify_all()

    def _expire_at(self, kind, deadline, scope):
        def expire():
            with self._lock:
                if self._timers.get(kind) is not timer:
                    return
                entry = self._take_idle(kind)
            self._finish_eviction(entry, scope, measure_release=kind == "stt")

        timer = Timer(max(0, deadline - monotonic()), expire)
        timer.daemon = True
        self._timers[kind] = timer
        timer.start()

    @contextmanager
    def borrow(self, key, create, stop, stage, scope, *, before_load=None, expires_at=None):
        kind = key.kind
        with self._lock:
            while kind in self._evicting and not stop.is_set():
                self._lock.wait(timeout=0.05)
            cancelled = stop.is_set()
            if not cancelled:
                if kind in self._claimed:
                    raise VoiceError("The speech model is already in use.")
                self._cancel_timer(kind)
                self._claimed.add(kind)
                entry = self._entries.get(kind)
        if cancelled:
            yield None
            return

        try:
            if entry is not None and (entry.key != key or not entry.model.ready):
                with self._lock:
                    del self._entries[key.kind]
                previous, entry = entry, None
                self._dispose(previous, scope)
            if entry is None:
                with (before_load() if before_load is not None else nullcontext(True)) as admitted:
                    if admitted and not stop.is_set():
                        try:
                            entry = self._load(key, create, stop, stage, scope)
                        finally:
                            with self._lock:
                                self._loading.pop(kind, None)
                                if entry is not None:
                                    self._entries[kind] = entry
                        if entry is not None:
                            self._notify("ready", entry, scope)
            else:
                stage(f"Reusing warm {_LABEL[key.kind]}")
                self._notify("reused", entry, scope)
            yield entry.model if entry is not None and not stop.is_set() else None
        finally:
            try:
                if entry is not None and not entry.model.ready:
                    with self._lock:
                        self._entries.pop(key.kind, None)
                    self._notify("failed", entry, scope)
                    self._dispose(entry, scope)
            finally:
                with self._lock:
                    self._claimed.remove(kind)
                    if kind in self._entries and expires_at is not None:
                        self._expire_at(kind, expires_at(), scope)

    def _load(self, key, create, stop, stage, scope):
        ident = uuid4().hex
        self.events.publish(Event("model.loading", scope, model=ModelNotice(ident, key.kind)))
        stage(f"Loading {_LABEL[key.kind]}")
        before = self.measure(None)
        started = monotonic()
        model = None
        kept = False
        try:
            model = create()
            with self._lock:
                self._loading[key.kind] = Resident(ident, key, model, None)
            model.prepare(stop)
            if stop.is_set():
                return None
            if not model.ready:
                raise VoiceError("The speech model did not become ready.")
            pid = getattr(model, "process_id", None)
            after = self.measure(pid)
            memory = after
            if not pid:
                memory = max(0, after - before) if before is not None and after is not None else None
            if key.kind == "stt":
                with self._lock:
                    self._stt_load_seconds = monotonic() - started
            kept = True
            return Resident(ident, key, model, memory)
        except Exception:
            self.events.publish(Event("model.failed", scope, model=ModelNotice(ident, key.kind)))
            raise
        finally:
            if model is not None and not kept:
                self._dispose(Resident(ident, key, model, None), scope)
