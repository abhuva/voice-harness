import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QTabWidget

from voice_harness.contracts import Message, VoiceError
from voice_harness.harness import Harness, Outcome
from voice_harness.presets import Recipe
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

    def test_chat_playback_collapses_with_keyboard_and_preserves_values(self):
        controls = self.window.playback
        self.assertFalse(controls.details.isVisible())
        QTest.keyClick(controls.toggle, Qt.Key.Key_Space)
        self.assertTrue(controls.details.isVisible())
        controls.speed.setValue(110)
        controls.bands[0].setValue(3)
        controls.mute.setChecked(True)
        QTest.keyClick(controls.toggle, Qt.Key.Key_Space)
        self.assertFalse(controls.details.isVisible())
        self.assertEqual(controls.toggle.accessibleDescription(), "Collapsed")
        settings = controls.apply_to(self.window.voice)
        self.assertEqual((settings.speed, settings.bass), (0, 3))
        QTest.keyClick(controls.toggle, Qt.Key.Key_Space)
        self.assertEqual(controls.speed.value(), 110)
        self.assertTrue(controls.mute.isChecked())

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

    def test_clone_progress_and_explicit_tts_selection(self):
        release = Event()
        ident = "c" * 32

        def clone(library, recipe, worker, stop):
            worker.progress.emit(1, 2, "reference clips prepared")
            if not release.wait(2):
                return "Fixture timed out"
            worker.stage.emit("Encoding the local voice profile")
            destination = library.presets / ident
            destination.mkdir(parents=True)
            (destination / "voice.safetensors").write_bytes(b"fixture")
            (destination / "recipe.json").write_text(json.dumps({
                "name": recipe.name, "samples": recipe.samples, "temperature": 0.45,
            }), encoding="utf-8")
            worker.cloned.emit(ident)
            return Outcome("Voice preset saved.")

        with patch("voice_harness.window.build_preset", side_effect=clone):
            self.window.cloning.clone_requested.emit(Recipe("Fixture", ("a.wav", "b.wav")))
            try:
                self.assertTrue(self.window.progress.isVisible())
                self.assertFalse(self.window.start.isEnabled())
                self.wait_for(lambda: self.window.progress.value() == 1)
                self.assertEqual(self.window.progress.maximum(), 2)
                self.assertEqual(self.window.progress.accessibleName(), "1 of 2 reference clips prepared")
            finally:
                release.set()
            self.wait_for(lambda: self.window.worker is None)

        self.assertEqual(self.window.tts.settings().preset, ident)
        self.assertEqual(self.window.voice.preset, "alba")
        self.window.findChild(QTabWidget).setCurrentWidget(self.window.tts)
        self.window.tts.use.click()
        self.assertEqual(self.settings.load()[1].preset, ident)
        self.window.tts.model.setCurrentIndex(self.window.tts.model.findData("7m"))
        self.assertEqual(self.window.tts.voice.count(), 1)
        self.assertEqual(self.window.tts.settings().preset, "alba")

        def preview(_directory, playback, _text, worker, stop):
            self.assertEqual(playback.model, "7m")
            return "Voice preview finished."

        with patch("voice_harness.window.preview_voice", side_effect=preview) as run:
            self.window.tts.preview.click()
            self.wait_for(lambda: self.window.worker is None)
            run.assert_called_once()
        self.assertEqual(self.window.status.text(), "Voice preview finished.")
        self.assertEqual(self.settings.load()[1].preset, ident)

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
