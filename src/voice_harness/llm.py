"""Saved reply profiles and the stateless chat completions connector."""

from dataclasses import dataclass
from collections import deque
import json
import re
from time import monotonic
from urllib.parse import urlsplit

from .contracts import DeliveryConfirmed, ReplyCompleted, ReplyFailed, ReplyText, VoiceError
from .provider import PostRejected, post_packets


OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.5-flash-lite"
KINDROID = "Kindroid"
MAX_REPLY = 512 * 1024


@dataclass(frozen=True)
class LlmProfile:
    name: str = "Gemini Fast"
    endpoint: str = OPENROUTER
    model: str = MODEL
    stream: bool = True
    temperature: float = 0.6
    max_tokens: int = 512
    system_prompt: str = ""
    credential: str = "OPENROUTER_API_KEY"

    def validate(self, *, system_limit=8192):
        if not isinstance(self.name, str) or not re.fullmatch(r"[^\x00-\x1f\x7f]{1,60}", self.name) or self.name.strip() != self.name or self.name.casefold() == KINDROID.casefold():
            raise VoiceError("Choose a unique profile name of up to 60 characters.")
        if not isinstance(self.model, str) or not re.fullmatch(r"[^\s\x00-\x1f]{1,200}", self.model):
            raise VoiceError("Enter a model ID.")
        if type(self.stream) is not bool or type(self.temperature) not in (float, int) or not 0 <= self.temperature <= 2:
            raise VoiceError("Choose a temperature from 0 to 2.")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 8192:
            raise VoiceError("Set the reply token limit between 1 and 8192.")
        if not isinstance(self.system_prompt, str) or len(self.system_prompt) > system_limit:
            raise VoiceError(f"Use up to {system_limit:,} characters for the system prompt.")
        if not isinstance(self.endpoint, str) or len(self.endpoint) > 2048 or not isinstance(self.credential, str):
            raise VoiceError("Enter a chat completions endpoint and credential name.")
        try:
            url = urlsplit(self.endpoint)
            port = url.port
        except ValueError:
            raise VoiceError("Enter a valid chat completions URL.") from None
        if (not url.hostname or url.username is not None or url.query or url.fragment or
                not url.path.endswith("/chat/completions") or
                not (url.scheme == "https" or url.scheme == "http" and url.hostname in ("127.0.0.1", "::1", "localhost"))):
            raise VoiceError("Use an HTTPS chat completions URL or an HTTP loopback endpoint.")
        if self.credential and (self.credential.startswith("KINDROID_") or
                                not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,79}", self.credential)):
            raise VoiceError("Enter the name of a key in the local .env file.")
        if self.credential == "OPENROUTER_API_KEY" and (url.hostname != "openrouter.ai" or url.scheme != "https" or port not in (None, 443)):
            raise VoiceError("Choose a separate credential name for a custom endpoint.")
        if self.credential and url.scheme != "https":
            raise VoiceError("Use HTTPS for endpoints that receive an API key.")
        return self


def parse_profiles(section):
    if section is None:
        profile = LlmProfile()
        return {profile.name: profile}, KINDROID
    try:
        active = section["active"]
        rows = [LlmProfile(**values).validate() for values in section["profiles"]]
        profiles = {profile.name: profile for profile in rows}
        if len({name.casefold() for name in profiles}) != len(rows) or len(rows) > 32:
            raise ValueError
        if active != KINDROID and active not in profiles:
            raise ValueError
        return profiles, active
    except (KeyError, TypeError, ValueError, VoiceError):
        raise VoiceError("Could not read the saved LLM profiles.") from None


