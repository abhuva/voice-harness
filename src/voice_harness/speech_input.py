"""Detect speech on cleaned mic frames and feed pause-based dictation."""

from collections import deque
import math

import numpy as np

from .contracts import MicrophoneDropout, PRE_ROLL_SECONDS
from .echo import FRAME_SECONDS, FRAME_SAMPLES


PRE_ROLL_FRAMES = round(PRE_ROLL_SECONDS / FRAME_SECONDS)
BARGE_GAP_FRAMES = round(1.2 / FRAME_SECONDS)
BARGE_MIN_VOICE_FRACTION = 1 / 3
MAX_MISSING_FRAMES = round(0.1 / FRAME_SECONDS)


class SpeechInput:
    def __init__(self, device, enhancement, meter):
        self.device = device
        self.enhancement = enhancement
        self.meter = meter
        self.last_speech_at = None
        self._recovering = False

    def open(self):
        return self.device.open()

    def discard_buffered(self):
        self.device.discard_buffered()

    def capture(self, settings, stop, stage, accept, *, speech_started, reply_pending):
        self.last_speech_at = None
        recent = deque(maxlen=PRE_ROLL_FRAMES)
        activity = deque(maxlen=15)
        threshold = 10 ** (settings.threshold / 20)
        finish_frames = round(settings.finish / FRAME_SECONDS)
        barge_frames = round(settings.barge_in / FRAME_SECONDS)
        candidate_gap = min(finish_frames, BARGE_GAP_FRAMES)
        candidate = None
        candidate_voiced = 0
        candidate_age = 0
        recording = False
        quiet = 0
        blocks = []
        block_active = False
        last_active = None
        meter_frame = 0
        missing_frames = 0
        stage("Microphone dropout · pause, then speak again" if self._recovering
              else "Listening · waiting for speech")

        def append(samples, active):
            nonlocal block_active
            blocks.append(samples)
            block_active |= active
            if len(blocks) == 10:
                accept(np.concatenate(blocks), block_active)
                blocks.clear()
                block_active = False

        def reset_candidate():
            nonlocal candidate, quiet, missing_frames
            candidate = None
            quiet = 0
            missing_frames = 0
            activity.clear()
            recent.clear()

        while (frame := self.device.read(stop)) is not None:
            samples, at, missing = frame
            malformed = len(samples) != FRAME_SAMPLES
            if missing or malformed:
                quiet = 0
                if not recording and candidate is None:
                    reset_candidate()
                    continue
                missing += int(malformed)
                missing_frames += missing
                if missing_frames > MAX_MISSING_FRAMES:
                    if recording:
                        self._recovering = True
                        stage("Microphone dropout · clearing the draft")
                        raise MicrophoneDropout("Microphone audio lost during dictation.")
                    reset_candidate()
                    continue
                silence = np.zeros((FRAME_SAMPLES, 1), dtype=np.int16)
                for _ in range(missing):
                    if recording:
                        append(silence, False)
                    else:
                        candidate.append((silence, False))
                if malformed:
                    continue
            samples, probability, speaker_recent = self.enhancement.clean(samples, at)
            values = samples.astype("float32")
            rms = math.sqrt(np.mean(values ** 2)) / 32768
            meter_frame += 1
            if meter_frame % 10 == 0:
                self.meter(20 * math.log10(max(rms, 1e-5)),
                           20 * math.log10(max(np.max(np.abs(values)) / 32768, 1e-5)))
            block_reply = reply_pending()
            if block_reply and not settings.interrupt:
                reset_candidate()
                continue

            active = rms >= threshold
            if speaker_recent:
                active &= probability >= 0.55 if probability is not None else not speaker_recent
            if active:
                last_active = at + FRAME_SECONDS
            quiet = 0 if active else quiet + 1

            if self._recovering:
                if quiet >= finish_frames:
                    self._recovering = False
                    stage("Listening · waiting for speech")
                continue

            if not recording:
                if candidate is None:
                    recent.append((samples, active))
                    activity.append(active)
                    needed = 10 if block_reply or speaker_recent else 3
                    if not active or sum(activity) < needed:
                        continue
                    candidate = list(recent)
                    candidate_voiced = sum(activity)
                    candidate_age = len(activity) - activity.index(True)
                else:
                    candidate.append((samples, active))
                    candidate_voiced += active
                    candidate_age += 1

                # Measure the speaking window, with a sustained voice requirement.
                # Natural syllable pauses and repaired frames cannot count as voice.
                sustained = (candidate_age >= barge_frames
                             and candidate_voiced >= candidate_age * BARGE_MIN_VOICE_FRACTION)
                if not active or (block_reply and not sustained):
                    if quiet >= candidate_gap or candidate_age >= 2 * barge_frames:
                        reset_candidate()
                    continue

                recording = True
                self.last_speech_at = last_active
                speech_started()
                for cached, voiced in candidate:
                    append(cached, voiced)
                candidate = None
                recent.clear()
                continue

            self.last_speech_at = last_active
            append(samples, active)
            if quiet >= finish_frames:
                if blocks:
                    silence = np.zeros_like(samples)
                    blocks.extend([silence] * (10 - len(blocks)))
                    accept(np.concatenate(blocks), block_active)
                return True
        return False
