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
from voice_harness.encoder import PocketEncoder
from voice_harness.engine import ResidentEngine
from voice_harness.presets import Playback, Recipe, VoiceLibrary
from voice_harness.recognizer import DictateRecognizer
from voice_harness.resources import resident_sample
from voice_harness.speaker import PocketSpeaker
from voice_harness.voice_jobs import preview_voice


@unittest.skipUnless(os.environ.get("VOICE_HARNESS_MODEL_FIXTURE"), "requires -LocalModels")
class LocalModelTests(unittest.TestCase):
    def test_installed_stt_and_tts_process_synthetic_inputs_without_devices(self):
        bundle = Path(os.environ["VOICE_HARNESS_MODEL_FIXTURE"])
        recognizer = DictateRecognizer(bundle / "voice/dictate")
        speaker = PocketSpeaker(bundle / "pocket-tts/4.0.1", Playback("18m", "alba", 0.3), Mock())
        self.addCleanup(recognizer.close)
        self.addCleanup(speaker.close)
        stop = Event()
        start, cpu_start = time.monotonic(), time.process_time()
        recognizer.select_model("multilingual", 2)
        recognizer.prepare(stop)
        self.assertIsInstance(recognizer.transcribe(np.zeros(16_000, dtype="int16")), str)
        speaker.prepare(stop)
        sample = resident_sample(start, cpu_start, speaker.engine.child.pid)
        self.assertGreater(sample.host_bytes, 0)
        self.assertGreater(sample.pocket_bytes, 0)
        self.assertGreater(sample.pocket_cpu_seconds, 0)
        pcm = speaker.engine.render("This is a local voice test.", stop)
        samples = np.frombuffer(pcm, dtype="<f4")
        self.assertGreater(len(samples), 2400)
        self.assertTrue(np.isfinite(samples).all())
        self.assertTrue(samples.any())

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
        worker = Mock()
        with patch("sounddevice.RawOutputStream", return_value=device):
            status = preview_voice(bundle, Playback("7m", "alba", 0.3),
                                   "This uses the seven million model.", worker, stop)
        self.assertEqual(status, "Voice preview finished.")
        worker.progress.emit.assert_called_with(1, 1, "preview segments queued for playback")
        device.stop.assert_called_once()
        device.close.assert_called_once()
        pcm = b"".join(call.args[0] for call in device.write.call_args_list)
        self.assertGreater(np.abs(np.frombuffer(pcm, dtype="<f4")).max(), 0.001)
