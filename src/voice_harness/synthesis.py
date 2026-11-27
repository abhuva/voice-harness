"""Pocket TTS adapter producing PCM without opening an audio device."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import PCM, VoiceError
from .engine import ResidentEngine
from .presets import SILENCIO_VOICES, VoiceLibrary
from .recordings import write_reference
from .residency import ModelKey


def synthesis_key(directory, settings):
    return ModelKey("tts", (directory, *settings.synthesis_parameters))


@dataclass(frozen=True)
class SynthesisFactory:
    directory: Path
    cuda_runtime: Path | None = None
    silencio_directory: Path | None = None

    def key(self, settings):
        directory = self.silencio_directory if settings.model == "silencio2" else self.directory
        return synthesis_key(directory, settings.synthesis)

    def create(self, settings, *, process_started):
        return PocketSynthesizer(self.directory, settings.synthesis, process_started=process_started,
                                 cuda_runtime=self.cuda_runtime, silencio_directory=self.silencio_directory)

    @staticmethod
    def resources(settings):
        if not settings.speed:
            return "muted", 0
        model = "18m clone" if settings.model == "18m" and settings.preset != "alba" else settings.model
        if settings.model == "silencio2":
            model = "Silencio-2"
        if settings.backend == "cuda":
            model += " CUDA"
        return model, 4


class PocketSynthesizer:
    def __init__(self, directory, settings, *, process_started=None, cuda_runtime=None, silencio_directory=None):
        self.directory = directory
        self.settings = settings
        self.silencio_directory = silencio_directory
        self.engine = ResidentEngine(directory, process_started=process_started, cuda_runtime=cuda_runtime)

    def prepare(self, stop):
        library = VoiceLibrary(self.directory)
        config, profile = library.resolve(self.settings)
        if self.settings.model == "silencio2":
            self.prepare_silencio(library, profile, stop)
            return
        weights = {"english.yaml": "model.safetensors", "cloning.yaml": "cloning.safetensors",
                   "english-7m.yaml": "model-7m.safetensors"}[config]
        required = (config, profile, weights, "tokenizer.model")
        if self.settings.backend == "cpu":
            required += ("voice-engine.exe",)
        if not all((self.directory / name).is_file() for name in required):
            raise VoiceError("Restore the Pocket TTS voice and model files in the local bundle.")
        self.engine.load(self.settings, config, profile, stop)

    def prepare_silencio(self, library, profile, stop):
        root = self.silencio_directory
        if root is None or not all((root / file).is_file() for file in (
            "config.yaml", "model.safetensors", "tokenizer.model", "silencio2/__init__.py",
        )):
            raise VoiceError("Install Silencio-2 with scripts/setup-silencio.ps1.")
        if profile in SILENCIO_VOICES:
            reference = root / "voices" / f"{profile}.wav"
            if not reference.is_file():
                raise VoiceError("The bundled Silencio-2 voice file is missing.")
            self.engine.load(self.settings, str(root.resolve()), str(reference.resolve()), stop)
            return
        recipe = library.recipes().get(profile)
        if recipe is None:
            raise VoiceError("The saved voice recipe is missing. Choose another preset.")
        with TemporaryDirectory(prefix="silencio-reference-") as staging:
            reference = Path(staging) / "reference.wav"
            if write_reference(library.samples, recipe, reference, stop, lambda *_: None):
                self.engine.load(self.settings, str(root.resolve()), str(reference), stop)

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
