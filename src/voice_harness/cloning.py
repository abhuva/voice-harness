"""Build one voice preset and publish its recipe and voice state together."""

from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from .contracts import VoiceError
from .recordings import write_reference


def clone_voice(library, encoder, recipe, stop, stage, progress):
    recipe.validate()
    if stop.is_set():
        return None
    encoder.check()
    try:
        library.presets.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".clone-", dir=library.presets) as work:
            reference = Path(work) / "reference.wav"
            bundle = Path(work) / "preset"
            bundle.mkdir()
            profile = bundle / "voice.safetensors"
            if not write_reference(library.samples, recipe, reference, stop, progress) or stop.is_set():
                return None
            stage("Encoding the local voice profile")
            if not encoder.export(reference, profile, stop) or stop.is_set():
                return None
            if not profile.is_file() or not profile.stat().st_size:
                raise VoiceError("Pocket TTS finished without writing a voice profile.")
            stage("Saving the voice preset")
            (bundle / "recipe.json").write_text(json.dumps(asdict(recipe), indent=2), encoding="utf-8")
            if stop.is_set():
                return None
            ident = uuid4().hex
            # A preset becomes discoverable only after both files are complete.
            bundle.rename(library.presets / ident)
            return ident
    except OSError:
        raise VoiceError("Could not save the voice preset. Check bundle permissions and disk space.") from None
