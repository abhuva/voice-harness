"""Windows capture at 16 kHz. One microphone opens for each operator turn."""

from collections import deque
import math

from .contracts import VoiceError


class WindowsMicrophone:
    def __init__(self, meter):
        self.meter = meter

    def capture(self, settings, stop, stage, accept):
        import numpy as np
        import sounddevice as sd

        pause_blocks = round(settings.finish * 10)
        threshold = 32768 * 10 ** (settings.threshold / 20)
        recent = deque(maxlen=3)
        recording = False
        quiet = 0
        try:
            with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1600) as mic:
                stage("Listening · waiting for speech")
                while not stop.is_set():
                    block, overflow = mic.read(1600)
                    if stop.is_set():
                        break
                    if overflow:
                        raise VoiceError("Microphone audio was interrupted. This turn was not sent.")
                    rms = math.sqrt(np.mean(block.astype("float32") ** 2))
                    peak = max(abs(int(block.min())), abs(int(block.max())))
                    self.meter(20 * math.log10(max(rms / 32768, 1e-5)),
                               20 * math.log10(max(peak / 32768, 1e-5)))
                    active = rms >= threshold
                    if not recording:
                        if not active:
                            recent.append(block)
                            continue
                        recording = True
                        stage("Recording · pause to finish")
                        for buffered in recent:
                            accept(buffered, False)
                    accept(block, active)
                    quiet = 0 if active else quiet + 1
                    if quiet >= pause_blocks:
                        return True
        except (OSError, sd.PortAudioError):
            raise VoiceError("Could not record. Check the Windows microphone and its permissions.") from None
        finally:
            self.meter(-100.0, -100.0)
        return False
