import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from voice_harness.cloning import clone_voice
from voice_harness.contracts import CallInput, Message, VoiceError
from voice_harness.jevos import Jevos, installation
from voice_harness.events import EventBus
from voice_harness.encoder import PocketEncoder
from voice_harness.engine import ResidentEngine
from voice_harness.presets import Playback, Recipe, VoiceLibrary
from voice_harness.recognizer import DictateRecognizer, recognition_key
from voice_harness.residency import ModelResidency
from voice_harness.resources import resident_sample
from voice_harness.speaker import PocketSpeaker
from voice_harness.voice_jobs import preview_voice
from voice_harness.harness import Harness
from voice_harness.state import State
from voice_harness.synthesis import SynthesisFactory


@unittest.skipUnless(os.environ.get("VOICE_HARNESS_MODEL_FIXTURE"), "requires -LocalModels")
class LocalModelTests(unittest.TestCase):
    def test_silencio_renders_tags_with_bundled_and_existing_clone_presets(self):
        assets = Path(os.environ["VOICE_HARNESS_MODEL_FIXTURE"])
        model_dir = assets / "silencio-2"
        if not (model_dir / "model.safetensors").is_file():
            self.skipTest("requires scripts/setup-silencio.ps1")
        stop = Event()
        pocket = assets / "pocket-tts/4.0.1"
        with TemporaryDirectory(prefix=".silencio-test-", dir=pocket) as directory:
            library = VoiceLibrary(Path(directory))
            library.samples.mkdir()
            sample_rate = 24_000
            samples = np.arange(sample_rate * 5) / sample_rate
            sf.write(library.samples / "fixture.wav", 0.2 * np.sin(2 * np.pi * 180 * samples), sample_rate)
            ident = clone_voice(library, PocketEncoder(pocket),
                                Recipe("Synthetic fixture", ("fixture.wav",), seconds=4), stop, Mock(), Mock())
            factory = SynthesisFactory(library.root, assets / "pocket-cuda/v4.0.1", model_dir)
            for preset, text in (("260", "[laugh]"), (ident, "<sad> What a long day. [sigh]")):
                with self.subTest(preset=preset):
                    synth = factory.create(Playback("silencio2", preset, 0.3), process_started=None)
                    try:
                        synth.prepare(stop)
                        pcm = synth.render(text, stop)
                        self.assertEqual(pcm.rate, sample_rate)
                        samples = np.frombuffer(pcm.data, dtype="<f4")
                        self.assertGreater(len(samples), 2400)
                        self.assertTrue(np.isfinite(samples).all())
                        self.assertGreater(np.max(np.abs(samples)), 0.001)
                    finally:
                        synth.close()

    def test_installed_jevos_scores_three_full_synthetic_messages(self):
        directory = Path(os.environ["VOICE_HARNESS_MODEL_FIXTURE"]) / "archive-models/jevos-v5-openvino-int8-v1"
        try:
            installation(directory)
        except VoiceError:
            self.skipTest("requires the installed Jevos v5 OpenVINO CPU bundle")
        stop = Event()
        model = Jevos(directory, lambda _pid: None)
        self.addCleanup(model.close)
        model.prepare(stop)
        messages = [
            Message("1", "Person 1", "We're debugging a Python program.\n\nThe function crashes."),
            Message("2", "Person 2", "Let's add a test for the error."),
            Message("3", "Person 1", "Great, I'm happy the test found the bug."),
        ]
        scores = model.score(messages, ("Are they discussing programming?", "Are they eating?"), stop)
        self.assertGreater(scores[0], scores[1])
        self.assertTrue(all(0 <= score <= 1 for score in scores))

    def test_installed_stt_and_tts_process_synthetic_inputs_without_devices(self):
        bundle = Path(os.environ["VOICE_HARNESS_MODEL_FIXTURE"])
        recognizer = DictateRecognizer(bundle / "voice/dictate")
        speaker = PocketSpeaker(bundle / "pocket-tts/4.0.1", Playback("18m", "alba", 0.3), Mock())
        self.addCleanup(recognizer.close)
        self.addCleanup(speaker.close)
        stop = Event()
        start, cpu_start = time.monotonic(), time.process_time()
        recognizer.select_model("multilingual", 2)
        key = recognition_key(bundle / "voice/dictate", CallInput(threads=2))
        events = EventBus()
        models = ModelResidency(events)
        self.addCleanup(models.evict_idle)
        with events.subscribe(("model.unloaded",)) as notices:
            with models.borrow(key, lambda: recognizer, stop, Mock(), None,
                               expires_at=lambda: time.monotonic() + 0.1) as loaded:
                self.assertIsInstance(loaded.transcribe(np.zeros(16_000, dtype="int16")), str)
                speaker.prepare(stop)
                sample = resident_sample(start, cpu_start, speaker.synthesis.engine.child.pid)
                self.assertGreater(sample.host_bytes, 0)
                self.assertGreater(sample.pocket_bytes, 0)
                self.assertGreater(sample.pocket_cpu_seconds, 0)
                pcm = speaker.synthesis.render("This is a local voice test.", stop)
                samples = np.frombuffer(pcm.data, dtype="<f4")
                self.assertGreater(len(samples), 2400)
                self.assertTrue(np.isfinite(samples).all())
                self.assertTrue(samples.any())
            self.assertEqual(notices.get(timeout=4).model.capability, "stt")
            self.assertFalse(recognizer.ready)
            self.assertGreater(models.recognition_relief()[1], 0)

    def test_installed_clone_and_7m_engine_render_pcm(self):
        bundle = Path(os.environ["VOICE_HARNESS_MODEL_FIXTURE"]) / "pocket-tts/4.0.1"
        stop = Event()
        with TemporaryDirectory(prefix=".clone-test-", dir=bundle) as directory:
            library = VoiceLibrary(Path(directory))
            library.samples.mkdir()
            samples = np.arange(24_000 * 5) / 24_000
            sf.write(library.samples / "fixture.wav", 0.2 * np.sin(2 * np.pi * 180 * samples), 24_000)
            recipe = Recipe("Synthetic fixture", ("fixture.wav",), seconds=4)
            ident = clone_voice(library, PocketEncoder(bundle), recipe, stop, Mock(), Mock())
            self.assertIn(ident, library.recipes())
            engine = ResidentEngine(bundle)
            try:
                profile = library.presets / ident / "voice.safetensors"
                engine.load(Playback("18m", ident, 0.3), "cloning.yaml", str(profile), stop)
                pcm = engine.render("This is a fixture.", stop)
                self.assertGreater(np.abs(np.frombuffer(pcm, dtype="<f4")).max(), 0.001)
            finally:
                engine.unload()

        device = Mock()
        playback = Playback("7m", "alba", 0.3)
        factory = SynthesisFactory(bundle)
        key = factory.key(playback)
        with TemporaryDirectory() as directory:
            harness = Harness(State(Path(directory)))
            worker = Mock(harness=harness)
            try:
                with harness.events.subscribe(("model.ready", "model.reused")) as updates:
                    for _ in range(2):
                        worker.job = harness.submit("Voice preview", models=(key,))
                        device.reset_mock()
                        with patch("sounddevice.RawOutputStream", return_value=device):
                            status = harness.execute(worker.job, lambda token: preview_voice(
                                factory, playback, "This uses the seven million model.", worker, token,
                            ))
                        self.assertEqual(status, "Voice preview finished.")
                    loaded, reused = updates.get_nowait(), updates.get_nowait()
                    self.assertEqual((loaded.kind, reused.kind), ("model.ready", "model.reused"))
                    self.assertEqual(loaded.model.id, reused.model.id)
            finally:
                harness.models.evict_idle()
        worker.progress.emit.assert_called_with(1, 1, "preview segments queued for playback")
        device.stop.assert_called_once()
        device.close.assert_called_once()
        pcm = b"".join(call.args[0] for call in device.write.call_args_list)
        self.assertGreater(np.abs(np.frombuffer(pcm, dtype="<f4")).max(), 0.001)
