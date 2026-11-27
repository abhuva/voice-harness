"""Qt delivery of job events. The selected operation runs on the worker thread."""

from contextlib import ExitStack, contextmanager, nullcontext
from time import monotonic

from PyQt6.QtCore import QThread, pyqtSignal

from .call import run_call
from .contracts import Message
from .echo import EchoProcessor, PlaybackReference
from .fluid_vulkan import COLD_HOST_BYTES, FluidVulkan
from .microphone import WindowsMicrophone
from .provider import KindroidClient
from .playback import AudioPlayback
from .recognizer import DictateRecognizer, recognition_key
from .speaker import SynthesizedSpeech
from .speech_input import SpeechInput
from .work_types import Lineage, ResourceProfile


class Worker(QThread):
    stage = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)
    imported = pyqtSignal(tuple)
    cloned = pyqtSignal(str)
    message = pyqtSignal(object)
    completed_message = pyqtSignal(str, object)
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
            status = self.harness.execute(self.job, lambda stop: self.operation(self, stop),
                                          waiting=self.stage.emit)
        except Exception:
            status = "Could not record the operation result. Check the local state folder."
        finally:
            self.operation = None
        self.result.emit(status)


def call_profile(call_input, playback, synthesis):
    voice, threads = synthesis.resources(playback)
    recognition = "on-demand STT" if call_input.stt_on_demand else "resident STT"
    if call_input.stt_backend == "vulkan":
        recognition += " · Vulkan"
    return ResourceProfile(
        cpu_threads=call_input.threads + threads,
        basis=f"{call_input.model}/{call_input.threads} threads · TTS {voice} · {recognition}",
    )


class CallOperation:
    def __init__(self, settings, call_input, playback, *, phone=None, desktop_phone_output=False):
        self.settings = settings
        self.call_input = call_input
        self.playback = playback
        self.phone = phone
        self.desktop_phone_output = desktop_phone_output
        self.speaker = None

    def tune(self, playback):
        self.playback = playback
        if self.speaker is not None:
            self.speaker.sink.settings = playback

    def __call__(self, worker, stop):
        provider = KindroidClient(*self.settings.credentials())
        reference = None
        if self.phone is None:
            reference = PlaybackReference()
            enhancement = EchoProcessor(reference)
            microphone = SpeechInput(WindowsMicrophone(), enhancement, worker.mic_level.emit)
        else:
            microphone = self.phone.microphone(worker.mic_level.emit)
        harness, job = worker.harness, worker.job
        scope, stage = job.work.scope, worker.stage.emit
        vulkan = self.call_input.stt_backend == "vulkan"
        vulkan_directory = self.settings.fluid_vulkan if vulkan else None
        stt_key = recognition_key(self.settings.dictate, self.call_input, vulkan_directory=vulkan_directory)

        def create_recognizer():
            if vulkan:
                return FluidVulkan(vulkan_directory, self.call_input.threads,
                                   lambda pid: harness.watch_process(job, pid, slot="stt"))
            recognizer = DictateRecognizer(self.settings.dictate)
            recognizer.select_model(self.call_input.model, self.call_input.threads)
            return recognizer

        def recognition_startup(token, status):
            return harness.recognition_startup(job, token, status, load_bytes=COLD_HOST_BYTES if vulkan else 0)

        @contextmanager
        def recognize_on_demand(turn_id, turn_stop, status):
            turn = Lineage(scope.workflow_id, scope.job_id, turn_id)
            with harness.telemetry.online("Speech recognition"), harness.models.borrow(
                stt_key, create_recognizer, turn_stop, status, turn,
                before_load=lambda: recognition_startup(turn_stop, status),
                expires_at=lambda: (microphone.last_speech_at or monotonic()) + self.call_input.stt_idle,
            ) as recognizer:
                if vulkan and recognizer is not None:
                    harness.watch_process(job, recognizer.process_id, warm=True, slot="stt")
                yield recognizer

        def create_synthesizer():
            return self.settings.synthesis.create(self.playback, process_started=lambda pid: harness.watch_process(job, pid))

        def ready():
            sample = harness.ready(job)
            details = sample.description() if sample is not None else ""
            if self.phone is None and self.call_input.interrupt and self.speaker is not None and not enhancement.available:
                details += "\nInstall the WebRTC audio package to interrupt during playback."
            worker.resources.emit(details)

        try:
            with ExitStack() as leases:
                harness.models.evict_idle(keep=(stt_key,), only="stt", scope=scope)
                recognize = recognize_on_demand
                if self.call_input.stt_on_demand:
                    harness.models.set_recognition_idle(self.call_input.stt_idle, scope=scope)
                else:
                    leases.enter_context(harness.telemetry.online("Speech recognition"))
                    recognizer = leases.enter_context(harness.models.borrow(
                        stt_key, create_recognizer, stop, stage, scope,
                        before_load=(lambda: recognition_startup(stop, stage)) if vulkan else None,
                    ))
                    if recognizer is None:
                        return "Call stopped."
                    if vulkan:
                        harness.watch_process(job, recognizer.process_id, warm=True, slot="stt")

                    def recognize(_turn_id, _turn_stop, _status):
                        return nullcontext(recognizer)
                if self.playback.speed:
                    model = leases.enter_context(harness.models.borrow(
                        self.settings.synthesis.key(self.playback), create_synthesizer,
                        stop, stage, scope,
                    ))
                    if model is None:
                        return "Call stopped."
                    harness.watch_process(job, model.process_id, warm=True)
                    sink = (self.phone.playback(self.playback, worker.speaker_level.emit)
                            if self.phone is not None and not self.desktop_phone_output
                            else AudioPlayback(self.playback, worker.speaker_level.emit, reference=reference))
                    self.speaker = SynthesizedSpeech(model, sink)
                run_call(microphone, recognize, self.speaker, provider, harness.state,
                         self.call_input, stop, stage, worker.message.emit, ready,
                         work=harness.call_work(job, cloud=True),
                         completed=lambda message: worker.completed_message.emit(job.id, message))
        finally:
            self.speaker = None
        return "Call ended."


def load_history(settings, worker, stop):
    provider = KindroidClient(*settings.credentials())
    with worker.harness.telemetry.online("Kindroid connector"):
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
