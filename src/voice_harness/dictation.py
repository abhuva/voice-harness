"""Transcribe bounded chunks while the microphone continues the turn."""

from collections import deque
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from .chunks import PauseChunks
from .contracts import VoiceError


def capture_turn(microphone, open_recognizer, settings, stop, stage, draft, *, speech_started, reply_pending,
                 timing=None):
    parts = []
    previous = None
    pending = deque()
    abandoned = Event()
    captured = Event()
    opening = None
    chars = 0

    def decode(samples, paragraph):
        nonlocal previous, chars
        if stop.is_set() or abandoned.is_set():
            return
        recognizer = opening.result()
        if recognizer is None or stop.is_set() or abandoned.is_set():
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

    lease = ExitStack()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="dictation") as decoder:
        def load():
            recognizer = lease.enter_context(open_recognizer(stop, stage))
            if recognizer is not None and not stop.is_set() and not abandoned.is_set():
                stage("Finishing your transcript" if captured.is_set() else "Recording · pause to finish")
            return recognizer

        def begin():
            nonlocal opening
            speech_started()
            stage("Recording · pause to finish")
            opening = decoder.submit(load)

        def submit(samples, paragraph):
            pending.append(decoder.submit(decode, samples, paragraph))

        chunks = PauseChunks(settings.chunk, settings.paragraph, submit)

        def accept(block, active):
            if opening is not None and opening.done():
                opening.result()
            while pending and pending[0].done():
                pending.popleft().result()
            if len(pending) >= 32:
                raise VoiceError("Dictation fell behind. This turn was not sent.")
            chunks.accept(block, active)

        try:
            ended = microphone.capture(settings, stop, stage, accept,
                                       speech_started=begin, reply_pending=reply_pending)
            if not ended or stop.is_set() or opening is None:
                return ""
            if timing is not None:
                at = getattr(microphone, "last_speech_at", None)
                if isinstance(at, (float, int)):
                    timing.mark("voice_end", at)
            captured.set()
            stage("Audio buffered · preparing speech recognition" if not opening.done() else "Finishing your transcript")
            chunks.flush()
            opening.result()
            while pending:
                pending.popleft().result()
        finally:
            abandoned.set()
            if opening is not None:
                opening.cancel()
            for job in pending:
                job.cancel()
            # A native decode or load can still be settling after Stop.
            decoder.submit(lease.close).result()

    if stop.is_set() or not parts:
        return ""
    if timing is not None:
        timing.mark("transcript_ready")
    return "".join(parts)
