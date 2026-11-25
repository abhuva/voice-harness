"""Transcribe bounded chunks while the microphone continues the turn."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from .chunks import PauseChunks
from .contracts import VoiceError


def capture_turn(microphone, recognizer, settings, stop, stage, draft, *, speech_started, reply_pending):
    parts = []
    previous = None
    pending = deque()
    abandoned = Event()
    chars = 0

    def decode(samples, paragraph):
        nonlocal previous, chars
        if stop.is_set() or abandoned.is_set():
            return
        text = recognizer.transcribe(samples).strip()
        if text and not stop.is_set() and not abandoned.is_set():
            separator = ("\n\n" if previous != paragraph else " ") if parts else ""
            chars += len(separator) + len(text)
            if chars > 16_384:
                raise VoiceError("The turn exceeded the transcript limit and was not sent.")
            parts.extend((separator, text))
            previous = paragraph
            draft("".join(parts))

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="dictation") as decoder:
        def submit(samples, paragraph):
            pending.append(decoder.submit(decode, samples, paragraph))

        chunks = PauseChunks(settings.chunk, settings.paragraph, submit)

        def accept(block, active):
            while pending and pending[0].done():
                pending.popleft().result()
            if len(pending) >= 32:
                raise VoiceError("Dictation fell behind. This turn was not sent.")
            chunks.accept(block, active)

        try:
            ended = microphone.capture(settings, stop, stage, accept,
                                       speech_started=speech_started, reply_pending=reply_pending)
            if not ended or stop.is_set():
                return ""
            stage("Finishing your transcript")
            chunks.flush()
            while pending:
                pending.popleft().result()
        finally:
            abandoned.set()
            for job in pending:
                job.cancel()

    return "" if stop.is_set() else "".join(parts)
