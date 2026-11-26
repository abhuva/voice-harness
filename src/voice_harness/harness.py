"""Own job admission, claim lifetimes, events and cancellation."""

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from threading import Condition, Event as StopToken, Lock
from collections.abc import Callable, Mapping

from .admission import Admission
from .catalog import ArtifactAccess, Catalog
from .contracts import VoiceError
from .events import Event, EventBus
from .profiler import Profiler
from .residency import ModelKey, ModelResidency
from .telemetry import Telemetry
from .work_store import Work, WorkStore
from .work_types import ArtifactRef, DataGrant, Lineage, ResourceProfile, WorkRequest


@dataclass(frozen=True)
class Job:
    work: Work
    label: str
    profile: ResourceProfile
    models: tuple[ModelKey, ...] = ()
    strict_memory: bool = False
    recognition_on_demand: bool = False
    stop: StopToken = field(default_factory=StopToken, compare=False)

    @property
    def id(self):
        return self.work.scope.job_id


@dataclass(frozen=True)
class Outcome:
    """A terminal result that reflects any effects already committed."""

    status: str


@dataclass(frozen=True)
class WorkerSpec:
    id: str
    capabilities: Mapping[str, Callable[[Work, ArtifactAccess, StopToken], ArtifactRef | None]]
    estimate: Callable[[Work], ResourceProfile]
    cloud: bool = False


