"""Offline operations submitted through the same foreground gate as calls."""

from .cloning import clone_voice
from .contracts import VoiceError
from .encoder import PocketEncoder
from .harness import Outcome
from .recordings import import_recording
from .playback import AudioPlayback
from .segments import paragraph_segments
from .speaker import SynthesizedSpeech


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
    encoder = PocketEncoder(library.root,
                            process_started=lambda pid: worker.harness.watch_process(worker.job, pid))
    ident = clone_voice(library, encoder, recipe, stop, worker.stage.emit, worker.progress.emit)
    if ident is not None:
        worker.cloned.emit(ident)
        return Outcome("Voice preset saved.")
    return Outcome("Voice cloning stopped.")


def preview_voice(factory, settings, text, worker, stop):
    segments = paragraph_segments(text)
    if not segments:
        raise VoiceError("Enter text to preview.")
    harness, job = worker.harness, worker.job

    def create():
        return factory.create(settings, process_started=lambda pid: harness.watch_process(job, pid))

    with harness.models.borrow(factory.key(settings), create, stop,
                               worker.stage.emit, job.work.scope) as model:
        if model is None:
            return "Voice preview stopped."
        harness.watch_process(job, model.process_id, warm=True)
        harness.ready(job)
        speaker = SynthesizedSpeech(model, AudioPlayback(settings, worker.speaker_level.emit))
        total = len(segments)
        worker.progress.emit(0, total, "preview segments queued for playback")
        remaining = iter(segments)
        speaker.speak(lambda _cancel: next(remaining, None), stop, worker.stage.emit,
                      played=lambda done: worker.progress.emit(done, total, "preview segments queued for playback"))
    return "Voice preview finished."
