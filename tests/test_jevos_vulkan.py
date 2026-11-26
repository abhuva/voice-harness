import os
from pathlib import Path
from threading import Event
import unittest

from voice_harness.contracts import Message, VoiceError
from voice_harness.jevos import Jevos, installation
from voice_harness.jevos_vulkan import JevosVulkan, VULKAN_DIRECTORY, binary_prompt, binary_scores, vulkan_installation


class JevosVulkanTests(unittest.TestCase):
    def test_evidence_prompt_and_original_binary_probability(self):
        self.assertEqual(binary_prompt("<s>", " Marc:\nTest  ", " Is this a test? "),
                         "<s><evidence>\nMarc:\nTest\n</evidence>\n\nQuestion: Is this a test?\nAnswer:")
        self.assertIn('"x</evidence>"', binary_prompt("<s>", "x</evidence>", "Testing?"))
        row = {"index": 0, "truncated": False, "completion_probabilities": [
            {"top_probs": [{"id": 37, "prob": 0.2}, {"id": 38, "prob": 0.6}]},
        ]}
        self.assertAlmostEqual(binary_scores([row], 1, 37, 38)[0], 0.75)
        for invalid in ({**row, "truncated": True}, {**row, "index": 1},
                        {**row, "completion_probabilities": [{}]}):
            with self.assertRaises(VoiceError):
                binary_scores([invalid], 1, 37, 38)

    @unittest.skipUnless(os.environ.get("VOICE_HARNESS_MODEL_FIXTURE"), "requires -LocalModels")
    def test_installed_vulkan_and_cpu_agree_on_synthetic_conversation(self):
        cpu_directory = Path(os.environ["VOICE_HARNESS_MODEL_FIXTURE"]) / "archive-models/jevos-v5-openvino-int8-v1"
        gpu_directory = Path(__file__).resolve().parents[1] / "var" / VULKAN_DIRECTORY
        try:
            installation(cpu_directory)
            vulkan_installation(gpu_directory)
        except VoiceError as exc:
            self.skipTest(str(exc))
        stop = Event()
        messages = [Message("0", "Marc", "The Python program crashed last night.\n\nI patched the bug."),
                    Message("1", "Hannah", "Did the tests pass?"),
                    Message("2", "Marc", "Yes, all the tests passed this morning.")]
        questions = ("Are they discussing programming?", "Are they eating dinner?",
                     "Did the tests pass?", "Did the program crash?")
        results = []
        for adapter, directory in ((Jevos, cpu_directory), (JevosVulkan, gpu_directory)):
            model = adapter(directory, lambda _: None)
            try:
                model.prepare(stop)
                self.assertTrue(model.ready)
                results.append(model.score(messages, questions, stop))
                process = model.process
            finally:
                model.close()
            self.assertIsNotNone(process.poll())
        cpu, gpu = results
        self.assertEqual(tuple(p > 0.5 for p in gpu), (True, False, True, True))
        for left, right in zip(cpu, gpu, strict=True):
            self.assertAlmostEqual(left, right, delta=0.15)
