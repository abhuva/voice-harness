"""Jevos v5 OpenVINO adapter. The owned native process only listens on loopback."""

import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import secrets
import socket
import subprocess
from threading import Thread
import time

import requests

from .contracts import VoiceError


MODEL = "jevos-v5"
THREADS = 1


def installation(directory):
    root = Path(directory).expanduser().resolve()
    executable = root / "jev" / "jev.exe"
    if not executable.is_file():
        executable = root / "jev.exe"
    model = root / "model"
    if not executable.is_file() or not (model / "openvino_model.xml").is_file():
        raise VoiceError("Choose a Jevos v5 folder containing jev.exe and the OpenVINO model folder.")
    return executable, model


def answers(body, questions):
    try:
        raw = body["answers"]
        if body["model"] != MODEL or not isinstance(raw, dict) or set(raw) != set(questions):
            raise ValueError
        scores = {}
        for key in questions:
            entry = raw[key]
            score = entry["noul"]
            if entry["type"] != "noul" or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError
            scores[key] = float(score)
        return scores
    except (KeyError, TypeError, ValueError, OverflowError):
        raise VoiceError("Jevos returned invalid scores.") from None


def conversation_state(messages):
    state = "\n\n".join(f"{message.speaker}:\n{message.body}" for message in messages)
    if len(state.encode("utf-8")) > 256 * 1024:
        raise VoiceError("The three full messages exceed the Jevos input limit.")
    return state


class DecisionProcess:
    """Authenticated local worker, with bounded responses and owned cancellation."""

    def __init__(self, directory, process_started):
        self.directory = directory
        self.process_started = process_started
        self.process = None
        self.client = None
        self.request = None
        self.reader = None
        self.loaded = False

    @property
    def process_id(self):
        return self.process.pid if self.process is not None else None

    @property
    def ready(self):
        return self.loaded and self.process.poll() is None

    def launch(self, executable, args, *, key_variable, env_updates=None, read_status=None):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        key = secrets.token_urlsafe(32)
        env = {name: os.environ[name] for name in ("SystemRoot", "WINDIR", "TEMP", "TMP") if name in os.environ}
        env["PATH"] = str(executable.parent) + os.pathsep + str(Path(env.get("SystemRoot", "C:/Windows")) / "System32")
        env[key_variable] = key
        env.update(env_updates or {})
        self.client = requests.Session()
        self.client.trust_env = False
        self.client.headers["Authorization"] = "Bearer " + key
        self.process = subprocess.Popen(
            [str(executable), *args, "--host", "127.0.0.1", "--port", str(port)],
            cwd=executable.parent, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE if read_status else subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.process_started(self.process.pid)
        if read_status:
            self.reader = Thread(target=read_status, args=(self.process.stderr,), name="jevos-startup")
            self.reader.start()

    def wait_ready(self, stop, status):
        deadline = time.monotonic() + 60
        while not stop.is_set() and time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise VoiceError("Jevos exited while loading. Check its local runtime and model files.")
            try:
                with self.client.get(self.url + "/health", timeout=0.4, allow_redirects=False) as response:
                    if (response.status_code == 200 and response.json().get("status") == status
                            and self.process.poll() is None):
                        return True
            except (requests.RequestException, ValueError, AttributeError):
                pass
            time.sleep(0.08)
        if not stop.is_set():
            raise VoiceError("Jevos did not finish loading within 60 seconds.")
        return False

    def request_json(self, endpoint, payload=None, *, timeout=45):
        method = "GET" if payload is None else "POST"
        with self.client.request(method, self.url + endpoint, json=payload, stream=True,
                                 allow_redirects=False, timeout=(1, timeout)) as response:
            if response.status_code in (400, 413, 422):
                raise VoiceError("Jevos rejected the input. The full messages may exceed its context window.")
            if response.status_code != 200:
                raise VoiceError("The local Jevos request failed.")
            raw = bytearray()
            for block in response.iter_content(4096):
                raw.extend(block)
                if len(raw) > 256 * 1024:
                    raise VoiceError("The local Jevos response exceeded its size limit.")
            return json.loads(raw)

    def infer(self, score, stop):
        results = Queue(maxsize=1)

        def run():
            try:
                results.put(score())
            except VoiceError as exc:
                results.put(exc)
            except (requests.RequestException, ValueError, OSError, AttributeError):
                results.put(VoiceError("Jevos did not return readable scores."))

        if stop.is_set():
            return None
        self.request = Thread(target=run, daemon=True, name="jevos-loopback")
        self.request.start()
        deadline = time.monotonic() + 45
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                result = results.get(timeout=0.05)
            except Empty:
                continue
            if isinstance(result, VoiceError):
                raise result
            return result
        self.close()
        if not stop.is_set():
            raise VoiceError("Jevos scoring timed out.")
        return None

    def close(self):
        self.loaded = False
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        finally:
            if self.reader is not None:
                self.reader.join(timeout=1)
                self.process.stderr.close()
                self.reader = None
            if self.request is not None:
                self.request.join(timeout=1)
                self.request = None
            if self.client is not None:
                self.client.close()
                self.client = None


class Jevos(DecisionProcess):
    def prepare(self, stop):
        executable, model = installation(self.directory)
        self.launch(executable, ["serve", "--model-dir", str(model), "--threads", str(THREADS), "--warmup", "0"],
                    key_variable="JEV_API_KEY")
        self.loaded = self.wait_ready(stop, "ready")

    def score(self, messages, questions, stop):
        state = conversation_state(messages)
        wire = {str(index): {"type": "noul", "instructions": prompt} for index, prompt in enumerate(questions)}

        def score():
            body = self.request_json("/v1/systemone", {"model": "jev-latest", "state": state, "questions": wire})
            scores = answers(body, wire)
            return tuple(scores[key] for key in wire)

        return self.infer(score, stop)
