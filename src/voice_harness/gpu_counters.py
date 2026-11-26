"""Read Windows WDDM GPU utilization and dedicated memory by process."""

import ctypes
from ctypes import wintypes
import os
import re


_PID = re.compile(r"^pid_(\d+)_", re.IGNORECASE)
_MORE_DATA = 0x800007D2
_DOUBLE = 0x200


class _Value(ctypes.Structure):
    _fields_ = [("status", wintypes.DWORD), ("value", ctypes.c_double)]


class _Item(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("value", _Value)]


def group_by_pid(items, combine):
    if items is None:
        return None
    result = {}
    for instance, value in items:
        match = _PID.match(instance)
        if match:
            pid = int(match.group(1))
            result[pid] = combine(result.get(pid, 0), value)
    return result


class GpuCounters:
    def __init__(self):
        self.query = None
        if os.name != "nt":
            return
        try:
            self.pdh = ctypes.WinDLL("pdh")
        except OSError:
            return
        handle = wintypes.HANDLE
        dword = wintypes.DWORD
        self.pdh.PdhOpenQueryW.argtypes = (wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(handle))
        self.pdh.PdhAddEnglishCounterW.argtypes = (handle, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(handle))
        self.pdh.PdhCollectQueryData.argtypes = (handle,)
        self.pdh.PdhGetFormattedCounterArrayW.argtypes = (handle, dword, ctypes.POINTER(dword),
                                                         ctypes.POINTER(dword), ctypes.c_void_p)
        self.pdh.PdhCloseQuery.argtypes = (handle,)
        query = handle()
        if not self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)):
            self.query = query
            self.engines = self._counter(r"\GPU Engine(*)\Utilization Percentage")
            self.memory = self._counter(r"\GPU Process Memory(*)\Dedicated Usage")
            self.pdh.PdhCollectQueryData(self.query)

    def _counter(self, path):
        counter = wintypes.HANDLE()
        if not self.pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(counter)):
            return counter
        return None

    def _values(self, counter):
        if counter is None:
            return None
        size = wintypes.DWORD()
        count = wintypes.DWORD()
        get = self.pdh.PdhGetFormattedCounterArrayW
        status = get(counter, _DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
        if status == 0 and count.value == 0:
            return []
        if status & 0xFFFFFFFF != _MORE_DATA:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if get(counter, _DOUBLE, ctypes.byref(size), ctypes.byref(count), buffer):
            return None
        items = ctypes.cast(buffer, ctypes.POINTER(_Item))
        return [(items[index].name, items[index].value.value) for index in range(count.value)
                if items[index].value.status in (0, 1)]

    def read(self):
        if self.query is None or self.pdh.PdhCollectQueryData(self.query):
            return None, None
        engines = group_by_pid(self._values(self.engines), max)
        memory = group_by_pid(self._values(self.memory), lambda left, right: left + right)
        return engines, memory

    def close(self):
        if self.query is not None:
            self.pdh.PdhCloseQuery(self.query)
            self.query = None
