"""Run the isolated CUDA runtime behind the Pocket TTS binary pipe protocol."""

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


def main():
    output = sys.stdout.buffer
    # Keep model-library diagnostics out of the binary channel.
    sys.stdout = sys.stderr
    packages, config, preset, temperature = sys.argv[1:]
    site.addsitedir(packages)
    import torch
    from pocket_tts import TTSModel

    torch.set_num_threads(4)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    model = TTSModel.load_model(config=Path(config).resolve(), temp=float(temperature)).eval().to("cuda:0")
    voice = model.get_state_for_audio_prompt(Path(preset).resolve())
    model.generate_audio(voice, "Ready.")
    torch.cuda.synchronize()

    pending = Queue(maxsize=1)
    Thread(target=commands, args=(sys.stdin.buffer, pending), daemon=True).start()
    packets(output, b"R", struct.pack("<I", model.sample_rate))
    while (job := pending.get()) is not None:
        text, stop = job
        pcm = bytearray()
        for chunk in model.generate_audio_stream(voice, text, stop=stop):
            pcm.extend(chunk.detach().cpu().numpy().astype("<f4", copy=False).tobytes())
            if len(pcm) > PCM_LIMIT:
                raise RuntimeError("Pocket TTS exceeded the PCM limit")
        if stop.is_set():
            packets(output, b"C")
        else:
            packets(output, b"A", pcm)


if __name__ == "__main__":
    main()
