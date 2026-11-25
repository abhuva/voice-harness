"""Data and capability contracts for the call workflow."""

from dataclasses import dataclass
from collections.abc import Callable, Iterator
from typing import Protocol


class VoiceError(Exception):
    """An error message safe to show to the operator."""


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


Stage = Callable[[str], None]
Meter = Callable[[float, float], None]


@dataclass(frozen=True)
class CallInput:
    model: str = "multilingual"
    threads: int = 4
    threshold: int = -50
    chunk: float = 1.0
    paragraph: float = 2.0
    finish: float = 4.0


@dataclass(frozen=True)
class Message:
    id: str
    speaker: str
    body: str
    status: str = ""
    origin: str = "API"


class Microphone(Protocol):
    def capture(self, settings: CallInput, stop: Cancellation, stage: Stage,
                accept: Callable[[object, bool], None]) -> bool: ...


class Recognizer(Protocol):
    def select_model(self, model: str, threads: int) -> None: ...
    def prepare(self, stop: Cancellation) -> None: ...
    def transcribe(self, pcm: object) -> str: ...
    def close(self) -> None: ...


class Speaker(Protocol):
    def prepare(self, stop: Cancellation) -> None: ...
    def speak(self, next_segment: Callable[[Cancellation], str | None],
              stop: Cancellation, stage: Stage) -> None: ...
    def close(self) -> None: ...


class ReplyStream(Iterator[str], Protocol):
    def close(self) -> None: ...


class Provider(Protocol):
    def send(self, message: str, stop: Cancellation) -> ReplyStream: ...
