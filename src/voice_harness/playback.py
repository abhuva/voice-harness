"""Play a PCM stream, own the output device and apply live tuning."""

from .contracts import VoiceError
from .output_audio import OutputAudio, output_levels
from .render_device import ReferencedOutput
from .tempo import playback_blocks


class AudioPlayback:
    def __init__(self, settings, meter, *, reference=None, device_factory=None):
        self.settings = settings
        self.meter = meter
        self.reference = reference
        self.device_factory = device_factory

    def play(self, next_pcm, stop, stage, *, played=None, first_audio=None):
        import sounddevice as sd

        device = None
        started = False
        drained = False
        rate = 0
        index = 0
        try:
            while not stop.is_set():
                clip = next_pcm()
                if clip is None or stop.is_set():
                    break
                if device is None:
                    rate = clip.rate
                    output = OutputAudio(rate)
                    if self.device_factory is not None:
                        device = self.device_factory(rate, stop)
                    elif self.reference is None and first_audio is None:
                        device = sd.RawOutputStream(samplerate=rate, channels=1,
                                                    dtype="float32", latency="low")
                    else:
                        device = ReferencedOutput(rate, self.reference, stop, first_audio=first_audio)
                    device.start()
                    started = True
                if clip.rate != rate:
                    raise VoiceError("Playback sample rate changed within a stream.")
                index += 1
                if played is None:
                    stage(f"Playing Kindroid reply · segment {index}")
                for block in playback_blocks(clip.data, rate, lambda: self.settings.speed):
                    if stop.is_set():
                        break
                    tuning = self.settings
                    block = output.process(block, (tuning.bass, tuning.mid, tuning.treble))
                    device.write(block)
                    self.meter(*output_levels(block))
                if played is not None and not stop.is_set():
                    played(index)
            if started and not stop.is_set():
                device.stop()
                drained = True
        except (OSError, sd.PortAudioError):
            raise VoiceError("Could not play speech. Check the Windows output device.") from None
        finally:
            if device is not None:
                try:
                    if started and not drained:
                        device.abort()
                finally:
                    device.close()
            self.meter(-100.0, -100.0)
