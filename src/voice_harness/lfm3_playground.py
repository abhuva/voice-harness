"""A standalone text playground for the laptop's local llama.cpp server."""

from collections import deque
from pathlib import Path
import sys
from threading import Event
from time import monotonic
from urllib.parse import urlsplit

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut, QTextCursor
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkProxy
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .contracts import ReplyCompleted, ReplyFailed, ReplyText, TurnRequest, VoiceError
from .lfm3_prompt import SYSTEM_LIMIT, context_budget, read_system_file
from .llm import ChatClient, LlmProfile
from .provider import post_packets


DEFAULT_ENDPOINT = "http://127.0.0.1:18770/v1/chat/completions"
DEFAULT_SYSTEM = "Keep replies brief and natural for a voice conversation. Use plain text."
LOOPBACK = ("127.0.0.1", "::1", "localhost")
DEFAULT_PROMPT_FILE = Path(__file__).resolve().parents[2] / "var/lfm3/test-prompt.md"


def integer(minimum, maximum, value):
    field = QSpinBox()
    field.setRange(minimum, maximum)
    field.setValue(value)
    return field


def decimal(minimum, maximum, value):
    field = QDoubleSpinBox()
    field.setRange(minimum, maximum)
    field.setSingleStep(0.05)
    field.setValue(value)
    return field


class Generation(QThread):
    fragment = pyqtSignal(str)
    thought = pyqtSignal(str)
    first_text = pyqtSignal()
    budget_checked = pyqtSignal(str)

    def __init__(self, profile, sampling, prompt, history, parent):
        super().__init__(parent)
        self.profile, self.sampling, self.prompt = profile, sampling, prompt
        self.history = history
        self.stop = Event()
        self.answer = None
        self.note = ""

    def request_with_budget(self, manager, endpoint, key, payload, stop, **options):
        counts = context_budget(endpoint, payload, stop)
        if stop.is_set():
            return
        if counts is None:
            self.budget_checked.emit("Token budget unavailable for this endpoint.")
        else:
            output = payload["max_tokens"]
            self.budget_checked.emit(counts.display(output))
            if counts.prompt + output > counts.context:
                raise VoiceError("This request exceeds the context. Shorten the prompt or lower max output tokens.")
        yield from post_packets(manager, endpoint, key, payload, stop, **options)

    def run(self):
        start = monotonic()
        first = None
        pieces = []
        saw_thinking = False

        def received_first():
            nonlocal first
            first = monotonic() - start
            self.first_text.emit()

        def received_thinking(text):
            nonlocal saw_thinking
            saw_thinking |= bool(text.strip())
            self.thought.emit(text)

        try:
            manager = QNetworkAccessManager()
            manager.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
            client = ChatClient(self.profile, "", manager=manager, sampling=self.sampling,
                                post=self.request_with_budget)
            client.history.extend(self.history)
            request = TurnRequest(self.prompt, on_first_text=received_first)
            for event in client.send(request, self.stop, on_reasoning=received_thinking):
                if isinstance(event, ReplyText):
                    pieces.append(event.text)
                    self.fragment.emit(event.text)
                elif isinstance(event, ReplyFailed):
                    self.note = event.message
                    if saw_thinking and self.note == "The LLM returned no reply text.":
                        self.note = "The model returned thinking text only. Lower its limit or raise max output tokens."
                elif isinstance(event, ReplyCompleted):
                    self.answer = "".join(pieces)
                    elapsed = monotonic() - start
                    latency = f" · first text {first:.2f}s" if first is not None else ""
                    self.note = f"Complete{latency} · total {elapsed:.2f}s"
        except VoiceError as exc:
            self.note = str(exc)
        except Exception:
            self.note = "Could not complete the local request."
        if self.stop.is_set():
            self.note = "Stopped."
            self.answer = None
        elif not self.note:
            self.note = "The local server closed the request."


