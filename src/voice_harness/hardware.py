"""Host-level readings for the monitor, sampled away from the Qt thread."""

import csv
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import time

from .admission import available_memory
from .gpu_counters import GpuCounters
from .resources import process_sample, working_set


def cpu_name():
    if os.name == "nt":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return os.environ.get("PROCESSOR_IDENTIFIER", "CPU")


def cpu_times():
    if os.name == "nt":
        idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
        if ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(x) for x in (idle, kernel, user))):
            idle, kernel, user = ((x.dwHighDateTime << 32) | x.dwLowDateTime for x in (idle, kernel, user))
            return idle, kernel + user
    else:
        try:
            parts = Path("/proc/stat").read_text("ascii").splitlines()[0].split()
            fields = [int(value) for value in parts[1:9]]
            return fields[3] + fields[4], sum(fields)
        except (OSError, ValueError, IndexError):
            pass
    return None


def _nvidia_readings():
    binary = shutil.which("nvidia-smi.exe" if os.name == "nt" else "nvidia-smi")
    if binary is None:
        return ()
    try:
        result = subprocess.run(
            [binary, "--query-gpu=name,utilization.gpu,memory.free,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, check=True, text=True, errors="replace", timeout=2,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return tuple(tuple(field.strip() for field in row) for row in csv.reader(result.stdout.splitlines()) if len(row) == 4)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ()


def gpus():
    def value(field, unit):
        return field + unit if field.isdecimal() else "unavailable"

    return "\n".join(f"{name} · {value(util, '%')} GPU · {value(free, ' MiB')} free / {value(total, ' MiB')} VRAM"
                     for name, util, free, total in _nvidia_readings()) or "GPU telemetry unavailable"


def available_nvidia_vram():
    """A single NVIDIA adapter can be matched to the confirmed Vulkan worker device."""
    rows = _nvidia_readings()
    if len(rows) == 1 and rows[0][2].isdecimal():
        return int(rows[0][2]) * 1024 ** 2
    return None


@dataclass(frozen=True)
class ModelUsage:
    rss: int | None
    cpu_pct: float | None
    gpu_pct: float | None
    vram: float | None


class HardwareSampler:
    def __init__(self):
        self.name = None
        self.prior_cpu = None
        self.prior_process = None
        self.prior_models = {}
        self.gpu_counters = None

    def sample(self, models):
        usage = self.model_usage(models)
        if self.name is None:
            self.name = cpu_name()
        host, process = cpu_times(), (time.monotonic(), time.process_time())
        host_pct = process_pct = None
        cores = os.cpu_count() or 1
        if host is not None and self.prior_cpu is not None:
            idle, total = (now - last for now, last in zip(host, self.prior_cpu))
            if total > 0:
                host_pct = min(100, max(0, (total - idle) / total * 100))
        if self.prior_process is not None:
            elapsed, used = (now - last for now, last in zip(process, self.prior_process))
            if elapsed > 0:
                process_pct = min(100, max(0, used / elapsed / cores * 100))
        self.prior_cpu, self.prior_process = host, process

        def percent(number):
            return "—" if number is None else f"{number:.1f}%"

        def memory(number):
            return "—" if number is None else f"{number / 2**20:,.0f} MiB"

        return (
            f"{self.name} · {cores} logical CPUs · {percent(host_pct)} total, {percent(process_pct)} Python process",
            f"{memory(available_memory())} RAM available · {memory(working_set())} app working set",
            gpus() or "GPU telemetry unavailable",
        ), usage

    def model_usage(self, models):
        now = time.monotonic()
        cores = os.cpu_count() or 1
        if self.gpu_counters is None:
            self.gpu_counters = GpuCounters()
        engines, memory = self.gpu_counters.read()
        prior, self.prior_models = self.prior_models, {}
        usage = {}
        for model in models:
            if model.pid is None:
                continue
            rss, seconds = process_sample(model.pid)
            cpu = None
            if seconds is not None:
                self.prior_models[model.id] = now, seconds
                if model.id in prior:
                    at, used = prior[model.id]
                    if now > at and seconds >= used:
                        cpu = min(100, 100 * (seconds - used) / (now - at) / cores)
            gpu = engines.get(model.pid, 0) if engines is not None and rss is not None else None
            vram = memory.get(model.pid, 0) if memory is not None and rss is not None else None
            usage[model.id] = ModelUsage(rss, cpu, gpu, vram)
        return usage

    def close(self):
        if self.gpu_counters is not None:
            self.gpu_counters.close()
