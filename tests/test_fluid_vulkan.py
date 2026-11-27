from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import MagicMock, Mock, patch
import wave

import numpy as np

from voice_harness.contracts import VoiceError
from voice_harness.fluid_vulkan import FLUID_VULKAN_DIRECTORY, MODEL, MODEL_BYTES, FluidVulkan


class FluidVulkanTests(unittest.TestCase):
    def test_missing_bundle_reports_how_to_use_cpu_or_install(self):
        with TemporaryDirectory() as directory:
            model = FluidVulkan(Path(directory), 4, Mock())
            with self.assertRaisesRegex(VoiceError, "setup-fluid-vulkan.*CPU"):
                model.prepare(Event())
            self.assertFalse(model.ready)

    def test_cpu_fallback_cannot_report_gpu_ready(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model = FluidVulkan(root, 4, Mock())
            (root / "parakeet-server.exe").touch()
            (root / MODEL).write_bytes(b"fixture")
            process = Mock(pid=123, stderr=BytesIO())
            process.poll.return_value = None

            def start(args, **kwargs):
                self.assertEqual(kwargs["env"]["PARAKEET_DEVICE"], "Vulkan0")
                port = args[args.index("--port") + 1]
                process.stderr.write(f"parakeet-server: listening on http://127.0.0.1:{port} (model: fixture)\n".encode())
                process.stderr.seek(0)
                return process

            with (patch("voice_harness.fluid_vulkan.MODEL_BYTES", len(b"fixture")),
                  patch("voice_harness.fluid_vulkan.subprocess.Popen", side_effect=start)):
                self.addCleanup(model.close)
                with self.assertRaisesRegex(VoiceError, "could not use Vulkan0"):
                    model.prepare(Event())
                self.assertFalse(model.ready)
                model.close()
                process.kill.assert_called_once()

    def test_sends_mono_pcm_and_treats_empty_transcript_as_valid(self):
        model = FluidVulkan(Path("."), 4, Mock())
        model.url = "http://127.0.0.1:1"
        model.client = MagicMock()
        response = model.client.post.return_value.__enter__.return_value
        response.status_code = 200
        response.json.return_value = {"text": " "}
        self.assertEqual(model._decode(np.zeros(1600, dtype=np.int16)), "")
        kwargs = model.client.post.call_args.kwargs
        self.assertFalse(kwargs["allow_redirects"])
        with wave.open(BytesIO(kwargs["files"]["file"][1]), "rb") as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()),
                             (1, 2, 16000, 1600))

    @unittest.skipUnless(os.environ.get("VOICE_HARNESS_MODEL_FIXTURE"), "requires -LocalModels")
    def test_installed_vulkan_worker_warms_decodes_and_exits(self):
        directory = Path(__file__).resolve().parents[1] / "var" / FLUID_VULKAN_DIRECTORY
        model = directory / MODEL
        if not model.is_file() or model.stat().st_size != MODEL_BYTES:
            self.skipTest("requires scripts/setup-fluid-vulkan.ps1")
        process_started = Mock()
        recognizer = FluidVulkan(directory, 4, process_started)
        try:
            recognizer.prepare(Event())
            process_started.assert_called_once_with(recognizer.process_id)
            self.assertTrue(recognizer.ready)
            self.assertIsInstance(recognizer.transcribe(np.zeros(16000, dtype=np.int16)), str)
            child = recognizer.process
        finally:
            recognizer.close()
        self.assertIsNotNone(child.poll())
