"""Read and validate Pocket TTS voice settings."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .contracts import VoiceError


SPEECH_SPEEDS = tuple(step / 4 for step in range(9))
TEMPO_MIN, TEMPO_MAX = 80, 120
EQ_LIMIT = 12


@dataclass(frozen=True)
class Playback:
    model: str
    preset: str
    temperature: float
    speed: float = 1.0
    bass: int = 0
    mid: int = 0
    treble: int = 0

    @property
    def synthesis_parameters(self):
        return self.model, self.preset, self.temperature


class VoiceLibrary:
    def __init__(self, directory):
        self.root = directory
        self.preference = directory / "chat-voice.json"

    def chat(self):
        if not self.preference.is_file():
            return Playback("18m", "original" if (self.root / "custom.safetensors").is_file() else "alba", 0.3)
        try:
            settings = Playback(**json.loads(self.preference.read_text("utf-8")))
            self.resolve(settings)
            return settings
        except (TypeError, ValueError, OSError):
            raise VoiceError("The existing chat voice settings could not be read.") from None

    def resolve(self, settings):
        speed = settings.speed
        # Accept older voice settings, including mute, when opening a saved voice.
        if speed not in SPEECH_SPEEDS and not (TEMPO_MIN / 100 <= speed <= TEMPO_MAX / 100
                                               and speed == round(speed, 2)):
            raise VoiceError("Choose a speaking speed from 0.80× to 1.20×.")
        for gain in (settings.bass, settings.mid, settings.treble):
            if type(gain) is not int or not -EQ_LIMIT <= gain <= EQ_LIMIT:
                raise VoiceError("Choose EQ gains between −12 and +12 dB.")
        if settings.model not in ("7m", "18m") or not 0 <= settings.temperature <= 1:
            raise VoiceError("Choose a supported Pocket TTS model and temperature.")
        if settings.preset == "alba":
            suffix = "-7m" if settings.model == "7m" else ""
            return f"english{suffix}.yaml", f"alba{suffix}.safetensors"
        if settings.model != "18m":
            raise VoiceError("This clone uses the English 18M REFL model.")
        if settings.preset == "original":
            voice = Path("custom.safetensors")
        elif re.fullmatch("[0-9a-f]{32}", settings.preset):
            voice = Path("presets") / settings.preset / "voice.safetensors"
        else:
            raise VoiceError("Choose an installed voice preset.")
        if not (self.root / voice).is_file():
            raise VoiceError("The saved voice file is missing. Choose another preset.")
        return "cloning.yaml", str(voice)
