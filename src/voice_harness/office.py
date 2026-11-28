"""Laptop adapter for the office worker reached over the local SSH tunnel."""

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Thread
from time import monotonic
from urllib.error import HTTPError

from .contracts import VoiceError
from .jevos import MODEL, answers, conversation_state
from .office_worker import SCORE, STATUS, request_json


class OfficeUnavailable(VoiceError):
    pass


@dataclass(frozen=True)
class OfficeStatus:
    node: str
    backend: str
    state: str
    ram: int | None
    vram: int | None
    cpus: int | None = None

    def describe(self):
        ram = f"{self.ram:,} MiB RAM free" if self.ram is not None else "RAM unknown"
        vram = f"{self.vram:,} MiB VRAM free" if self.vram is not None else "VRAM unknown"
        cpu = f" · {self.cpus} logical CPUs" if self.cpus is not None else ""
        return f"Jevos v5 ({self.backend}) · {self.state} · {ram} · {vram}{cpu}"


class OfficeJevos:
    def __init__(self, url):
        self.url = url

    def status(self):
        try:
            value = request_json(self.url + STATUS)
            if value["protocol"] != 1 or value["model"] != MODEL or "decision.score" not in value["capabilities"]:
                raise ValueError
            node, backend, state = value["node"], value["backend"], value["state"]
            ram, vram = value["ram_free_mib"], value["vram_free_mib"]
            cpus = value.get("logical_cpus")
            if (not all(isinstance(s, str) and s.isprintable() and 0 < len(s) <= 100 for s in (node, backend))
                    or state not in ("ready", "busy", "unavailable")
                    or any(n is not None and (type(n) is not int or n < 0) for n in (ram, vram, cpus))):
                raise ValueError
            return OfficeStatus(node, backend, state, ram, vram, cpus)
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def score(self, scope, messages, questions, stop):
        payload = {"job": scope.job_id, "workflow": scope.workflow_id, "turn": scope.turn_id,
                   "state": conversation_state(messages), "questions": list(questions)}
        slots = [str(i) for i in range(len(questions))]
        result = Queue(maxsize=1)

        def fetch():
            try:
                result.put(request_json(self.url + SCORE, payload, timeout=23))
            except (OSError, ValueError) as exc:
                result.put(exc)

        if stop.is_set():
            return None
        Thread(target=fetch, name="office-jevos-request", daemon=True).start()
        deadline = monotonic() + 24
        while not stop.is_set() and monotonic() < deadline:
            try:
                body = result.get(timeout=0.05)
            except Empty:
                continue
            if isinstance(body, HTTPError) and body.code == 409:
                raise OfficeUnavailable("Office worker is busy.")
            if isinstance(body, OSError):
                raise OfficeUnavailable("Office Jevos is unavailable.")
            try:
                if body["job"] != scope.job_id:
                    raise ValueError
                scores = answers(body["reply"], slots)
                return tuple(scores[key] for key in slots)
            except (KeyError, TypeError, ValueError):
                raise VoiceError("The office worker returned an invalid result.") from None
        if not stop.is_set():
            raise OfficeUnavailable("Office Jevos did not finish in time.")
        return None