def chunks(packets, streaming, stop, *, on_reasoning=None):
    size = 0
    pending = bytearray()
    finished = False

    def content(message):
        reasoning = message.get("reasoning_content")
        if on_reasoning is not None and isinstance(reasoning, str):
            on_reasoning(reasoning)
        return message.get("content")

    try:
        for packet in packets:
            if stop.is_set():
                return
            size += len(packet)
            if size > MAX_REPLY:
                raise VoiceError("The LLM reply exceeded the size limit.")
            pending.extend(packet)
            if not streaming:
                continue
            while (end := pending.find(b"\n")) >= 0:
                line = bytes(pending[:end]).strip()
                del pending[:end + 1]
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    return
                body = json.loads(payload)
                if body.get("error"):
                    raise VoiceError("The LLM interrupted the reply.")
                for choice in body.get("choices", ()):
                    if choice.get("index", 0) == 0:
                        text = content(choice.get("delta", {}))
                        if text:
                            if not isinstance(text, str):
                                raise ValueError
                            yield text
                        finished |= choice.get("finish_reason") in ("stop", "length")
        if stop.is_set():
            return
        if streaming:
            if not finished:
                raise VoiceError("The LLM reply ended before completion.")
        else:
            body = json.loads(pending)
            text = content(body["choices"][0]["message"])
            if text is None and on_reasoning is not None:
                return
            if not isinstance(text, str):
                raise ValueError
            yield text
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        raise VoiceError("The LLM returned an unreadable chat reply.") from None


def separate_thinking(texts, on_reasoning):
    """Separate raw <think> blocks when a local server leaves them inside content."""
    pending = ""
    thinking = False
    for text in texts:
        pending += text
        while pending:
            marker = "</think>" if thinking else "<think>"
            at = pending.find(marker)
            if at >= 0:
                piece, pending = pending[:at], pending[at + len(marker):]
            else:
                hold = max((n for n in range(1, len(marker)) if pending.endswith(marker[:n])), default=0)
                end = len(pending) - hold
                piece, pending = pending[:end], pending[end:]
            if piece:
                if thinking:
                    on_reasoning(piece)
                else:
                    yield piece
            if at < 0:
                break
            thinking = not thinking
    if pending:
        if thinking:
            on_reasoning(pending)
        else:
            yield pending


class ChatClient:
    def __init__(self, profile, key, *, manager=None, post=post_packets, sampling=None):
        if manager is None:
            from PyQt6.QtNetwork import QNetworkAccessManager
            manager = QNetworkAccessManager()
        self.manager, self.profile, self.key, self.post = manager, profile, key, post
        self.sampling = dict(sampling or {})
        self.label = profile.name
        self.speaker = f"LLM · {profile.name}"
        self.history = deque(maxlen=8)

    def send(self, request, stop, *, on_reasoning=None):
        profile = self.profile
        history = []
        budget = 24 * 1024
        for user, assistant in reversed(self.history):
            budget -= len(user) + len(assistant)
            if budget < 0:
                break
            history[0:0] = [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
        if profile.system_prompt:
            history.insert(0, {"role": "system", "content": profile.system_prompt})
        history.append({"role": "user", "content": request.text})
        payload = {**self.sampling, "model": profile.model, "messages": history, "stream": profile.stream,
                   "temperature": profile.temperature, "max_tokens": profile.max_tokens}
        packets = self.post(self.manager, profile.endpoint, self.key, payload, stop,
                            service=profile.name, on_submit=request.on_submit)
        parts = []
        try:
            reply = chunks(packets, profile.stream, stop, on_reasoning=on_reasoning)
            if on_reasoning is not None:
                reply = separate_thinking(reply, on_reasoning)
            for text in reply:
                if not parts:
                    if not text.strip():
                        continue
                    if request.on_first_text is not None:
                        request.on_first_text()
                    yield DeliveryConfirmed()
                parts.append(text)
                yield ReplyText(text)
            if stop.is_set():
                return
            if not parts:
                raise VoiceError("The LLM returned no reply text.")
            self.history.append((request.text, "".join(parts)))
            yield ReplyCompleted()
        except PostRejected as exc:
            rejected = exc.status in (400, 401, 402, 403, 404, 413, 422, 429)
            yield ReplyFailed(str(exc), "confirmed" if parts else "not_sent" if rejected else "uncertain")
        except VoiceError as exc:
            yield ReplyFailed(str(exc), "confirmed" if parts else "uncertain")
        finally:
            packets.close()
