"""Windows process samples for the load-to-ready interval."""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import time


@dataclass(frozen=True)
class ResourceSample:
    host_bytes: int | None
    pocket_bytes: int | None
    host_cpu_seconds: float
    pocket_cpu_seconds: float | None
    elapsed_seconds: float

    def description(self):
        def memory(size):
            return f"{size / 1024 ** 2:.0f} MiB" if size is not None else "unavailable"

        engine_cpu = (f"{self.pocket_cpu_seconds:.2f} s" if self.pocket_cpu_seconds is not None
                      else "unavailable")
        return (f"Resident working set · Python {memory(self.host_bytes)} · "
                f"child workers {memory(self.pocket_bytes)}\n"
                f"CPU during startup · Python {self.host_cpu_seconds:.2f} s · "
                f"child workers {engine_cpu} · {self.elapsed_seconds:.2f} s elapsed")


def resident_sample(start, cpu_start, pocket_pid):
    host_bytes, _ = process_sample(os.getpid())
    pocket_bytes, pocket_cpu = process_sample(pocket_pid) if pocket_pid else (0, 0.0)
    return ResourceSample(host_bytes, pocket_bytes, time.process_time() - cpu_start,
                          pocket_cpu, time.monotonic() - start)


def working_set(pid=None):
    return process_sample(pid or os.getpid())[0]


def process_sample(pid):
    if os.name != "nt":
        try:
            fields = Path(f"/proc/{pid}/statm").read_text(encoding="ascii").split()
            stat = Path(f"/proc/{pid}/stat").read_text("ascii").rsplit(")", 1)[1].split()
            seconds = (int(stat[11]) + int(stat[12])) / os.sysconf("SC_CLK_TCK")
            return int(fields[1]) * os.sysconf("SC_PAGE_SIZE"), seconds
        except (OSError, ValueError, IndexError):
            return None, None

    class MemoryCounters(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (field, ctypes.c_size_t) for field in (
                "peak_working_set", "working_set", "peak_paged_pool", "paged_pool",
                "peak_nonpaged_pool", "nonpaged_pool", "pagefile", "peak_pagefile")
        ]

    kernel = ctypes.WinDLL("kernel32")
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD)
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None, None
    try:
        counters = MemoryCounters()
        counters.size = ctypes.sizeof(counters)
        read_memory = psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.size)
        creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        read_times = kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in
                                                     (creation, exit_time, kernel_time, user_time)))
        cpu = (sum((value.dwHighDateTime << 32) | value.dwLowDateTime
                   for value in (kernel_time, user_time)) / 10_000_000 if read_times else None)
        return counters.working_set if read_memory else None, cpu
    finally:
        kernel.CloseHandle(handle)
