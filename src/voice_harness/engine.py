"""One resident Pocket TTS process, serialized by the desktop audio worker."""

import json
import os
from pathlib import Path
from queue import Empty, Queue
import struct
import subprocess
from threading import Thread
import time

from .contracts import VoiceError


def _read_packets(pipe, packets):
    try:
        while header := pipe.read(5):
            if len(header) != 5:
                break
            tag, size = struct.unpack("<cI", header)
            if size > 128 * 1024 * 1024:
                break
            payload = pipe.read(size)
            if len(payload) != size:
                break
            packets.put((tag, payload))
    except (ValueError, OSError):
        pass
    finally:
        packets.put((b"X", b""))


def cuda_command(runtime):
    if runtime is not None:
        try:
            values = (line.split("=", 1) for line in (runtime / "pyvenv.cfg").read_text("utf-8").splitlines()
                      if "=" in line)
            config = {key.strip(): value.strip() for key, value in values}
            python = Path(config["home"]) / "python.exe"
            site = runtime / "Lib/site-packages"
            if python.is_absolute() and python.is_file() and (site / "torch/__init__.py").is_file():
                # Launch the real interpreter so its PID owns the CUDA context and can be reaped.
                worker = Path(__file__).with_name("pocket_cuda_worker.py").resolve()
                return [str(python), "-I", "-S", "-u", str(worker), str(site.resolve())]
        except (OSError, KeyError, UnicodeError):
            pass
    raise VoiceError("Install Pocket TTS CUDA with scripts/setup-pocket-cuda.ps1, or select CPU in TTS.")


class ResidentEngine:
    def __init__(self, directory, *, process_started=None, cuda_runtime=None):
        self.directory = directory
        self.process_started = process_started
        self.cuda_runtime = cuda_runtime
        self.child = None
        self.reader = None
        self.settings = None
        self.rate = 0

    def require_on(self):
        if self.child is None or self.child.poll() is not None:
            raise VoiceError("Pocket TTS is not ready. Start the call again.")

    def unload(self):
        child = self.child
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait()
            child.stdin.close()
            if self.reader:
                self.reader.join()
            child.stdout.close()
        self.reader = self.child = self.settings = None

    def _receive(self, stop, *, loading):
        deadline = time.monotonic() + (45 if loading else 180)
        cancelled = False
        while time.monotonic() < deadline:
            if stop.is_set() and not cancelled:
                if loading:
                    self.unload()
                    return None
                self.child.stdin.write(b"\n")
                self.child.stdin.flush()
                cancelled = True
                deadline = time.monotonic() + 4
            try:
                tag, payload = self.packets.get(timeout=0.04)
            except Empty:
                continue
            if tag == b"X":
                raise VoiceError("The Pocket TTS process exited. Start the call again.")
            if cancelled or tag == b"C":
                return None
            if tag != (b"R" if loading else b"A") or not payload or len(payload) % 4:
                raise VoiceError("Pocket TTS returned an invalid audio packet.")
            return payload
        raise VoiceError("Pocket TTS timed out. Start the call again.")

    def load(self, settings, config, profile, stop):
        if (self.settings and self.settings.synthesis_parameters == settings.synthesis_parameters
                and self.child and self.child.poll() is None):
            return
        self.unload()
        if stop.is_set():
            return
        cuda = settings.backend == "cuda"
        env = None
        if cuda:
            command = cuda_command(self.cuda_runtime)
            env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "LOCALAPPDATA")
                   if key in os.environ}
            env["HF_HUB_OFFLINE"] = "1"
        else:
            command = [str(self.directory / "voice-engine.exe")]
        process_started, self.process_started = self.process_started, None
        try:
            self.child = subprocess.Popen(
                [*command, config, profile, str(settings.temperature)],
                cwd=self.directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if process_started is not None:
                process_started(self.child.pid)
            self.packets = Queue()
            self.reader = Thread(target=_read_packets, args=(self.child.stdout, self.packets), daemon=True)
            self.reader.start()
            packet = self._receive(stop, loading=True)
            if packet is None:
                return
            if len(packet) != 4 or int.from_bytes(packet, "little") != 24000:
                raise VoiceError("Unexpected Pocket TTS sample rate.")
            self.rate = 24000
            self.settings = settings
        except (OSError, VoiceError) as exc:
            self.unload()
            if cuda and not stop.is_set():
                raise VoiceError("Pocket TTS CUDA could not load. Check the GPU and free VRAM, or select CPU in TTS.") from None
            if isinstance(exc, VoiceError):
                raise
            raise VoiceError("Could not launch voice-engine.exe. Restore the local voice bundle.") from None

    def render(self, paragraph, stop):
        """One request at a time. Drain cancellation acknowledgement before reusing the pipe."""
        self.require_on()
        if stop.is_set():
            return None
        try:
            self.child.stdin.write(json.dumps(paragraph, ensure_ascii=False).encode("utf-8") + b"\n")
            self.child.stdin.flush()
            payload = self._receive(stop, loading=False)
            return payload
        except (OSError, VoiceError) as exc:
            self.unload()
            if isinstance(exc, VoiceError):
                raise
            raise VoiceError("Lost the Pocket TTS process. Start the call again.") from None