class Harness:
    def __init__(self, state, *, admission=None):
        self.state = state
        self.events = EventBus()
        self.telemetry = Telemetry()
        self.models = ModelResidency(self.events)
        self.queue = WorkStore(state)
        self.catalog = Catalog(state)
        self.admission = admission or Admission()
        self._lock = Lock()
        self._changed = Condition(self._lock)
        self._active = None
        self._last_call_models = ()
        self._background = {}
        self._reservations = {}
        self._profilers = {}
        self._running = False
        self._admitted = False
        self._recognition_loading = False
        self._call_phases = set()

    def submit(self, label, *, profile=None, models=(), strict_memory=False, recognition_on_demand=False):
        request = WorkRequest("desktop." + label)
        profile = self.queue.calibrated(request.capability, profile or ResourceProfile())
        with self._lock:
            if self._active is not None:
                raise VoiceError("Another operation already owns the interactive slot.")
            work = self.queue.enqueue(request, foreground=True)
            job = Job(work, label, profile, tuple(models), strict_memory, recognition_on_demand)
            self.state.begin(job.id, job.label)
            self._active = job
            self._yield_background()
        self.events.publish(Event("job.queued", job.work.scope))
        return job

    def _yield_background(self, *, accelerator_only=False):
        for ident, (work_item, stop) in self._background.items():
            if work_item.request.yield_to_interactive and (
                not accelerator_only or self._reservations[ident].accelerator_bytes
            ):
                stop.set()

    @contextmanager
    def recognition_startup(self, job, stop, waiting, *, load_bytes=0):
        """Reserve the cold recognizer load inside an already admitted call."""
        with self._changed:
            active_call = self._active is job and self._admitted and not stop.is_set()
            if active_call:
                self._recognition_loading = True
                self._yield_background()
        if not active_call:
            yield False
            return

        profile = replace(job.profile, resident_bytes=0)
        if load_bytes:
            profile = replace(profile, extra_bytes=max(load_bytes, profile.extra_bytes or 0),
                              advisory_memory=True)
        admitted = False
        notified = None
        try:
            while not stop.is_set():
                self.models.evict_idle(only="decision", scope=job.work.scope)
                with self._changed:
                    active = tuple(self._reservations[ident] for ident in self._background)
                    yielding = any(work.request.yield_to_interactive for work, _ in self._background.values())
                    if not yielding and self.admission.allows(
                        profile, active, interactive=True, strict_memory=job.strict_memory,
                    ):
                        admitted = True
                        break
                status = self._memory_wait_status(profile, active, job.strict_memory)
                if status != notified:
                    waiting("Speech recognition · " + status)
                    notified = status
                with self._changed:
                    if not stop.is_set():
                        self._changed.wait(timeout=0.25)
            yield admitted and not stop.is_set()
        finally:
            with self._changed:
                self._recognition_loading = False
                self._changed.notify_all()

    def cancel(self, job):
        with self._lock:
            if job is self._active:
                job.stop.set()
                self._changed.notify_all()

    @contextmanager
    def _profile(self, ident):
        with Profiler(self.state, ident) as profiler:
            with self._lock:
                self._profilers[ident] = profiler
            try:
                yield
            finally:
                with self._lock:
                    self._profilers.pop(ident, None)

    def watch_process(self, job, pid, *, warm=False, slot="worker"):
        self.watch_job_process(job.id, pid, warm=warm, slot=slot)

    def watch_job_process(self, ident, pid, *, warm=False, slot="worker"):
        with self._lock:
            profiler = self._profilers.get(ident)
        if profiler is not None:
            profiler.set_process(pid, warm=warm, slot=slot)

    def mark_ready(self, ident):
        with self._lock:
            profiler = self._profilers.get(ident)
        return profiler.ready() if profiler is not None else None

    def ready(self, job):
        sample = self.mark_ready(job.id)
        if job.label == "Call" and sample is not None and None not in (sample.host_bytes, sample.pocket_bytes):
            # Host available memory already excludes the live working sets. Keep room for
            # the call's transient allocations even on a first run with no calibration.
            with self._lock:
                held = self._reservations[job.id]
                if held.memory is None or held.advisory_memory:
                    used = sample.host_bytes + sample.pocket_bytes
                    remaining = 256 * 1024 ** 2
                    if not job.recognition_on_demand:
                        remaining = max(remaining, (job.profile.memory or 0) - used)
                    self._reservations[job.id] = replace(held, resident_bytes=0, extra_bytes=remaining)
        return sample

    def _admit_desktop(self, job, waiting):
        waiting_with_other_work = None
        self.models.evict_idle(only="decision", scope=job.work.scope)
        while not job.stop.is_set():
            profile = job.profile
            if job.recognition_on_demand and profile.advisory_memory and profile.memory is not None:
                # Calibrated ready samples precede lazy STT. Its peak demand is checked on wake.
                profile = replace(profile, extra_bytes=0)
            if profile.resident_bytes is not None:
                credit = self.models.credit(job.models)
                profile = replace(profile, resident_bytes=max(0, profile.resident_bytes - credit))
            with self._changed:
                if job.stop.is_set():
                    return False
                active = tuple(self._reservations.values())
                if self.admission.allows(profile, active, interactive=True, strict_memory=job.strict_memory):
                    if not self.queue.claim(job.work, "desktop", job.profile):
                        raise VoiceError("Could not claim the desktop job.")
                    self._admitted = True
                    self._reservations[job.id] = profile
                    if job.label == "Call":
                        self._last_call_models = job.models
                    return True
            if self.models.evict_idle(keep=job.models, scope=job.work.scope):
                continue
            if waiting_with_other_work != bool(active) and not job.stop.is_set():
                waiting_with_other_work = bool(active)
                waiting(self._memory_wait_status(profile, active, job.strict_memory))
            with self._changed:
                if not job.stop.is_set():
                    self._changed.wait(timeout=0.25)
        return False

    def _memory_wait_status(self, profile, active, strict_memory):
        if active:
            return "Waiting for memory while another job is running"
        available = self.admission.available()
        if available is not None and profile.memory is not None:
            mib = 1024 ** 2
            predicted = (profile.memory + mib - 1) // mib
            status = f"Waiting for memory · estimated {predicted} MiB, {available // mib} MiB available"
        else:
            status = "Waiting for available memory"
        if strict_memory and profile.advisory_memory:
            status += " · Stop to change SST settings"
        return status

    def execute(self, job, operation, *, waiting=lambda _text: None):
        with self._lock:
            if job is not self._active or self._running:
                raise VoiceError("This job is already owned or finished.")
            self._running = True
        status, terminal = f"{job.label} stopped.", "cancelled"
        try:
            if self._admit_desktop(job, waiting):
                self.events.publish(Event("job.claimed", job.work.scope))
                with self._profile(job.id):
                    result = operation(job.stop) if not job.stop.is_set() else None
                    if isinstance(result, Outcome):
                        status = result.status
                    elif not job.stop.is_set():
                        status = result
                    terminal = "cancelled" if job.stop.is_set() else "completed"
        except VoiceError as exc:
            status, terminal = str(exc), "failed"
        except Exception:
            status = f"{job.label} failed. Check the local settings, runtime files and devices."
            terminal = "failed"
        finally:
            with self._lock:
                try:
                    if self._admitted:
                        terminal = self.queue.finish(job.work, "desktop", terminal) or terminal
                    else:
                        self.queue.cancel(job.id)
                    self.state.finish(job.id, status)
                finally:
                    self._active = None
                    self._call_phases.clear()
                    self._running = False
                    self._admitted = False
                    self._reservations.pop(job.id, None)
                    self._changed.notify_all()
        self.events.publish(Event("job." + terminal, job.work.scope))
        return status

    def submit_work(self, request, *, workflow_id=None, turn_id=None):
        for grant in request.grants:
            self.catalog.describe(grant.ref)
        if request.for_next_turn and (not workflow_id or not turn_id):
            raise ValueError("A result for a later turn requires call and turn IDs.")
        work = self.queue.enqueue(request, workflow_id=workflow_id, turn_id=turn_id)
        self.events.publish(Event("job.queued", work.scope))
        return work

    def cancel_work(self, work):
        ident = work.scope.job_id
        with self._lock:
            changed = self.queue.cancel(ident)
            if ident in self._background:
                self._background[ident][1].set()
        if changed:
            status = self.queue.status(ident)
            self.events.publish(Event("job." + status, work.scope))

    def _claim_next(self, worker, accelerator_available):
        with self._lock:
            if self._active is not None and not self._admitted:
                return None, "waiting for foreground startup"
            if self._recognition_loading:
                return None, "waiting for speech recognition to load"
            if self._background:
                return None, "waiting for another background job"
            call_active = self._active is not None and self._active.label == "Call"
            reserve = self.queue.call_reservation() if not call_active else 0
            resident = self.models.credit(self._last_call_models) if not call_active else 0
            reason = "waiting for a queued job"
            active = tuple(self._reservations.values())
            for work in self.queue.queued():
                request = work.request
                if request.capability not in worker.capabilities or request.cloud != worker.cloud:
                    continue
                profile = worker.estimate(work)
                if profile.accelerator_bytes and self._call_phases:
                    reason = ("waiting for dictation to finish" if "dictation" in self._call_phases
                              else "waiting for the current reply to finish")
                    continue
                blocker = self.admission.blocker(profile, active, interactive=False, call_active=call_active,
                                                 call_reserve=reserve, call_resident=resident,
                                                 accelerator_available=accelerator_available)
                if blocker:
                    reason = blocker
                    continue
                if worker.cloud and not self.admission.allows_remote(profile):
                    reason = "waiting for remote budget"
                    continue
                if self.queue.claim(work, worker.id, profile):
                    if worker.cloud:
                        self.admission.charge_remote(profile)
                    ident = work.scope.job_id
                    token = StopToken()
                    self._reservations[ident] = profile
                    self._background[ident] = (work, token)
                    return (work, token), None
        return None, reason

    def run_next(self, worker, *, accelerator_available=None, waiting=lambda _text: None):
        """Claim and run one eligible job on the calling worker thread."""
        claim, reason = self._claim_next(worker, accelerator_available)
        if claim is None:
            waiting(reason)
            return None
        work, token = claim
        ident = work.scope.job_id
        self.events.publish(Event("job.claimed", work.scope))
        artifact, terminal = None, "interrupted"
        try:
            with self._profile(ident):
                if not token.is_set():
                    handler = worker.capabilities[work.request.capability]
                    artifact = handler(work, self.catalog.access(work), token)
                    if artifact and self.catalog.describe(artifact).producer != work.scope:
                        raise VoiceError("A job must publish its own output.")
                terminal = "cancelled" if token.is_set() else "completed"
        except Exception:
            artifact, terminal = None, "failed"
        finally:
            with self._lock:
                try:
                    terminal = self.queue.finish(work, worker.id, terminal,
                                                 artifact if terminal == "completed" else None) or terminal
                finally:
                    self._background.pop(ident, None)
                    self._reservations.pop(ident, None)
                    self._changed.notify_all()
        self.events.publish(Event("job." + terminal, work.scope))
        if artifact and terminal == "completed":
            self.events.publish(Event("artifact.ready", work.scope, artifact))
        return work

    def call_work(self, job, *, use_context=False, cloud=True):
        return CallWork(self, job, use_context, cloud)


