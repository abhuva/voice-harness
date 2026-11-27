"""Pocket TTS model compatibility and the bundle's voice preset catalogue."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .contracts import VoiceError


TTS_MODELS = {"18m": "Pocket TTS English 18M REFL", "7m": "Pocket TTS English 7M REFL",
              "silencio2": "Silencio-2 English"}
SILENCIO_VOICES = ("260", "1320", "2094", "3575", "5639", "6930", "7127", "7729")
RECORDING_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg")
SPEECH_SPEEDS = tuple(step / 4 for step in range(9))
TEMPO_MIN, TEMPO_MAX = 80, 120
EQ_LIMIT = 12
_PRESET_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class SynthesisSettings:
    model: str = "18m"
    preset: str = "alba"
    temperature: float = 0.3
    backend: str = "cpu"

    @property
    def synthesis_parameters(self):
        return self.model, self.preset, self.temperature, self.backend


@dataclass(frozen=True)
class OutputSettings:
    speed: float = 1.0
    bass: int = 0
    mid: int = 0
    treble: int = 0
    muted: bool = False

    def validate(self):
        if (type(self.speed) not in (float, int) or self.speed <= 0
                or (self.speed not in SPEECH_SPEEDS and not (
                    TEMPO_MIN / 100 <= self.speed <= TEMPO_MAX / 100 and self.speed == round(self.speed, 2)
                ))):
            raise VoiceError("Choose a speaking speed from 0.80× to 1.20×.")
        if any(type(gain) is not int or not -EQ_LIMIT <= gain <= EQ_LIMIT
               for gain in (self.bass, self.mid, self.treble)):
            raise VoiceError("Choose EQ gains between −12 and +12 dB.")
        if type(self.muted) is not bool:
            raise VoiceError("Choose whether to mute playback.")
        return self

    def apply_to(self, voice):
        return Playback(voice.model, voice.preset, voice.temperature, 0 if self.muted else self.speed,
                        self.bass, self.mid, self.treble, backend=voice.backend)


@dataclass(frozen=True)
class Playback:
    model: str
    preset: str
    temperature: float
    speed: float = 1.0
    bass: int = 0
    mid: int = 0
    treble: int = 0
    backend: str = "cpu"

    @property
    def synthesis_parameters(self):
        return self.synthesis.synthesis_parameters

    @property
    def synthesis(self):
        return SynthesisSettings(self.model, self.preset, self.temperature, self.backend)

    @property
    def output(self):
        return OutputSettings(1.0 if self.speed == 0 else self.speed, self.bass, self.mid, self.treble, self.speed == 0)


@dataclass(frozen=True)
class Recipe:
    name: str
    samples: tuple[str, ...]
    offset: int = 0
    seconds: int = 24
    temperature: float = 0.3

    def validate(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise VoiceError("Enter a preset name of up to 80 characters.")
        if not isinstance(self.samples, (tuple, list)) or not 1 <= len(self.samples) <= 8:
            raise VoiceError("Select one to eight source recordings.")
        if (type(self.offset) is not int or not 0 <= self.offset <= 3600
                or type(self.seconds) is not int or not 3 <= self.seconds <= 30
                or type(self.temperature) not in (float, int) or not 0 <= self.temperature <= 1):
            raise VoiceError("Voice preset settings are outside the supported range.")
        for name in self.samples:
            if (not isinstance(name, str) or any(char in name for char in "/\\:\0")
                    or Path(name).suffix.lower() not in RECORDING_EXTENSIONS):
                raise VoiceError("Select recordings from the speech bundle's sample folder.")
        if len({name.casefold() for name in self.samples}) != len(self.samples):
            raise VoiceError("Use each recording only once per preset.")


class VoiceLibrary:
    def __init__(self, directory):
        self.root = directory
        self.samples = directory / "samples"
        self.presets = directory / "presets"
        self.preference = directory / "chat-voice.json"

    def recordings(self):
        return sorted(path.name for path in self.samples.glob("*") if path.is_file()
                      and not path.is_symlink() and path.suffix.lower() in RECORDING_EXTENSIONS)

    def recipes(self):
        result = {}
        for path in sorted(self.presets.glob("*/recipe.json")):
            if not _PRESET_ID.fullmatch(path.parent.name) or not path.with_name("voice.safetensors").is_file():
                continue
            try:
                data = json.loads(path.read_text("utf-8"))
                recipe = Recipe(**data)
                recipe.validate()
                result[path.parent.name] = Recipe(recipe.name, tuple(recipe.samples), recipe.offset,
                                                  recipe.seconds, recipe.temperature)
            except (OSError, ValueError, TypeError, VoiceError):
                continue
        return result

    def voices(self, model, recipes):
        choices = ({ident: (f"Silencio voice {ident}", 0.3) for ident in SILENCIO_VOICES}
                   if model == "silencio2" else {"alba": ("Alba", 0.3)})
        if model == "18m":
            if (self.root / "custom.safetensors").is_file():
                choices["original"] = ("Original clone", 0.3)
        if model in ("18m", "silencio2"):
            choices.update((ident, (f"{recipe.name} · {ident[:6]}", recipe.temperature))
                           for ident, recipe in recipes.items())
        return choices

    def chat(self):
        if not self.preference.is_file():
            return Playback("18m", "original" if (self.root / "custom.safetensors").is_file() else "alba", 0.3)
        try:
            return Playback(**json.loads(self.preference.read_text("utf-8")))
        except (TypeError, ValueError, OSError):
            raise VoiceError("The existing chat voice settings could not be read.") from None

    def resolve(self, settings, *, check_assets=True):
        if isinstance(settings, Playback):
            settings.output.validate()
        if (not isinstance(settings.model, str) or settings.model not in TTS_MODELS
                or type(settings.temperature) not in (float, int) or not 0 <= settings.temperature <= 1):
            raise VoiceError("Choose a supported TTS model and temperature.")
        if settings.backend not in ("cpu", "cuda"):
            raise VoiceError("Choose CPU or GPU (CUDA) for synthesis.")
        if settings.model == "silencio2":
            if settings.preset not in SILENCIO_VOICES and not (
                isinstance(settings.preset, str) and _PRESET_ID.fullmatch(settings.preset)
            ):
                raise VoiceError("Choose a Silencio-2 voice or a clone with its source recordings.")
            if check_assets and settings.preset not in SILENCIO_VOICES and settings.preset not in self.recipes():
                raise VoiceError("The saved voice recipe is missing. Choose another preset.")
            return "config.yaml", settings.preset
        if settings.preset == "alba":
            suffix = "-7m" if settings.model == "7m" else ""
            return f"english{suffix}.yaml", f"alba{suffix}.safetensors"
        if settings.model != "18m":
            raise VoiceError("This clone uses the English 18M REFL model.")
        if settings.preset == "original":
            voice = Path("custom.safetensors")
        elif isinstance(settings.preset, str) and _PRESET_ID.fullmatch(settings.preset):
            voice = Path("presets") / settings.preset / "voice.safetensors"
        else:
            raise VoiceError("Choose an installed voice preset.")
        if check_assets and not (self.root / voice).is_file():
            raise VoiceError("The saved voice file is missing. Choose another preset.")
        return "cloning.yaml", str(voice)
