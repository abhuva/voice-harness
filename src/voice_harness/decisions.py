"""Observe complete conversation messages and run resource-admitted local scoring."""

from collections import deque
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from threading import Condition, Event, Thread
from time import monotonic, time
from uuid import uuid4

from .contracts import VoiceError
from .harness import WorkerSpec
from .hardware import available_nvidia_vram
from .jevos import Jevos, MODEL, THREADS, installation
from .jevos_vulkan import JevosVulkan, VULKAN_DIRECTORY, VULKAN_LABEL, vulkan_installation
from .residency import ModelKey
from .work_types import ResourceProfile, WorkRequest


QUESTIONS = (
    "Is anyone expressing anger, irritation or frustration?",
    "Is anyone expressing anxiety, fear or worry?",
    "Is anyone expressing hope or optimism?",
    "Is anyone expressing joy or happiness?",
    "Is anyone expressing gratitude or feeling fortunate?",
    "Are they discussing physical activity or exercise?",
    "Are they discussing programming or software development?",
    "Are they discussing a game or playing one?",
    "Are they discussing eating, drinking or preparing food?",
    "Is anyone offering or seeking comfort or reassurance?",
    "Is anyone expressing affection or talking about hugging or cuddling?",
    "Is the conversation working through a complex topic that could benefit from deeper analysis?",
)
CAPABILITY = "decision.score"
MIB = 1024 ** 2
# An initial peak allowance for OpenVINO weights, context and runtime buffers.
PROFILE = ResourceProfile(resident_bytes=800 * MIB, extra_bytes=300 * MIB,
                          cpu_threads=THREADS, basis="Jevos v5 OpenVINO INT8 · initial memory estimate")
VULKAN_PROFILE = ResourceProfile(resident_bytes=1024 * MIB, extra_bytes=128 * MIB, cpu_threads=THREADS,
                                 accelerator_bytes=1280 * MIB, basis="Jevos v5 Vulkan Q8 · GTX 1050 Ti fixture estimates")
DEVICES = {"cpu": "CPU (OpenVINO)", "vulkan": "GPU (Vulkan)"}
VRAM_RESERVE = 256 * MIB


@dataclass(frozen=True)
class DecisionSettings:
    enabled: bool
    directory: str
    questions: tuple[str, ...]
    backend: str = "cpu"


def read_settings(path, assets):
    default = DecisionSettings(False, str(assets / "archive-models" / "jevos-v5-openvino-int8-v1"), QUESTIONS)
    try:
        values = json.loads(path.read_text("utf-8"))
        return validate_settings(values["enabled"], values["directory"], values["questions"],
                                 backend=values.get("backend", "cpu"), check_install=False)
    except FileNotFoundError:
        try:
            installation(default.directory)
        except (OSError, VoiceError):
            return default
        return replace(default, enabled=True)
    except (OSError, KeyError, TypeError, ValueError, VoiceError):
        return default


def validate_settings(enabled, directory, questions, *, backend="cpu", vulkan_directory=None, check_install=True):
    if type(enabled) is not bool or not isinstance(directory, str) or len(directory) > 4096:
        raise VoiceError("Use a local Jevos installation folder.")
    if backend not in ("cpu", "vulkan"):
        raise VoiceError("Choose CPU or GPU for Jevos.")
    if not isinstance(questions, (list, tuple)) or not 1 <= len(questions) <= 24:
        raise VoiceError("Enter 1 to 24 yes/no questions, one per line.")
    if any(not isinstance(q, str) or not q.strip() or len(q) > 240 or "\x00" in q for q in questions):
        raise VoiceError("Use between 1 and 240 characters per question.")
    questions = tuple(q.strip() for q in questions)
    if len(set(questions)) != len(questions):
        raise VoiceError("Each Jevos question must be unique.")
    if enabled and check_install:
        if backend == "vulkan":
            vulkan_installation(vulkan_directory)
        elif not directory.strip():
            raise VoiceError("Choose the local Jevos v5 installation folder.")
        else:
            installation(directory)
    return DecisionSettings(enabled, directory.strip(), questions, backend)


def save_settings(path, settings):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".jevos-{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Score:
    tick: int
    observed: float
    model: str
    probabilities: dict[str, float]


