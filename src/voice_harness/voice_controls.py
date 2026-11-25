"""Desktop forms for model selection and recipe editing."""

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .presets import Playback, Recipe, RECORDING_EXTENSIONS, TTS_MODELS


def temperature_input():
    control = QDoubleSpinBox()
    control.setRange(0, 1)
    control.setSingleStep(0.05)
    control.setValue(0.3)
    return control


class TtsControls(QWidget):
    preview_requested = pyqtSignal()
    apply_requested = pyqtSignal()

    def __init__(self, library, recipes):
        super().__init__()
        self.library = library
        self.recipes = recipes
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.model = QComboBox()
        for ident, name in TTS_MODELS.items():
            self.model.addItem(name, ident)
        self.voice = QComboBox()
        self.temperature = temperature_input()
        form.addRow("TTS &model", self.model)
        form.addRow("&Voice preset", self.voice)
        form.addRow("&Temperature", self.temperature)
        self.model.currentIndexChanged.connect(self.model_changed)
        self.voice.currentIndexChanged.connect(self.voice_changed)
        self.model_changed()

        self.text = QPlainTextEdit("Hey, it's good to hear from you. How has your day been?")
        self.text.setAccessibleName("Text to preview")
        layout.addWidget(self.text)
        self.preview = QPushButton("&Speak preview")
        self.preview.clicked.connect(self.preview_requested)
        layout.addWidget(self.preview)
        self.use = QPushButton("&Use for Kindroid calls")
        self.use.clicked.connect(self.apply_requested)
        layout.addWidget(self.use)
        self.active = QLabel()
        self.active.setTextFormat(Qt.TextFormat.PlainText)
        self.active.setWordWrap(True)
        layout.addWidget(self.active)

    def populate(self, preset):
        choices = self.library.voices(self.model.currentData(), self.recipes)
        with QSignalBlocker(self.voice):
            self.voice.clear()
            for ident, (label, _) in choices.items():
                self.voice.addItem(label, ident)
            if preset and preset not in choices:
                self.voice.addItem("Previously saved preset", preset)
            index = self.voice.findData(preset)
            self.voice.setCurrentIndex(max(0, index))

    def model_changed(self):
        self.populate(None)
        self.voice_changed()

    def voice_changed(self):
        choices = self.library.voices(self.model.currentData(), self.recipes)
        self.temperature.setValue(choices.get(self.voice.currentData(), ("", 0.3))[1])

    def load(self, playback):
        with QSignalBlocker(self.model):
            self.model.setCurrentIndex(self.model.findData(playback.model))
        self.populate(playback.preset)
        self.temperature.setValue(playback.temperature)

    def select_clone(self, ident, recipes):
        self.recipes = recipes
        with QSignalBlocker(self.model):
            self.model.setCurrentIndex(self.model.findData("18m"))
        self.populate(ident)
        self.voice_changed()

    def settings(self):
        return Playback(self.model.currentData(), self.voice.currentData(), self.temperature.value())

    def show_active(self, playback):
        choices = self.library.voices(playback.model, self.recipes)
        voice = choices.get(playback.preset, ("Saved preset",))[0]
        self.active.setText(f"Call voice · {TTS_MODELS[playback.model]} · {voice}")


class CloningControls(QWidget):
    import_requested = pyqtSignal(tuple)
    clone_requested = pyqtSignal(object)

    def __init__(self, library, recipes):
        super().__init__()
        self.library = library
        self.recipes = recipes
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.template = QComboBox()
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.clips = QListWidget()
        self.offset = QSpinBox()
        self.offset.setRange(0, 3600)
        self.offset.setSuffix(" s")
        self.duration = QSpinBox()
        self.duration.setRange(3, 30)
        self.duration.setSuffix(" s")
        self.duration.setValue(24)
        self.temperature = temperature_input()
        self.import_button = QPushButton("&Import audio files…")
        self.import_button.clicked.connect(self.choose_files)
        for name, field in (
            ("Load clone &settings", self.template), ("Preset &name", self.name),
            ("Source &recordings", self.clips), ("Start within each recording", self.offset),
            ("Total reference duration", self.duration), ("Default TTS temperature", self.temperature),
        ):
            form.addRow(name, field)
        layout.addWidget(self.import_button)
        self.build = QPushButton("Clone and &save new preset")
        self.build.clicked.connect(self.request_clone)
        layout.addWidget(self.build)
        self.refresh_clips(())
        self.refresh_recipes(recipes)
        self.template.currentIndexChanged.connect(self.load_template)

    def choose_files(self):
        extensions = " ".join(f"*{extension}" for extension in RECORDING_EXTENSIONS)
        paths, _ = QFileDialog.getOpenFileNames(self, "Import voice recordings", "", f"Audio files ({extensions})")
        if paths:
            self.import_requested.emit(tuple(paths))

    def refresh_clips(self, selected):
        self.clips.clear()
        for name in self.library.recordings():
            item = QListWidgetItem(name, self.clips)
            item.setCheckState(Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked)

    def refresh_recipes(self, recipes, selected=None):
        self.recipes = recipes
        with QSignalBlocker(self.template):
            self.template.clear()
            self.template.addItem("New settings", None)
            for ident, recipe in recipes.items():
                self.template.addItem(f"{recipe.name} · {ident[:6]}", ident)
            self.template.setCurrentIndex(max(0, self.template.findData(selected)))

    def load_template(self):
        recipe = self.recipes.get(self.template.currentData(), Recipe("", ()))
        self.name.setText(recipe.name)
        self.offset.setValue(recipe.offset)
        self.duration.setValue(recipe.seconds)
        self.temperature.setValue(recipe.temperature)
        self.refresh_clips(recipe.samples)

    def request_clone(self):
        samples = tuple(self.clips.item(index).text() for index in range(self.clips.count())
                        if self.clips.item(index).checkState() == Qt.CheckState.Checked)
        self.clone_requested.emit(Recipe(self.name.text().strip(), samples, self.offset.value(),
                                         self.duration.value(), self.temperature.value()))
