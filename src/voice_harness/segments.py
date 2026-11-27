"""Break streamed text into bounded synthesis inputs."""

import re
import textwrap


_BREAK = re.compile(r"""[.!?…]+["'”’)]*(?=\s)|[\r\n]+""")
_LIMIT = 480


class SentenceSegments:
    def __init__(self):
        self.pending = ""

    def feed(self, delta):
        self.pending += delta
        while self.pending:
            match = _BREAK.search(self.pending, 0, _LIMIT + 1)
            if match and match.end() <= _LIMIT:
                end = match.end()
            elif len(self.pending) > _LIMIT:
                end = self.pending.rfind(" ", 1, _LIMIT) + 1 or _LIMIT
            else:
                return
            segment, self.pending = self.pending[:end].strip(), self.pending[end:]
            if segment:
                yield segment

    def finish(self):
        segment, self.pending = self.pending.strip(), ""
        return segment


def paragraph_segments(text):
    return [line for paragraph in re.split(r"\n(?:[^\S\n]*\n)+", text.strip())
            for line in textwrap.wrap(paragraph, width=_LIMIT)]
