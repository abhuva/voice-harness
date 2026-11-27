import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QPushButton, QTabWidget

from voice_harness.contracts import Message, VoiceError
from voice_harness.decisions import CAPABILITY
from voice_harness.harness import Harness, Outcome
from voice_harness.phone_audio import PhoneAudio
from voice_harness.phone_pairing import PhonePairing
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

    def test_memory_guard_setting_applies_to_the_next_job(self):
        self.assertFalse(self.window.strict_memory.isChecked())
        self.window.findChild(QTabWidget).setCurrentIndex(1)
        self.assertTrue(self.window.strict_memory.isVisible())
        self.window.strict_memory.setChecked(True)
        self.window.save.click()
        self.assertTrue(self.settings.load_sst()[1])
        with patch("voice_harness.window.CallOperation", return_value=lambda *_: "Call ended."):
            self.window.start.click()
            self.assertIsNotNone(self.window.worker)
            self.assertTrue(self.window.worker.job.strict_memory)
            self.wait_for(lambda: self.window.worker is None)
        self.assertTrue(self.window.strict_memory.isEnabled())

    def test_phone_uses_desktop_call_slot_and_respects_delivery_guard(self):
        self.window.enable_phone("http://127.0.0.1:8768", PhonePairing())
        self.state.claim_delivery("prior")
        refused = PhoneAudio("ptt")
        self.window.phone_requested.emit(refused)
        self.wait_for(refused.finished.is_set)
        self.assertIn("unresolved", refused.events[-1]["text"])
        self.state.clear_delivery("prior")

        def operation(worker, stop):
            worker.stage.emit("Fixture phone call")
            stop.wait(2)
            return "Done"

        phone = PhoneAudio("rtc")
        with patch("voice_harness.window.CallOperation", return_value=operation) as factory:
            self.window.phone_requested.emit(phone)
            self.wait_for(lambda: any(item.get("text") == "Fixture phone call" for item in phone.events))
            self.assertIs(factory.call_args.kwargs["phone"], phone)
            self.assertFalse(self.window.start.isEnabled())
            self.assertEqual(self.window.worker.job.label, "Call")
            duplicate = PhoneAudio("ptt")
            self.window.phone_requested.emit(duplicate)
            self.wait_for(duplicate.finished.is_set)
            self.assertIn("busy", duplicate.events[-1]["text"])
            phone.disconnect()
            self.window.phone_disconnected.emit(phone)
            self.wait_for(phone.finished.is_set)
            self.wait_for(lambda: self.window.worker is None)
        self.assertEqual(phone.events[-1]["text"], "Call stopped.")
        self.assertTrue(self.window.start.isEnabled())

    def test_phone_pairing_pin_can_be_edited(self):
        pairing = PhonePairing()
        origin = "http://127.0.0.1:8768"
        self.window.enable_phone(origin, pairing)
        page = self.window.tabs.widget(self.window.tabs.count() - 1)
        pin, link = page.findChildren(QLineEdit)
        apply = page.findChild(QPushButton)
        original = pairing.pin
        self.assertEqual(pin.text(), original)
        self.assertFalse(pin.isReadOnly())

        pin.setText("ABC")
        apply.click()
        self.assertEqual(pairing.pin, original)
        self.assertTrue(link.text().endswith(original))

        pin.setText("0420")
        apply.click()
        self.assertEqual(pairing.pin, "0420")
        self.assertEqual(link.text(), origin + "/#code=0420")

    def test_sst_residency_toggle_saves_and_applies_to_next_call(self):
        tabs = self.window.findChild(QTabWidget)
        self.assertEqual(tabs.tabText(1), "SST")
        tabs.setCurrentIndex(1)
        controls = self.window.dictation
        self.assertTrue(controls.stt_on_demand.isChecked())
        self.assertTrue(controls.stt_idle.isEnabled())
        controls.stt_idle.setValue(42)
        controls.stt_on_demand.setChecked(False)
        self.assertFalse(controls.stt_idle.isEnabled())
        with patch.object(self.window.harness.models, "set_recognition_idle") as apply_idle:
            self.window.save.click()
            apply_idle.assert_called_once_with(None)
        saved, _ = self.settings.load_sst()
        self.assertFalse(saved.stt_on_demand)
        self.assertEqual(saved.stt_idle, 42)

        with patch("voice_harness.window.CallOperation", return_value=lambda *_: "Call ended.") as call:
            self.window.start.click()
            self.assertFalse(call.call_args.args[1].stt_on_demand)
            self.assertFalse(self.window.worker.job.recognition_on_demand)
            self.assertIn("resident STT", self.window.worker.job.profile.basis)
            self.wait_for(lambda: self.window.worker is None)
            controls.stt_on_demand.setChecked(True)
            self.assertTrue(controls.stt_idle.isEnabled())
            self.assertEqual(controls.stt_idle.value(), 42)
            self.assertIn("Unsaved SST changes", self.window.sst_status.text())
            self.window.start.click()
            self.assertFalse(call.call_args.args[1].stt_on_demand)
            self.wait_for(lambda: self.window.worker is None)
            self.window.save.click()
            self.window.start.click()
            self.assertTrue(call.call_args.args[1].stt_on_demand)
            self.assertTrue(self.window.worker.job.recognition_on_demand)
            self.assertIn("on-demand STT", self.window.worker.job.profile.basis)
            self.wait_for(lambda: self.window.worker is None)

    def test_fluid_device_choice_persists_and_selects_the_next_call(self):
        controls = self.window.dictation
        self.assertFalse(controls.stt_backend.isEnabled())
        controls.model.setCurrentIndex(controls.model.findData("fluid-speech-pro"))
        self.assertTrue(controls.stt_backend.isEnabled())
        self.assertEqual(controls.stt_backend.currentData(), "cpu")
        controls.stt_backend.setCurrentIndex(controls.stt_backend.findData("vulkan"))
        self.window.save.click()
        self.assertEqual(self.settings.load_sst()[0].stt_backend, "vulkan")
        with patch("voice_harness.window.CallOperation", return_value=lambda *_: "Call ended.") as call:
            self.window.start.click()
            self.assertEqual(call.call_args.args[1].stt_backend, "vulkan")
            self.assertEqual(self.window.worker.job.models[0].configuration[-1], "vulkan")
            self.wait_for(lambda: self.window.worker is None)
            controls.stt_backend.setCurrentIndex(controls.stt_backend.findData("cpu"))
            self.window.save.click()
            self.window.start.click()
            self.assertEqual(call.call_args.args[1].stt_backend, "cpu")
            self.assertEqual(self.window.worker.job.models[0].configuration[-1], "cpu")
            self.wait_for(lambda: self.window.worker is None)
        controls.model.setCurrentIndex(controls.model.findData("german"))
        self.assertFalse(controls.stt_backend.isEnabled())
        self.assertEqual(controls.settings().stt_backend, "cpu")

    def test_tts_device_applies_to_preview_and_saved_call_voice(self):
        self.assertEqual(self.window.tts.backend.currentData(), "cpu")
        self.window.tts.backend.setCurrentIndex(self.window.tts.backend.findData("cuda"))
        self.assertIn("Unsaved TTS changes", self.window.tts.active.text())
        self.assertIn("CPU", self.window.tts.active.text())
        with patch("voice_harness.window.preview_voice", return_value="Fixture preview.") as preview:
            self.window.start_preview()
            self.wait_for(lambda: self.window.worker is None)
            self.assertEqual(preview.call_args.args[1].backend, "cuda")
            self.assertIs(preview.call_args.args[0], self.settings.synthesis)

        with patch("voice_harness.window.CallOperation", return_value=lambda *_: "Call ended.") as call:
            self.window.save.click()
            self.assertEqual(self.settings.load_tts().backend, "cpu")
            with patch.object(self.settings, "save_tts", side_effect=OSError):
                self.window.tts.save.click()
            self.assertIn("Could not save TTS settings", self.window.status.text())
            self.window.start.click()
            self.assertEqual(call.call_args.args[2].backend, "cpu")
            self.wait_for(lambda: self.window.worker is None)
            self.window.tts.save.click()
            self.assertEqual(self.settings.load_tts().backend, "cuda")
            self.assertIn("GPU (CUDA)", self.window.tts.active.text())
            self.assertNotIn("Unsaved", self.window.tts.active.text())
            self.window.close()
            self.window = Window(self.settings, Harness(State(self.settings.local)))
            self.window.start.click()
            self.assertEqual(call.call_args.args[2].backend, "cuda")
            self.assertEqual(self.window.worker.job.models[-1].configuration[-1], "cuda")
            self.wait_for(lambda: self.window.worker is None)
        self.window.tts.backend.setCurrentIndex(self.window.tts.backend.findData("cpu"))
        self.window.tts.save.click()
        self.assertEqual(self.settings.load_tts().backend, "cpu")

    def test_playback_and_model_tabs_save_independent_changes(self):
        self.window.playback.speed.setValue(112)
        self.window.playback.mute.setChecked(True)
        self.window.dictation.threads.setValue(2)
        self.window.tts.backend.setCurrentIndex(self.window.tts.backend.findData("cuda"))
        self.window.tts.save.click()
        self.assertFalse(self.settings.load_output().muted)
        self.assertEqual(self.settings.load_sst()[0].threads, 4)
        self.window.save.click()
        self.assertEqual(self.settings.load_sst()[0].threads, 2)
        self.assertFalse(self.settings.load_output().muted)
        self.window.playback.save.click()
        self.assertEqual(self.settings.load_tts().backend, "cuda")
        self.window.close()
        self.window = Window(self.settings, Harness(State(self.settings.local)))
        self.assertTrue(self.window.playback.mute.isChecked())
        self.assertEqual(self.window.playback.speed.value(), 112)
        self.assertEqual(self.window.dictation.threads.value(), 2)
        self.assertEqual(self.window.tts.backend.currentData(), "cuda")

    def test_save_disables_jevos_during_a_call_and_across_restart(self):
        controls = self.window.decision_controls
        bundle = Path(controls.directory.text())
        (bundle / "model").mkdir(parents=True)
        (bundle / "model/openvino_model.xml").touch()
        (bundle / "jev.exe").touch()
        controls.enabled.setChecked(True)
        controls.save.click()
        self.assertTrue(self.window.decision_poll.isActive())
        self.assertTrue(self.window.heatmap.isVisible())
        release = Event()

        def operation(worker, stop):
            if not release.wait(2):
                return "Fixture timed out"
            for number in range(3):
                message = Message(str(number), "Hannah", f"Reply {number}")
                worker.message.emit(message)
                worker.completed_message.emit(worker.job.id, message)
            return "Call ended."

        with patch("voice_harness.window.CallOperation", return_value=operation), \
                patch.object(self.window.decisions, "observe") as observe, \
                patch.object(self.window.decisions.history, "latest") as latest:
            self.window.start.click()
            controls.questions.clear()
            controls.enabled.setChecked(False)
            controls.save.click()
            self.assertEqual(self.window.decision_status.text(), "Jevos disabled")
            self.assertFalse(self.window.decision_poll.isActive())
            self.assertFalse(self.window.heatmap.isVisible())
            release.set()
            self.wait_for(lambda: self.window.worker is None)
            self.assertEqual(self.window.status.text(), "Call ended.")
            self.assertEqual(self.window.chat_view.entries["2"][2].text(), "Reply 2")
            observe.assert_not_called()
            latest.assert_not_called()
        self.assertFalse(any(row[1] == CAPABILITY for row in self.window.harness.queue.recent()))

        self.window.close()
        with patch("voice_harness.decisions.installation") as installation:
            restarted = Window(self.settings, Harness(State(self.settings.local)))
            self.addCleanup(restarted.close)
            self.assertFalse(restarted.decision_controls.enabled.isChecked())
            self.assertTrue(restarted.decision_controls.questions.toPlainText())
            self.assertFalse(restarted.decision_poll.isActive())
            installation.assert_not_called()

    def test_jevos_gpu_device_saves_independently_of_enablement(self):
        controls = self.window.decision_controls
        self.assertFalse(controls.enabled.isChecked())
        self.assertTrue(controls.backend.isEnabled())
        controls.backend.setCurrentIndex(controls.backend.findData("vulkan"))
        controls.save.click()
        self.assertFalse(self.window.decisions.settings.enabled)
        self.assertEqual(self.window.decisions.settings.backend, "vulkan")
        self.assertFalse(self.window.decision_poll.isActive())
        self.window.close()
        self.window = Window(self.settings, Harness(State(self.settings.local)))
        controls = self.window.decision_controls
        self.assertEqual(controls.backend.currentData(), "vulkan")
        self.assertFalse(controls.enabled.isChecked())
        self.assertIn("Off · GPU (Vulkan)", controls.status.text())
        controls.enabled.setChecked(True)
        with patch("voice_harness.decisions.vulkan_installation") as validate:
            controls.save.click()
            validate.assert_called_once_with(self.window.decisions.vulkan_directory)
        self.assertTrue(self.window.decisions.settings.enabled)
        self.assertTrue(self.window.decision_poll.isActive())
        controls.enabled.setChecked(False)
        controls.save.click()
        self.assertEqual(self.window.decisions.settings.backend, "vulkan")
        self.assertFalse(self.window.decision_poll.isActive())

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
        self.window.tts.save.click()
        self.assertEqual(self.settings.load_tts().preset, ident)
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
        self.assertEqual(self.settings.load_tts().preset, ident)

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
