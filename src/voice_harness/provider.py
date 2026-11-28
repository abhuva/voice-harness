"""Bounded Kindroid API operations owned by the job thread."""

import codecs
from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import time

from .contracts import DeliveryConfirmed, ReplyCompleted, ReplyFailed, ReplyText, VoiceError


BASE = "https://api.kindroid.ai/v1/"
ERROR_MARKERS = ("<<ERROR>>", "<<HTTP_ERROR>>")
HISTORY_SPACING = 15
HISTORY_LOOKBACK_HOURS = 24


def stream_reply(manager, key, kin_id, message, stop, *, on_submit=None):
    payload = {"ai_id": kin_id, "message": message, "stream": True}
    packets = post_packets(manager, BASE + "send-message", key, payload, stop, on_submit=on_submit,
                           rejection=lambda status: PostRejected(
                               status, f"Kindroid returned HTTP {status}. Check delivery before calling again."))
    try:
        yield from decode_reply(packets, stop)
    finally:
        packets.close()


class PostRejected(VoiceError):
    def __init__(self, status, message=None):
        self.status = status
        super().__init__(message or f"The reply endpoint returned HTTP {status}.")


def post_packets(manager, endpoint, key, payload, stop, *, on_submit=None, service="Kindroid", rejection=PostRejected):
    """A cancellable POST with a non-rewindable request body."""
    from PyQt6.QtCore import QEventLoop, QIODevice, QTimer, QUrl
    from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest

    if stop.is_set():
        return

    class OneShotUpload(QIODevice):
        # Qt can try to replay an upload after a disconnect. Give it no way
        # to rewind the body once the server may have received the message.
        def __init__(self, data):
            super().__init__()
            self.remaining = data
            self.open(QIODevice.OpenModeFlag.ReadOnly)

        def isSequential(self):
            return True

        def bytesAvailable(self):
            return len(self.remaining) + super().bytesAvailable()

        def readData(self, size):
            block, self.remaining = self.remaining[:size], self.remaining[size:]
            return block

        def reset(self):
            return False

        def seek(self, position):
            return False

    data = json.dumps(payload).encode()
    upload = OneShotUpload(data)
    request = QNetworkRequest(QUrl(endpoint))
    if key:
        request.setRawHeader(b"Authorization", b"Bearer " + key.encode("ascii"))
    request.setRawHeader(b"Content-Type", b"application/json")
    request.setHeader(QNetworkRequest.KnownHeaders.ContentLengthHeader, len(data))
    request.setAttribute(QNetworkRequest.Attribute.DoNotBufferUploadDataAttribute, True)
    request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
    request.setTransferTimeout(180_000)
    if on_submit is not None:
        on_submit(time.monotonic())
    response = manager.post(request, upload)
    upload.setParent(response)
    response.setReadBufferSize(65536)
    loop = QEventLoop()
    response.readyRead.connect(loop.quit)
    response.finished.connect(loop.quit)
    tick = QTimer()
    tick.timeout.connect(loop.quit)
    tick.start(75)

    deadline = time.monotonic() + 180

    def packets():
        while not stop.is_set():
            if time.monotonic() >= deadline:
                raise VoiceError(f"The {service} reply timed out. Check the conversation.")
            status = response.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if status is not None and status != 200:
                raise rejection(status)
            if response.bytesAvailable() and status == 200:
                yield bytes(response.readAll())
            elif response.isFinished():
                if response.error() != QNetworkReply.NetworkError.NoError:
                    raise VoiceError(f"Delivery is uncertain. Check the {service} conversation before calling again.")
                return
            else:
                loop.exec()
    try:
        yield from packets()
    finally:
        tick.stop()
        if not response.isFinished():
            response.abort()
        response.deleteLater()


def decode_reply(packets, stop):
    decoder = codecs.getincrementaldecoder("utf-8")()
    held = ""
    started = False
    size = 0
    try:
        for packet in packets:
            size += len(packet)
            if size > 512 * 1024:
                raise VoiceError("Kindroid reply exceeded the display limit. Check the conversation.")
            text = held + decoder.decode(packet)
            if not started:
                text = text.lstrip()
            errors = [pos for marker in ERROR_MARKERS if (pos := text.find(marker)) >= 0]
            if errors:
                if prefix := text[:min(errors)]:
                    yield prefix
                raise VoiceError("Kindroid interrupted the reply. Check delivery before calling again.")
            keep = max((n for marker in ERROR_MARKERS for n in range(1, len(marker))
                        if text.endswith(marker[:n])), default=0)
            held = text[-keep:] if keep else ""
            safe = text[:-keep] if keep else text
            if safe:
                started = True
                yield safe
        if stop.is_set():
            return
        held += decoder.decode(b"", final=True)
        if held:
            yield held
        elif not started:
            raise VoiceError("Kindroid returned no reply. Check delivery before calling again.")
    except UnicodeError:
        raise VoiceError("Kindroid interrupted the reply. Check delivery before calling again.") from None


