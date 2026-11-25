from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock

import numpy as np

from voice_harness.call import run_call
from voice_harness.contracts import CallInput, VoiceError
from voice_harness.reply import receive_reply
from voice_harness.state import State


class CallTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.state = State(self.path)
        self.stop = Event()
        self.recognizer, self.microphone, self.speaker, self.provider = (Mock() for _ in range(4))
        self.messages, self.played = [], []
        self.first_played = Event()
        self.speaker.speak.side_effect = self.play
        self.block = np.ones((1600, 1), dtype="int16")

    def play(self, take, token, _stage):
        while piece := take(token):
            self.played.append(piece)
            self.first_played.set()

    def run_call(self, speaker=True):
        run_call(self.microphone, self.recognizer, self.speaker if speaker else None, self.provider,
                 self.state, CallInput(chunk=0.2, paragraph=0.4), self.stop, Mock(),
                 self.messages.append, Mock())

    def capture_one(self, settings, stop, stage, accept):
        self.assertEqual(settings.finish, 4.0)
        for gap in (4, 2):
            accept(self.block, True)
            for _ in range(gap):
                accept(self.block * 0, False)
        return True

    def test_two_turns_use_replacement_workers_and_wait_for_delayed_playback(self):
        captures = 0

        def capture(settings, stop, stage, accept):
            nonlocal captures
            if captures:
                self.assertEqual(self.played[-1], "Last fragment")
            captures += 1
            if captures == 3:
                stop.set()
                return False
            return self.capture_one(settings, stop, stage, accept)

        def stream(_text, _token):
            self.assertIsNotNone(self.state.pending_delivery())
            self.first_played.clear()
            yield "First sentence. "
            self.assertTrue(self.first_played.wait(2))
            yield "Next line\nLast fragment"

        self.microphone.capture.side_effect = capture
        self.recognizer.transcribe.side_effect = ["Hello", "First", "Hello", "Again"]
        self.provider.send.side_effect = stream
        self.run_call()
        self.assertEqual(self.played, ["First sentence.", "Next line", "Last fragment"] * 2)
        self.assertEqual([call.args[0] for call in self.provider.send.call_args_list],
                         ["Hello\n\nFirst", "Hello\n\nAgain"])
        self.recognizer.prepare.assert_called_once()
        self.speaker.prepare.assert_called_once()
        self.speaker.close.assert_called_once()
        self.assertIsNone(self.state.pending_delivery())

    def test_failed_send_leaves_one_durable_claim_and_preserves_message(self):
        self.microphone.capture.side_effect = self.capture_one
        self.recognizer.transcribe.return_value = "Hello"
        self.provider.send.side_effect = VoiceError("Delivery is uncertain.")
        with self.assertRaisesRegex(VoiceError, "uncertain"):
            self.run_call()
        self.provider.send.assert_called_once()
        self.assertEqual(self.messages[-1].status, "Check delivery")
        restarted = State(self.path)
        self.assertEqual(restarted.pending_delivery(), self.messages[-1].id)
        with self.assertRaises(VoiceError):
            restarted.claim_delivery("second-turn")

    def test_stop_in_capture_sends_nothing(self):
        def capture(settings, stop, stage, accept):
            stop.set()
            return False

        self.microphone.capture.side_effect = capture
        self.run_call(speaker=False)
        self.provider.send.assert_not_called()
        self.assertIsNone(self.state.pending_delivery())
        self.speaker.prepare.assert_not_called()

    def test_cancel_before_reply_has_no_retry_and_marks_delivery_uncertain(self):
        self.microphone.capture.side_effect = self.capture_one
        self.recognizer.transcribe.return_value = "Hello"

        def stream(_text, _token):
            self.stop.set()
            yield from ()

        self.provider.send.side_effect = stream
        self.run_call(speaker=False)
        self.provider.send.assert_called_once()
        self.assertIsNotNone(self.state.pending_delivery())

    def test_speaker_failure_closes_stream_and_preserves_partial_reply(self):
        self.speaker.speak.side_effect = VoiceError("Output device failed.")
        closed = Event()
        replies = []

        def stream(token):
            try:
                yield "First sentence. "
                for _ in range(100):
                    if token.is_set():
                        return
                    self.stop.wait(0.01)
                self.fail("Playback failure did not cancel the provider")
            finally:
                closed.set()

        with self.assertRaisesRegex(VoiceError, "Output"):
            receive_reply(self.speaker, stream, self.stop, Mock(), lambda *args: replies.append(args))
        self.assertTrue(closed.is_set())
        self.assertEqual(replies[-1], ("First sentence. ", "Incomplete reply"))

    def test_speech_queue_bounds_segment_length(self):
        giant = "x" * 2100
        receive_reply(self.speaker, lambda token: (item for item in [giant]), self.stop, Mock(), Mock())
        self.assertEqual("".join(self.played), giant)
        self.assertLessEqual(max(map(len, self.played)), 480)
