"""Group 100 ms microphone blocks for bounded local decoding."""

from collections import deque

from .contracts import PRE_ROLL_SECONDS


class PauseChunks:
    # 100 ms microphone blocks. Bound the work of one decode even without pauses.
    max_blocks = 80

    def __init__(self, pause_seconds, paragraph_seconds, submit):
        self.pause_blocks = round(pause_seconds * 10)
        self.paragraph_blocks = round(paragraph_seconds * 10)
        self.paragraph = 0
        self.submit = submit
        self.preroll = deque(maxlen=PRE_ROLL_SECONDS * 10)
        self.frames = []
        self.quiet = 0

    def accept(self, block, active):
        if active:
            if self.quiet >= self.paragraph_blocks:
                self.flush()
                self.paragraph += 1
            self.quiet = 0
        else:
            self.quiet += 1
        if not self.frames:
            if not active:
                self.preroll.append(block)
                return
            self.frames.extend(self.preroll)
            self.preroll = deque(maxlen=3)
        self.frames.append(block)
        if self.quiet >= self.pause_blocks or len(self.frames) >= self.max_blocks:
            self.flush()

    def flush(self):
        import numpy as np

        if self.frames:
            # Keep the ends of words, but spare the decoder a long silence.
            end = len(self.frames) - max(0, self.quiet - 2)
            self.submit(np.concatenate(self.frames[:end]), self.paragraph)
            self.preroll.extend(self.frames[end:])
            self.frames.clear()
