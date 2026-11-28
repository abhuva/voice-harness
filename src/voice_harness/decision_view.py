"""Jevos settings and a rolling question-by-time score heatmap."""

from dataclasses import replace
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHeaderView, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .contracts import VoiceError
from .decisions import DEVICES, QUESTIONS, save_settings, validate_settings
from .office_worker import loopback_url


QUESTION_LABELS = dict(zip(QUESTIONS, (
    "Anger / frustration", "Anxiety / fear", "Hope", "Joy", "Gratitude", "Physical activity",
    "Programming", "Gaming", "Eating / drinking", "Comfort / reassurance", "Affection", "Complex topic",
)))


def score_colors(probability):
    background = QColor.fromHsl(204, 150, round(243 - probability * 154))

    def linear(channel):
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    luminance = sum(weight * linear(channel) for weight, channel in
                    zip((0.2126, 0.7152, 0.0722), background.getRgbF()))
    return background, QColor("#ffffff" if luminance < 0.179 else "#000000")


class DecisionControls(QWidget):
    saved = pyqtSignal(object)

    def __init__(self, settings, path, *, vulkan_directory=None):
        super().__init__()
        self.path = path
        self.vulkan_directory = vulkan_directory
        self.settings = settings
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.enabled = QCheckBox("Enable Jevos decision scoring")
        self.enabled.setChecked(settings.enabled)
        form.addRow(self.enabled)
        self.route = QComboBox()
        self.route.addItem("Local laptop", "local")
        self.route.addItem("Office only", "office")
        self.route.setCurrentIndex(self.route.findData(settings.route))
        form.addRow("Run scoring &on", self.route)
        self.office_url = QLineEdit(settings.office_url)
        self.office_url.setToolTip("Local address of the SSH tunnel to the office worker.")
        form.addRow("Office &tunnel URL", self.office_url)
        self.backend = QComboBox()
        for ident, label in DEVICES.items():
            self.backend.addItem(label, ident)
        self.backend.setCurrentIndex(self.backend.findData(settings.backend))
        self.backend.setToolTip("GPU (Vulkan) uses the local bundle installed by scripts/setup-jevos-vulkan.ps1.")
        form.addRow("Scoring &device", self.backend)
        folder = QHBoxLayout()
        self.directory = QLineEdit(settings.directory)
        self.directory.setAccessibleName("Jevos v5 installation folder")
        folder.addWidget(self.directory)
        self.browse_button = QPushButton("&Browse…")
        self.browse_button.clicked.connect(self.browse)
        folder.addWidget(self.browse_button)
        form.addRow("Jevos CPU installation &folder", folder)
        self.questions = QPlainTextEdit("\n".join(settings.questions))
        self.questions.setAccessibleName("Jevos yes/no questions")
        form.addRow("Yes/no &questions, one per line", self.questions)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.save = QPushButton("&Save Jevos settings")
        self.save.clicked.connect(self.apply)
        actions.addWidget(self.save)
        defaults = QPushButton("Restore default &questions")
        defaults.clicked.connect(lambda: self.questions.setPlainText("\n".join(QUESTIONS)))
        actions.addWidget(defaults)
        actions.addStretch()
        for field in (self.questions, defaults):
            field.setEnabled(settings.enabled)
            self.enabled.toggled.connect(field.setEnabled)
        self.enabled.toggled.connect(self.update_fields)
        self.route.currentIndexChanged.connect(self.update_fields)
        self.backend.currentIndexChanged.connect(self.update_fields)
        self.status = QLabel()
        self.status.setAccessibleName("Jevos settings status")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.update_fields()

    def update_fields(self):
        local = self.route.currentData() == "local"
        self.backend.setEnabled(local)
        self.office_url.setEnabled(not local)
        cpu = local and self.enabled.isChecked() and self.backend.currentData() == "cpu"
        self.directory.setEnabled(cpu)
        self.browse_button.setEnabled(cpu)
        state = "On" if self.settings.enabled else "Off"
        target = "Office only" if self.settings.route == "office" else DEVICES[self.settings.backend]
        self.status.setText(f"Saved · {state} · {target}")

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Choose the Jevos v5 installation", self.directory.text())
        if path:
            self.directory.setText(path)

    def apply(self):
        notice = ""
        try:
            backend = self.backend.currentData()
            route = self.route.currentData()
            office_url = self.office_url.text()
            if self.enabled.isChecked():
                questions = [line.strip() for line in self.questions.toPlainText().splitlines() if line.strip()]
                settings = validate_settings(True, self.directory.text(), questions, backend=backend,
                                             vulkan_directory=self.vulkan_directory, route=route, office_url=office_url)
            else:
                try:
                    office_url = loopback_url(office_url.strip())
                except ValueError:
                    office_url = self.settings.office_url
                    notice = " · Kept the saved tunnel URL. Enter a valid loopback URL to change it."
                settings = replace(self.settings, enabled=False, backend=backend, route=route, office_url=office_url)
            save_settings(self.path, settings)
        except VoiceError as exc:
            self.status.setText(str(exc))
        except OSError:
            self.status.setText("Could not save Jevos settings in the local state folder.")
        else:
            self.settings = settings
            self.update_fields()
            if notice:
                self.status.setText(self.status.text() + notice)
            self.saved.emit(settings)


class DecisionHeatmap(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(285)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel("Jevos conversation scores")
        layout.addWidget(self.heading)
        self.summary = QLabel("Scores appear after Jevos processes three complete messages.")
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName("Jevos score history status")
        layout.addWidget(self.summary)
        self.table = QTableWidget()
        self.table.setAccessibleName("Jevos scores by question and time")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setDefaultSectionSize(58)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(25)
        self.table.verticalHeader().setFixedWidth(162)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.table, 1)

    def load(self, rows, questions):
        # Preserve prior question wording so editing the current list cannot relabel old scores.
        prompts = list(dict.fromkeys((*questions, *(q for row in reversed(rows) for q in row.probabilities))))
        bar = self.table.horizontalScrollBar()
        x = bar.value()
        ybar = self.table.verticalScrollBar()
        y = ybar.value()
        follow = x >= bar.maximum() - 2
        self.table.clear()
        self.table.setRowCount(len(prompts))
        self.table.setColumnCount(len(rows))
        positions = {}
        for index, prompt in enumerate(prompts):
            positions[prompt] = index
            label = QTableWidgetItem(QUESTION_LABELS.get(prompt, prompt))
            label.setToolTip(prompt)
            label.setData(Qt.ItemDataRole.AccessibleDescriptionRole, prompt)
            self.table.setVerticalHeaderItem(index, label)
        for index, row in enumerate(rows):
            at = datetime.fromtimestamp(row.observed)
            timestamp = at.strftime("%Y-%m-%d %H:%M:%S")
            heading = QTableWidgetItem(at.strftime("%H:%M"))
            heading.setToolTip(timestamp)
            self.table.setHorizontalHeaderItem(index, heading)
            for prompt, probability in row.probabilities.items():
                value = f"{probability:.0%}"
                cell = QTableWidgetItem(value)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                background, foreground = score_colors(probability)
                cell.setBackground(background)
                cell.setForeground(foreground)
                cell.setToolTip(f"{timestamp}\n{prompt}\nP(yes) {probability:.1%} · {row.model}")
                self.table.setItem(positions[prompt], index, cell)
        if rows:
            self.summary.setText(f"{len(rows)} of 100 recent samples · newest at right")
        else:
            self.summary.setText("Scores appear after Jevos processes three complete messages.")
        bar.setValue(bar.maximum() if follow else x)
        ybar.setValue(y)
