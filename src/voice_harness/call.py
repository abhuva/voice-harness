"""Own a call, including one incoming draft while the prior reply is active."""

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from .contracts import Message, VoiceError
from .dictation import capture_turn
from .reply import receive_reply


@dataclass
class Turn:
    id: str = field(default_factory=lambda: uuid4().hex)
    started: Event = field(default_factory=Event)
    reply_done: Event = field(default_factory=Event)
    draft: str = ""
    submitted: bool = False
    confirmed: bool = False
    waited_for_delivery: bool = False


def run_call(microphone, recognizer, speaker, provider, state, settings, stop, stage, publish, ready):
    try:
        if stop.is_set():
            return
        stage("Preparing local speech recognition")
        recognizer.select_model(settings.model, settings.threads)
        recognizer.prepare(stop)
        if speaker is not None and not stop.is_set():
            stage("Loading Pocket TTS voice")
            speaker.prepare(stop)
        if not stop.is_set():
            stage("Opening microphone")
            with microphone.open():
                ready()
                _conversation(microphone, recognizer, speaker, provider, state, settings, stop, stage, publish)
    finally:
        try:
            if speaker is not None:
                speaker.close()
        finally:
            recognizer.close()


def _conversation(microphone, recognizer, speaker, provider, state, settings, stop, stage, publish):
    closing = Event()
    capture_failed = Event()
    previous = None

    class CallStop:
        def is_set(self):
            return stop.is_set() or closing.is_set() or capture_failed.is_set()

    cancel = CallStop()

    def collect(turn):
        def capture_stage(message):
            if not cancel.is_set() and (turn.started.is_set() or turn.reply_done.is_set()):
                stage(message)

        def draft(text):
            turn.draft = text
            publish(Message(turn.id, "Marc", text, "Dictating"))

        try:
            while not cancel.is_set():
                text = capture_turn(microphone, recognizer, settings, cancel, capture_stage, draft,
                                    speech_started=turn.started.set,
                                    reply_pending=lambda: not turn.reply_done.is_set())
                if text:
                    if not turn.reply_done.is_set():
                        turn.waited_for_delivery = True
                        stage("Draft ready · waiting for the earlier delivery check")
                    return text
            return ""
        except Exception:
            capture_failed.set()
            raise

    pending = Turn()
    pending.reply_done.set()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="call-mic") as microphone_worker:
        incoming = microphone_worker.submit(collect, pending)
        try:
            while not stop.is_set():
                text = incoming.result()
                if cancel.is_set():
                    break
                state.claim_delivery(pending.id)
                if cancel.is_set():
                    state.clear_delivery(pending.id)
                    break

                previous = pending
                previous.submitted = True
                publish(Message(previous.id, "Marc", text, "Sending"))
                stage("Waiting for Kindroid · speak to interrupt" if settings.interrupt
                      else "Waiting for Kindroid")
                if previous.waited_for_delivery:
                    microphone.discard_buffered()
                pending = Turn()
                incoming = microphone_worker.submit(collect, pending)
                interrupt = pending.started if settings.interrupt else None
                try:
                    _receive(provider, speaker, text, previous, interrupt, state, cancel, stage, publish)
                finally:
                    pending.reply_done.set()
                if not cancel.is_set() and not pending.started.is_set():
                    stage("Listening · waiting for speech")
        finally:
            closing.set()
            # Join the mic and decoder before publishing their last visible draft status.
            # An exception surfaced from the exchange or incoming.result() stays the primary error.
            try:
                incoming.result()
            except Exception:
                pass
            if pending.draft and not pending.submitted:
                held = previous is not None and not previous.confirmed
                status = "Held · check earlier delivery" if held else "Not sent"
                publish(Message(pending.id, "Marc", pending.draft, status))


def _receive(provider, speaker, text, sent, interrupt, state, stop, stage, publish):
    answer_id = uuid4().hex

    def reply_update(body, status):
        if not sent.confirmed:
            state.clear_delivery(sent.id)
            sent.confirmed = True
            publish(Message(sent.id, "Marc", text, "Sent"))
        publish(Message(answer_id, "Hannah", body, status))

    try:
        receive_reply(speaker, lambda cancel: provider.send(text, cancel), stop, stage, reply_update,
                      interrupt=interrupt)
        if not sent.confirmed and not stop.is_set():
            raise VoiceError("Kindroid returned no reply. Check delivery before calling again.")
    finally:
        if not sent.confirmed:
            publish(Message(sent.id, "Marc", text, "Check delivery"))
