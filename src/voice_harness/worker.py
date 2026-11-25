"""Qt delivery of job events. The selected operation runs on the worker thread."""

from PyQt6.QtCore import QThread, pyqtSignal
import time

from .call import run_call
from .contracts import Message
from .microphone import WindowsMicrophone
from .provider import KindroidClient
from .recognizer import DictateRecognizer
from .resources import resident_sample
from .speaker import PocketSpeaker


class Worker(QThread):
    stage = pyqtSignal(str)
    message = pyqtSignal(object)
    mic_level = pyqtSignal(float, float)
    speaker_level = pyqtSignal(float, float)
    resources = pyqtSignal(str)
    result = pyqtSignal(str)

    def __init__(self, harness, job, operation, parent):
        super().__init__(parent)
        self.harness = harness
        self.job = job
        self.operation = operation

    def run(self):
        try:
            status = self.harness.execute(self.job, lambda stop: self.operation(self, stop))
        except Exception:
            status = "Could not record the operation result. Check the local state folder."
        finally:
            self.operation = None
        self.result.emit(status)


class CallOperation:
    def __init__(self, settings, call_input, playback):
        self.settings = settings
        self.call_input = call_input
        self.playback = playback
        self.speaker = None

    def tune(self, playback):
        self.playback = playback
        if self.speaker is not None:
            self.speaker.playback = playback

    def __call__(self, worker, stop):
        provider = KindroidClient(*self.settings.credentials())
        microphone = WindowsMicrophone(worker.mic_level.emit)
        recognizer = DictateRecognizer(self.settings.dictate)
        speaker = None
        if self.playback.speed:
            speaker = PocketSpeaker(self.settings.pocket, self.playback, worker.speaker_level.emit)
            self.speaker = speaker
            speaker.playback = self.playback
        start, cpu_start = time.monotonic(), time.process_time()

        def ready():
            child = speaker.engine.child if speaker else None
            sample = resident_sample(start, cpu_start, child.pid if child else None)
            worker.resources.emit(sample.description())

        run_call(microphone, recognizer, speaker, provider, worker.harness.state,
                 self.call_input, stop, worker.stage.emit, worker.message.emit, ready)
        return "Call ended."


def load_history(settings, worker, stop):
    provider = KindroidClient(*settings.credentials())
    rows, more = provider.history(settings.history_guard, stop)
    if stop.is_set():
        return "History stopped."
    for index, (speaker, source, body) in enumerate(rows):
        worker.message.emit(Message(f"history-{index}", speaker, body, origin=source))
    if more:
        return f"Recent history · {len(rows)} shown · newer messages outside this page."
    if rows:
        return f"Recent history · {len(rows)} shown."
    return "No displayable messages in the last 24 hours."
