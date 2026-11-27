"""Pocket TTS adapter producing PCM without opening an audio device."""

from dataclasses import dataclass
from pathlib import Path

from .contracts import PCM, VoiceError
from .engine import ResidentEngine
from .presets import VoiceLibrary
from .residency import ModelKey


def synthesis_key(directory, settings):
    return ModelKey("tts", (directory, *settings.synthesis_parameters))


@dataclass(frozen=True)
class SynthesisFactory:
    directory: Path
    cuda_runtime: Path | None = None

    def key(self, settings):
        return synthesis_key(self.directory, settings.synthesis)

    def create(self, settings, *, process_started):
        return PocketSynthesizer(self.directory, settings.synthesis, process_started=process_started,
                                 cuda_runtime=self.cuda_runtime)

    @staticmethod
    def resources(settings):
        if not settings.speed:
            return "muted", 0
        model = "18m clone" if settings.model == "18m" and settings.preset != "alba" else settings.model
        if settings.backend == "cuda":
            model += " CUDA"
        return model, 4


class PocketSynthesizer:
    def __init__(self, directory, settings, *, process_started=None, cuda_runtime=None):
        self.directory = directory
        self.settings = settings
        self.engine = ResidentEngine(directory, process_started=process_started, cuda_runtime=cuda_runtime)

    def prepare(self, stop):
        config, profile = VoiceLibrary(self.directory).resolve(self.settings)
        weights = {"english.yaml": "model.safetensors", "cloning.yaml": "cloning.safetensors",
                   "english-7m.yaml": "model-7m.safetensors"}[config]
        required = (config, profile, weights, "tokenizer.model")
        if self.settings.backend == "cpu":
            required += ("voice-engine.exe",)
        if not all((self.directory / name).is_file() for name in required):
            raise VoiceError("Restore the Pocket TTS voice and model files in the local bundle.")
        self.engine.load(self.settings, config, profile, stop)

    @property
    def process_id(self):
        child = self.engine.child
        return child.pid if child is not None and child.poll() is None else None

    @property
    def ready(self):
        return self.engine.settings is not None and self.process_id is not None

    def render(self, text, stop):
        data = self.engine.render(text, stop)
        return PCM(self.engine.rate, data) if data is not None else None

    def close(self):
        self.engine.unload()
