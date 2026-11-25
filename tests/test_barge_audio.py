from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from sounddevice import CallbackStop

from voice_harness.contracts import CallInput
from voice_harness.echo import EchoProcessor, FRAME_SAMPLES, PlaybackReference
from voice_harness.render_device import ReferencedOutput
from voice_harness.speech_input import SpeechInput


class BargeAudioTests(unittest.TestCase):
    def test_reference_spans_two_render_timestamps_and_expires(self):
        ref = PlaybackReference()
        ref.add(np.full(240, 0.25, dtype="<f4").tobytes(), 24_000, 1.0)
        ref.add(np.full(240, 0.75, dtype="<f4").tobytes(), 24_000, 1.01)
        aligned, recent = ref.at(1.005)
        np.testing.assert_array_equal(aligned[:80], np.full(80, 0.25))
        np.testing.assert_array_equal(aligned[80:], np.full(80, 0.75))
        self.assertTrue(recent)
        self.assertFalse(ref.at(1.5)[1])

    def test_cleaned_speech_uses_confirmation_and_preserves_onset(self):
        device = Mock()
        device.open.return_value = nullcontext()
        processor = Mock()
        onset, ended = Mock(), Mock()
        voiced = np.ones((FRAME_SAMPLES, 1), dtype=np.int16) * 4000
        early = np.ones_like(voiced) * 100
        silence = np.zeros_like(voiced)
        pattern = [(voiced, 0.1)] * 12 + [(early, 0.9)] * 2 + [(voiced, 0.9)] * 10 + [(silence, 0.1)] * 10
        device.read.side_effect = [(pcm, n / 100, False) for n, (pcm, _) in enumerate(pattern)]
        processor.clean.side_effect = [(pcm, score, True) for pcm, score in pattern]
        microphone = SpeechInput(device, processor, Mock())
        done = microphone.capture(CallInput(finish=0.1), Event(), Mock(), ended,
                                  speech_started=onset, reply_pending=lambda: True)
        self.assertTrue(done)
        onset.assert_called_once()
        self.assertGreaterEqual(len(ended.call_args_list), 3)
        frames = np.concatenate([args[0][0] for args in ended.call_args_list])
        self.assertIn(100, frames)

    def test_disabling_barge_in_discards_audio_while_the_reply_is_pending(self):
        device = Mock()
        processor = Mock()
        voice = np.ones((FRAME_SAMPLES, 1), dtype=np.int16) * 4000
        device.read.side_effect = [(voice, i / 100, False) for i in range(20)] + [None]
        processor.clean.return_value = (voice, 1.0, False)
        started, accept = Mock(), Mock()
        microphone = SpeechInput(device, processor, Mock())
        self.assertFalse(microphone.capture(CallInput(interrupt=False), Event(), Mock(), accept,
                                           speech_started=started, reply_pending=lambda: True))
        started.assert_not_called()
        accept.assert_not_called()

    def test_playback_fades_last_sample_and_discards_queued_audio(self):
        token = Event()
        reference = Mock()
        timing = SimpleNamespace(currentTime=2.0, outputBufferDacTime=2.02)
        buffer = bytearray(240 * 4)
        with patch("sounddevice.RawOutputStream"):
            device = ReferencedOutput(24_000, reference, token)
            first = np.full(240, 0.5, dtype="<f4").tobytes()
            second = np.full(240, 0.9, dtype="<f4").tobytes()
            device.write(first + second)
            device._render(buffer, 240, timing, None)
            self.assertEqual(bytes(buffer), first)
            token.set()
            with self.assertRaises(CallbackStop):
                device._render(buffer, 240, timing, None)
        fade = np.frombuffer(buffer, dtype="<f4")
        self.assertAlmostEqual(fade[0], 0.5)
        self.assertEqual(fade[-1], 0.0)
        self.assertTrue(np.all(np.diff(fade) <= 0))
        self.assertEqual(reference.add.call_args.args[0], bytes(buffer))

    def test_installed_webrtc_suppresses_a_delayed_synthetic_echo(self):
        ref = PlaybackReference()
        echo = EchoProcessor(ref)
        if echo.processor is None:
            self.skipTest("requires the local WebRTC audio package")
        rng = np.random.default_rng(6)
        signal = rng.uniform(-0.22, 0.22, size=(202, FRAME_SAMPLES)).astype("<f4")
        clean_power = []
        raw_power = []
        for i in range(2, len(signal)):
            when = i / 100
            ref.add(signal[i].tobytes(), 16_000, when)
            mic = (signal[i - 2] * 14000).astype(np.int16).reshape(-1, 1)
            clean, _, _ = echo.clean(mic, when)
            if i >= 150:
                clean_power.append(np.mean(clean.astype(np.float32) ** 2))
                raw_power.append(np.mean(mic.astype(np.float32) ** 2))
        self.assertLess(np.mean(clean_power), np.mean(raw_power) * 0.5)
