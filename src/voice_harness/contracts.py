"""Data and capability contracts for the call workflow."""

from dataclasses import dataclass, field
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Literal, Protocol

from .work_types import ArtifactRef


class VoiceError(Exception):
    """An error message safe to show to the operator."""


class MicrophoneDropout(VoiceError):
    """The current utterance lost audio while the capture device stayed open."""


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


Stage = Callable[[str], None]
Meter = Callable[[float, float], None]
PRE_ROLL_SECONDS = 2


@dataclass(frozen=True)
class CallInput:
    model: str = "multilingual"
    threads: int = 4
    threshold: int = -50
    chunk: float = 1.0
    paragraph: float = 2.0
    finish: float = 4.0
    interrupt: bool = True
    stt_idle: int = 60
    barge_in: float = 3.0
    stt_on_demand: bool = True
    stt_backend: Literal["cpu", "vulkan"] = "cpu"


@dataclass(frozen=True)
class Message:
    id: str
    speaker: str
    body: str
    status: str = ""
    origin: str = "API"


class Microphone(Protocol):
    def open(self) -> AbstractContextManager: ...
    def discard_buffered(self) -> None: ...
    def capture(self, settings: CallInput, stop: Cancellation, stage: Stage,
                accept: Callable[[object, bool], None], *,
                speech_started: Callable[[], None], reply_pending: Callable[[], bool]) -> bool: ...


class Recognizer(Protocol):
    def select_model(self, model: str, threads: int) -> None: ...
    def prepare(self, stop: Cancellation) -> None: ...
    def transcribe(self, pcm: object) -> str: ...
    def close(self) -> None: ...


class Speaker(Protocol):
    def prepare(self, stop: Cancellation) -> None: ...
    def speak(self, next_segment: Callable[[Cancellation], str | None],
              stop: Cancellation, stage: Stage, *,
              first_audio: Callable[[float], None] | None = None) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class PCM:
    rate: int
    data: bytes


class Synthesizer(Protocol):
    def prepare(self, stop: Cancellation) -> None: ...
    def render(self, text: str, stop: Cancellation) -> PCM | None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class TurnRequest:
    text: str
    context: tuple[ArtifactRef, ...] = ()
    read_context: Callable[[ArtifactRef], bytes] | None = None
    on_submit: Callable[[float], None] | None = field(default=None, compare=False, repr=False)
    on_first_text: Callable[[], None] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class DeliveryConfirmed:
    pass


@dataclass(frozen=True)
class ReplyText:
    text: str


@dataclass(frozen=True)
class ReplyCompleted:
    pass


@dataclass(frozen=True)
class ReplyFailed:
    message: str
    delivery: Literal["confirmed", "uncertain", "not_sent"] = "uncertain"


ReplyEvent = DeliveryConfirmed | ReplyText | ReplyCompleted | ReplyFailed


class ReplyStream(Iterator[ReplyEvent], Protocol):
    def close(self) -> None: ...


class Provider(Protocol):
    def send(self, request: TurnRequest, stop: Cancellation) -> ReplyStream: ...
