"""Own a call, including one incoming draft while the prior reply is active."""

from contextlib import nullcontext
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from .contracts import Message, MicrophoneDropout, TurnRequest, VoiceError
from .dictation import capture_turn
from .reply import receive_reply
from .telemetry import TurnTiming, online
from .work_types import Lineage


@dataclass
class Turn:
    id: str = field(default_factory=lambda: uuid4().hex)
    started: Event = field(default_factory=Event)
    reply_done: Event = field(default_factory=Event)
    draft: str = ""
    submitted: bool = False
    confirmed: bool = False
    delivery: str = "uncertain"
    waited_for_delivery: bool = False
    timing: TurnTiming | None = None


def run_call(microphone, open_recognizer, speaker, provider, state, settings, stop, stage, publish, ready,
             *, work=None, completed=lambda _message: None):
    """Own devices and turn order and obtain a recognizer for each utterance."""
    if not stop.is_set():
        stage("Opening microphone")
        telemetry = work.harness.telemetry if work else None
        with microphone.open(), online(telemetry, "Call orchestrator", "Microphone / speech detection"):
            ready()
            _conversation(microphone, open_recognizer, speaker, provider, state, settings, stop, stage,
                          publish, work, completed, telemetry)


def _conversation(microphone, open_recognizer, speaker, provider, state, settings, stop, stage,
                  publish, work, completed, telemetry):
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

        def speech_started():
            turn.started.set()
            if work:
                work.activity("dictation", True)
                telemetry.add_turn(Lineage(work.job.id, work.job.id, turn.id), turn.timing)

        def draft(text):
            turn.draft = text
            publish(Message(turn.id, "Marc", text, "Dictating"))

        def recognize(stop_token, status):
            return open_recognizer(turn.id, stop_token, status)

        try:
            while not cancel.is_set():
                turn.timing = TurnTiming() if telemetry is not None else None
                try:
                    text = capture_turn(microphone, recognize, settings, cancel, capture_stage, draft,
                                        speech_started=speech_started,
                                        reply_pending=lambda: not turn.reply_done.is_set(), timing=turn.timing)
                except MicrophoneDropout:
                    publish(Message(turn.id, "Marc", turn.draft, "Not sent · microphone dropout"))
                    turn.id, turn.draft = uuid4().hex, ""
                    continue
                finally:
                    if work and turn.started.is_set():
                        work.activity("dictation", False)
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
                    attempted = False
                    try:
                        with (work.reply(previous.id) if work else nullcontext()) as context:
                            timing = previous.timing
                            request = TurnRequest(
                                text, context.refs if context else (), context.read if context else None,
                                on_submit=(lambda at: timing.mark("send", at)) if timing else None,
                                on_first_text=(lambda: timing.mark("first_text")) if timing else None,
                            )
                            attempted = True
                            _receive(provider, speaker, request, previous, interrupt, state,
                                     cancel, stage, publish, work=work, completed=completed, telemetry=telemetry)
                            if work and not previous.confirmed:
                                raise VoiceError("Delivery was interrupted. Review the conversation.")
                    except Exception:
                        if not attempted:
                            state.clear_delivery(previous.id)
                            previous.delivery = "not_sent"
                            publish(Message(previous.id, "Marc", text, "Not sent"))
                        if not cancel.is_set():
                            raise
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
                held = previous is not None and previous.delivery == "uncertain"
                status = "Held · check earlier delivery" if held else "Not sent"
                publish(Message(pending.id, "Marc", pending.draft, status))


def _receive(provider, speaker, request, sent, interrupt, state, stop, stage, publish, *,
             work, completed, telemetry):
    answer_id = uuid4().hex

    def delivery(status):
        sent.delivery = status
        if status in ("confirmed", "not_sent"):
            state.clear_delivery(sent.id)
        if status == "confirmed":
            sent.confirmed = True
            if work:
                work.consumed(request.context)
            message = Message(sent.id, "Marc", request.text, "Sent")
            publish(message)
            completed(message)

    def reply_update(body, status):
        publish(Message(answer_id, "Hannah", body, status))

    def reply_complete(body):
        completed(Message(answer_id, "Hannah", body))

    try:
        with online(telemetry, "Kindroid connector"):
            receive_reply(speaker, lambda cancel: provider.send(request, cancel), stop, stage, reply_update,
                          interrupt=interrupt, delivery=delivery, complete_text=reply_complete,
                          timing=sent.timing, telemetry=telemetry)
        if not sent.confirmed and not stop.is_set():
            raise VoiceError("Kindroid returned no reply. Check delivery before calling again.")
    finally:
        if not sent.confirmed:
            status = "Not sent" if sent.delivery == "not_sent" else "Check delivery"
            publish(Message(sent.id, "Marc", request.text, status))
