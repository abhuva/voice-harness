from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from pathlib import Path
import json
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import unittest

import numpy as np

from voice_harness.admission import Admission
from voice_harness.call import run_call
from voice_harness.contracts import (
    CallInput, DeliveryConfirmed, PCM, ReplyCompleted, ReplyText, VoiceError,
)
from voice_harness.events import Event as Notice
from voice_harness.harness import Harness, WorkerSpec
from voice_harness.profiler import Profiler, measurement_for
from voice_harness.resources import ResourceSample
from voice_harness.speaker import SynthesizedSpeech
from voice_harness.state import State
from voice_harness.work_types import DataGrant, ResourceProfile, WorkRequest


SMALL = ResourceProfile(resident_bytes=16, extra_bytes=16, cpu_threads=1, basis="Synthetic fixture")
CALL = ResourceProfile(resident_bytes=400, extra_bytes=100, cpu_threads=2, basis="Synthetic call")


class HarnessContractTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state = State(self.directory)
        self.harness = Harness(self.state, admission=Admission(lambda: 2048, reserve_bytes=750))

    def worker(self, handler, estimate=SMALL, *, capability="analysis", cloud=False):
        return WorkerSpec("fixture-worker", {capability: handler}, lambda _: estimate, cloud)

    def test_migration_preserves_unresolved_delivery_and_enables_wal(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            with closing(sqlite3.connect(path / "state.sqlite3")) as db, db:
                db.executescript("""
                    CREATE TABLE operation (slot INTEGER PRIMARY KEY, id TEXT, label TEXT,
                                            status TEXT, active INTEGER);
                    INSERT INTO operation VALUES (1, 'existing-job', 'Call', 'Listening', 1);
                    CREATE TABLE delivery (slot INTEGER PRIMARY KEY, turn_id TEXT);
                    INSERT INTO delivery VALUES (1, 'existing-turn');
                    PRAGMA user_version = 1;
                """)
            migrated = State(path)
            self.assertEqual(migrated.pending_delivery(), "existing-turn")
            self.assertEqual(migrated.last_status(), "Call interrupted by shutdown.")
            with migrated.connect() as db:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_one_eligible_claim_and_cancel_are_durable(self):
        starting, finish = Event(), Event()

        def handle(work, access, stop):
            starting.set()
            self.assertTrue(finish.wait(2))
            self.assertTrue(stop.is_set())

        work = self.harness.submit_work(WorkRequest("analysis", retry_safe=True))
        unrelated = self.worker(handle, capability="other")
        eligible = self.worker(handle)
        self.assertIsNone(self.harness.run_next(unrelated))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.harness.run_next, eligible)
            try:
                self.assertTrue(starting.wait(2))
                self.assertIsNone(self.harness.run_next(eligible))
                self.harness.cancel_work(work)
            finally:
                finish.set()
            self.assertEqual(future.result(2), work)
        self.assertEqual(self.harness.queue.status(work.scope.job_id), "cancelled")
        self.assertIsNone(self.harness.run_next(eligible))

    def test_restart_requeues_repeatable_jobs_and_keeps_uncertain_effects_interrupted(self):
        repeatable = self.harness.submit_work(WorkRequest("analysis", retry_safe=True))
        effect = self.harness.submit_work(WorkRequest("analysis", cloud=True))
        for queued in (repeatable, effect):
            self.assertTrue(self.harness.queue.claim(queued, "dead-worker", SMALL))
        restarted = Harness(State(self.directory))
        self.assertEqual(restarted.queue.status(repeatable.scope.job_id), "queued")
        self.assertEqual(restarted.queue.status(effect.scope.job_id), "interrupted")

    def test_claims_enforce_call_reservation_and_explicit_cloud_budgets(self):
        harness = Harness(self.state, admission=Admission(
            lambda: 1000, reserve_bytes=800, remote_requests=1, remote_cost_microunits=8,
        ))
        harness.submit_work(WorkRequest("analysis"))
        large = ResourceProfile(200, 100, 1)
        self.assertIsNone(harness.run_next(self.worker(lambda *args: None, estimate=large)))
        self.assertIsNotNone(harness.run_next(self.worker(lambda *args: None)))
        catalog = harness.catalog
        ref = catalog.save(b"private", source="fixture", revision="one", media_type="text/plain")
        with self.assertRaises(ValueError):
            WorkRequest("analysis", (DataGrant(ref),), cloud=True)
        description = ResourceProfile(16, 16, 1, remote_requests=1, remote_cost_microunits=8)
        readings = []
        remote = self.worker(lambda work, data, stop: readings.append(data.read(ref)),
                             estimate=description, cloud=True)
        for _ in range(2):
            harness.submit_work(WorkRequest("analysis", (DataGrant(ref, allow_cloud=True),), cloud=True))
        self.assertIsNotNone(harness.run_next(remote))
        self.assertIsNone(harness.run_next(remote))
        self.assertEqual(readings, [b"private"])

    def test_observed_ram_estimate_does_not_block_a_sole_call_by_default(self):
        mib = 1024 ** 2
        harness = Harness(self.state, admission=Admission(lambda: 631 * mib))
        declaration = ResourceProfile(cpu_threads=4, basis="call fixture")
        prior = harness.queue.enqueue(WorkRequest("desktop.Call"), foreground=True)
        harness.queue.claim(prior, "desktop", declaration)
        sample = {
            "startup_seconds": 1.0,
            "ready_host_bytes": 772 * mib, "ready_child_bytes": 122 * mib,
            "peak_host_bytes": 796 * mib, "peak_child_bytes": 177 * mib,
        }
        with self.state.connect() as db:
            db.execute("INSERT INTO measurement(job, sample) VALUES (?, ?)",
                       (prior.scope.job_id, json.dumps(sample)))
        harness.queue.finish(prior, "desktop", "completed")

        job = harness.submit("Call", profile=declaration)
        self.assertTrue(job.profile.advisory_memory)
        self.assertGreater(job.profile.memory, 631 * mib)
        self.assertFalse(harness.admission.allows(job.profile, (SMALL,), interactive=True))

        default_wait = []

        def cancel_if_waiting(text):
            default_wait.append(text)
            harness.cancel(job)

        self.assertEqual(harness.execute(job, lambda _: "Started", waiting=cancel_if_waiting), "Started")
        self.assertEqual(default_wait, [])

        strict = harness.submit("Call", profile=declaration, strict_memory=True)
        notices = []

        def stop_wait(text):
            notices.append(text)
            harness.cancel(strict)

        def unexpected_start(_):
            self.fail("Strict memory checking admitted the job")

        self.assertEqual(harness.execute(strict, unexpected_start, waiting=stop_wait), "Call stopped.")
        self.assertEqual(len(notices), 1)
        self.assertIn("631 MiB available", notices[0])
        self.assertIn("Stop to change SST settings", notices[0])

    def test_cold_recognition_in_a_listening_call_yields_background_ram(self):
        harness = self.harness
        call = harness.submit("Call", profile=CALL, recognition_on_demand=True)
        listening, running, wake = Event(), Event(), Event()
        first = harness.submit_work(WorkRequest("fixture.score", yield_to_interactive=True))
        queued = harness.submit_work(WorkRequest("fixture.score", yield_to_interactive=True))

        def score(work, access, stop):
            running.set()
            self.assertTrue(stop.wait(3))

        worker = WorkerSpec("fixture", {"fixture.score": score}, lambda _: SMALL)

        def operation(stop):
            listening.set()
            self.assertTrue(wake.wait(3))
            with harness.recognition_startup(call, stop, lambda _: None) as admitted:
                self.assertTrue(admitted)
                self.assertEqual(harness.queue.status(first.scope.job_id), "cancelled")
                self.assertIsNone(harness.run_next(worker))
            return "Fixture complete"

        with ThreadPoolExecutor(max_workers=2) as pool:
            desktop = pool.submit(harness.execute, call, operation)
            try:
                self.assertTrue(listening.wait(2))
                background = pool.submit(harness.run_next, worker)
                self.assertTrue(running.wait(2))
            finally:
                wake.set()
            self.assertEqual(desktop.result(4), "Fixture complete")
            self.assertEqual(background.result(3), first)
        self.assertEqual(harness.queue.status(queued.scope.job_id), "queued")

    def test_waiting_call_has_priority_over_additional_background_work(self):
        harness = Harness(self.state, admission=Admission(lambda: 1000, reserve_bytes=100))
        background_started, release, waiting, call_started = (Event() for _ in range(4))
        profile = ResourceProfile(850, 20, 2)

        def held_work(*_):
            background_started.set()
            release.wait(2)

        harness.submit_work(WorkRequest("analysis"))
        background = self.worker(held_work, estimate=ResourceProfile(150, 10, 1))
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(harness.run_next, background)
            try:
                self.assertTrue(background_started.wait(2))
                next_work = harness.submit_work(WorkRequest("analysis"))
                call = harness.submit("Call", profile=profile)

                def call_operation(stop):
                    call_started.set()
                    return "Call complete"

                started = pool.submit(harness.execute, call, call_operation,
                                      waiting=lambda _: waiting.set())
                self.assertTrue(waiting.wait(2))
                self.assertFalse(call_started.is_set())
                self.assertEqual(harness.queue.status(next_work.scope.job_id), "queued")
                self.assertIsNone(harness.run_next(background))
            finally:
                release.set()
            self.assertIsNotNone(running.result(2))
            self.assertEqual(started.result(2), "Call complete")
        self.assertTrue(call_started.is_set())

    def test_catalog_scope_version_integrity_and_external_references(self):
        catalog = self.harness.catalog
        ref = catalog.save(b"v1", source="fixture", revision="1", media_type="text/plain")
        latest = catalog.save(b"v2", source="fixture", revision="2", media_type="text/plain", previous=ref)
        repo = catalog.reference(media_type="application/git", source="fixture-repository", revision="commit-abc")
        self.assertEqual(latest.id, ref.id)
        self.assertNotEqual(latest.version, ref.version)
        work = self.harness.submit_work(WorkRequest("analysis", (DataGrant(ref), DataGrant(repo))))

        def inspect(job, data, stop):
            self.assertEqual(data.read(ref), b"v1")
            with self.assertRaisesRegex(VoiceError, "no access"):
                data.read(latest)
            self.assertEqual(data.describe(repo).revision, "commit-abc")
            with self.assertRaisesRegex(VoiceError, "external"):
                data.read(repo)
            (catalog.root / f"{ref.version}.data").write_bytes(b"tampered")
            with self.assertRaisesRegex(VoiceError, "matches"):
                data.read(ref)

        self.harness.run_next(self.worker(inspect))
        self.assertEqual(self.harness.queue.status(work.scope.job_id), "completed")
        with self.assertRaisesRegex(VoiceError, "expired"):
            catalog.access(work).read(ref)

    def test_profiler_separates_ready_idle_and_peak(self):
        values = {"host": 100, "child": 20}

        def sample(*_):
            return ResourceSample(values["host"], values["child"], 0.25, 0.10, 0.30)

        with Profiler(self.state, "fixture-profile", sample=sample, interval=0.01) as profiler:
            profiler.ready()
            profiler.set_activity("inference", True)
            values.update(host=250, child=45)
        profile = measurement_for(self.state, "fixture-profile")
        self.assertEqual((profile.ready_host_bytes, profile.idle_child_bytes), (100, 20))
        self.assertEqual((profile.peak_host_bytes, profile.peak_child_bytes), (250, 45))
        self.assertIsNotNone(profile.startup_seconds)

    def test_profiler_counts_speech_workers_in_separate_slots(self):
        def sample(_start, _cpu_start, pid):
            return ResourceSample(100, {1: 20, 2: 45}.get(pid, 0), 0.25, 0.10, 0.30)

        with Profiler(self.state, "two-workers", sample=sample) as profiler:
            profiler.set_process(1)
            profiler.set_process(2, slot="stt")
            self.assertEqual(profiler.ready().pocket_bytes, 65)
        profile = measurement_for(self.state, "two-workers")
        self.assertEqual((profile.ready_host_bytes, profile.ready_child_bytes), (100, 65))

    def test_synthesis_pipeline_can_feed_a_pcm_sink_without_a_device(self):
        class Synth:
            def render(self, segment, stop):
                return PCM(24_000, segment.encode())

        received = []

        class Sink:
            def play(self, next_pcm, stop, stage, *, played=None):
                while clip := next_pcm():
                    received.append(clip)

        parts = iter(["one", "two"])
        speech = SynthesizedSpeech(Synth(), Sink())
        speech.speak(lambda _: next(parts, None), Event(), lambda _: None)
        self.assertEqual(received, [PCM(24_000, b"one"), PCM(24_000, b"two")])

    def test_delayed_work_is_claimed_during_call_and_used_by_a_later_turn_once(self):
        harness = self.harness
        call = harness.submit("Call", profile=CALL)
        first_reply, research_finished, next_input = (Event() for _ in range(3))
        context_seen, reply_events = [], []
        clip = np.ones((1600, 1), dtype=np.int16)

        class Microphone:
            turns = 0

            def open(self):
                return nullcontext()

            def discard_buffered(self):
                pass

            def capture(self, settings, stop, stage, accept, *, speech_started, reply_pending):
                self.turns += 1
                if self.turns == 2:
                    if not next_input.wait(3):
                        raise RuntimeError("Test did not release next input")
                if self.turns > 2:
                    while not stop.is_set():
                        call.stop.wait(0.01)
                    return False
                speech_started()
                accept(clip, True)
                return True

        class Recognizer:
            def select_model(self, *_): pass
            def prepare(self, *_): pass
            def close(self): pass
            def transcribe(self, _):
                return "A spoken turn"

        class LocalConnector:
            def send(self, request, stop):
                reply_events.append(request)
                yield DeliveryConfirmed()
                if len(reply_events) == 1:
                    first_reply.set()
                    if not research_finished.wait(3):
                        raise RuntimeError("Test did not finish research")
                    yield ReplyText("First answer")
                    yield ReplyCompleted()
                else:
                    context_seen.extend(request.read_context(ref) for ref in request.context)
                    yield ReplyText("Answer using background data")
                    yield ReplyCompleted()

        def publish(message):
            if message.body == "First answer" and message.status == "":
                next_input.set()
            if message.body == "Answer using background data" and message.status == "":
                call.stop.set()

        def call_operation(stop):
            run_call(Microphone(), lambda *_: nullcontext(Recognizer()), None, LocalConnector(), self.state, CallInput(),
                     stop, lambda _: None, publish, lambda: harness.ready(call),
                     work=harness.call_work(call, use_context=True, cloud=False))
            return "Call ended."

        result = []

        def research(work, access, stop):
            self.assertTrue(first_reply.is_set())
            result.append(access.save_result(b"Background report"))
            return result[0]

        with (harness.events.subscribe(["turn.ready"], workflow_id=call.id) as observer_a,
              harness.events.subscribe(["turn.ready"], workflow_id=call.id) as observer_b,
              harness.events.subscribe(["artifact.ready"]) as results,
              ThreadPoolExecutor(max_workers=2) as threads):
            live = threads.submit(harness.execute, call, call_operation)
            try:
                event = observer_a.get(timeout=3)
                self.assertEqual(event, observer_b.get(timeout=3))
                self.assertIsInstance(event, Notice)
                self.assertTrue(first_reply.wait(3))
                self.assertIsNone(self.state.pending_delivery())  # Explicit ack arrived before text.
                queued = harness.submit_work(WorkRequest("analysis", for_next_turn=True),
                                             workflow_id=call.id, turn_id=event.scope.turn_id)
                self.assertEqual(threads.submit(harness.run_next, self.worker(research)).result(3), queued)
                saved = results.get(timeout=3)
                self.assertEqual(saved.artifact, result[0])
                self.assertEqual(harness.catalog.describe(saved.artifact).producer, queued.scope)
            finally:
                research_finished.set()
                next_input.set()
                if not result:
                    call.stop.set()
            live.result(4)
        self.assertEqual(context_seen, [b"Background report"])
        self.assertEqual(harness.queue.context(call.id, "later"), ())
        self.assertIsNone(self.state.pending_delivery())
        self.assertIsNotNone(measurement_for(self.state, call.id))

    def test_late_result_is_available_as_artifact_but_scoped_to_its_call(self):
        old = self.harness.submit("Call", profile=CALL)
        self.harness.execute(old, lambda stop: "Call ended")
        work = self.harness.submit_work(WorkRequest("analysis", for_next_turn=True),
                                        workflow_id=old.id, turn_id="earlier")
        self.harness.run_next(self.worker(lambda job, data, stop: data.save_result(b"late")))
        current = self.harness.submit("Call", profile=CALL)
        try:
            self.assertEqual(self.harness.queue.context(current.id, "current"), ())
            self.assertEqual(len(self.harness.queue.context(old.id, "current")), 1)
            with self.assertRaisesRegex(VoiceError, "Call stopped"):
                with self.harness.call_work(old, use_context=True, cloud=False).reply("late"):
                    self.fail("A stopped workflow received a new reply job")
        finally:
            self.harness.execute(current, lambda stop: "Call ended")
