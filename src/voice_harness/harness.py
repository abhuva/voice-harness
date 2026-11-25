"""Claim one interactive job, own cancellation, and report its terminal result."""

from dataclasses import dataclass, field
from threading import Event, Lock
from uuid import uuid4

from .contracts import VoiceError


@dataclass(frozen=True)
class Job:
    id: str
    label: str
    stop: Event = field(default_factory=Event, compare=False)


@dataclass(frozen=True)
class Outcome:
    """A terminal result that reflects any effects already committed."""

    status: str


class Harness:
    def __init__(self, state):
        self.state = state
        self._lock = Lock()
        self._active = None
        self._running = False

    def submit(self, label):
        with self._lock:
            if self._active is not None:
                raise VoiceError("Another operation already owns the interactive slot.")
            job = Job(uuid4().hex, label)
            self.state.begin(job.id, job.label)
            self._active = job
            return job

    def cancel(self, job):
        with self._lock:
            if job is self._active:
                job.stop.set()

    def execute(self, job, operation):
        with self._lock:
            if job is not self._active or self._running:
                raise VoiceError("This job is already owned or finished.")
            self._running = True
        status = f"{job.label} interrupted."
        try:
            if not job.stop.is_set():
                result = operation(job.stop)
                if isinstance(result, Outcome):
                    status = result.status
                else:
                    status = f"{job.label} stopped." if job.stop.is_set() else result
            else:
                status = f"{job.label} stopped."
        except VoiceError as exc:
            status = str(exc)
        except Exception:
            status = f"{job.label} failed. Check the local settings, runtime files and devices."
        finally:
            with self._lock:
                try:
                    self.state.finish(job.id, status)
                finally:
                    self._active = None
                    self._running = False
        return status
