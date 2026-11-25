import os
from pathlib import Path
from threading import Event
import time
import unittest
from unittest.mock import Mock

import numpy as np

from voice_harness.presets import Playback
from voice_harness.recognizer import DictateRecognizer
from voice_harness.resources import resident_sample
from voice_harness.speaker import PocketSpeaker


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
