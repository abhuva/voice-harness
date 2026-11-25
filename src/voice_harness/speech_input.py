"""Detect speech on cleaned mic frames and feed the existing pause-based dictation."""

from collections import deque
import math

import numpy as np

from .contracts import VoiceError
from .echo import FRAME_SECONDS, FRAME_SAMPLES


class SpeechInput:
    def __init__(self, device, enhancement, meter):
        self.device = device
        self.enhancement = enhancement
        self.meter = meter

    def open(self):
        return self.device.open()

    def discard_buffered(self):
        self.device.discard_buffered()

    def capture(self, settings, stop, stage, accept, *, speech_started, reply_pending):
        recent = deque(maxlen=30)
        activity = deque(maxlen=15)
        threshold = 10 ** (settings.threshold / 20)
        finish_frames = round(settings.finish / FRAME_SECONDS)
        recording = False
        quiet = 0
        blocks = []
        block_active = False
        meter_frame = 0
        stage("Listening · waiting for speech")

        def append(samples, active):
            nonlocal block_active
            blocks.append(samples)
            block_active |= active
            if len(blocks) == 10:
                accept(np.concatenate(blocks), block_active)
                blocks.clear()
                block_active = False

        while (frame := self.device.read(stop)) is not None:
            samples, at, discontinuity = frame
            if discontinuity or len(samples) != FRAME_SAMPLES:
                if recording:
                    raise VoiceError("Microphone audio was interrupted. This turn was not sent.")
                activity.clear()
                recent.clear()
                continue
            samples, probability, speaker_recent = self.enhancement.clean(samples, at)
            rms = math.sqrt(np.mean(samples.astype("float32") ** 2)) / 32768
            meter_frame += 1
            if meter_frame % 10 == 0:
                # Keep UI progress traffic below the audio frame rate, including while idle.
                self.meter(20 * math.log10(max(rms, 1e-5)),
                           20 * math.log10(max(np.max(np.abs(samples.astype("float32"))) / 32768, 1e-5)))
            block_reply = reply_pending()
            if block_reply and not settings.interrupt:
                recent.clear()
                activity.clear()
                continue
            active = rms >= threshold
            if block_reply or speaker_recent:
                active &= probability >= 0.55 if probability is not None else not speaker_recent
            if not recording:
                recent.append((samples, active))
                activity.append(active)
                needed = 10 if block_reply or speaker_recent else 3
                if sum(activity) < needed:
                    continue
                recording = True
                speech_started()
                stage("Recording · pause to finish")
                for cached, voiced in recent:
                    append(cached, voiced)
                continue
            append(samples, active)
            quiet = 0 if active else quiet + 1
            if quiet >= finish_frames:
                if blocks:
                    silence = np.zeros_like(samples)
                    blocks.extend([silence] * (10 - len(blocks)))
                    accept(np.concatenate(blocks), block_active)
                return True
        return False
