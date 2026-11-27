"""Sample a job independently of its worker and persist cold, idle and peak observations."""

from dataclasses import asdict, dataclass, replace
import json
from threading import Event, Lock, Thread
import time

from .resources import resident_sample


@dataclass(frozen=True)
class Measurement:
    startup_seconds: float | None
    elapsed_seconds: float
    host_cpu_seconds: float
    child_cpu_seconds: float | None
    ready_host_bytes: int | None
    ready_child_bytes: int | None
    idle_host_bytes: int | None
    idle_child_bytes: int | None
    peak_host_bytes: int | None
    peak_child_bytes: int | None
    startup_peak_host_bytes: int | None
    startup_peak_child_bytes: int | None
    active_peak_host_bytes: int | None
    active_peak_child_bytes: int | None


def _peak(a, b):
    if a is None:
        return b
    return a if b is None else max(a, b)


class Profiler:
    def __init__(self, state, ident, *, sample=resident_sample, interval=0.10):
        self.state, self.ident = state, ident
        self.sample, self.interval = sample, interval
        self.started, self.cpu_start = time.monotonic(), time.process_time()
        self.children = {}
        self.activity = set()
        self.ready_sample = None
        self.peak_host = self.peak_child = self.child_cpu = None
        self.idle_host = self.idle_child = None
        self.startup_host = self.startup_child = None
        self.active_host = self.active_child = None
        self._lock = Lock()
        self._stop = Event()
        self._thread = Thread(target=self._run, name="resource-profiler")

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join()
        self._observe()
        ready = self.ready_sample
        result = Measurement(
            ready.elapsed_seconds if ready else None, time.monotonic() - self.started,
            time.process_time() - self.cpu_start, self.child_cpu,
            ready.host_bytes if ready else None, ready.pocket_bytes if ready else None,
            self.idle_host, self.idle_child, self.peak_host, self.peak_child,
            self.startup_host, self.startup_child, self.active_host, self.active_child,
        )
        with self.state.connect() as db:
            db.execute("INSERT INTO measurement(job, sample) VALUES (?, ?) "
                       "ON CONFLICT(job) DO UPDATE SET sample = excluded.sample",
                       (self.ident, json.dumps(asdict(result))))

    def set_process(self, pid, *, warm=False, slot="worker"):
        with self._lock:
            if slot in self.children and pid == self.children[slot][0]:
                return
            baseline = 0.0
            if warm:
                sample = self.sample(self.started, self.cpu_start, pid)
                baseline = sample.pocket_cpu_seconds or 0.0
            self.children[slot] = (pid, baseline)

    def ready(self):
        return self._observe(ready=True)

    def set_activity(self, phase, active):
        with self._lock:
            if active:
                self.activity.add(phase)
            else:
                self.activity.discard(phase)

    def _run(self):
        while not self._stop.is_set():
            self._observe()
            self._stop.wait(self.interval)

    def _observe(self, *, ready=False):
        with self._lock:
            sample = self._sample_workers()
            self.peak_host = _peak(self.peak_host, sample.host_bytes)
            self.peak_child = _peak(self.peak_child, sample.pocket_bytes)
            self.child_cpu = _peak(self.child_cpu, sample.pocket_cpu_seconds)
            if self.ready_sample is None:
                self.startup_host = _peak(self.startup_host, sample.host_bytes)
                self.startup_child = _peak(self.startup_child, sample.pocket_bytes)
            if ready:
                self.ready_sample = sample
            if self.ready_sample is not None:
                if self.activity:
                    self.active_host = _peak(self.active_host, sample.host_bytes)
                    self.active_child = _peak(self.active_child, sample.pocket_bytes)
                else:
                    self.idle_host = _peak(self.idle_host, sample.host_bytes)
                    self.idle_child = _peak(self.idle_child, sample.pocket_bytes)
            return sample

    def _sample_workers(self):
        if not self.children:
            return self.sample(self.started, self.cpu_start, None)
        children = [(self.sample(self.started, self.cpu_start, pid), baseline)
                    for pid, baseline in self.children.values()]
        memory = [sample.pocket_bytes for sample, _ in children if sample.pocket_bytes is not None]
        cpu = [max(0.0, sample.pocket_cpu_seconds - baseline) for sample, baseline in children
               if sample.pocket_cpu_seconds is not None]
        return replace(children[0][0], pocket_bytes=sum(memory) if memory else None,
                       pocket_cpu_seconds=sum(cpu) if cpu else None)


def measurement_for(state, job_id):
    with state.connect() as db:
        row = db.execute("SELECT sample FROM measurement WHERE job = ?", (job_id,)).fetchone()
    return Measurement(**json.loads(row[0])) if row else None
