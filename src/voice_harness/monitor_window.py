"""Independent read-only desktop monitor for the local harness."""

from concurrent.futures import ThreadPoolExecutor
from sqlite3 import Error as StateError

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QHeaderView, QLabel, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .hardware import HardwareSampler
from .jevos_vulkan import VULKAN_LABEL
from .recognizer import STT_MODELS


MODULES = (
    "Harness / job scheduler", "Call orchestrator", "Microphone / speech detection", "Speech recognition",
    "Kindroid connector", "Speech synthesis", "Audio playback", "Decision workflow", "Jevos scorer",
)
DISPLAY_MODELS = {"stt": "Speech recognition", "tts": "Speech synthesis", "decision": "Decision scoring"}


class Table(QTableWidget):
    def __init__(self, name, headers):
        super().__init__(0, len(headers))
        self.setAccessibleName(name)
        self.setHorizontalHeaderLabels(headers)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self._rows = None

    def load(self, rows):
        if self._rows == rows:
            return
        self._rows = rows
        self.setRowCount(len(rows))
        for y, row in enumerate(rows):
            for x, text in enumerate(row):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.setItem(y, x, item)


def model_text(key):
    if key.kind == "stt":
        device = "GPU (Vulkan)" if key.configuration[-1] == "vulkan" else "CPU"
        return f"{STT_MODELS.get(key.configuration[1], key.configuration[1])} · {device}"
    if key.kind == "tts":
        device = "GPU (CUDA)" if key.configuration[-1] == "cuda" else "CPU"
        model = key.configuration[1]
        label = "Silencio-2 English" if model == "silencio2" else f"Pocket TTS {model.upper()} REFL"
        return f"{label} · {device}"
    return VULKAN_LABEL if key.configuration[-1] == "vulkan" else "Jevos v5 OpenVINO INT8 · CPU"


class MonitorWindow(QWidget):
    def __init__(self, harness, parent):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Voice Harness Monitor")
        self.resize(1040, 580)
        self.harness = harness
        self.sampler = HardwareSampler()
        self.executor = None
        self.sample = None
        self.model_usage = {}
        layout = QVBoxLayout(self)
        self.hardware = QLabel("Reading CPU, GPU and memory…")
        self.hardware.setTextFormat(Qt.TextFormat.PlainText)
        self.hardware.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hardware.setAccessibleName("CPU, GPU and memory")
        self.hardware.setWordWrap(True)
        layout.addWidget(self.hardware)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        self.models = Table("Loaded models", ("Capability", "Model and device", "State", "Host RAM", "CPU", "GPU", "VRAM"))
        self.modules = Table("Module activity", ("Module", "State"))
        self.jobs = Table("Recent harness jobs", ("Capability", "Status", "Owner", "Job ID", "Turn ID"))
        self.latency = Table("Call timing by turn", (
            "Turn started", "Last speech → STT ready", "Send → first Kindroid text", "First text → first audio", "Turn ID",
        ))
        models_page = QWidget()
        model_layout = QVBoxLayout(models_page)
        self.stt_stats = QLabel()
        self.stt_stats.setAccessibleName("Speech recognition load and unload measurements")
        self.stt_stats.setWordWrap(True)
        model_layout.addWidget(self.stt_stats)
        model_layout.addWidget(self.models, 1)
        readings = QLabel("CPU is a share of total CPU capacity. GPU shows the busiest engine. "
                          "Shared CPU belongs to the Python process. An em dash marks an unavailable reading.")
        readings.setWordWrap(True)
        model_layout.addWidget(readings)
        for page, title in ((self.modules, "Modules"), (models_page, "Models"),
                            (self.jobs, "Jobs"), (self.latency, "Call timings")):
            tabs.addTab(page, title)
        self.timing_note = "An em dash marks an unreached timing milestone. First audio uses the output device timestamp."
        self.note = QLabel(self.timing_note)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, event):
        if self.executor is None:
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="monitor-hardware")
        self.refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def shutdown(self):
        self.close()
        if self.executor is not None:
            self.executor.submit(self.sampler.close)
            self.executor.shutdown(wait=False)
            self.executor = None

    def refresh(self):
        observations = self.harness.models.observations()
        if self.sample is not None and self.sample.done():
            try:
                hardware, self.model_usage = self.sample.result()
                self.hardware.setText("\n".join(hardware))
            except Exception:
                self.model_usage = {}
                self.hardware.setText("Host readings are temporarily unavailable.")
            self.sample = None
        if self.sample is None and self.executor is not None:
            self.sample = self.executor.submit(self.sampler.sample, observations)
        modules, turns = self.harness.telemetry.snapshot()
        self.modules.load(tuple(
            (name, "Online" if name in modules or name == MODULES[0] else "Idle") for name in MODULES
        ))
        self.models.load(tuple(self.model_row(row) for row in observations))
        load, released = self.harness.models.recognition_relief()
        cold = "—" if load is None else f"{load:.2f} s"
        memory = "—" if released is None else f"{released / 2**20:,.0f} MiB"
        self.stt_stats.setText(f"Last cold STT load {cold} · STT working-set decrease at idle unload {memory}")

        def seconds(value):
            return "—" if value is None else f"{value:.3f} s"

        self.latency.load(tuple((timing.started.strftime("%H:%M:%S"), *(seconds(x) for x in timing.seconds()), scope.turn_id[:8])
                                for scope, timing in reversed(turns)))
        try:
            self.jobs.load(tuple((capability, status.title(), owner or "—", ident[:8], turn[:8] if turn else "—")
                                 for ident, capability, status, owner, turn in self.harness.queue.recent()))
            self.note.setText(self.timing_note)
        except StateError:
            self.note.setText("Could not read the local job history.")

    def model_row(self, row):
        measured = self.model_usage.get(row.id)
        gpu_backend = row.key.configuration[-1] in ("vulkan", "cuda")

        def memory(size):
            return "—" if size is None else f"{size / 2**20:,.0f} MiB"

        def percent(value):
            return "—" if value is None else f"{value:.1f}%"

        cpu = percent(measured.cpu_pct if measured else None)
        if row.id is not None and row.pid is None and row.key.kind == "stt":
            cpu = "Shared"
        gpu = percent(measured.gpu_pct if measured else None) if gpu_backend else "0.0%"
        vram = memory(measured.vram if measured else None) if gpu_backend else "0 MiB"
        host = measured.rss if measured and measured.rss is not None else row.memory
        return DISPLAY_MODELS[row.key.kind], model_text(row.key), row.state, memory(host), cpu, gpu, vram
