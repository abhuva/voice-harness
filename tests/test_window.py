from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QTabWidget

from voice_harness.contracts import Message, VoiceError
from voice_harness.harness import Harness
from voice_harness.settings import Settings
from voice_harness.state import State
from voice_harness.window import Window


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.settings = Settings(root, root / "bundle")
        self.state = State(self.settings.local)
        self.window = Window(self.settings, Harness(self.state))
        self.window.show()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.close()
        self.wait_for(lambda: self.window.worker is None)
        self.app.processEvents()

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while not condition():
            self.assertLess(time.monotonic(), deadline)
            QTest.qWait(10)

    def test_immediate_progress_navigation_stop_and_terminal_state(self):
        working = Event()

        def operation(worker, stop):
            worker.stage.emit("Listening")
            worker.message.emit(Message("turn", "Marc", "<b>plain</b>", "Dictating"))
            working.set()
            stop.wait(2)
            return "Done"

        self.window.playback.mute.setChecked(True)
        with patch("voice_harness.window.CallOperation", return_value=operation) as factory:
            self.window.start.click()
            self.assertEqual(factory.call_args.args[2].speed, 0)
            self.assertTrue(self.window.progress.isVisible())
            self.assertFalse(self.window.start.isEnabled())
            self.wait_for(lambda: self.window.status.text() == "Listening")
            self.assertTrue(working.is_set())
            self.assertEqual(self.window.progress.accessibleName(), "Listening")
            self.assertEqual(self.window.chat_view.entries["turn"][2].text(), "<b>plain</b>")
            self.window.findChild(QTabWidget).setCurrentIndex(1)
            self.assertTrue(self.window.progress.isVisible())
            self.window.stop.click()
            self.assertIn("Stopping", self.window.status.text())
            self.wait_for(lambda: self.window.worker is None)
        self.assertEqual(self.window.status.text(), "Call stopped.")
        self.assertFalse(self.window.progress.isVisible())
        self.assertTrue(self.window.start.isEnabled())

    def test_uncertain_delivery_pauses_call_across_window_restart(self):
        def operation(worker, stop):
            self.state.claim_delivery("turn")
            raise VoiceError("Delivery is uncertain.")

        with patch("voice_harness.window.CallOperation", return_value=operation):
            self.window.start.click()
            self.wait_for(lambda: self.window.worker is None)
        self.assertFalse(self.window.start.isEnabled())
        self.assertTrue(self.window.delivery.isVisible())
        self.window.close()

        self.window = Window(self.settings, Harness(State(self.settings.local)))
        self.window.show()
        self.assertIn("uncertain", self.window.status.text())
        self.assertFalse(self.window.start.isEnabled())
        self.window.review.click()
        self.assertIsNone(self.state.pending_delivery())
        self.assertTrue(self.window.start.isEnabled())
