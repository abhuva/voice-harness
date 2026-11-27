from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PyQt6.QtWidgets import QApplication, QTabWidget

from voice_harness.decision_view import DecisionHeatmap
from voice_harness.decisions import QUESTIONS, Score
from voice_harness.harness import Harness
from voice_harness.hardware import ModelUsage
from voice_harness.gpu_counters import group_by_pid
from voice_harness.residency import ModelKey, ModelObservation
from voice_harness.settings import Settings
from voice_harness.state import State
from voice_harness.telemetry import TurnTiming
from voice_harness.window import Window
from voice_harness.work_types import Lineage


class MonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_monitor_is_separate_and_shows_modules_jobs_and_per_turn_ms(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(root, root / "bundle")
            harness = Harness(State(settings.local))
            window = Window(settings, harness)
            window.show()
            self.addCleanup(window.close)
            job = harness.submit("Local fixture")
            harness.execute(job, lambda _: "Completed")
            timing = TurnTiming()
            for name, at in (("voice_end", 1), ("transcript_ready", 5.15), ("send", 5.2),
                             ("first_text", 5.7), ("first_audio", 6.05), ("first_audio", 7)):
                timing.mark(name, at)
            harness.telemetry.add_turn(Lineage("call", job.id, "turn"), timing)

            with harness.telemetry.online("Audio playback"):
                window.monitor_button.click()
                monitor = window.monitor
                self.assertTrue(monitor.isWindow() and monitor.isVisible())
                self.assertTrue(any(monitor.modules.item(row, 0).text() == "Audio playback" and
                                    monitor.modules.item(row, 1).text() == "Online"
                                    for row in range(monitor.modules.rowCount())))
                self.assertEqual(monitor.jobs.item(0, 1).text(), "Completed")
                self.assertEqual([monitor.latency.item(0, column).text() for column in range(1, 4)],
                                 ["4.150 s", "0.500 s", "0.350 s"])
                monitor.close()
                self.assertTrue(window.isVisible())
                self.assertFalse(monitor.timer.isActive())
            self.assertTrue(any(window.findChild(QTabWidget).tabText(i) == "Jevos"
                                for i in range(window.findChild(QTabWidget).count())))
            window.close()

    def test_heatmap_shows_percentages_and_preserves_question_provenance(self):
        heatmap = DecisionHeatmap()
        heatmap.load([
            Score(1, 0, "jevos-v5", {"Older question?": 0.05}),
            Score(2, 60, "jevos-v5", {QUESTIONS[0]: 0.73, "New question?": 1.0}),
        ], (QUESTIONS[0], "New question?"))
        table = heatmap.table
        self.assertEqual((table.rowCount(), table.columnCount()), (3, 2))
        self.assertEqual(table.verticalHeaderItem(0).text(), "Anger / frustration")
        self.assertEqual(table.item(0, 1).text(), "73%")
        self.assertIsNone(table.item(0, 0))
        self.assertEqual(table.item(2, 0).text(), "5%")
        self.assertIn("Older question?", table.item(2, 0).toolTip())

    def test_model_rows_show_measured_gpu_and_distinguish_shared_cpu(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(root, root / "assets")
            window = Window(settings, Harness(State(settings.local)))
            self.addCleanup(window.close)
            monitor = window.monitor
            usage = ModelUsage(200 * 2**20, 12.5, 38.2, 480 * 2**20)
            monitor.model_usage = {"cuda": usage}
            tts = ModelObservation(ModelKey("tts", (root, "18m", "alba", 0.3, "cuda")), "Warm", None, "cuda", 123)
            cpu = ModelObservation(ModelKey("stt", (root, "german", 4, "cpu")), "Warm", 300 * 2**20, "cpu")
            self.assertEqual(monitor.model_row(tts)[1:],
                             ("Pocket TTS 18M REFL · GPU (CUDA)", "Warm", "200 MiB", "12.5%", "38.2%", "480 MiB"))
            self.assertEqual(monitor.model_row(cpu)[3:], ("300 MiB", "Shared", "0.0%", "0 MiB"))
            window.close()

        instances = [("pid_123_luid_1_eng_0", 30), ("pid_123_luid_1_eng_1", 41), ("pid_1234_luid_1_eng_0", 9)]
        self.assertEqual(group_by_pid(instances, max), {123: 41, 1234: 9})
