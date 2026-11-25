"""Resident Pocket TTS and Windows audio playback."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from .contracts import VoiceError
from .engine import ResidentEngine
from .output_audio import OutputAudio, output_levels
from .presets import VoiceLibrary
from .tempo import playback_blocks


class PocketSpeaker:
    def __init__(self, directory, settings, meter):
        self.directory = directory
        self.playback = settings
        self.meter = meter
        self.engine = ResidentEngine(directory)

    def prepare(self, stop):
        config, profile = VoiceLibrary(self.directory).resolve(self.playback)
        weights = {"english.yaml": "model.safetensors", "cloning.yaml": "cloning.safetensors",
                   "english-7m.yaml": "model-7m.safetensors"}[config]
        required = (config, profile, weights, "tokenizer.model", "voice-engine.exe")
        if not all((self.directory / name).is_file() for name in required):
            raise VoiceError("Restore the Pocket TTS model files and voice-engine.exe.")
        self.engine.load(self.playback, config, profile, stop)

    def close(self):
        self.engine.unload()

    def speak(self, next_segment, stop, stage):
        import sounddevice as sd

        abandoned = Event()

        class PlaybackCancellation:
            def is_set(self):
                return stop.is_set() or abandoned.is_set()

        token = PlaybackCancellation()

        def render_next():
            segment = next_segment(token)
            if segment is not None and not token.is_set():
                return self.engine.render(segment, token)
            return None

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="pocket-render") as producer:
            pending = producer.submit(render_next)
            device = None
            started = False
            drained = False
            try:
                output = OutputAudio(self.engine.rate)
                device = sd.RawOutputStream(samplerate=self.engine.rate, channels=1,
                                             dtype="float32", latency="low")
                index = 0
                while not token.is_set():
                    pcm = pending.result()
                    if pcm is None or token.is_set():
                        break
                    pending = producer.submit(render_next)
                    if not started:
                        device.start()
                        started = True
                    index += 1
                    stage(f"Playing Kindroid reply · segment {index}")
                    for block in playback_blocks(pcm, self.engine.rate, lambda: self.playback.speed):
                        if token.is_set():
                            break
                        tuning = self.playback
                        block = output.process(block, (tuning.bass, tuning.mid, tuning.treble))
                        device.write(block)
                        self.meter(*output_levels(block))
                if started and not token.is_set():
                    device.stop()
                    drained = True
            except (OSError, sd.PortAudioError):
                raise VoiceError("Could not play the reply. Check the Windows output device.") from None
            finally:
                abandoned.set()
                if device is not None:
                    try:
                        if started and not drained:
                            device.abort()
                    finally:
                        device.close()
                self.meter(-100.0, -100.0)
