"""Desktop controls and presentation. Work advances outside the UI thread."""

from functools import partial
from sqlite3 import Error as StateError

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .chat_view import ChatView
from .contracts import CallInput, VoiceError
from .level_meter import LevelMeter
from .playback_controls import PlaybackControls
from .presets import Playback, VoiceLibrary
from .voice_controls import CloningControls, TtsControls
from .voice_jobs import build_preset, import_clips, preview_voice
from .recognizer import STT_MODELS
from .worker import CallOperation, Worker, load_history


class DictationControls(QWidget):
    def __init__(self):
        super().__init__()
        form = QFormLayout(self)
        self.model = QComboBox()
        for ident, label in STT_MODELS.items():
            self.model.addItem(label, ident)
        self.threads = QSpinBox()
        self.threads.setRange(1, 16)
        self.threshold = QSpinBox()
        self.threshold.setRange(-80, -5)
        self.threshold.setSuffix(" dBFS")
        self.chunk, self.paragraph, self.finish = (QDoubleSpinBox() for _ in range(3))
        for field in (self.chunk, self.paragraph, self.finish):
            field.setRange(0.1, 10.0)
            field.setDecimals(1)
            field.setSingleStep(0.1)
            field.setSuffix(" s")
        for text, control in (
            ("&Recognizer", self.model), ("CPU &threads", self.threads),
            ("Speech thres&hold", self.threshold), ("Pause to transcribe a chun&k", self.chunk),
            ("Pause for a new p&aragraph", self.paragraph), ("Pause to &finish", self.finish),
        ):
            form.addRow(text, control)
        self.interrupt = QCheckBox("Interrupt Kindroid replies by speaking")
        form.addRow(self.interrupt)

    def load(self, settings):
        self.model.setCurrentIndex(self.model.findData(settings.model))
        self.interrupt.setChecked(settings.interrupt)
        for name in ("threads", "threshold", "chunk", "paragraph", "finish"):
            getattr(self, name).setValue(getattr(settings, name))

    def settings(self):
        return CallInput(self.model.currentData(), self.threads.value(), self.threshold.value(),
                         self.chunk.value(), self.paragraph.value(), self.finish.value(),
                         self.interrupt.isChecked())


