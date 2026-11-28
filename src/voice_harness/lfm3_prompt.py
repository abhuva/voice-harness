"""Read lab prompt files and check the formatted request against llama.cpp's context."""

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests

from .contracts import VoiceError


SYSTEM_LIMIT = 1024 * 1024
METADATA_LIMIT = 4 * 1024 * 1024


def read_system_file(path):
    try:
        with Path(path).open("rb") as source:
            data = source.read(SYSTEM_LIMIT + 1)
        if len(data) > SYSTEM_LIMIT:
            raise VoiceError("Choose a prompt file of up to 1 MiB.")
        return data.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise VoiceError("Could not read the system prompt file. Use UTF-8 text.") from None


@dataclass(frozen=True)
class PromptBudget:
    prompt: int
    context: int

    def display(self, output_tokens):
        return f"{self.prompt:,} prompt + {output_tokens:,} output / {self.context:,} context tokens"


def _metadata(session, method, url, stop, payload=None, *, read_timeout=10):
    if stop.is_set():
        return None
    with session.request(method, url, json=payload, stream=True,
                         timeout=(2, read_timeout), allow_redirects=False) as response:
        if response.status_code in (404, 405, 501):
            return None
        response.raise_for_status()
        if response.status_code != 200:
            return None
        data = bytearray()
        for block in response.iter_content(8192):
            if stop.is_set():
                return None
            data.extend(block)
            if len(data) > METADATA_LIMIT:
                raise VoiceError("The formatted prompt is too large to check. Shorten the system prompt or history.")
        return json.loads(data)


def context_budget(endpoint, payload, stop):
    url = urlsplit(endpoint)
    root = urlunsplit((url.scheme, url.netloc, "", "", ""))
    with requests.Session() as session:
        session.trust_env = False
        try:
            model = payload["model"]
            props = _metadata(session, "GET", root + "/props?" + urlencode({"model": model}), stop,
                              read_timeout=25)
            if props is None:
                return None
            limit = props["default_generation_settings"]["n_ctx"]
            if type(limit) is not int or limit <= 0:
                return None
            formatted = _metadata(session, "POST", root + "/apply-template", stop, payload)
            if formatted is None:
                return None
            tokens = _metadata(session, "POST", root + "/tokenize", stop,
                               {"model": model, "content": formatted["prompt"], "add_special": False,
                                "parse_special": True})
            if tokens is None or not isinstance(tokens["tokens"], list):
                return None
            return PromptBudget(len(tokens["tokens"]), limit)
        except (requests.RequestException, KeyError, TypeError, ValueError):
            return None
