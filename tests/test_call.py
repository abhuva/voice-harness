from contextlib import nullcontext
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
        self.microphone.open.return_value = nullcontext()
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

    def capture_one(self, settings, stop, stage, accept, *, speech_started, reply_pending):
        self.assertEqual(settings.finish, 4.0)
        speech_started()
        for gap in (4, 2):
            accept(self.block, True)
            for _ in range(gap):
                accept(self.block * 0, False)
        return True

    def test_two_turns_use_replacement_workers_with_no_overlap_in_sends(self):
        captures = 0
        finished = [Event(), Event()]
        plays = 0

        def play(take, token, stage):
            nonlocal plays
            self.play(take, token, stage)
            finished[plays].set()
            plays += 1

        def capture(settings, stop, stage, accept, **hooks):
            nonlocal captures
            captures += 1
            if captures > 1:
                self.assertTrue(finished[captures - 2].wait(2))
            if captures == 3:
                self.stop.set()
                return False
            # This is a normal turn. Let the prior reply finish settling.
            if captures == 2:
                for _ in range(200):
                    if not hooks["reply_pending"]():
                        break
                    self.stop.wait(0.01)
                self.assertFalse(hooks["reply_pending"]())
            return self.capture_one(settings, stop, stage, accept, **hooks)

        self.speaker.speak.side_effect = play

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
        checked = next(message for message in self.messages if message.status == "Check delivery")
        restarted = State(self.path)
        self.assertEqual(restarted.pending_delivery(), checked.id)
        with self.assertRaises(VoiceError):
            restarted.claim_delivery("second-turn")

    def test_stop_in_capture_sends_nothing(self):
        def capture(settings, stop, stage, accept, **hooks):
            self.stop.set()
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

    def interrupt_fixture(self, trigger):
        captures = 0
        self.recognizer.transcribe.side_effect = ["First", "message", "New", "speech"]

        def capture(settings, cancel, stage, accept, **hooks):
            nonlocal captures
            captures += 1
            if captures == 2:
                self.assertTrue(trigger.wait(2))
                self.assertTrue(hooks["reply_pending"]())
            if captures >= 3:
                while not cancel.is_set():
                    self.stop.wait(0.01)
                return False
            return self.capture_one(settings, cancel, stage, accept, **hooks)

        self.microphone.capture.side_effect = capture
        self.draft_ready = Event()

        class Messages(list):
            def append(messages, message):
                super().append(message)
                if message.body == "New\n\nspeech" and message.status == "Dictating":
                    self.draft_ready.set()
                if message.body == "Second answer." and message.status == "":
                    self.stop.set()

        self.messages = Messages()

    def test_interrupt_before_confirmation_captures_draft_and_waits_to_send(self):
        waiting = Event()
        first_closed = Event()
        self.interrupt_fixture(waiting)

        def send(text, token):
            if text.startswith("First"):
                try:
                    waiting.set()
                    self.assertTrue(self.draft_ready.wait(2))
                    self.assertFalse(token.is_set())
                    self.assertIsNotNone(self.state.pending_delivery())
                    self.assertEqual(self.provider.send.call_count, 1)
                    yield "First acknowledgment."
                    self.fail("Stream should close after confirming the interrupted send")
                finally:
                    first_closed.set()
            else:
                self.assertTrue(first_closed.is_set())
                yield "Second answer."

        self.provider.send.side_effect = send
        self.run_call()
        self.assertEqual(self.provider.send.call_count, 2)
        self.assertEqual(self.played, ["Second answer."])
        self.assertTrue(any(message.status == "Interrupted" for message in self.messages))
        self.assertIsNone(self.state.pending_delivery())

    def test_interrupt_playback_cancels_audio_and_reopens_no_device(self):
        playing = Event()
        ended = Event()
        self.interrupt_fixture(playing)

        def play(take, token, stage):
            self.played.append(take(token))
            if self.played[-1] == "First audio.":
                playing.set()
                for _ in range(200):
                    if token.is_set():
                        ended.set()
                        return
                    self.stop.wait(0.01)
                self.fail("Playback did not stop")
            while take(token):
                pass

        def send(text, token):
            if text.startswith("First"):
                yield "First audio. "
                for _ in range(200):
                    if token.is_set():
                        return
                    self.stop.wait(0.01)
                self.fail("Reply did not stop")
            else:
                self.assertTrue(ended.is_set())
                yield "Second answer."

        self.speaker.speak.side_effect = play
        self.provider.send.side_effect = send
        self.run_call()
        self.assertEqual(self.played, ["First audio.", "Second answer."])
        self.assertEqual(self.provider.send.call_count, 2)
        self.microphone.open.assert_called_once()

    def test_interrupted_draft_is_held_if_earlier_delivery_is_uncertain(self):
        waiting = Event()
        self.interrupt_fixture(waiting)

        def send(text, token):
            waiting.set()
            self.assertTrue(self.draft_ready.wait(2))
            self.assertFalse(token.is_set())
            raise VoiceError("Delivery is uncertain.")

        self.provider.send.side_effect = send
        with self.assertRaisesRegex(VoiceError, "uncertain"):
            self.run_call()
        self.provider.send.assert_called_once()
        self.assertEqual(self.messages[-1].status, "Held · check earlier delivery")
        self.assertEqual(self.messages[-1].body, "New\n\nspeech")
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
