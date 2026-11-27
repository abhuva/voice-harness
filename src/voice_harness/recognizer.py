"""Load and run the installed local Dictate recognizer."""

import importlib.util
import json
from pathlib import Path

from .contracts import VoiceError
from .residency import ModelKey

STT_MODELS = {
    "multilingual": "Parakeet multilingual",
    "german": "Parakeet German",
    "fluid-speech-pro": "Fluid Speech Pro",
}
FLUID_PROFILE = Path(__file__).with_name("fluid-speech-pro.json")


def recognition_key(directory, settings, *, vulkan_directory=None):
    if settings.stt_backend == "vulkan":
        return ModelKey("stt", (vulkan_directory, settings.model, settings.threads, "vulkan"))
    return ModelKey("stt", (directory, settings.model, settings.threads, "cpu"))


class DictateRecognizer:
    def __init__(self, dictate_dir):
        self.dictate_dir = dictate_dir
        self.model_dir = dictate_dir / "models" / "multilingual"
        self.recognizer = None
        self.threads = 4

    def select_model(self, model, threads=4):
        if model != self.model_dir.name or threads != self.threads:
            self.recognizer = None
            self.model_dir = self.model_dir.with_name(model)
            self.threads = threads

    def prepare(self, stop):
        if self.recognizer is None:
            source = self.dictate_dir / "upstream" / "local_stt.py"
            registry_path = self.dictate_dir / "model-registry.json"
            if not source.is_file():
                raise VoiceError("Restore the local Dictate files under var/voice/dictate.")
            if self.model_dir.name == "fluid-speech-pro":
                files = json.loads(FLUID_PROFILE.read_text("utf-8"))["files"]
                missing = "Fluid Speech Pro is incomplete. Restore the Fluid model in the speech bundle."
            else:
                if not registry_path.is_file():
                    raise VoiceError("Restore the local Dictate files under var/voice/dictate.")
                registry = json.loads(registry_path.read_text("utf-8"))
                files = next(m["files"] for m in registry["models"] if m["id"] == self.model_dir.name)
                missing = "Dictate model is incomplete. Run NICA Dictate setup."
            for entry in files:
                path = self.model_dir / entry["name"]
                if not path.is_file() or path.stat().st_size != entry["bytes"]:
                    raise VoiceError(missing)
            spec = importlib.util.spec_from_file_location("voice_local_stt", source)
            stt = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(stt)
            stt.MODEL_DIR = self.model_dir
            self.recognizer = stt.LocalRecognizer(num_threads=self.threads)
        # Dictate owns a native loader thread. Hold the job until it settles.
        self.recognizer.wait_ready()
        if not self.recognizer.is_ready() and not stop.is_set():
            raise VoiceError("Could not load the local Dictate model.")

    @property
    def ready(self):
        return self.recognizer is not None and self.recognizer.is_ready()

    def transcribe(self, samples):
        return self.recognizer.transcribe(samples)

    def close(self):
        self.recognizer = None
