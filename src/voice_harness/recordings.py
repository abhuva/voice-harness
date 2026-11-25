"""Import local audio and assemble 24 kHz mono reference clips for the encoder."""

import filecmp
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import VoiceError
from .presets import RECORDING_EXTENSIONS


def import_recording(samples, source, stop):
    import soundfile as sf

    source = Path(source)
    if source.suffix.lower() not in RECORDING_EXTENSIONS:
        raise VoiceError("Choose a WAV, MP3, FLAC or Ogg recording.")
    try:
        samples.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".import-", dir=samples) as staging:
            staged = Path(staging) / source.name
            with source.open("rb") as reader, staged.open("xb") as writer:
                while not stop.is_set() and (data := reader.read(1024 * 1024)):
                    writer.write(data)
            if stop.is_set():
                return None
            if sf.info(staged).frames < 1:
                raise VoiceError("The selected recording contains no audio.")
            target = samples / source.name
            suffix = 2
            while not stop.is_set():
                try:
                    if os.name == "nt":
                        staged.rename(target)
                    else:
                        os.link(staged, target)
                    return target.name
                except FileExistsError:
                    if target.is_file() and not target.is_symlink() and filecmp.cmp(staged, target, shallow=False):
                        return target.name
                    target = samples / f"{source.stem} ({suffix}){source.suffix}"
                    suffix += 1
    except (OSError, RuntimeError):
        raise VoiceError("Could not import the recording. Check the audio format and library permissions.") from None
    return None


def write_reference(samples, recipe, destination, stop, progress):
    import numpy as np
    import soundfile as sf

    rate = 24_000
    total = len(recipe.samples)
    seconds = recipe.seconds / total
    parts = []
    progress(0, total, "reference clips prepared")
    for index, name in enumerate(recipe.samples, start=1):
        if stop.is_set():
            return False
        path = samples / name
        if path.is_symlink() or not path.is_file():
            raise VoiceError("A selected recording is missing. Import it again.")
        try:
            with sf.SoundFile(path) as clip:
                if not 8000 <= clip.samplerate <= 192000 or not 1 <= clip.channels <= 8:
                    raise VoiceError("Choose recordings with a standard sample rate and up to eight channels.")
                if recipe.offset * clip.samplerate >= clip.frames:
                    raise VoiceError("The start offset falls past the end of a selected recording.")
                clip.seek(recipe.offset * clip.samplerate)
                mono = clip.read(int(clip.samplerate * (seconds + 3)), dtype="float32", always_2d=True).mean(axis=1)
                voiced = np.flatnonzero(np.abs(mono) > 0.003)
                if not len(voiced):
                    raise VoiceError("A reference clip is silent. Choose recordings with audible speech.")
                mono = mono[voiced[0]:min(voiced[-1] + 1, voiced[0] + int(seconds * clip.samplerate))]
                step = clip.samplerate / rate
                grid = np.arange(int(len(mono) * rate / clip.samplerate)) * step
                parts.append(np.interp(grid, np.arange(len(mono)), mono))
        except (OSError, RuntimeError):
            raise VoiceError("A reference clip could not be decoded. Try WAV, FLAC, Ogg or MP3.") from None
        progress(index, total, "reference clips prepared")
    if stop.is_set():
        return False
    if sum(map(len, parts)) < 3 * rate:
        raise VoiceError("Choose clips containing at least three seconds of audible speech in total.")
    merged = []
    for part in parts:
        merged.extend((part, np.zeros(rate // 8)))
    try:
        sf.write(destination, np.concatenate(merged), rate)
    except (OSError, RuntimeError):
        raise VoiceError("Could not write the temporary voice reference. Check available disk space.") from None
    return True
