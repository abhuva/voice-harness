"""Local paths, operator-owned preferences and scoped Kindroid credentials."""

from dataclasses import asdict
import json
from pathlib import Path
import shlex
from uuid import uuid4

from .contracts import CallInput, VoiceError
from .presets import Playback, VoiceLibrary
from .recognizer import STT_MODELS


class Settings:
    def __init__(self, root: Path, assets: Path):
        self.local = root / "var"
        self.assets = assets
        self.env_file = root / ".env"
        self.dictate = assets / "voice/dictate"
        self.pocket = assets / "pocket-tts/4.0.1"
        self.history_guard = assets / "voice/history-next-read"

    def load(self):
        local = self.local / "preferences.json"
        if local.is_file():
            try:
                profile = json.loads(local.read_text("utf-8"))
                if profile["version"] != 1:
                    raise ValueError
                call = validate_call(CallInput(**profile["dictation"]))
                voice = Playback(**profile["playback"])
                VoiceLibrary(self.pocket).resolve(voice)
                return call, voice
            except (OSError, KeyError, TypeError, ValueError, VoiceError):
                raise VoiceError("Could not use saved Voice Harness settings. Review the defaults before calling.") from None

        try:
            legacy = self.assets / "voice/chat-stt.json"
            call = validate_call(CallInput(**json.loads(legacy.read_text("utf-8")))) if legacy.is_file() else CallInput()
            return call, VoiceLibrary(self.pocket).chat()
        except (OSError, TypeError, ValueError, VoiceError):
            raise VoiceError("Could not use the existing chat settings. Review the defaults before calling.") from None

    def save(self, call, voice):
        validate_call(call)
        VoiceLibrary(self.pocket).resolve(voice)
        self.local.mkdir(parents=True, exist_ok=True)
        target = self.local / "preferences.json"
        temporary = target.with_name(f".preferences-{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps({
                "version": 1, "dictation": asdict(call), "playback": asdict(voice),
            }, indent=2), encoding="utf-8")
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


def validate_call(call):
    if (not isinstance(call.model, str) or call.model not in STT_MODELS
            or type(call.interrupt) is not bool
            or type(call.threads) is not int or not 1 <= call.threads <= 16
            or type(call.threshold) is not int or not -80 <= call.threshold <= -5
            or any(type(value) not in (int, float) or not 0.1 <= value <= 10.0
                   for value in (call.chunk, call.paragraph, call.finish))):
        raise VoiceError("Dictation settings are outside the supported range.")
    return call
