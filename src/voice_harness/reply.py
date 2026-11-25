"""Join the remote reply stream with bounded sentence-ahead playback."""

from concurrent.futures import ThreadPoolExecutor
from queue import Empty, Full, Queue
import re
from threading import Event
import time

from .contracts import VoiceError


_BREAK = re.compile(r"""[.!?…]+["'”’)]*(?=\s)|[\r\n]+""")
_MAX_SEGMENT = 480


def receive_reply(speaker, open_stream, stop, stage, update):
    speech = Queue(maxsize=8)
    failed = Event()
    input_done = Event()

    class PlaybackStop:
        def is_set(self):
            return failed.is_set() or stop.is_set()

    token = PlaybackStop()

    def take_segment(cancel):
        while not cancel.is_set():
            try:
                return speech.get(timeout=0.075)
            except Empty:
                if input_done.is_set():
                    return None

    def play():
        try:
            speaker.speak(take_segment, token, stage)
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
                stage("Generating first reply audio")
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

        stream = open_stream(token)
        try:
            for delta in stream:
                if token.is_set():
                    break
                if not reply:
                    stage("Receiving Kindroid reply")
                reply += delta
                if len(reply) > 512 * 1024:
                    raise VoiceError("Kindroid reply exceeded the display limit.")
                now = time.monotonic()
                if now - last_update >= 0.05:
                    update(reply, "Receiving")
                    last_update = now
                if speaker is not None:
                    pending += delta
                    while pending and not token.is_set():
                        match = _BREAK.search(pending, 0, _MAX_SEGMENT + 1)
                        if match and match.end() <= _MAX_SEGMENT:
                            end = match.end()
                        elif len(pending) > _MAX_SEGMENT:
                            end = pending.rfind(" ", 1, _MAX_SEGMENT) + 1 or _MAX_SEGMENT
                        else:
                            break
                        enqueue(pending[:end].strip())
                        pending = pending[end:]
            if speaker is not None:
                enqueue(pending.strip())
            input_done.set()
            if playing is not None:
                if not token.is_set():
                    stage("Finishing reply playback")
                playing.result()
            if reply:
                update(reply, "Stopped" if stop.is_set() else "")
        except Exception:
            failed.set()
            if reply:
                update(reply, "Incomplete reply")
            raise
        finally:
            stream.close()