class DecisionHistory:
    def __init__(self, state):
        self.state = state

    def save(self, work, observed, questions, scores, *, model=MODEL):
        with self.state.connect() as db:
            db.execute("""
                INSERT INTO decision_score(job, model, observed, questions, scores) VALUES (?, ?, ?, ?, ?)
            """, (work.scope.job_id, model, observed, json.dumps(questions), json.dumps(scores)))

    def prune(self):
        with self.state.connect() as db:
            db.execute("""
                DELETE FROM decision_score WHERE job IN (
                    SELECT id FROM work WHERE status NOT IN ('running', 'completed')
                ) OR tick IN (SELECT tick FROM decision_score JOIN work ON work.id = decision_score.job
                    WHERE work.status = 'completed' ORDER BY tick DESC LIMIT -1 OFFSET 100)
            """)
            rows = db.execute("""
                DELETE FROM work WHERE id IN (
                    SELECT id FROM work WHERE json_extract(request, '$.capability') = ?
                    AND status NOT IN ('queued', 'running', 'cancelling')
                    AND id NOT IN (SELECT job FROM decision_score)
                    ORDER BY rowid DESC LIMIT -1 OFFSET 100
                ) RETURNING id
            """, (CAPABILITY,)).fetchall()
            db.executemany("DELETE FROM measurement WHERE job = ?", rows)

    def latest(self, tick):
        with self.state.connect() as db:
            db.execute("BEGIN")
            newest = db.execute("""
                SELECT MAX(tick) FROM decision_score JOIN work ON work.id = decision_score.job
                WHERE work.status = 'completed'
            """).fetchone()[0]
            if newest == tick:
                return tick, None
            rows = db.execute("""
                SELECT tick, observed, model, questions, scores FROM decision_score
                JOIN work ON work.id = decision_score.job WHERE work.status = 'completed'
                ORDER BY tick DESC LIMIT 100
            """).fetchall()
        return newest, [Score(ident, at, model, dict(zip(json.loads(questions), json.loads(scores))))
                        for ident, at, model, questions, scores in reversed(rows)]


@dataclass(frozen=True)
class Sample:
    workflow: str
    messages: tuple
    observed: float
    settings: DecisionSettings
    invalidated: Event


class Cancellation:
    def __init__(self, job, invalidated):
        self.job = job
        self.invalidated = invalidated

    def is_set(self):
        return self.job.is_set() or self.invalidated.is_set()


