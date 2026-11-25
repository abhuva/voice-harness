"""Join the remote reply stream with bounded sentence-ahead playback."""

from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Full, Queue
import re
from threading import Event
import time

from .contracts import VoiceError


_BREAK = re.compile(r"""[.!?…]+["'”’)]*(?=\s)|[\r\n]+""")
_MAX_SEGMENT = 480


def receive_reply(speaker, open_stream, stop, stage, update, *, interrupt=None):
    speech = Queue(maxsize=8)
    failed = Event()
    input_done = Event()
    confirmed = Event()
    prompt = " · speak to interrupt" if interrupt is not None else ""
    interrupt = interrupt if interrupt is not None else Event()

    class ProviderStop:
        def is_set(self):
            return stop.is_set() or failed.is_set() or (interrupt.is_set() and confirmed.is_set())

    class PlaybackStop:
        def is_set(self):
            return failed.is_set() or stop.is_set() or interrupt.is_set()

    token = PlaybackStop()

    def reply_stage(message):
        if not token.is_set():
            stage(message)

    def take_segment(cancel):
        while not cancel.is_set():
            try:
                return speech.get(timeout=0.075)
            except Empty:
                if input_done.is_set():
                    return None

    def play():
        try:
            speaker.speak(take_segment, token, reply_stage)
        except Exception:
            failed.set()
            raise

    reply = pending = ""
    last_update = 0.0
    playing = None
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="reply-playback") as player:
        def enqueue(piece):
            nonlocal playing
            if not piece or token.is_set():
                return
            if playing is None:
                reply_stage("Generating first reply audio" + prompt)
                playing = player.submit(play)
            while not token.is_set():
                if playing.done():
                    playing.result()
                    raise VoiceError("Playback ended before the reply was complete.")
                try:
                    speech.put(piece, timeout=0.075)
                    return
                except Full:
                    continue

        def queue_segments(text):
            while text and not token.is_set():
                match = _BREAK.search(text, 0, _MAX_SEGMENT + 1)
                if match and match.end() <= _MAX_SEGMENT:
                    end = match.end()
                elif len(text) > _MAX_SEGMENT:
                    end = text.rfind(" ", 1, _MAX_SEGMENT) + 1 or _MAX_SEGMENT
                else:
                    break
                enqueue(text[:end].strip())
                text = text[end:]
            return text

        try:
            stream = open_stream(ProviderStop())
            try:
                for delta in stream:
                    if stop.is_set() or failed.is_set():
                        break
                    if not delta:
                        continue
                    if not reply:
                        reply_stage("Receiving Kindroid reply" + prompt)
                    reply += delta
                    if len(reply) > 512 * 1024:
                        raise VoiceError("Kindroid reply exceeded the display limit.")
                    now = time.monotonic()
                    if not confirmed.is_set() or now - last_update >= 0.05:
                        update(reply, "Interrupted" if interrupt.is_set() else "Receiving")
                        confirmed.set()
                        last_update = now
                    if interrupt.is_set():
                        break
                    if speaker is not None:
                        pending = queue_segments(pending + delta)
            finally:
                stream.close()
            if speaker is not None:
                enqueue(pending.strip())
            input_done.set()
            if playing is not None:
                reply_stage("Finishing reply playback" + prompt)
                playing.result()
            if reply:
                status = "Stopped" if stop.is_set() else "Interrupted" if interrupt.is_set() else ""
                update(reply, status)
        except Exception:
            failed.set()
            if reply:
                update(reply, "Incomplete reply")
            raise
        finally:
            input_done.set()
