"""Loopback-only bridge to an existing Jevos v5 server. Runs on stdlib alone."""

import argparse
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shutil
import socket
import subprocess
from threading import Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


LIMIT = 320 * 1024
STATUS = "/office/v1/status"
SCORE = "/office/v1/score"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def loopback_url(value):
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
            or url.username is not None or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("Use http://127.0.0.1:PORT for the SSH tunnel.")
    return f"http://127.0.0.1:{url.port}"


def request_json(url, payload=None, *, key="", timeout=2):
    headers = {"X-Voice-Harness-Office": "1"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode()
    client = build_opener(ProxyHandler({}), NoRedirect())
    with client.open(Request(url, data=data, headers=headers), timeout=timeout) as reply:
        value = reply.read(LIMIT + 1)
        if len(value) > LIMIT:
            raise ValueError("Reply too large.")
        return json.loads(value)


def readings():
    ram = vram = None
    if os.name == "nt":
        values = (ctypes.c_uint64 * 8)()
        values[0] = ctypes.sizeof(values)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(values)):
            ram = values[2] // 2**20
    elif hasattr(os, "sysconf"):
        try:
            ram = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 2**20
        except (OSError, ValueError):
            pass
    binary = shutil.which("nvidia-smi")
    if binary:
        try:
            result = subprocess.run([binary, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                                    capture_output=True, check=True, timeout=1, text=True,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            free = result.stdout.strip()
            if free.isdecimal():
                vram = int(free)
        except (OSError, subprocess.SubprocessError):
            pass
    return ram, vram


class Worker:
    def __init__(self, upstream, key, backend):
        self.upstream, self.key, self.backend = upstream, key, backend
        self.active = Lock()
        self.samples = Lock()
        self.sampled = -10.0
        self.metrics = (None, None)

    def status(self):
        if self.samples.acquire(blocking=False):
            try:
                if monotonic() - self.sampled >= 5:
                    self.metrics = readings()
                    self.sampled = monotonic()
            finally:
                self.samples.release()
        busy = self.active.locked()
        try:
            state = "busy" if busy else request_json(self.upstream + "/health", key=self.key)["status"]
        except (OSError, ValueError, KeyError, TypeError):
            state = "unavailable"
        return {"protocol": 1, "node": socket.gethostname(), "capabilities": ["decision.score"],
                "model": "jevos-v5", "backend": self.backend,
                "state": state if state in ("ready", "busy") else "unavailable",
                "logical_cpus": os.cpu_count(),
                "ram_free_mib": self.metrics[0], "vram_free_mib": self.metrics[1]}

    def score(self, payload):
        ident = payload["job"]
        state, questions = payload["state"], payload["questions"]
        if (not isinstance(ident, str) or not 1 <= len(ident) <= 80 or not isinstance(state, str)
                or not 1 <= len(state.encode()) <= 256 * 1024 or not isinstance(questions, list)
                or not 1 <= len(questions) <= 24 or any(
                    not isinstance(q, str) or not 1 <= len(q) <= 240 for q in questions)):
            raise ValueError
        wire = {str(i): {"type": "noul", "instructions": question} for i, question in enumerate(questions)}
        reply = request_json(self.upstream + "/v1/systemone",
                             {"model": "jev-latest", "state": state, "questions": wire},
                             key=self.key, timeout=20)
        return {"job": ident, "reply": reply}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self, path):
        if self.path != path or self.headers.get("X-Voice-Harness-Office") != "1" or "Origin" in self.headers:
            self.send_error(404)
            return False
        return True

    def do_GET(self):
        if self.authorized(STATUS):
            self.send_json(200, self.server.worker.status())

    def do_POST(self):
        if not self.authorized(SCORE):
            return
        worker = self.server.worker
        if not worker.active.acquire(blocking=False):
            self.send_json(409, {"error": "busy"})
            return
        try:
            self.connection.settimeout(3)
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= LIMIT or self.headers.get_content_type() != "application/json":
                raise ValueError
            payload = json.loads(self.rfile.read(size))
            result = worker.score(payload)
        except (KeyError, TypeError, ValueError, RecursionError):
            status, result = 400, {"error": "invalid scoring request"}
        except (HTTPError, URLError, OSError):
            status, result = 503, {"error": "Jevos unavailable"}
        else:
            status = 200
        finally:
            worker.active.release()
        try:
            self.send_json(status, result)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jevos-url", type=loopback_url, required=True)
    parser.add_argument("--port", type=int, default=18769)
    parser.add_argument("--backend", default="OpenVINO")
    parser.add_argument("--seconds", type=int, default=0, help="Optional trial lifetime")
    args = parser.parse_args()
    worker = Worker(args.jevos_url, os.environ.get("JEV_API_KEY", ""), args.backend)
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.worker = worker
        server.timeout = 0.25
        deadline = monotonic() + args.seconds if args.seconds else float("inf")
        print(f"Office Jevos bridge listening on 127.0.0.1:{args.port}", flush=True)
        while monotonic() < deadline:
            server.handle_request()


if __name__ == "__main__":
    main()