class Window(QWidget):
    def __init__(self, settings, harness):
        super().__init__()
        self.setWindowTitle("Voice Harness")
        self.resize(720, 600)
        self.settings = settings
        self.harness = harness
        self.worker = None
        self.call = None
        self.closing = False
        self.history_requested = False

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        chat_page = QWidget()
        tabs.addTab(chat_page, "Kindroid chat")
        chat_layout = QVBoxLayout(chat_page)
        row = QHBoxLayout()
        chat_layout.addLayout(row, 1)
        self.chat_view = ChatView()
        row.addWidget(self.chat_view, 1)
        self.microphone = LevelMeter("Microphone")
        self.speaker = LevelMeter("Output")
        row.addWidget(self.microphone)
        row.addWidget(self.speaker)
        self.playback = PlaybackControls()
        self.playback.changed.connect(self.tune)
        chat_layout.addWidget(self.playback)

        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        self.dictation = DictationControls()
        settings_layout.addWidget(self.dictation)
        self.voice_label = QLabel()
        self.voice_label.setTextFormat(Qt.TextFormat.PlainText)
        settings_layout.addWidget(self.voice_label)
        self.save = QPushButton("Sa&ve settings")
        self.save.clicked.connect(self.save_settings)
        settings_layout.addWidget(self.save)
        settings_layout.addStretch()
        tabs.addTab(settings_page, "Settings")
        self.library = VoiceLibrary(settings.pocket)
        recipes = self.library.recipes()
        self.tts = TtsControls(self.library, recipes)
        self.tts.preview_requested.connect(self.start_preview)
        self.tts.apply_requested.connect(self.apply_voice)
        tabs.addTab(self.tts, "TTS")
        self.cloning = CloningControls(self.library, recipes)
        self.cloning.import_requested.connect(self.start_import)
        self.cloning.clone_requested.connect(self.start_clone)
        tabs.addTab(self.cloning, "Voice cloning")

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

        try:
            call_settings, self.voice = settings.load()
        except VoiceError as exc:
            call_settings, self.voice = CallInput(), Playback("18m", "alba", 0.3)
            self.status.setText(str(exc))
        self.saved_call_input = call_settings
        self.tts.load(self.voice)
        self.show_voice()
        self.dictation.load(call_settings)
        self.playback.load(self.voice)
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
        self.tts.setEnabled(not active)
        self.cloning.setEnabled(not active)
        self.save.setEnabled(not active)
        self.playback.mute.setEnabled(not active)

    def start_history(self):
        if self.history_requested or not self.settings.env_file.is_file():
            return
        self.history_requested = True
        if self.previous.text() != "Ready":
            self.previous.show()
        self.begin("Recent history", partial(load_history, self.settings), "Loading recent Kindroid history")

    def start_call(self):
        if self.worker is not None:
            return
        try:
            if self.harness.state.pending_delivery():
                return
        except StateError:
            self.update_controls()
            return
        self.call = CallOperation(self.settings, self.dictation.settings(), self.playback.apply_to(self.voice))
        self.begin("Call", self.call, "Preparing call")

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
        self.begin("Voice preview", partial(preview_voice, self.settings.pocket, playback, preview),
                   "Preparing voice preview")

    def show_voice(self):
        self.tts.show_active(self.voice)
        self.voice_label.setText(self.tts.active.text())

    def apply_voice(self):
        if self.worker is not None:
            return
        playback = self.playback.apply_to(self.tts.settings())
        try:
            self.settings.save(self.saved_call_input, playback)
        except (OSError, VoiceError) as exc:
            self.status.setText(str(exc) if isinstance(exc, VoiceError) else "Could not save the call voice.")
            return
        self.voice = playback
        self.show_voice()
        self.status.setText("Saved voice for Kindroid calls.")

    def show_clone(self, ident):
        recipes = self.library.recipes()
        self.cloning.refresh_recipes(recipes, ident)
        self.tts.select_clone(ident, recipes)

    def begin(self, label, operation, stage):
        try:
            job = self.harness.submit(label)
        except (OSError, StateError, VoiceError):
            self.call = None
            self.status.setText("Could not start the operation. Check the local state folder.")
            return
        if label != "Recent history":
            self.previous.hide()
        self.worker = Worker(self.harness, job, operation, self)
        self.worker.stage.connect(self.show_stage)
        self.worker.progress.connect(self.show_progress)
        self.worker.imported.connect(self.cloning.refresh_clips)
        self.worker.cloned.connect(self.show_clone)
        self.worker.message.connect(self.show_message)
        self.worker.mic_level.connect(self.microphone.set_levels)
        self.worker.speaker_level.connect(self.speaker.set_levels)
        self.worker.resources.connect(self.status.setToolTip)
        self.worker.result.connect(self.status.setText)
        self.worker.finished.connect(self.finish_work)
        self.status.setToolTip("")
        self.show_stage(stage)
        self.progress.show()
        self.update_controls()
        self.worker.start()

    def show_message(self, message):
        self.chat_view.put(message.id, message.speaker, message.origin, message.body, message.status)

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
        self.progress.hide()
        self.microphone.reset()
        self.speaker.reset()
        self.update_controls()
        if self.closing:
            self.close()

    def tune(self):
        if self.call is not None:
            self.call.tune(self.playback.apply_to(self.voice))

    def save_settings(self):
        call_input = self.dictation.settings()
        try:
            self.settings.save(call_input, self.playback.apply_to(self.voice))
        except (OSError, VoiceError):
            self.status.setText("Could not save settings. Review the selected voice and local state folder.")
        else:
            self.saved_call_input = call_input
            self.status.setText("Settings saved.")

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
        if self.worker is not None:
            self.closing = True
            self.stop_work()
            event.ignore()
        else:
            event.accept()
