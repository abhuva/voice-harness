"""Invoke the installed Windows Pocket TTS encoder with a private reference."""

import subprocess
from tempfile import TemporaryFile
import time

from .contracts import VoiceError


class PocketEncoder:
    def __init__(self, directory, *, process_started=None):
        self.directory = directory
        self.process_started = process_started

    def check(self):
        files = ("voice-clone.exe", "cloning.yaml", "cloning.safetensors", "tokenizer.model")
        if not all((self.directory / name).is_file() for name in files):
            raise VoiceError("Restore voice-clone.exe and the Pocket TTS 18M cloning files in the speech bundle.")

    def export(self, reference, profile, stop):
        if stop.is_set():
            return False
        try:
            with TemporaryFile() as errors, subprocess.Popen(
                [str(self.directory / "voice-clone.exe"), "export-voice", str(reference), str(profile)],
                cwd=self.directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=errors, creationflags=subprocess.CREATE_NO_WINDOW,
            ) as child:
                try:
                    if self.process_started is not None:
                        self.process_started(child.pid)
                    deadline = time.monotonic() + 180
                    while not stop.is_set():
                        try:
                            code = child.wait(timeout=0.15)
                        except subprocess.TimeoutExpired:
                            if time.monotonic() >= deadline:
                                raise VoiceError("Voice cloning exceeded its three-minute limit.")
                            continue
                        if code == 0:
                            return True
                        errors.seek(0)
                        if code in (0xC0000017, 0xC000012D) or b"memory allocation" in errors.read(16384):
                            raise VoiceError("Windows ran out of memory while cloning. Reduce the reference duration.")
                        raise VoiceError("Pocket TTS could not encode the reference. Check the cloning files and disk space.")
                    return False
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
        except OSError:
            raise VoiceError("Could not run voice-clone.exe from the speech bundle.") from None
