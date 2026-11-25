from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest

from voice_harness.contracts import VoiceError
from voice_harness.harness import Harness
from voice_harness.state import State


class StateTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.state = State(self.path)
        self.harness = Harness(self.state)

    def test_job_submission_claim_and_cancel_have_single_owner(self):
        running, finish = Event(), Event()

        def operation(stop):
            running.set()
            self.assertTrue(finish.wait(2))
            self.assertTrue(stop.is_set())
            return "Finished"

        job = self.harness.submit("Call")
        self.assertEqual(self.state.last_status(), "Call submitted")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.harness.execute, job, operation)
            try:
                self.assertTrue(running.wait(2))
                with self.assertRaises(VoiceError):
                    self.harness.submit("History")
                with self.assertRaises(VoiceError):
                    self.harness.execute(job, operation)
                self.harness.cancel(job)
            finally:
                finish.set()
            self.assertEqual(future.result(timeout=2), "Call stopped.")
        self.assertEqual(self.state.last_status(), "Call stopped.")
        history = self.harness.submit("History")
        self.assertEqual(self.harness.execute(history, lambda stop: "Recent history"), "Recent history")

    def test_restart_does_not_retry_or_drop_delivery(self):
        self.harness.submit("Call")
        self.state.claim_delivery("unresolved-turn")
        reopened = State(self.path)
        self.assertEqual(reopened.last_status(), "Call interrupted by shutdown.")
        self.assertEqual(reopened.pending_delivery(), "unresolved-turn")
        reopened.finish("unrelated-job", "Old late result")
        self.assertEqual(reopened.last_status(), "Call interrupted by shutdown.")
        reopened.clear_delivery("wrong-id")
        self.assertEqual(reopened.pending_delivery(), "unresolved-turn")
        reopened.clear_delivery("unresolved-turn")
        self.assertIsNone(reopened.pending_delivery())

    def test_unexpected_worker_error_is_sanitized(self):
        def fail(stop):
            raise RuntimeError("private provider body")

        status = self.harness.execute(self.harness.submit("Call"), fail)
        self.assertIn("failed", status)
        self.assertNotIn("private", self.state.last_status())
