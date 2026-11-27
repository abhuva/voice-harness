"""Local paths, operator-owned preferences and scoped Kindroid credentials."""

from dataclasses import asdict
import json
from pathlib import Path
import shlex
from uuid import uuid4

from .contracts import CallInput, VoiceError
from .fluid_vulkan import FLUID_VULKAN_DIRECTORY
from .presets import OutputSettings, SynthesisSettings, VoiceLibrary
from .recognizer import STT_MODELS
from .receiver import receiver_id
from .synthesis import SynthesisFactory


class Settings:
    def __init__(self, root: Path, assets: Path):
        self.local = root / "var"
        self.assets = assets
        self.env_file = root / ".env"
        self.preferences = self.local / "preferences.json"
        self.dictate = assets / "voice/dictate"
        self.fluid_vulkan = self.local / FLUID_VULKAN_DIRECTORY
        self.pocket = assets / "pocket-tts/4.0.1"
        self.synthesis = SynthesisFactory(self.pocket, self.local / "pocket-cuda/v4.0.1")
        self.history_guard = assets / "voice/history-next-read"

    def load_sst(self):
        profile = self._read_profile()
        try:
            if "dictation" in profile:
                values = profile["dictation"]
            else:
                legacy = self.assets / "voice/chat-stt.json"
                values = json.loads(legacy.read_text("utf-8")) if legacy.is_file() else {}
            return validate_call(CallInput(**values)), profile.get("strict_memory") is True
        except (OSError, TypeError, ValueError, VoiceError):
            raise VoiceError("Could not load SST settings. Review the recognizer defaults.") from None

    def load_tts(self):
        profile = self._read_profile()
        library = VoiceLibrary(self.pocket)
        try:
            voice = SynthesisSettings(**profile["synthesis"]) if "synthesis" in profile else library.chat().synthesis
            library.resolve(voice, check_assets=False)
            return voice
        except (OSError, TypeError, ValueError, VoiceError):
            raise VoiceError("Could not load TTS settings. Review the voice and device defaults.") from None

    def load_output(self):
        profile = self._read_profile()
        try:
            output = OutputSettings(**profile["output"]) if "output" in profile else VoiceLibrary(self.pocket).chat().output
            return output.validate()
        except (OSError, TypeError, ValueError, VoiceError):
            raise VoiceError("Could not load playback settings. Review the playback defaults.") from None

    def save_sst(self, call: CallInput, *, strict_memory: bool):
        validate_call(call)
        self._save(dictation=asdict(call), strict_memory=strict_memory)

    def save_tts(self, voice: SynthesisSettings):
        VoiceLibrary(self.pocket).resolve(voice)
        self._save(synthesis=asdict(voice))

    def save_output(self, output: OutputSettings):
        self._save(output=asdict(output.validate()))

    def load_phone_output(self):
        return self._read_profile().get("phone_output", "phone")

    def save_phone_output(self, destination):
        if destination not in ("phone", "desktop") and not (
            isinstance(destination, str) and destination.startswith("receiver:")
            and receiver_id(destination.removeprefix("receiver:"))
        ):
            raise VoiceError("Choose a phone playback destination.")
        self._save(phone_output=destination)

    def _read_profile(self):
        try:
            profile = json.loads(self.preferences.read_text("utf-8"))
            if isinstance(profile, dict) and profile.get("version") == 2:
                return profile
            if isinstance(profile, dict) and profile.get("version") == 1:
                return _upgrade_profile(profile)
        except FileNotFoundError:
            return {"version": 2}
        except (OSError, ValueError):
            pass
        raise VoiceError("Could not read the local Voice Harness preferences file.")

    def _save(self, **sections):
        profile = self._read_profile()
        profile.update(sections)
        self.local.mkdir(parents=True, exist_ok=True)
        target = self.preferences
        temporary = target.with_name(f".preferences-{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(profile, indent=2), encoding="utf-8")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def credentials(self):
        values = {}
        try:
            with self.env_file.open(encoding="utf-8-sig") as config:
                for line in config:
                    name, separator, raw = line.partition("=")
                    name = name.strip()
                    if separator and name in ("KINDROID_API_KEY", "KINDROID_AI_ID"):
                        parts = shlex.split(raw, comments=True)
                        if len(parts) != 1 or not parts[0].isascii():
                            raise ValueError
                        values[name] = parts[0]
        except (OSError, ValueError):
            raise VoiceError("Set KINDROID_API_KEY and KINDROID_AI_ID in the local .env file.") from None
        if not all(values.get(name) for name in ("KINDROID_API_KEY", "KINDROID_AI_ID")):
            raise VoiceError("Set KINDROID_API_KEY and KINDROID_AI_ID in the local .env file.")
        return values["KINDROID_API_KEY"], values["KINDROID_AI_ID"]


def _upgrade_profile(profile):
    profile["version"] = 2
    if "playback" in profile:
        playback = profile.pop("playback")
        if isinstance(playback, dict):
            profile["synthesis"] = {key: playback[key] for key in ("model", "preset", "temperature", "backend")
                                    if key in playback}
            profile["output"] = {key: playback[key] for key in ("speed", "bass", "mid", "treble") if key in playback}
            if profile["output"].get("speed") == 0:
                profile["output"].update(speed=1.0, muted=True)
        else:
            profile.update(synthesis=playback, output=playback)
    return profile


def validate_call(call):
    if (not isinstance(call.model, str) or call.model not in STT_MODELS
            or type(call.interrupt) is not bool
            or type(call.stt_on_demand) is not bool
            or call.stt_backend not in ("cpu", "vulkan")
            or (call.stt_backend == "vulkan" and call.model != "fluid-speech-pro")
            or type(call.threads) is not int or not 1 <= call.threads <= 16
            or type(call.stt_idle) is not int or not 1 <= call.stt_idle <= 3600
            or type(call.threshold) is not int or not -80 <= call.threshold <= -5
            or any(type(value) not in (int, float) or not 0.1 <= value <= 10.0
                   for value in (call.chunk, call.paragraph, call.finish, call.barge_in))):
        raise VoiceError("Dictation settings are outside the supported range.")
    return call
