"""Windows Fluid Speech Pro worker using the pinned parakeet.cpp Vulkan server."""

from io import BytesIO
import os
from pathlib import Path
import socket
import subprocess
from threading import Event, Thread
from time import monotonic, sleep
import wave

import numpy as np
import requests

from .contracts import VoiceError


FLUID_VULKAN_DIRECTORY = "fluid-vulkan/v0.6.1"
MODEL = "fluid-speech-pro-q8_0.gguf"
MODEL_BYTES = 940_663_968
# The GTX 1050 Ti fixture peaked near 975 MiB of host RSS during cold loading.
COLD_HOST_BYTES = 1152 * 1024 ** 2
DEVICE = "Vulkan0"


class FluidVulkan:
    def __init__(self, directory, threads, process_started):
        self.directory = directory
        self.threads = threads
        self.process_started = process_started
        self.process = None
        self.client = None
        self.reader = None
        self.loaded = False

    @property
    def process_id(self):
        return self.process.pid if self.process is not None else None

    @property
    def ready(self):
        return self.loaded and self.process is not None and self.process.poll() is None

    def prepare(self, stop):
        executable = self.directory / "parakeet-server.exe"
        model = self.directory / MODEL
        if not executable.is_file() or not model.is_file() or model.stat().st_size != MODEL_BYTES:
            raise VoiceError("Install Fluid Speech Pro Vulkan with scripts/setup-fluid-vulkan.ps1, or select CPU in SST.")
        if stop.is_set():
            return

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA")
               if key in os.environ}
        env["PATH"] = os.pathsep.join((str(self.directory), str(Path(env.get("SystemRoot", "C:/Windows")) / "System32")))
        env["PARAKEET_DEVICE"] = DEVICE
        env["VK_LOADER_LAYERS_DISABLE"] = "VK_LAYER_AMD_switchable_graphics,VK_LAYER_OW_*"
        self.client = requests.Session()
        self.client.trust_env = False
        try:
            self.process = subprocess.Popen(
                [str(executable), "--model", str(model), "--host", "127.0.0.1", "--port", str(port),
                 "--threads", str(self.threads), "--concurrency", "1"],
                cwd=self.directory, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            raise VoiceError("Could not start Fluid Speech Pro Vulkan. Check the Windows runtime or select CPU in SST.") from None
        self.process_started(self.process.pid)

        device_confirmed = Event()
        listening = Event()

        def read_status(pipe):
            for line in pipe:
                if line.strip() == f"[parakeet] pk::Backend using device: {DEVICE}".encode("ascii"):
                    device_confirmed.set()
                if line.startswith(f"parakeet-server: listening on {self.url} ".encode("ascii")):
                    listening.set()

        self.reader = Thread(target=read_status, args=(self.process.stderr,), name="fluid-vulkan-status")
        self.reader.start()
        deadline = monotonic() + 45
        while not stop.is_set() and monotonic() < deadline:
            if self.process.poll() is not None or (listening.is_set() and not device_confirmed.is_set()):
                raise VoiceError("Fluid Speech Pro could not use Vulkan0. Check the GPU or select CPU in SST.")
            if listening.is_set():
                try:
                    with self.client.get(self.url + "/health", timeout=0.3, allow_redirects=False) as response:
                        if response.status_code == 200:
                            break
                except requests.RequestException:
                    pass
            sleep(0.05)
        if stop.is_set():
            return
        if monotonic() >= deadline:
            raise VoiceError("Fluid Speech Pro Vulkan did not finish loading. Check the GPU or select CPU in SST.")
        for frames in (16_000, 48_000):
            if stop.is_set():
                return
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise VoiceError("Fluid Speech Pro Vulkan did not finish warming up. Check the GPU or select CPU in SST.")
            self._decode(np.zeros(frames, dtype=np.int16), timeout=min(15, remaining))
        self.loaded = True

    def transcribe(self, samples):
        if not len(samples):
            return ""
        if not self.ready:
            raise VoiceError("Fluid Speech Pro Vulkan stopped. Start the call again or select CPU in SST.")
        return self._decode(samples)

    def _decode(self, samples, *, timeout=15):
        pcm = np.asarray(samples, dtype=np.int16).reshape(-1)
        peak = float(np.abs(pcm.astype(np.float32)).max())
        if 0 < peak < 16_384:
            pcm = (pcm * min(16_384 / peak, 30)).astype(np.int16)
        audio = BytesIO()
        with wave.open(audio, "wb") as output:
            output.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
            output.writeframes(pcm.astype("<i2", copy=False).tobytes())
        try:
            with self.client.post(self.url + "/v1/audio/transcriptions", timeout=(1, timeout),
                                  files={"file": ("chunk.wav", audio.getvalue(), "audio/wav")},
                                  allow_redirects=False) as response:
                if response.status_code != 200:
                    raise ValueError
                text = response.json()["text"]
                if not isinstance(text, str):
                    raise ValueError
                return text.strip()
        except (requests.RequestException, ValueError, KeyError, TypeError):
            self.close()
            raise VoiceError("Fluid Speech Pro Vulkan could not decode audio. Start the call again or select CPU in SST.") from None

    def close(self):
        self.loaded = False
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait()
            if self.reader is not None:
                self.reader.join()
            self.process.stderr.close()
            self.process = self.reader = None
        if self.client is not None:
            self.client.close()
            self.client = None