def history_rows(payload, start_after):
    messages = payload["messages"]
    if not isinstance(messages, list) or len(messages) > 100:
        raise ValueError
    rows = []
    seen = set()
    for item in messages:
        timestamp = item.get("timestamp")
        if not isinstance(timestamp, int) or timestamp <= start_after:
            continue
        ident = item.get("id")
        body = item.get("message")
        if not isinstance(body, str) or not body.strip() or (ident and ident in seen):
            continue
        if ident:
            seen.add(ident)
        role = item.get("sender_type") or item.get("sender")
        speaker = {"user": "Marc", "ai": "Hannah"}.get(role, "Unknown speaker")
        if speaker == "Marc" and body.lstrip().startswith("[CODEX]:"):
            speaker = "Codex"
        source = {"api": "API", "kindroid_text": "TK", "voice_call": "VK"}.get(item.get("source"), "?")
        if item.get("type") == "call_message" or item.get("voice_call_message_id"):
            source = "VK"
        rows.append((speaker, source, body))
    return rows[-10:], bool(payload.get("pagination", {}).get("hasMore"))


def read_history(get, key, kin_id, guard, stop):
    """Read one recent page per launch, using Kindroid's forward timestamp cursor."""
    import requests

    def defer(seconds):
        tmp = guard.with_suffix(".tmp")
        tmp.write_text(str(time.time() + seconds), encoding="ascii")
        tmp.replace(guard)

    try:
        if guard.exists() and float(guard.read_text(encoding="ascii")) > time.time():
            raise VoiceError("History read deferred by the request spacing or provider cooldown.")
        if stop.is_set():
            return [], False
        start_after = int((time.time() - HISTORY_LOOKBACK_HOURS * 3600) * 1000)
        defer(HISTORY_SPACING)
        with get(BASE + "get-chat-messages",
                 params={"ai_id": kin_id, "limit": 100, "start_after_timestamp": start_after},
                 headers={"Authorization": "Bearer " + key}, allow_redirects=False,
                 timeout=(8, 15), stream=True) as response:
            if response.status_code == 429:
                delay = 3600
                retry = response.headers.get("Retry-After", "").strip()
                if retry.isdigit():
                    delay = max(1, int(retry))
                elif retry:
                    try:
                        date = parsedate_to_datetime(retry)
                        if date.tzinfo is None:
                            date = date.replace(tzinfo=timezone.utc)
                        delay = max(1, date.timestamp() - time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
                # Retry-After can specify a shorter wait than local spacing.
                defer(max(HISTORY_SPACING, delay))
            if response.status_code != 200:
                raise VoiceError(f"History unavailable · Kindroid HTTP {response.status_code}.")
            body = bytearray()
            for chunk in response.iter_content(16384):
                if stop.is_set():
                    return [], False
                body.extend(chunk)
                if len(body) > 4 * 1024 * 1024:
                    raise VoiceError("History page exceeded the display limit.")
            return history_rows(json.loads(body), start_after)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, requests.RequestException):
        raise VoiceError("Could not load the Kindroid history page.") from None


def reply_events(chunks, stop, *, on_first_text=None):
    """Translate Kindroid's first valid text into its delivery acknowledgment."""
    confirmed = False
    try:
        for text in chunks:
            if not confirmed:
                if on_first_text is not None:
                    on_first_text()
                confirmed = True
                yield DeliveryConfirmed()
            yield ReplyText(text)
        if not stop.is_set():
            yield ReplyCompleted()
    except VoiceError as exc:
        yield ReplyFailed(str(exc), "confirmed" if confirmed else "uncertain")
    finally:
        chunks.close()


class KindroidClient:
    def __init__(self, key, kin):
        from PyQt6.QtNetwork import QNetworkAccessManager

        self.key = key
        self.kin = kin
        self.manager = QNetworkAccessManager()

    def send(self, request, stop):
        chunks = stream_reply(self.manager, self.key, self.kin, request.text, stop,
                              on_submit=request.on_submit)
        return reply_events(chunks, stop, on_first_text=request.on_first_text)

    def history(self, guard, stop):
        import requests

        with requests.Session() as http:
            http.trust_env = False
            return read_history(http.get, self.key, self.kin, guard, stop)
