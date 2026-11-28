"""Edit named chat completion profiles independently of the active reply selection."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                            QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .contracts import VoiceError
from .llm import KINDROID, LlmProfile


class LlmControls(QWidget):
    saved = pyqtSignal()

    def __init__(self, settings, profiles, active):
        super().__init__()
        self.settings, self.profiles, self.active = settings, profiles, active
        self.editing = None
        self._changing = False
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        picker = QHBoxLayout()
        self.profiles_box = QComboBox()
        self.profiles_box.setAccessibleName("Saved LLM profiles")
        self.profiles_box.currentTextChanged.connect(self.load)
        picker.addWidget(self.profiles_box, 1)
        add = QPushButton("New profile")
        add.clicked.connect(self.new_profile)
        picker.addWidget(add)
        form.addRow("Saved profile", picker)
        self.name = QLineEdit()
        self.endpoint = QLineEdit()
        self.model = QLineEdit()
        self.key = QLineEdit()
        self.key.setPlaceholderText("Name in .env")
        self.stream = QCheckBox("Stream replies")
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 2)
        self.temperature.setSingleStep(0.1)
        self.tokens = QSpinBox()
        self.tokens.setRange(1, 8192)
        self.prompt = QPlainTextEdit()
        self.prompt.setAccessibleName("System prompt")
        form.addRow("&Name", self.name)
        form.addRow("&Chat completions URL", self.endpoint)
        form.addRow("&Model ID", self.model)
        form.addRow("API key &variable", self.key)
        form.addRow(self.stream)
        form.addRow("&Temperature", self.temperature)
        form.addRow("&Reply token limit", self.tokens)
        form.addRow("System &prompt", self.prompt)
        save = QPushButton("Save LLM profile")
        save.clicked.connect(self.save_profile)
        layout.addWidget(save)
        self.status = QLabel("Choose the profile for your next call in Chat.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh_editor(next(iter(self.profiles), ""))

    def refresh_editor(self, name):
        self._changing = True
        self.profiles_box.clear()
        self.profiles_box.addItems(self.profiles)
        self.profiles_box.setCurrentText(name)
        self._changing = False
        self.load(name)

    def load(self, name):
        if self._changing:
            return
        profile = self.profiles.get(name, LlmProfile())
        self.editing = name if name in self.profiles else None
        self.name.setText(profile.name)
        self.endpoint.setText(profile.endpoint)
        self.model.setText(profile.model)
        self.key.setText(profile.credential)
        self.stream.setChecked(profile.stream)
        self.temperature.setValue(profile.temperature)
        self.tokens.setValue(profile.max_tokens)
        self.prompt.setPlainText(profile.system_prompt)

    def new_profile(self):
        self.profiles_box.setCurrentIndex(-1)
        self.load("")
        self.name.clear()
        self.name.setFocus()

    def save_profile(self):
        profile = LlmProfile(self.name.text().strip(), self.endpoint.text().strip(), self.model.text().strip(),
                             self.stream.isChecked(), self.temperature.value(), self.tokens.value(),
                             self.prompt.toPlainText(), self.key.text().strip())
        profiles = {name: item for name, item in self.profiles.items() if name != self.editing}
        active = profile.name if self.editing == self.active else self.active
        try:
            profile.validate()
            if any(name.casefold() == profile.name.casefold() for name in profiles):
                raise VoiceError("That profile name is already in use.")
            profiles[profile.name] = profile
            self.settings.save_llm(profiles, active)
        except (OSError, VoiceError) as exc:
            self.status.setText(str(exc) if isinstance(exc, VoiceError) else "Could not save the LLM profile.")
            return
        self.profiles, self.active = profiles, active
        self.refresh_editor(profile.name)
        self.status.setText(f"Saved {profile.name}.")
        self.saved.emit()

    def select(self, name):
        if name == self.active:
            return
        if name == KINDROID or name in self.profiles:
            self.settings.save_llm(self.profiles, name)
            self.active = name