@dataclass(frozen=True)
class ReplyContext:
    refs: tuple[ArtifactRef, ...]
    read: Callable[[ArtifactRef], bytes]


class CallWork:
    def __init__(self, harness, job, use_context, cloud):
        self.harness = harness
        self.job = job
        self.use_context = use_context
        self.cloud = cloud

    def activity(self, phase, active):
        harness = self.harness
        with harness._lock:
            profiler = harness._profilers.get(self.job.id)
            if harness._active is self.job:
                if active:
                    harness._call_phases.add(phase)
                    harness._yield_background(accelerator_only=True)
                else:
                    harness._call_phases.discard(phase)
        if profiler is not None:
            profiler.set_activity(phase, active)

    @contextmanager
    def reply(self, turn_id):
        owner = "reply-connector"
        harness = self.harness
        with harness._lock:
            if self.job is not harness._active or not harness._admitted or self.job.stop.is_set():
                raise VoiceError("Call stopped before reply submission.")
        harness.events.publish(Event("turn.ready", Lineage(self.job.id, self.job.id, turn_id)))
        refs = harness.queue.context(self.job.id, turn_id) if self.use_context and not self.cloud else ()
        request = WorkRequest("reply.generate", grants=tuple(DataGrant(ref) for ref in refs), cloud=self.cloud)
        job = harness.queue.enqueue(request, workflow_id=self.job.id, turn_id=turn_id, foreground=True)
        harness.events.publish(Event("job.queued", job.scope))
        if not harness.queue.claim(job, owner, ResourceProfile(
                resident_bytes=0, extra_bytes=0, basis="Covered by the parent call reservation")):
            raise VoiceError("Could not claim the reply job.")
        harness.events.publish(Event("job.claimed", job.scope))
        terminal = "failed"
        self.activity("reply", True)
        try:
            yield ReplyContext(refs, harness.catalog.access(job).read)
            terminal = "cancelled" if self.job.stop.is_set() else "completed"
        finally:
            self.activity("reply", False)
            harness.queue.finish(job, owner, terminal)
            harness.events.publish(Event("job." + terminal, job.scope))

    def consumed(self, refs):
        self.harness.queue.consume_context(self.job.id, refs)