class Decisions:
    """One scored snapshot in flight and one newest pending snapshot, both in RAM."""

    def __init__(self, harness, settings, *, engine=Jevos, gpu_engine=JevosVulkan, available_vram=available_nvidia_vram):
        self.harness = harness
        self.history = DecisionHistory(harness.state)
        self.settings = settings
        self.engines = {"cpu": engine, "vulkan": gpu_engine}
        self.vulkan_directory = harness.state.path.parent / VULKAN_DIRECTORY
        self.available_vram = available_vram
        self._messages = deque(maxlen=3)
        self._changed = Condition()
        self._pending = None
        self._invalidated = Event()
        self._closing = False
        self._thread = None
        self._warm_since = None
        self.status = self._idle()

    def seed(self, message):
        with self._changed:
            if self.settings.enabled and not self._closing and message.body.strip():
                self._messages.append(message)

    def observe(self, workflow, message):
        with self._changed:
            if self._closing or not self.settings.enabled:
                return
            if not message.body.strip() or any(prior.id == message.id for prior in self._messages):
                return
            self._messages.append(message)
            if len(self._messages) < 3:
                self.status = (f"Jevos · waiting for 3 complete messages ({len(self._messages)}/3)", False)
                return
            self._pending = Sample(workflow, tuple(self._messages), time(), self.settings, self._invalidated)
            if not self.status[1]:
                self.status = ("Jevos · queued for scoring", True)
            if self._thread is None:
                self._start_thread()
            self._changed.notify()

    def _start_thread(self):
        self._thread = Thread(target=self._loop, name="decision-workflow")
        self._thread.start()

    def configure(self, settings):
        with self._changed:
            self.settings = settings
            self._invalidated.set()
            self._invalidated = Event()
            self._pending = None
            self.status = self._idle()
            if not settings.enabled:
                self._messages.clear()
                if self._thread is not None:
                    self.status = ("Jevos · stopping local scoring", True)
            self._changed.notify()

    def _idle(self):
        return ("Jevos · waiting for new complete messages" if self.settings.enabled else "Jevos disabled", False)

    def close(self):
        with self._changed:
            self._closing = True
            self._invalidated.set()
            self._pending = None
            self._messages.clear()
            self._changed.notify()

    @property
    def stopped(self):
        with self._changed:
            return self._thread is None

    def _report(self, sample, text, busy=False):
        with self._changed:
            if not sample.invalidated.is_set():
                self.status = (text, busy)

    def _loop(self):
        try:
            while True:
                with self._changed:
                    self._changed.wait_for(self._wake, timeout=1 if self._warm_since is not None else None)
                    if self._closing or not self.settings.enabled:
                        return
                    sample, self._pending = self._pending, None
                self._expire_model()
                if sample is not None:
                    self._run_sample(sample)
        finally:
            try:
                self.harness.models.evict_idle(only="decision")
            finally:
                with self._changed:
                    self._thread = None
                    self._warm_since = None
                    if not self.settings.enabled:
                        self.status = self._idle()
                    elif not self._closing and self._pending is not None:
                        self._start_thread()

    def _wake(self):
        return (self._closing or not self.settings.enabled or self._pending is not None
                or (self._warm_since is not None and self._warm_since[1].is_set()))

    def _expire_model(self):
        if self._warm_since is not None:
            since, invalidated = self._warm_since
            if invalidated.is_set() or monotonic() - since >= 30:
                self.harness.models.evict_idle(only="decision")
                self._warm_since = None

    def _run_sample(self, sample):
        backend = sample.settings.backend
        gpu = backend == "vulkan"
        directory = self.vulkan_directory if gpu else Path(sample.settings.directory)
        key = ModelKey("decision", (MODEL, str(directory), backend))
        profile = VULKAN_PROFILE if gpu else PROFILE

        def estimate(_work):
            credit = self.harness.models.credit((key,))
            warm = gpu and any(row[0] == key for row in self.harness.models.snapshot())
            return replace(profile, resident_bytes=max(0, profile.resident_bytes - credit),
                           accelerator_bytes=256 * MIB if warm else profile.accelerator_bytes)

        def score(claimed, _access, stop):
            if claimed != work:
                raise VoiceError("The decision snapshot is no longer available.")
            try:
                self._score(sample, claimed, Cancellation(stop, sample.invalidated), key, directory)
            finally:
                if sample.invalidated.is_set():
                    self.harness.cancel_work(claimed)
                if stop.is_set():
                    self.harness.models.evict_idle(only="decision", scope=claimed.scope)
                if stop.is_set() and not sample.invalidated.is_set():
                    self._report(sample, "Jevos · yielded to interactive work")

        work = None
        try:
            if sample.invalidated.is_set():
                return
            with self.harness.telemetry.online("Decision workflow"):
                work = self.harness.submit_work(WorkRequest(CAPABILITY, session_only=True, yield_to_interactive=True),
                                                workflow_id=sample.workflow, turn_id=sample.messages[-1].id)
                worker = WorkerSpec(f"jevos-{backend}", {CAPABILITY: score}, estimate)

                def waiting(reason):
                    self._report(sample, "Jevos · " + reason, True)

                while not sample.invalidated.is_set():
                    self._expire_model()
                    free_vram = self.available_vram() if gpu else None
                    if sample.invalidated.is_set():
                        return
                    if gpu and free_vram is None:
                        self._report(sample, "Jevos Vulkan needs VRAM readings from one NVIDIA GPU. Select CPU in Jevos.")
                        return
                    budget = max(0, free_vram - VRAM_RESERVE) if gpu else None
                    if self.harness.run_next(worker, accelerator_available=budget, waiting=waiting):
                        if gpu and self.harness.queue.status(work.scope.job_id) == "cancelled":
                            with self._changed:
                                if not sample.invalidated.is_set() and self._pending is None:
                                    self._pending = sample
                        return
                    with self._changed:
                        if sample.invalidated.is_set() or self._pending is not None:
                            return
                        self._changed.wait(timeout=0.5)
        except Exception:
            self._report(sample, "Jevos · could not run the local scoring job")
        finally:
            if work is not None:
                try:
                    self.harness.cancel_work(work)
                    self.history.prune()
                except Exception:
                    self._report(sample, "Jevos · could not update the local job state")

    def _score(self, sample, work, stop, key, directory):
        backend = sample.settings.backend

        def create():
            return self.engines[backend](directory, lambda pid: self.harness.watch_job_process(work.scope.job_id, pid))

        try:
            with self.harness.telemetry.online("Jevos scorer"), self.harness.models.borrow(
                key, create, stop, lambda stage: self._report(sample, "Jevos · " + stage, True),
                work.scope,
            ) as model:
                if model is None:
                    return
                pid = getattr(model, "process_id", None)
                if pid is not None:
                    self.harness.watch_job_process(work.scope.job_id, pid, warm=True)
                self.harness.mark_ready(work.scope.job_id)
                self._report(sample, "Jevos · scoring the latest 3 messages", True)
                self._warm_since = (monotonic(), sample.invalidated)
                scores = model.score(sample.messages, sample.settings.questions, stop)
                if scores is None or stop.is_set():
                    return
                self._warm_since = (monotonic(), sample.invalidated)
            if not stop.is_set():
                label = VULKAN_LABEL if backend == "vulkan" else MODEL
                self.history.save(work, sample.observed, sample.settings.questions, scores, model=label)
                self._report(sample, "Jevos · scores saved")
        except (OSError, VoiceError) as exc:
            self._report(sample, str(exc) if isinstance(exc, VoiceError) else "Jevos · could not start the local model")
            raise
        except Exception:
            self._report(sample, "Jevos · could not save local scores")
            raise
