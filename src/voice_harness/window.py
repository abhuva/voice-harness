"""Desktop controls and presentation. Work advances outside the UI thread."""

from functools import partial
from sqlite3 import Error as StateError

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .chat_view import ChatView
from .contracts import CallInput, VoiceError
from .decisions import Decisions, read_settings
from .decision_view import DecisionControls, DecisionHeatmap
from .level_meter import LevelMeter
from .monitor_window import MonitorWindow
from .playback_controls import PlaybackControls
from .presets import OutputSettings, SynthesisSettings, VoiceLibrary
from .voice_controls import CloningControls, TtsControls
from .voice_jobs import build_preset, import_clips, preview_voice
from .recognizer import STT_MODELS, recognition_key
from .worker import CallOperation, Worker, call_profile, load_history


class DictationControls(QWidget):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        form = QFormLayout(self)
        self.model = QComboBox()
        for ident, label in STT_MODELS.items():
            self.model.addItem(label, ident)
        self.stt_backend = QComboBox()
        self.stt_backend.addItem("CPU", "cpu")
        self.stt_backend.addItem("GPU (Vulkan)", "vulkan")
        self.stt_backend.setToolTip("GPU is available for Fluid Speech Pro with the optional Windows Vulkan bundle.")
        self.model.currentIndexChanged.connect(self.update_backend)
        self.update_backend()
        self.threads = QSpinBox()
        self.threads.setRange(1, 16)
        self.threshold = QSpinBox()
        self.threshold.setRange(-80, -5)
        self.threshold.setSuffix(" dBFS")
        self.stt_on_demand = QCheckBox("Load recognizer on demand and unload after silence")
        self.stt_on_demand.setToolTip("Clear to load when a call starts and keep the model warm after Stop.")
        self.stt_idle = QSpinBox()
        self.stt_idle.setRange(1, 3600)
        self.stt_idle.setSuffix(" s")
        self.chunk, self.paragraph, self.finish, self.barge_in = (QDoubleSpinBox() for _ in range(4))
        for field in (self.chunk, self.paragraph, self.finish, self.barge_in):
            field.setRange(0.1, 10.0)
            field.setDecimals(1)
            field.setSingleStep(0.1)
            field.setSuffix(" s")
        for text, control in (
            ("&Recognizer", self.model), ("Recognition &device", self.stt_backend), ("CPU &threads", self.threads),
            ("Speech thres&hold", self.threshold), ("Pause to transcribe a chun&k", self.chunk),
            ("Pause for a new p&aragraph", self.paragraph), ("Pause to &finish", self.finish),
        ):
            form.addRow(text, control)
        form.addRow(self.stt_on_demand)
        form.addRow("Unload after &silence", self.stt_idle)
        self.stt_on_demand.toggled.connect(self.stt_idle.setEnabled)
        form.addRow("&Speech needed to interrupt", self.barge_in)
        self.interrupt = QCheckBox("Interrupt Kindroid replies by speaking")
        form.addRow(self.interrupt)
        self.interrupt.toggled.connect(self.barge_in.setEnabled)
        for field in (self.threads, self.threshold, self.chunk, self.paragraph, self.finish, self.stt_idle, self.barge_in):
            field.valueChanged.connect(self.changed)
        for field in (self.model, self.stt_backend):
            field.currentIndexChanged.connect(self.changed)
        for field in (self.interrupt, self.stt_on_demand):
            field.toggled.connect(self.changed)

    def load(self, settings):
        self.model.setCurrentIndex(self.model.findData(settings.model))
        self.stt_backend.setCurrentIndex(self.stt_backend.findData(settings.stt_backend))
        self.interrupt.setChecked(settings.interrupt)
        self.barge_in.setEnabled(settings.interrupt)
        self.stt_on_demand.setChecked(settings.stt_on_demand)
        self.stt_idle.setEnabled(settings.stt_on_demand)
        for name in ("threads", "threshold", "chunk", "paragraph", "finish", "stt_idle", "barge_in"):
            getattr(self, name).setValue(getattr(settings, name))

    def settings(self):
        model = self.model.currentData()
        return CallInput(model, self.threads.value(), self.threshold.value(),
                         self.chunk.value(), self.paragraph.value(), self.finish.value(),
                         self.interrupt.isChecked(), self.stt_idle.value(), self.barge_in.value(),
                         stt_on_demand=self.stt_on_demand.isChecked(),
                         stt_backend=self.stt_backend.currentData() if model == "fluid-speech-pro" else "cpu")

    def update_backend(self):
        fluid = self.model.currentData() == "fluid-speech-pro"
        self.stt_backend.setEnabled(fluid)
        if not fluid:
            self.stt_backend.setCurrentIndex(self.stt_backend.findData("cpu"))


