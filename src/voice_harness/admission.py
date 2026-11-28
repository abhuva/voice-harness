"""Resource admission and interactive reservation, independent of scheduling."""

import ctypes
import os
from pathlib import Path


_MIB = 1024 ** 2


def _mib(size, *, round_up=False):
    if round_up:
        size += _MIB - 1
    return f"{size // _MIB:,} MiB"


def available_memory():
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
                (name, ctypes.c_uint64) for name in ("total", "available", "page_total",
                                                   "page_available", "virtual_total",
                                                   "virtual_available", "virtual_extended")
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.available
    elif os.path.exists("/proc/meminfo"):
        try:
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            pass
    return None


class Admission:
    def __init__(self, available=available_memory, *, reserve_bytes=1024 * _MIB,
                 background_threads=1, remote_requests=None, remote_cost_microunits=None):
        self.available = available
        self.reserve_bytes = reserve_bytes
        self.background_threads = background_threads
        self.remote_requests = remote_requests
        self.remote_cost_microunits = remote_cost_microunits
        self._requests = self._cost = 0

    def allows(self, profile, active, **conditions):
        return self.blocker(profile, active, **conditions) is None

    def blocker(self, profile, active, *, interactive, strict_memory=False,
                call_active=False, call_reserve=0, call_resident=0, accelerator_available=None):
        if not interactive and (profile.cpu_threads is None or profile.cpu_threads > self.background_threads):
            return "waiting for a job that fits the background CPU limit"
        accelerator = profile.accelerator_bytes
        if accelerator is None or (accelerator and accelerator_available is None):
            return "waiting for a VRAM estimate and reading"
        if accelerator and accelerator > accelerator_available:
            return (f"waiting for VRAM ({_mib(accelerator, round_up=True)} VRAM needed, "
                    f"{_mib(accelerator_available)} available for this job)")
        demand = profile.memory
        if not interactive and demand == 0:
            # Dispatch using existing buffers does not compete for model RAM.
            return None
        available = self.available()
        if interactive and not active:
            if available is None or demand is None or (profile.advisory_memory and not strict_memory):
                return None
        estimates = [other.memory for other in active]
        if available is None:
            return "waiting for a system RAM reading"
        if demand is None or None in estimates:
            return "waiting for a RAM estimate for the current jobs"
        # An already admitted call has a reservation in active.
        reserve = 0
        if not interactive and not call_active:
            reserve = max(0, max(self.reserve_bytes, call_reserve) - call_resident)
        committed = sum(estimates) + reserve
        if demand + committed > available:
            detail = (f"{_mib(demand, round_up=True)} needed, "
                      f"{_mib(max(0, available - committed))} available for this job")
            if committed:
                detail += f", {_mib(committed)} reserved for other work"
            return f"waiting for RAM ({detail})"
        return None

    def allows_remote(self, profile):
        if None in (self.remote_requests, self.remote_cost_microunits,
                    profile.remote_requests, profile.remote_cost_microunits):
            return False
        return (self._requests + profile.remote_requests <= self.remote_requests
                and self._cost + profile.remote_cost_microunits <= self.remote_cost_microunits)

    def charge_remote(self, profile):
        self._requests += profile.remote_requests
        self._cost += profile.remote_cost_microunits
