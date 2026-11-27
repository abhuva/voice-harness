"""Run Python synthesis models behind the resident binary pipe protocol."""

import json
from pathlib import Path
from queue import Queue
import site
import struct
import sys
from threading import Event, Thread


PCM_LIMIT = 128 * 1024 * 1024


def packets(output, tag, pcm=b""):
    output.write(struct.pack("<cI", tag, len(pcm)))
    output.write(pcm)
    output.flush()


def commands(input_pipe, pending):
    stop = Event()
    try:
        for line in input_pipe:
            if not line.strip():
                stop.set()
                continue
            text = json.loads(line)
            if not isinstance(text, str):
                break
            stop = Event()
            pending.put((text, stop))
    finally:
        stop.set()
        pending.put(None)


def load_silencio(root, preset, temperature, device):
    sys.path.insert(0, str(Path(root).resolve()))
    from silencio2 import Silencio2TTS
    from silencio2.tts import EMOTIONS, EMOTION_TAG, SOUNDS, SOUND_TAG

    tts = Silencio2TTS.from_pretrained(root, device=device, temperature=temperature)
    voice = tts.voice(preset)

    def generate(text, stop):
        # Provider text can contain ordinary brackets and arbitrary markup.
        text = SOUND_TAG.sub(lambda match: match[0] if match[1] in SOUNDS else match[1], text)
        text = EMOTION_TAG.sub(lambda match: match[0] if match[1] in EMOTIONS else "", text)
        if not EMOTION_TAG.sub("", text).strip():
            return
        for emotion, chunk in tts.chunks(text):
            if stop.is_set():
                return
            yield from tts.model.generate_audio_stream(
                voice, f"<{emotion}> {chunk}" if emotion else chunk, stop=stop,
            )

    return tts.sample_rate, generate


def load_pocket(config, preset, temperature):
    from pocket_tts import TTSModel

    model = TTSModel.load_model(config=Path(config).resolve(), temp=temperature).eval().to("cuda:0")
    voice = model.get_state_for_audio_prompt(Path(preset).resolve())
    return model.sample_rate, lambda text, stop: model.generate_audio_stream(voice, text, stop=stop)


def main():
    output = sys.stdout.buffer
    # Keep model-library diagnostics out of the binary channel.
    sys.stdout = sys.stderr
    packages, config, preset, temperature, *selection = sys.argv[1:]
    site.addsitedir(packages)
    import torch

    torch.set_num_threads(4)
    device = selection[1] if selection else "cuda"
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    if selection:
        rate, generate = load_silencio(config, preset, float(temperature), device)
    else:
        rate, generate = load_pocket(config, preset, float(temperature))
    for _ in generate("Ready.", Event()):
        pass
    if device == "cuda":
        torch.cuda.synchronize()

    pending = Queue(maxsize=1)
    Thread(target=commands, args=(sys.stdin.buffer, pending), daemon=True).start()
    packets(output, b"R", struct.pack("<I", rate))
    while (job := pending.get()) is not None:
        text, stop = job
        pcm = bytearray()
        for chunk in generate(text, stop):
            pcm.extend(chunk.detach().cpu().numpy().astype("<f4", copy=False).tobytes())
            if len(pcm) > PCM_LIMIT:
                raise RuntimeError("Synthesis exceeded the PCM limit")
        if stop.is_set():
            packets(output, b"C")
        else:
            packets(output, b"A", pcm or (bytes(rate // 50 * 4) if selection else b""))


if __name__ == "__main__":
    main()
