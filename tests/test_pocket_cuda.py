import os
from pathlib import Path
from threading import Event, Timer
import unittest

import numpy as np

from voice_harness.contracts import VoiceError
from voice_harness.engine import ResidentEngine
from voice_harness.presets import Playback
from voice_harness.synthesis import SynthesisFactory, synthesis_key


class PocketCudaTests(unittest.TestCase):
    def test_backend_participates_in_resident_model_key(self):
        root = Path("fixture")
        cpu = Playback("18m", "alba", 0.3)
        cuda = Playback("18m", "alba", 0.3, backend="cuda")
        self.assertNotEqual(synthesis_key(root, cpu), synthesis_key(root, cuda))
        self.assertEqual(synthesis_key(root, cuda).configuration[-1], "cuda")

    def test_missing_cuda_runtime_reports_cpu_choice(self):
        engine = ResidentEngine(Path("fixture"))
        with self.assertRaisesRegex(VoiceError, "setup-pocket-cuda.*CPU"):
            engine.load(Playback("18m", "alba", 0.3, backend="cuda"), "english.yaml", "alba.safetensors", Event())

    @unittest.skipUnless(os.environ.get("VOICE_HARNESS_MODEL_FIXTURE"), "requires -LocalModels")
    def test_installed_cuda_worker_renders_both_models_and_recovers_after_cancel(self):
        root = Path(os.environ["VOICE_HARNESS_MODEL_FIXTURE"]) / "pocket-tts/4.0.1"
        runtime = Path(__file__).resolve().parents[1] / "var/pocket-cuda/v4.0.1"
        if not (runtime / "Lib/site-packages/torch/__init__.py").is_file():
            self.skipTest("requires scripts/setup-pocket-cuda.ps1")
        factory = SynthesisFactory(root, runtime)
        for model_id in ("18m", "7m"):
            with self.subTest(model=model_id):
                model = factory.create(Playback(model_id, "alba", 0.3, backend="cuda"), process_started=None)
                try:
                    model.prepare(Event())
                    self.assertTrue(model.ready)
                    child = model.engine.child
                    if model_id == "18m":
                        stop = Event()
                        timer = Timer(0.2, stop.set)
                        timer.start()
                        try:
                            self.assertIsNone(model.render("A sentence for testing cancellation. " * 8, stop))
                        finally:
                            timer.cancel()
                            timer.join()
                        self.assertTrue(model.ready)
                    pcm = model.render("The speech model is ready.", Event())
                    self.assertEqual(pcm.rate, 24000)
                    samples = np.frombuffer(pcm.data, dtype="<f4")
                    self.assertTrue(len(samples) > 2400 and np.isfinite(samples).all())
                    self.assertGreater(np.abs(samples).max(), 0.001)
                finally:
                    model.close()
                self.assertIsNotNone(child.poll())
