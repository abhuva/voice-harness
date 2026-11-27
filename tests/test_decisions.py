from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest

from voice_harness.admission import Admission
from voice_harness.call import run_call
from voice_harness.contracts import CallInput, Message, MicrophoneDropout, VoiceError
from voice_harness.decisions import CAPABILITY, DecisionHistory, DecisionSettings, Decisions, read_settings, save_settings
from voice_harness.harness import Harness
from voice_harness.jevos import answers
from voice_harness.jevos_vulkan import VULKAN_LABEL
from voice_harness.state import State
from voice_harness.work_types import ResourceProfile, WorkRequest


class DecisionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = State(Path(directory.name))
        self.available = 0
        self.vram = 0
        self.harness = Harness(self.state, admission=Admission(available=lambda: self.available))
        self.started = Event()
        self.closed = Event()
        self.inputs = []
        self.released = Event()
        test = self

        class Scorer:
            def __init__(self, *_):
                self.ready = False

            def prepare(self, stop):
                self.ready = True

            def score(self, messages, questions, stop):
                test.started.set()
                test.inputs.append((tuple(m.body for m in messages), questions))
                while not stop.is_set():
                    if test.released.wait(0.01):
                        return (0.73,)
                return None

            def close(self):
                self.ready = False
                test.closed.set()

        self.decisions = Decisions(self.harness, DecisionSettings(True, ".", ("Complex topic?",)), engine=Scorer,
                                   gpu_engine=Scorer, available_vram=lambda: self.vram)
        self.addCleanup(self.stop_decisions)

    def wait_for(self, predicate):
        deadline = monotonic() + 4
        while not predicate():
            self.assertLess(monotonic(), deadline, self.decisions.status)
            sleep(0.02)

    def stop_decisions(self):
        self.decisions.close()
        self.wait_for(lambda: self.decisions.stopped)

    def message(self, number, body):
        self.decisions.observe("call", Message(str(number), "Marc" if number % 2 else "Hannah", body))

    def test_scoring_waits_for_ram_and_uses_only_latest_three_full_messages(self):
        self.decisions.seed(Message("0", "Hannah", "Earlier"))
        self.message(1, "First")
        self.assertIn("2/3", self.decisions.status[0])
        self.message(2, "Second\n\nSame message, next paragraph.")
        self.wait_for(lambda: any(row[1:3] == (CAPABILITY, "queued") for row in self.harness.queue.recent()))
        self.message(3, "Third")
        self.message(4, "Fourth")
        self.wait_for(lambda: any(row[1:3] == (CAPABILITY, "queued") and row[-1] == "4"
                                  for row in self.harness.queue.recent()))
        self.assertFalse(self.started.is_set())
        self.available = 8 * 2**30
        self.assertTrue(self.started.wait(3))
        self.assertEqual(self.inputs, [(("Second\n\nSame message, next paragraph.", "Third", "Fourth"),
                                       ("Complex topic?",))])
        self.released.set()
        self.wait_for(lambda: self.decisions.history.latest(None)[1])
        self.assertEqual(self.decisions.history.latest(None)[1][0].probabilities, {"Complex topic?": 0.73})
        self.assertIn("scores saved", self.decisions.status[0])
        with self.state.connect() as db:
            self.assertNotIn("Same message", repr(db.execute("SELECT * FROM decision_score").fetchall()))
            self.assertNotIn("Same message", repr(db.execute("SELECT * FROM work").fetchall()))

    def test_foreground_submission_cancels_running_scoring(self):
        self.available = 8 * 2**30
        for number in range(3):
            self.message(number, str(number))
        self.assertTrue(self.started.wait(3))
        job = self.harness.submit("Call", profile=ResourceProfile())
        self.assertEqual(self.harness.execute(job, lambda _: "Call ended."), "Call ended.")
        self.wait_for(lambda: any(row[1:3] == (CAPABILITY, "cancelled") for row in self.harness.queue.recent()))
        self.assertEqual(self.decisions.history.latest(None), (None, None))
        self.assertEqual(self.harness.models.snapshot(), ())

    def test_disable_cancels_queued_scoring(self):
        for number in range(3):
            self.message(number, str(number))
        self.wait_for(lambda: any(row[1:3] == (CAPABILITY, "queued") for row in self.harness.queue.recent()))
        self.decisions.configure(replace(self.decisions.settings, enabled=False))
        self.wait_for(lambda: self.decisions.stopped)
        self.assertEqual(self.harness.queue.recent()[0][1:3], (CAPABILITY, "cancelled"))
        self.assertFalse(self.started.is_set())

    def test_disable_cancels_scoring_and_discards_context_until_reenabled(self):
        self.available = 8 * 2**30
        enabled = self.decisions.settings
        for number in range(3):
            self.message(number, str(number))
        self.assertTrue(self.started.wait(3))
        self.message(3, "Pending")
        self.decisions.configure(replace(enabled, enabled=False))
        self.wait_for(lambda: self.decisions.stopped)
        self.assertEqual(self.decisions.status, ("Jevos disabled", False))
        self.assertTrue(self.closed.is_set())
        self.assertEqual(self.harness.models.snapshot(), ())
        self.assertEqual([row[1:3] for row in self.harness.queue.recent()], [(CAPABILITY, "cancelled")])
        self.assertEqual(self.decisions.history.latest(None), (None, None))

        self.decisions.seed(Message("history-0", "Hannah", "History while off"))
        self.message(4, "Message while off")
        self.assertTrue(self.decisions.stopped)
        self.closed.clear()
        self.decisions.configure(enabled)
        self.message(5, "New first")
        self.assertIn("1/3", self.decisions.status[0])
        self.message(6, "New second")
        self.message(7, "New third")
        self.released.set()
        self.wait_for(lambda: self.decisions.history.latest(None)[1])
        self.assertEqual(self.inputs[-1][0], ("New first", "New second", "New third"))

        self.assertEqual(self.harness.models.snapshot()[0][1], "Warm")
        self.decisions.configure(replace(enabled, enabled=False))
        self.wait_for(lambda: self.decisions.stopped)
        self.assertTrue(self.closed.is_set())
        self.assertEqual(self.harness.models.snapshot(), ())
        self.assertEqual(len(self.decisions.history.latest(None)[1]), 1)

    def test_rotation_and_restart_retain_the_latest_hundred_scores(self):
        history = DecisionHistory(self.state)
        profile = ResourceProfile(resident_bytes=0, extra_bytes=0)
        for index in range(105):
            work = self.harness.submit_work(WorkRequest(CAPABILITY, session_only=True),
                                            workflow_id="call", turn_id=str(index))
            self.harness.queue.claim(work, "fixture", profile)
            history.save(work, index, ("Fixture?",), (0.3,))
            self.harness.queue.finish(work, "fixture", "completed")
        history.prune()
        with self.state.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM decision_score").fetchone()[0], 100)
        unfinished = self.harness.submit_work(WorkRequest(CAPABILITY, session_only=True))
        reopened = State(self.state.path.parent)
        self.assertEqual(Harness(reopened).queue.status(unfinished.scope.job_id), "interrupted")
        tick, rows = DecisionHistory(reopened).latest(None)
        self.assertEqual((len(rows), rows[0].observed, rows[-1].observed), (100, 5, 104))
        self.assertEqual(DecisionHistory(reopened).latest(tick), (tick, None))

    def test_gpu_choice_can_be_saved_off_and_scoring_waits_for_vram(self):
        self.available = 8 * 2**30
        enabled = replace(self.decisions.settings, backend="vulkan")
        self.decisions.configure(enabled)
        for number in range(3):
            self.message(number, str(number))
        self.wait_for(lambda: "VRAM needed" in self.decisions.status[0])
        self.assertFalse(self.started.is_set())
        self.vram = 3 * 2**30
        self.released.set()
        self.wait_for(lambda: self.decisions.history.latest(None)[1])
        self.assertEqual(self.decisions.history.latest(None)[1][0].model, VULKAN_LABEL)
        self.assertEqual(self.harness.models.snapshot()[0][0].configuration[-1], "vulkan")

        disabled = replace(enabled, enabled=False)
        path = self.state.path.parent / "jevos.json"
        save_settings(path, disabled)
        self.decisions.configure(read_settings(path, path.parent))
        self.wait_for(lambda: self.decisions.stopped)
        self.assertEqual(self.decisions.settings, disabled)
        self.assertTrue(self.closed.is_set())
        self.assertEqual(self.harness.models.snapshot(), ())

    def test_gpu_ram_status_uses_the_budget_left_after_call_reserve(self):
        mib = 2**20
        self.available = 1800 * mib
        self.vram = 3 * 2**30
        self.decisions.configure(replace(self.decisions.settings, backend="vulkan"))
        for number in range(3):
            self.message(number, str(number))
        self.wait_for(lambda: "waiting for RAM" in self.decisions.status[0])
        self.assertIn("1,152 MiB needed, 776 MiB available for this job", self.decisions.status[0])
        self.assertIn("1,024 MiB reserved", self.decisions.status[0])
        self.assertFalse(self.started.is_set())

        self.available = 2176 * mib
        self.released.set()
        self.wait_for(lambda: self.decisions.history.latest(None)[1])

    def test_gpu_scoring_yields_to_speech_in_an_existing_call(self):
        self.available = 8 * 2**30
        self.vram = 3 * 2**30
        self.decisions.configure(replace(self.decisions.settings, backend="vulkan"))
        job = self.harness.submit("Call", profile=ResourceProfile(resident_bytes=0, extra_bytes=0))
        call = self.harness.call_work(job)

        def operation(_):
            call.activity("dictation", True)
            for number in range(3):
                self.message(number, str(number))
            self.wait_for(lambda: "waiting for dictation" in self.decisions.status[0])
            self.assertFalse(self.started.is_set())
            call.activity("dictation", False)
            self.assertTrue(self.started.wait(3))
            call.activity("reply", True)
            self.wait_for(lambda: any(row[1:3] == (CAPABILITY, "cancelled") for row in self.harness.queue.recent()))
            self.assertTrue(self.closed.is_set())
            self.wait_for(lambda: "waiting for the current reply" in self.decisions.status[0])
            self.assertEqual(self.decisions.history.latest(None), (None, None))
            self.released.set()
            call.activity("reply", False)
            self.wait_for(lambda: self.decisions.history.latest(None)[1])
            return "Call ended."

        self.assertEqual(self.harness.execute(job, operation), "Call ended.")
        self.assertEqual([messages for messages, _ in self.inputs], [("0", "1", "2")] * 2)
        self.assertEqual(len(self.decisions.history.latest(None)[1]), 1)

    def test_gpu_scoring_can_start_while_listening_after_abandoned_utterances(self):
        self.available = 8 * 2**30
        self.vram = 3 * 2**30
        self.released.set()
        self.decisions.configure(replace(self.decisions.settings, backend="vulkan"))
        job = self.harness.submit("Call", profile=ResourceProfile(resident_bytes=0, extra_bytes=0))
        attempts = 0

        def capture(_settings, stop, _stage, _accept, *, speech_started, reply_pending):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                speech_started()
                for number in range(3):
                    self.message(number, str(number))
                self.wait_for(lambda: "waiting for dictation" in self.decisions.status[0])
                raise MicrophoneDropout("Fixture dropout.")
            if attempts == 2:
                self.wait_for(lambda: self.decisions.history.latest(None)[1])
                speech_started()
                self.message(3, "3")
                self.wait_for(lambda: "waiting for dictation" in self.decisions.status[0])
                return False
            self.wait_for(lambda: len(self.decisions.history.latest(None)[1] or []) == 2)
            job.stop.set()
            return False

        def operation(stop):
            microphone = SimpleNamespace(capture=capture, open=nullcontext)
            run_call(microphone, lambda *_: nullcontext(), None, None, self.state, CallInput(),
                     stop, lambda _: None, lambda _: None, lambda: None, work=self.harness.call_work(job))
            return "Call ended."

        self.assertEqual(self.harness.execute(job, operation), "Call stopped.")
        self.assertEqual([messages for messages, _ in self.inputs], [("0", "1", "2"), ("1", "2", "3")])

    def test_bad_response_is_an_error_and_zero_is_a_valid_score(self):
        def body(score):
            return {"model": "jevos-v5", "answers": {"fixture": {"type": "noul", "noul": score}}}

        self.assertEqual(answers(body(0), {"fixture"}), {"fixture": 0})
        for value in (None, True, float("nan"), -0.1, 1.1):
            with self.assertRaises(VoiceError):
                answers(body(value), {"fixture"})
