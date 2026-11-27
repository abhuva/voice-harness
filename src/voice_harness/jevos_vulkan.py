"""Jevos v5 binary scoring with the published Q8 GGUF on llama.cpp Vulkan."""

import json
import math
from pathlib import Path
import re
from threading import Event

import requests

from .contracts import VoiceError
from .jevos import DecisionProcess, THREADS, conversation_state


VULKAN_DIRECTORY = "jevos-vulkan/b12777"
VULKAN_MODEL = "jevos-v5-q8_0.gguf"
VULKAN_LABEL = "Jevos v5 Q8 · GPU (Vulkan)"
CONTEXT = 4096


def vulkan_installation(directory):
    if directory is not None:
        root = Path(directory).resolve()
        executable, model = root / "llama-server.exe", root / VULKAN_MODEL
        if executable.is_file() and model.is_file() and model.stat().st_size == 942_872_288:
            return executable, model
    raise VoiceError("Install Jevos Vulkan with scripts/setup-jevos-vulkan.ps1, or select CPU in Jevos.")


def binary_prompt(bos, state, question):
    # Match Jevos v5's evidence formatting and binary prompt, including the BOS token.
    body = json.dumps(state, ensure_ascii=False) if "</evidence>" in state.lower() else state.strip()
    return f"{bos}<evidence>\n{body}\n</evidence>\n\nQuestion: {question.strip()}\nAnswer:"


def binary_scores(rows, count, no, yes):
    try:
        scores = {}
        if not isinstance(rows, list) or len(rows) != count:
            raise ValueError
        for row in rows:
            index = row["index"]
            if type(index) is not int or not 0 <= index < count or index in scores or row["truncated"] is not False:
                raise ValueError
            step, = row["completion_probabilities"]
            options = step["top_probs"]
            probs = {item["id"]: item["prob"] for item in options}
            if len(options) != 2 or probs.keys() != {no, yes} or any(
                    type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in probs.values()):
                raise ValueError
            scores[index] = probs[yes] / (probs[no] + probs[yes])
        return tuple(scores[index] for index in range(count))
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        raise VoiceError("Jevos Vulkan did not return valid binary scores.") from None


class JevosVulkan(DecisionProcess):
    def prepare(self, stop):
        executable, model = vulkan_installation(self.directory)
        device = Event()
        offloaded = Event()

        def read_status(pipe):
            for line in pipe:
                if b"using device Vulkan0 (NVIDIA " in line:
                    device.set()
                layers = re.search(rb"offloaded (\d+)/(\d+) layers to GPU", line)
                if layers and layers[1] == layers[2] and int(layers[1]) > 0:
                    offloaded.set()

        self.launch(executable, ["--model", str(model), "--threads", str(THREADS), "--threads-batch", str(THREADS),
                                 "--ctx-size", str(CONTEXT), "--parallel", "1", "--device", "Vulkan0",
                                 "--n-gpu-layers", "all", "--fit", "off", "--no-webui", "--no-context-shift", "-lv", "4"],
                    key_variable="LLAMA_API_KEY",
                    env_updates={"VK_LOADER_LAYERS_DISABLE": "VK_LAYER_AMD_switchable_graphics,VK_LAYER_OW_*"},
                    read_status=read_status)
        if not self.wait_ready(stop, "ok"):
            return
        if not device.is_set() or not offloaded.is_set():
            raise VoiceError("Jevos Vulkan could not confirm full layer offload on the NVIDIA GPU. Select CPU in Jevos.")
        try:
            props = self.request_json("/props", timeout=2)
            self.bos = props["bos_token"]
            self.no, = self.tokenize("0", timeout=2)
            self.yes, = self.tokenize("1", timeout=2)
            if not isinstance(self.bos, str) or not self.bos or self.no == self.yes:
                raise ValueError
        except (requests.RequestException, KeyError, TypeError, ValueError):
            raise VoiceError("Jevos Vulkan could not read the model's binary tokens.") from None
        self.loaded = not stop.is_set()

    def tokenize(self, text, *, timeout=5):
        body = self.request_json("/tokenize", {"content": text, "add_special": False, "parse_special": True},
                                 timeout=timeout)
        tokens = body.get("tokens") if isinstance(body, dict) else None
        if not isinstance(tokens, list) or not tokens or any(type(token) is not int or token < 0 for token in tokens):
            raise VoiceError("Jevos Vulkan returned invalid prompt tokens.")
        return tokens

    def score(self, messages, questions, stop):
        state = conversation_state(messages)

        def score():
            prompts = []
            for question in questions:
                if stop.is_set():
                    return None
                prompt = self.tokenize(binary_prompt(self.bos, state, question))
                if len(prompt) >= CONTEXT:
                    raise VoiceError("The three full messages exceed the Jevos Vulkan context window.")
                prompts.append(prompt)
            if stop.is_set():
                return None
            # Equal bias brings both targets into the reported pair without changing their logit difference.
            payload = {"prompt": prompts, "n_predict": 1, "n_probs": 2, "temperature": 1.0,
                       "logit_bias": [[self.no, 100], [self.yes, 100]], "samplers": ["temperature"],
                       "post_sampling_probs": True, "cache_prompt": True,
                       "response_fields": ["index", "completion_probabilities", "truncated"]}
            return binary_scores(self.request_json("/completion", payload), len(prompts), self.no, self.yes)

        return self.infer(score, stop)