class Playground(QWidget):
    def __init__(self, model_ids=()):
        super().__init__()
        self.setWindowTitle("LFM3 Playground")
        self.resize(1060, 720)
        self.history = deque(maxlen=8)
        self.history_target = None
        self.system_file = None
        self.job = None
        self.clearing = False
        self.closing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        settings = QGroupBox("Generation settings")
        layout.addWidget(settings)
        settings_layout = QVBoxLayout(settings)
        server = QFormLayout()
        settings_layout.addLayout(server)
        self.endpoint = QLineEdit(DEFAULT_ENDPOINT)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.addItems(model_ids or ("lfm3-local",))
        server.addRow("&Local endpoint", self.endpoint)
        server.addRow("&Model", self.model)

        sampling = QHBoxLayout()
        settings_layout.addLayout(sampling)
        left, right = QFormLayout(), QFormLayout()
        sampling.addLayout(left, 1)
        sampling.addLayout(right, 1)
        self.temperature = decimal(0, 2, 0.1)
        self.tokens = integer(1, 4096, 192)
        self.top_p = decimal(0, 1, 0.95)
        self.top_k = integer(0, 512, 50)
        self.repeat = decimal(0.5, 2, 1.1)
        self.seed = integer(-1, 2_147_483_647, -1)
        self.seed.setSpecialValueText("Random")
        self.thinking_mode = QComboBox()
        self.thinking_mode.addItem("Off", "off")
        self.thinking_mode.addItem("On", "on")
        self.thinking_mode.addItem("Server default", "server")
        self.thinking_limit = integer(-1, 4096, 64)
        self.thinking_limit.setSpecialValueText("Server default")
        self.thinking_limit.setEnabled(False)
        self.thinking_mode.currentIndexChanged.connect(
            lambda _: self.thinking_limit.setEnabled(self.thinking_mode.currentData() == "on"))
        left.addRow("&Temperature", self.temperature)
        left.addRow("Top &p", self.top_p)
        left.addRow("Top &k", self.top_k)
        left.addRow("&Thinking mode", self.thinking_mode)
        self.tokens.setToolTip("The output limit includes thinking and final reply tokens.")
        right.addRow("Max output &tokens", self.tokens)
        right.addRow("Repetition &penalty", self.repeat)
        right.addRow("Random s&eed", self.seed)
        right.addRow("Thinking token &limit", self.thinking_limit)

        switches = QHBoxLayout()
        settings_layout.addLayout(switches)
        self.stream = QCheckBox("Stream replies")
        self.stream.setChecked(True)
        switches.addWidget(self.stream)
        self.context = QCheckBox("Include previous turns")
        self.context.setChecked(True)
        switches.addWidget(self.context)
        switches.addStretch()

        workspace = QSplitter(Qt.Orientation.Horizontal)
        workspace.setChildrenCollapsible(False)
        layout.addWidget(workspace, 1)
        prompts = QWidget()
        prompt_layout = QVBoxLayout(prompts)
        prompt_layout.setContentsMargins(0, 0, 4, 0)
        workspace.addWidget(prompts)
        self.system = QPlainTextEdit(DEFAULT_SYSTEM)
        self.system.setAccessibleName("System prompt")
        self.user = QPlainTextEdit()
        self.user.setAccessibleName("User prompt")
        self.user.setPlaceholderText("Type a prompt…")
        system_header = QHBoxLayout()
        prompt_layout.addLayout(system_header)
        system_label = QLabel("&System prompt")
        system_label.setBuddy(self.system)
        system_header.addWidget(system_label, 1)
        self.load_prompt = QPushButton("Load file…")
        self.load_prompt.clicked.connect(self.choose_system_file)
        system_header.addWidget(self.load_prompt)
        self.reload_prompt = QPushButton("Reload file")
        self.reload_prompt.setEnabled(False)
        self.reload_prompt.clicked.connect(self.reload_system_file)
        system_header.addWidget(self.reload_prompt)
        prompt_layout.addWidget(self.system, 1)
        user_label = QLabel("&User prompt")
        user_label.setBuddy(self.user)
        prompt_layout.addWidget(user_label)
        prompt_layout.addWidget(self.user, 1)

        prompt_actions = QHBoxLayout()
        prompt_layout.addLayout(prompt_actions)
        self.send = QPushButton("Send")
        self.send.setToolTip("Send prompt (Ctrl+Enter)")
        self.send.clicked.connect(self.generate)
        prompt_actions.addWidget(self.send)
        self.clear_prompts = QPushButton("Clear prompts")
        self.clear_prompts.clicked.connect(self.reset_prompts)
        prompt_actions.addWidget(self.clear_prompts)
        prompt_actions.addStretch()
        self.shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.shortcut.activated.connect(self.generate)

        transcript = QWidget()
        output_layout = QVBoxLayout(transcript)
        output_layout.setContentsMargins(4, 0, 0, 0)
        workspace.addWidget(transcript)
        self.output = QPlainTextEdit()
        self.output.setAccessibleName("Chat output")
        self.output.setReadOnly(True)
        self.output.document().setMaximumBlockCount(2500)
        self.thinking_output = QPlainTextEdit()
        self.thinking_output.setAccessibleName("Thinking text")
        self.thinking_output.setReadOnly(True)
        self.thinking_output.setPlaceholderText("Thinking text appears here when the model returns it.")
        output_tabs = QTabWidget()
        output_tabs.addTab(self.output, "Chat output")
        output_tabs.addTab(self.thinking_output, "Thinking")
        output_layout.addWidget(output_tabs, 1)
        output_actions = QHBoxLayout()
        output_layout.addLayout(output_actions)
        self.stop = QPushButton("Stop")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.cancel)
        self.clear = QPushButton("Clear chat")
        self.clear.clicked.connect(self.clear_chat)
        output_actions.addWidget(self.stop)
        output_actions.addWidget(self.clear)
        output_actions.addStretch()
        workspace.setSizes([445, 560])

        self.status = QLabel("Ready")
        self.status.setAccessibleName("Generation status")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.budget = QLabel()
        self.budget.setAccessibleName("Last request token budget")
        self.budget.setTextFormat(Qt.TextFormat.PlainText)
        self.budget.setWordWrap(True)
        layout.addWidget(self.budget)
        self.settings_fields = settings
        self.user.setFocus()

    def choose_system_file(self):
        current = self.system_file or DEFAULT_PROMPT_FILE
        path, _ = QFileDialog.getOpenFileName(self, "Load system prompt", str(current),
                                              "Markdown and text (*.md *.txt);;All files (*)")
        if path:
            self.load_system_file(Path(path))

    def load_system_file(self, path):
        try:
            prompt = read_system_file(path)
        except VoiceError as exc:
            self.status.setText(str(exc))
            return
        self.system.setPlainText(prompt)
        self.system_file = path
        self.reload_prompt.setEnabled(True)
        self.reload_prompt.setToolTip(str(path))
        self.status.setText(f"Loaded {path.name}.")

    def reload_system_file(self):
        if self.system_file is not None:
            self.load_system_file(self.system_file)

    def generate(self):
        if self.job is not None or self.closing:
            return
        prompt = self.user.toPlainText().strip()
        if not prompt or len(prompt) > 16_384:
            self.status.setText("Enter a user prompt of 1 to 16,384 characters.")
            return
        try:
            endpoint = self.endpoint.text().strip()
            if urlsplit(endpoint).hostname not in LOOPBACK:
                raise VoiceError("Use a loopback endpoint on this computer.")
            system = self.system.toPlainText()
            if len(system.encode("utf-8")) > SYSTEM_LIMIT:
                raise VoiceError("Use a system prompt of up to 1 MiB.")
            profile = LlmProfile(name="LFM3", endpoint=endpoint, model=self.model.currentText().strip(),
                                 stream=self.stream.isChecked(), temperature=self.temperature.value(),
                                 max_tokens=self.tokens.value(), system_prompt=system,
                                 credential="").validate(system_limit=SYSTEM_LIMIT)
        except (ValueError, VoiceError) as exc:
            self.status.setText(str(exc))
            return

        sampling = {"top_p": self.top_p.value(), "top_k": self.top_k.value(),
                    "repeat_penalty": self.repeat.value(), "seed": self.seed.value(),
                    "reasoning_format": "deepseek"}
        mode = self.thinking_mode.currentData()
        if mode == "off":
            sampling.update(chat_template_kwargs={"enable_thinking": False}, reasoning_effort="none",
                            reasoning_budget_tokens=0)
        elif mode == "on":
            sampling.update(chat_template_kwargs={"enable_thinking": True},
                            reasoning_budget_tokens=self.thinking_limit.value())
        target = (profile.endpoint, profile.model)
        if self.history_target != target:
            self.history.clear()
            self.history_target = target
        history = tuple(self.history) if self.context.isChecked() else ()
        self.job = Generation(profile, sampling, prompt, history, self)
        self.job.fragment.connect(self.append_reply)
        self.job.thought.connect(self.append_thinking)
        self.job.budget_checked.connect(self.show_budget)
        self.job.first_text.connect(self.show_receiving)
        self.job.finished.connect(self.finish)
        separator = "" if self.output.document().isEmpty() else "\n\n"
        self.append_text(f"{separator}You\n{prompt}\n\nLFM3 · {profile.model}\n")
        self.thinking_output.clear()
        self.budget.clear()
        self.status.setText("Checking model and prompt…")
        self.send.setEnabled(False)
        self.settings_fields.setEnabled(False)
        self.stop.setEnabled(True)
        self.job.start()

    def append_text(self, text, editor=None):
        editor = self.output if editor is None else editor
        cursor = QTextCursor(editor.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        follow = editor.verticalScrollBar().value() == editor.verticalScrollBar().maximum()
        cursor.insertText(text)
        if follow:
            editor.setTextCursor(cursor)
            editor.ensureCursorVisible()

    def append_reply(self, text):
        if self.job is not None and not self.clearing:
            self.append_text(text)

    def append_thinking(self, text):
        if self.job is not None and not self.clearing:
            self.append_text(text, self.thinking_output)

    def show_budget(self, text):
        if self.job is not None and not self.job.stop.is_set():
            self.budget.setText(text)
            self.status.setText("Waiting for LFM3…")

    def show_receiving(self):
        if self.job is not None and not self.job.stop.is_set():
            self.status.setText("Receiving reply…")

    def cancel(self):
        if self.job is not None:
            self.job.stop.set()
            self.stop.setEnabled(False)
            self.status.setText("Stopping…")

    def clear_chat(self):
        self.history.clear()
        self.output.clear()
        self.thinking_output.clear()
        self.budget.clear()
        if self.job is not None:
            self.clearing = True
            self.cancel()
        else:
            self.status.setText("Chat cleared.")

    def reset_prompts(self):
        self.system.clear()
        self.user.clear()
        self.user.setFocus()

    def finish(self):
        job, self.job = self.job, None
        if self.clearing:
            self.status.setText("Chat cleared.")
        else:
            note = "Stopped." if job.stop.is_set() else job.note
            if job.answer is not None and not job.stop.is_set():
                self.history.append((job.prompt, job.answer))
            elif note:
                self.append_text(f"\n[{note}]")
            self.status.setText(note)
        job.deleteLater()
        self.clearing = False
        self.send.setEnabled(True)
        self.settings_fields.setEnabled(True)
        self.stop.setEnabled(False)
        if self.closing:
            self.close()

    def closeEvent(self, event):
        if self.job is not None:
            self.closing = True
            self.cancel()
            event.ignore()
        else:
            event.accept()


def main(model_ids=()):
    app = QApplication(sys.argv[:1])
    font = app.font()
    font.setPointSize(11)
    app.setFont(font)
    window = Playground(model_ids)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
