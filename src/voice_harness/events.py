"""Bounded, nonblocking fan-out for small lifecycle notifications."""

from contextlib import contextmanager
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Lock

from .work_types import ArtifactRef, Lineage


@dataclass(frozen=True)
class ModelNotice:
    id: str
    capability: str
    resident_bytes: int | None = None


@dataclass(frozen=True)
class Event:
    kind: str
    scope: Lineage | None
    artifact: ArtifactRef | None = None
    model: ModelNotice | None = None


class EventBus:
    def __init__(self):
        self._subscribers = {}
        self._lock = Lock()

    @contextmanager
    def subscribe(self, kinds=(), *, workflow_id=None):
        inbox = Queue(maxsize=64)
        with self._lock:
            self._subscribers[inbox] = (frozenset(kinds), workflow_id)
        try:
            yield inbox
        finally:
            with self._lock:
                self._subscribers.pop(inbox, None)

    def publish(self, event):
        with self._lock:
            for inbox, (kinds, workflow_id) in self._subscribers.items():
                if kinds and event.kind not in kinds:
                    continue
                if workflow_id and (event.scope is None or event.scope.workflow_id != workflow_id):
                    continue
                try:
                    inbox.put_nowait(event)
                except Full:
                    # Durable job and artifact state remains available by ID.
                    try:
                        inbox.get_nowait()
                    except Empty:
                        pass
                    inbox.put_nowait(event)
