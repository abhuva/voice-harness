"""Per-launch phone PIN and bounded pairing attempts."""

from collections import deque
from hmac import compare_digest
from secrets import randbelow
from threading import Lock
from time import monotonic

from .contracts import VoiceError


class PhonePairing:
    def __init__(self, clock=monotonic):
        self._pin = f"{randbelow(1_000_000):06d}"
        self._clock = clock
        self._failed = deque()
        self._lock = Lock()

    @property
    def pin(self):
        with self._lock:
            return self._pin

    def set_pin(self, pin):
        if not isinstance(pin, str) or not 4 <= len(pin) <= 8 or not pin.isascii() or not pin.isdecimal():
            raise VoiceError("Enter 4 to 8 digits.")
        with self._lock:
            self._pin = pin
            self._failed.clear()

    def verify(self, pin):
        with self._lock:
            now = self._clock()
            while self._failed and now - self._failed[0] >= 60:
                self._failed.popleft()
            if len(self._failed) >= 5:
                raise VoiceError("Too many incorrect PINs. Wait one minute or set a PIN on the laptop.")
            if not isinstance(pin, str) or not pin.isascii() or not compare_digest(pin, self._pin):
                self._failed.append(now)
                raise VoiceError("Check the pairing PIN in the laptop’s Phone tab.")
