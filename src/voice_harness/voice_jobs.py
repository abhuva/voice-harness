"""Offline operations submitted through the same foreground gate as calls."""

import re
import textwrap

from .cloning import clone_voice
from .contracts import VoiceError
from .encoder import PocketEncoder
from .harness import Outcome
from .recordings import import_recording
from .speaker import PocketSpeaker


def import_clips(library, sources, worker, stop):
    imported = []
    total = len(sources)
    worker.progress.emit(0, total, "recordings imported")
    try:
        for source in sources:
            name = import_recording(library.samples, source, stop)
            if name is None:
                break
            imported.append(name)
            worker.progress.emit(len(imported), total, "recordings imported")
    except VoiceError as exc:
        return Outcome(f"Import failed · {len(imported)} of {total} recordings imported. {exc}")
    finally:
        if imported:
            worker.imported.emit(tuple(imported))
    prefix = "Import stopped · " if len(imported) < total else ""
    return Outcome(f"{prefix}{len(imported)} of {total} recordings imported.")


def build_preset(library, recipe, worker, stop):
    ident = clone_voice(library, PocketEncoder(library.root), recipe, stop,
                        worker.stage.emit, worker.progress.emit)
    if ident is not None:
        worker.cloned.emit(ident)
        return Outcome("Voice preset saved.")
    return Outcome("Voice cloning stopped.")


def preview_voice(directory, settings, text, worker, stop):
    paragraphs = re.split(r"\n(?:[^\S\n]*\n)+", text.strip())
    segments = [line for paragraph in paragraphs for line in textwrap.wrap(paragraph, width=480)]
    if not segments:
        raise VoiceError("Enter text to preview.")
    speaker = PocketSpeaker(directory, settings, worker.speaker_level.emit)
    try:
        worker.stage.emit("Loading Pocket TTS for preview")
        speaker.prepare(stop)
        if stop.is_set():
            return "Voice preview stopped."
        total = len(segments)
        worker.progress.emit(0, total, "preview segments queued for playback")
        remaining = iter(segments)
        speaker.speak(lambda _cancel: next(remaining, None), stop, worker.stage.emit,
                      played=lambda done: worker.progress.emit(done, total, "preview segments queued for playback"))
    finally:
        speaker.close()
    return "Voice preview finished."
