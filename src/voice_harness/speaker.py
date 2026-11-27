"""Compose a synthesizer and a PCM sink with one segment rendering ahead."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from .playback import AudioPlayback
from .synthesis import PocketSynthesizer


class SynthesizedSpeech:
    def __init__(self, synthesis, sink):
        self.synthesis = synthesis
        self.sink = sink

    def prepare(self, stop):
        self.synthesis.prepare(stop)

    def close(self):
        self.synthesis.close()

    def speak(self, next_segment, stop, stage, *, played=None, first_audio=None):
        abandoned = Event()

        class PlaybackCancellation:
            def is_set(self):
                return stop.is_set() or abandoned.is_set()

        token = PlaybackCancellation()

        def render_next():
            segment = next_segment(token)
            if segment is not None and not token.is_set():
                return self.synthesis.render(segment, token)
            return None

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech-render") as producer:
            pending = producer.submit(render_next)

            def next_pcm():
                nonlocal pending
                pcm = pending.result()
                if pcm is not None and not token.is_set():
                    pending = producer.submit(render_next)
                return pcm

            try:
                hooks = {"first_audio": first_audio} if first_audio is not None else {}
                self.sink.play(next_pcm, token, stage, played=played, **hooks)
            finally:
                abandoned.set()


class PocketSpeaker(SynthesizedSpeech):
    def __init__(self, directory, settings, meter, *, reference=None, process_started=None):
        super().__init__(PocketSynthesizer(directory, settings, process_started=process_started),
                         AudioPlayback(settings, meter, reference=reference))

    @property
    def playback(self):
        return self.sink.settings

    @playback.setter
    def playback(self, settings):
        self.sink.settings = settings
