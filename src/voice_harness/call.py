"""Own the half-duplex call and advance it only after each worker completes."""

from collections.abc import Callable
from uuid import uuid4

from .contracts import CallInput, Cancellation, Message, Microphone, Provider, Recognizer, Speaker, Stage, VoiceError
from .dictation import capture_turn
from .reply import receive_reply


def run_call(microphone: Microphone, recognizer: Recognizer, speaker: Speaker | None,
             provider: Provider, state, settings: CallInput, stop: Cancellation, stage: Stage,
             publish: Callable[[Message], None], ready: Callable[[], None]):
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
            ready()
        while not stop.is_set():
            _turn(microphone, recognizer, speaker, provider, state, settings, stop, stage, publish)
    finally:
        try:
            if speaker is not None:
                speaker.close()
        finally:
            recognizer.close()


def _turn(microphone, recognizer, speaker, provider, state, settings, stop, stage, publish):
    turn_id = uuid4().hex
    draft = ""

    def draft_update(text):
        nonlocal draft
        draft = text
        publish(Message(turn_id, "Marc", text, "Dictating"))

    try:
        text = capture_turn(microphone, recognizer, settings, stop, stage, draft_update)
    except Exception:
        if draft:
            publish(Message(turn_id, "Marc", draft, "Not sent"))
        raise
    if stop.is_set():
        if draft:
            publish(Message(turn_id, "Marc", draft, "Not sent"))
        return
    if not text:
        return

    # Persist the one-shot claim before entering the network boundary.
    state.claim_delivery(turn_id)
    if stop.is_set():
        state.clear_delivery(turn_id)
        publish(Message(turn_id, "Marc", text, "Not sent"))
        return
    publish(Message(turn_id, "Marc", text, "Sending"))
    stage("Waiting for Kindroid reply")
    answer_id = uuid4().hex
    confirmed = False

    def reply_update(body, status):
        nonlocal confirmed
        if not confirmed:
            state.clear_delivery(turn_id)
            confirmed = True
            publish(Message(turn_id, "Marc", text, "Sent"))
        publish(Message(answer_id, "Hannah", body, status))

    try:
        receive_reply(speaker, lambda cancel: provider.send(text, cancel), stop, stage, reply_update)
        if not confirmed and not stop.is_set():
            raise VoiceError("Kindroid returned no reply. Check delivery before calling again.")
    finally:
        if not confirmed:
            publish(Message(turn_id, "Marc", text, "Check delivery"))