class Window(QWidget):
    phone_requested = pyqtSignal(object)
    phone_disconnected = pyqtSignal(object)

    def __init__(self, settings, harness):
        super().__init__()
        self.setWindowTitle("Voice Harness")
        self.resize(1060, 630)
        self.settings = settings
        self.harness = harness
        self.worker = None
        self.call = None
        self.closing = False
        self.history_requested = False
        decision_path = settings.local / "jevos.json"
        decision_settings = read_settings(decision_path, settings.assets)
        self.decisions = Decisions(harness, decision_settings)
        self.monitor = MonitorWindow(harness, self)
        self.decision_tick = None
        self.decision_rows = []
        self.decision_questions = ()

        layout = QVBoxLayout(self)
        tabs = self.tabs = QTabWidget()
        self.phone_call = None
        layout.addWidget(tabs)
        chat_page = QWidget()
        tabs.addTab(chat_page, "Kindroid chat")
        chat_layout = QVBoxLayout(chat_page)
        columns = QSplitter(Qt.Orientation.Horizontal)
        columns.setChildrenCollapsible(False)
        chat_layout.addWidget(columns, 1)
        conversation = QWidget()
        columns.addWidget(conversation)
        row = QHBoxLayout(conversation)
        row.setContentsMargins(0, 0, 0, 0)
        self.chat_view = ChatView()
        row.addWidget(self.chat_view, 1)
        self.microphone = LevelMeter("Microphone")
        self.speaker = LevelMeter("Output")
        row.addWidget(self.microphone)
        row.addWidget(self.speaker)
        self.heatmap = DecisionHeatmap()
        columns.addWidget(self.heatmap)
        columns.setSizes([630, 400])
        self.playback = PlaybackControls()
        self.playback.changed.connect(self.tune)
        self.playback.save.clicked.connect(self.save_playback)
        chat_layout.addWidget(self.playback)

        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        self.dictation = DictationControls()
        settings_layout.addWidget(self.dictation)
        self.strict_memory = QCheckBox("Wait for estimated RAM before starting speech work")
        self.strict_memory.setToolTip(
            "Uses measurements from previous jobs. Leave unchecked to start when the harness is idle."
        )
        settings_layout.addWidget(self.strict_memory)
        self.sst_status = QLabel()
        self.sst_status.setTextFormat(Qt.TextFormat.PlainText)
        self.sst_status.setAccessibleName("Saved SST settings")
        self.sst_status.setWordWrap(True)
        settings_layout.addWidget(self.sst_status)
        self.save = QPushButton("Sa&ve SST settings")
        self.save.clicked.connect(self.save_sst)
        settings_layout.addWidget(self.save)
        settings_layout.addStretch()
        tabs.addTab(settings_page, "SST")
        self.library = VoiceLibrary(settings.pocket)
        recipes = self.library.recipes()
        self.tts = TtsControls(self.library, recipes)
        self.tts.preview_requested.connect(self.start_preview)
        self.tts.save_requested.connect(self.save_tts)
        tabs.addTab(self.tts, "TTS")
        self.cloning = CloningControls(self.library, recipes)
        self.cloning.import_requested.connect(self.start_import)
        self.cloning.clone_requested.connect(self.start_clone)
        tabs.addTab(self.cloning, "Voice cloning")
        self.decision_controls = DecisionControls(decision_settings, decision_path, vulkan_directory=self.decisions.vulkan_directory)
        self.decision_controls.saved.connect(self.configure_decisions)
        tabs.addTab(self.decision_controls, "Jevos")

        self.delivery = QLabel()
        self.delivery.setWordWrap(True)
        self.delivery.setText("Check the Kindroid conversation for the last message before starting another call.")
        layout.addWidget(self.delivery)

        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.start = QPushButton("&Call")
        self.start.clicked.connect(self.start_call)
        self.stop = QPushButton("&Stop")
        self.stop.clicked.connect(self.stop_work)
        self.review = QPushButton("I checked &delivery")
        self.review.clicked.connect(self.acknowledge_delivery)
        controls.addWidget(self.start)
        controls.addWidget(self.stop)
        controls.addWidget(self.review)
        controls.addStretch()
        self.monitor_button = QPushButton("&Monitor")
        self.monitor_button.clicked.connect(self.show_monitor)
        controls.addWidget(self.monitor_button)

        self.previous = QLabel(self.harness.state.last_status())
        self.previous.setAccessibleName("Previous operation")
        self.previous.setTextFormat(Qt.TextFormat.PlainText)
        self.previous.setWordWrap(True)
        self.previous.hide()
        layout.addWidget(self.previous)
        self.status = QLabel(self.previous.text())
        self.status.setAccessibleName("Operation status")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.decision_status = QLabel()
        self.decision_status.setAccessibleName("Jevos work status")
        self.decision_status.setTextFormat(Qt.TextFormat.PlainText)
        self.decision_status.setWordWrap(True)
        layout.addWidget(self.decision_status)
        self.decision_progress = QProgressBar()
        self.decision_progress.setRange(0, 0)
        self.decision_progress.setTextVisible(False)
        layout.addWidget(self.decision_progress)
        self.decision_poll = QTimer(self)
        self.decision_poll.setInterval(750)
        self.decision_poll.timeout.connect(self.refresh_decisions)
        self.refresh_decisions()

        errors = []

        def load_section(load, default):
            try:
                return load()
            except VoiceError as exc:
                errors.append(str(exc))
                return default

        self.saved_call_input, self.saved_strict_memory = load_section(settings.load_sst, (CallInput(), False))
        self.voice = load_section(settings.load_tts, SynthesisSettings())
        output = load_section(settings.load_output, OutputSettings())
        self.tts.load(self.voice)
        self.tts.show_saved(self.voice)
        self.dictation.load(self.saved_call_input)
        self.strict_memory.setChecked(self.saved_strict_memory)
        self.dictation.changed.connect(self.show_saved_sst)
        self.strict_memory.toggled.connect(self.show_saved_sst)
        self.show_saved_sst()
        self.playback.load(output)
        if errors:
            self.status.setText(" ".join(dict.fromkeys(errors)))
        self.update_controls()

    def update_controls(self):
        active = self.worker is not None
        state_ok = True
        try:
            pending = self.harness.state.pending_delivery() is not None
        except StateError:
            state_ok = pending = False
            self.status.setText("Could not read the local delivery state.")
        self.start.setEnabled(state_ok and not active and not pending and not self.closing)
        self.stop.setEnabled(active and not self.worker.job.stop.is_set())
        self.review.setVisible(pending)
        self.review.setEnabled(not active)
        self.delivery.setVisible(pending)
        self.dictation.setEnabled(not active)
        self.strict_memory.setEnabled(not active)
        self.tts.setEnabled(not active)
        self.cloning.setEnabled(not active)
        self.save.setEnabled(not active)
        self.playback.mute.setEnabled(not active)
        self.playback.save.setEnabled(not active)

    def start_history(self):
        if self.history_requested or not self.settings.env_file.is_file():
            return
        self.history_requested = True
        if self.previous.text() != "Ready":
            self.previous.show()
        self.begin("Recent history", partial(load_history, self.settings), "Loading recent Kindroid history")

    def enable_phone(self, origin, pairing):
        page = QWidget()
        form = QFormLayout(page)
        pin = QLineEdit(pairing.pin)
        pin.setMaxLength(8)
        pin.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        font = pin.font()
        font.setPointSize(24)
        pin.setFont(font)
        form.addRow("Pairing &PIN", pin)
        save_pin = QPushButton("Set pairing PIN")
        form.addRow(save_pin)
        notice = QLabel("Use 4 to 8 digits.")
        notice.setAccessibleName("Pairing PIN status")
        form.addRow(notice)
        link = QLineEdit(f"{origin}/#code={pairing.pin}")
        link.setReadOnly(True)
        form.addRow("Phone pairing &link", link)

        def apply():
            try:
                pairing.set_pin(pin.text().strip())
            except VoiceError as exc:
                notice.setText(str(exc))
                return
            pin.setText(pairing.pin)
            link.setText(f"{origin}/#code={pairing.pin}")
            notice.setText("Pairing PIN set.")

        pin.textEdited.connect(lambda _: notice.setText("Click Set pairing PIN to apply."))
        pin.returnPressed.connect(apply)
        save_pin.clicked.connect(apply)
        self.tabs.addTab(page, "Phone")
        self.phone_requested.connect(self.start_phone_call, Qt.ConnectionType.QueuedConnection)
        self.phone_disconnected.connect(self.stop_phone_call, Qt.ConnectionType.QueuedConnection)

    def start_call(self):
        self.begin_call()

    def start_phone_call(self, phone):
        if not phone.closed.is_set():
            self.begin_call(phone)

    def stop_phone_call(self, phone):
        if self.phone_call is phone:
            self.stop_work()

    def begin_call(self, phone=None):
        def blocked(text):
            if phone is not None:
                phone.finish(text)

        if self.closing:
            blocked("Voice Harness is closing.")
            return
        if self.worker is not None:
            blocked("The laptop is busy. Wait for the current operation to end.")
            return
        try:
            if self.harness.state.pending_delivery():
                blocked("Review the unresolved Kindroid delivery on the laptop.")
                return
        except StateError:
            self.update_controls()
            blocked("Could not read the local delivery state.")
            return
        call_input, playback = self.saved_call_input, self.playback.apply_to(self.voice)
        self.call = CallOperation(self.settings, call_input, playback, phone=phone)
        keys = [recognition_key(self.settings.dictate, call_input, vulkan_directory=self.settings.fluid_vulkan)]
        if playback.speed:
            keys.append(self.settings.synthesis.key(playback))
        stage = "Preparing phone call" if phone is not None else "Preparing call"
        self.begin("Call", self.call, stage, models=keys, phone=phone,
                   profile=call_profile(call_input, playback, self.settings.synthesis),
                   recognition_on_demand=call_input.stt_on_demand)

    def start_import(self, paths):
        if self.worker is None and paths:
            self.begin("Recording import", partial(import_clips, self.library, paths), "Importing recordings")

    def start_clone(self, recipe):
        if self.worker is not None:
            return
        try:
            recipe.validate()
        except VoiceError as exc:
            self.status.setText(str(exc))
            return
        self.begin("Voice cloning", partial(build_preset, self.library, recipe), "Preparing reference clips")

    def start_preview(self):
        if self.worker is not None:
            return
        playback = self.playback.apply_to(self.tts.settings())
        if playback.speed == 0:
            self.status.setText("Expand Playback in Kindroid chat and unmute to hear the preview.")
            return
        preview = self.tts.text.toPlainText().strip()
        if not 1 <= len(preview) <= 4000:
            self.status.setText("Enter 1 to 4,000 characters to preview.")
            return
        factory = self.settings.synthesis
        self.begin("Voice preview", partial(preview_voice, factory, playback, preview),
                   "Preparing voice preview", models=(factory.key(playback),))

    def save_tts(self):
        if self.worker is not None:
            return
        voice = self.tts.settings()
        try:
            self.settings.save_tts(voice)
        except (OSError, VoiceError) as exc:
            self.status.setText(str(exc) if isinstance(exc, VoiceError) else "Could not save TTS settings.")
            return
        self.voice = voice
        self.tts.show_saved(voice)
        self.status.setText("TTS settings saved for the next call.")

    def show_clone(self, ident):
        recipes = self.library.recipes()
        self.cloning.refresh_recipes(recipes, ident)
        self.tts.select_clone(ident, recipes)

    def begin(self, label, operation, stage, *, models=(), profile=None, recognition_on_demand=False, phone=None):
        try:
            job = self.harness.submit(label, profile=profile, models=models,
                                      strict_memory=self.saved_strict_memory,
                                      recognition_on_demand=recognition_on_demand)
        except VoiceError as exc:
            self.call = None
            self.status.setText(str(exc))
            if phone is not None:
                phone.finish(str(exc))
            return
        except (OSError, StateError):
            self.call = None
            self.status.setText("Could not start the operation. Check the local state folder.")
            if phone is not None:
                phone.finish(self.status.text())
            return
        if label != "Recent history":
            self.previous.hide()
        self.worker = Worker(self.harness, job, operation, self)
        self.worker.stage.connect(self.show_stage)
        self.worker.progress.connect(self.show_progress)
        self.worker.imported.connect(self.cloning.refresh_clips)
        self.worker.cloned.connect(self.show_clone)
        self.worker.message.connect(self.show_message)
        if self.decisions.settings.enabled:
            self.worker.completed_message.connect(self.observe_message)
        self.worker.mic_level.connect(self.microphone.set_levels)
        self.worker.speaker_level.connect(self.speaker.set_levels)
        self.worker.resources.connect(self.status.setToolTip)
        self.worker.result.connect(self.status.setText)
        self.worker.finished.connect(self.finish_work)
        self.phone_call = phone
        if phone is not None:
            phone.bind_call(job.stop)
            self.worker.stage.connect(phone.say)
            self.worker.message.connect(phone.message)
            self.worker.result.connect(phone.finish)
            phone.say(stage)
        self.status.setToolTip("")
        self.show_stage(stage)
        self.progress.show()
        self.update_controls()
        self.worker.start()

    def show_message(self, message):
        self.chat_view.put(message.id, message.speaker, message.origin, message.body, message.status)
        if self.decisions.settings.enabled and message.id.startswith("history-"):
            self.decisions.seed(message)

    def observe_message(self, workflow, message):
        if (self.decisions.settings.enabled and self.worker is not None and not self.closing
                and self.worker.job.id == workflow):
            self.decisions.observe(workflow, message)
            self.show_decision_progress()

    def configure_decisions(self, settings):
        was_enabled = self.decisions.settings.enabled
        self.decisions.configure(settings)
        if self.worker is not None and was_enabled != settings.enabled:
            if settings.enabled:
                self.worker.completed_message.connect(self.observe_message)
            else:
                self.worker.completed_message.disconnect(self.observe_message)
        self.refresh_decisions()

    def show_decision_progress(self):
        status, busy = self.decisions.status
        self.decision_status.setText(status)
        self.decision_progress.setAccessibleName(status)
        self.decision_progress.setVisible(busy)

    def refresh_decisions(self):
        enabled = self.decisions.settings.enabled
        self.heatmap.setVisible(enabled)
        if enabled or not self.decisions.stopped:
            if not self.decision_poll.isActive():
                self.decision_poll.start()
        else:
            self.decision_poll.stop()
        self.show_decision_progress()
        if not enabled:
            return
        try:
            self.decision_tick, rows = self.decisions.history.latest(self.decision_tick)
        except StateError:
            self.heatmap.summary.setText("Could not read the local score history.")
            return
        questions = self.decisions.settings.questions
        if rows is not None or questions != self.decision_questions:
            if rows is not None:
                self.decision_rows = rows
            self.heatmap.load(self.decision_rows, questions)
            self.decision_questions = questions

    def show_monitor(self):
        self.monitor.show()
        self.monitor.raise_()
        self.monitor.activateWindow()

    def show_stage(self, text):
        if self.worker is not None and not self.worker.job.stop.is_set():
            self.status.setText(text)
            self.progress.setAccessibleName(text)
            self.progress.setRange(0, 0)

    def show_progress(self, done, total, unit):
        if self.worker is None or self.worker.job.stop.is_set():
            return
        text = f"{done} of {total} {unit}"
        self.status.setText(text)
        self.progress.setAccessibleName(text)
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def stop_work(self):
        if self.worker is None:
            return
        self.harness.cancel(self.worker.job)
        text = "Stopping · waiting for the current operation"
        self.status.setText(text)
        self.progress.setAccessibleName(text)
        self.progress.setRange(0, 0)
        self.update_controls()

    def finish_work(self):
        self.worker.deleteLater()
        self.worker = self.call = None
        self.phone_call = None
        self.progress.hide()
        self.microphone.reset()
        self.speaker.reset()
        self.update_controls()
        if self.closing:
            self.close()

    def tune(self):
        if self.call is not None:
            self.call.tune(self.playback.apply_to(self.voice))

    def show_saved_sst(self):
        saved = self.saved_call_input
        device = self.dictation.stt_backend.itemText(self.dictation.stt_backend.findData(saved.stt_backend))
        message = f"Saved for calls · {STT_MODELS[saved.model]} · {device}"
        if self.dictation.settings() != saved or self.strict_memory.isChecked() != self.saved_strict_memory:
            message += "\nUnsaved SST changes"
        self.sst_status.setText(message)

    def save_sst(self):
        if self.worker is not None:
            return
        call_input = self.dictation.settings()
        strict_memory = self.strict_memory.isChecked()
        try:
            self.settings.save_sst(call_input, strict_memory=strict_memory)
        except (OSError, VoiceError) as exc:
            self.status.setText(str(exc) if isinstance(exc, VoiceError) else "Could not save SST settings.")
        else:
            self.saved_call_input = call_input
            self.saved_strict_memory = strict_memory
            self.harness.models.set_recognition_idle(call_input.stt_idle if call_input.stt_on_demand else None)
            self.show_saved_sst()
            self.status.setText("SST settings saved for the next call.")

    def save_playback(self):
        if self.worker is not None:
            return
        try:
            self.settings.save_output(self.playback.settings())
        except (OSError, VoiceError) as exc:
            self.status.setText(str(exc) if isinstance(exc, VoiceError) else "Could not save playback settings.")
        else:
            self.status.setText("Playback settings saved.")

    def acknowledge_delivery(self):
        if self.worker is not None:
            return
        try:
            turn = self.harness.state.pending_delivery()
            if turn:
                self.harness.state.clear_delivery(turn)
                self.status.setText("Delivery check recorded. A new call can be started.")
        except StateError:
            self.status.setText("Could not record the delivery check.")
        self.update_controls()

    def closeEvent(self, event):
        if not self.closing:
            self.closing = True
            self.decisions.close()
            self.decision_poll.stop()
            self.monitor.shutdown()
            if self.worker is not None:
                self.stop_work()
        if self.worker is not None or not self.decisions.stopped:
            event.ignore()
            self.decision_status.setText("Closing · waiting for local workers")
            if self.worker is None:
                QTimer.singleShot(75, self.close)
            self.decision_progress.setAccessibleName(self.decision_status.text())
            self.decision_progress.show()
            return
        event.accept()
