from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from sounddevice import CallbackStop

from voice_harness.contracts import CallInput, VoiceError
from voice_harness.dictation import capture_turn
from voice_harness.echo import EchoProcessor, FRAME_SAMPLES, PlaybackReference
from voice_harness.microphone import WindowsMicrophone
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
        done = microphone.capture(CallInput(finish=0.1, barge_in=0.1), Event(), Mock(), ended,
                                  speech_started=onset, reply_pending=lambda: True)
        self.assertTrue(done)
        self.assertAlmostEqual(microphone.last_speech_at, 0.24)
        onset.assert_called_once()
        self.assertGreaterEqual(len(ended.call_args_list), 3)
        frames = np.concatenate([args[0][0] for args in ended.call_args_list])
        self.assertIn(100, frames)

    def test_cold_recognizer_keeps_recording_and_decodes_two_seconds_of_lead_in(self):
        def frame(level):
            return np.full((FRAME_SAMPLES, 1), level, dtype=np.int16)

        device = Mock()
        levels = [0] * 220 + [4000] * 35 + [0] * 10
        levels[45] = 95  # Quiet onset about 1.75 seconds before the trigger.
        device.read.side_effect = [(frame(level), i / 100, False) for i, level in enumerate(levels)]
        processor = Mock()
        processor.clean.side_effect = lambda samples, at: (samples, 1.0, False)
        microphone = SpeechInput(device, processor, Mock())
        loading, buffered, release = Event(), Event(), Event()
        decoded = []
        recognizer = Mock()
        recognizer.transcribe.side_effect = lambda samples: decoded.append(samples) or "Complete utterance"

        @contextmanager
        def open_recognizer(stop, stage):
            loading.set()
            self.assertTrue(release.wait(3))
            yield recognizer

        def stage(message):
            if message.startswith("Audio buffered"):
                buffered.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(capture_turn, microphone, open_recognizer, CallInput(finish=0.1),
                                 Event(), stage, Mock(), speech_started=Mock(), reply_pending=lambda: False)
            try:
                self.assertTrue(loading.wait(2))
                self.assertTrue(buffered.wait(2))
                self.assertEqual(device.read.call_count, len(levels))
                self.assertFalse(decoded)
            finally:
                release.set()
            self.assertEqual(result.result(3), "Complete utterance")

        self.assertIn(95, np.concatenate(decoded))
        self.assertIn(4000, np.concatenate(decoded))
        self.assertAlmostEqual(microphone.last_speech_at, 2.55)

    def test_barge_in_requires_three_seconds_and_keeps_all_speech(self):
        device = Mock()
        voiced = np.full((FRAME_SAMPLES, 1), 4000, dtype=np.int16)
        quiet = np.zeros_like(voiced)
        pattern = [voiced] * 180 + [quiet] * 20 + [voiced] * 120 + [quiet] * 40
        device.read.side_effect = [(pcm, i / 100, False) for i, pcm in enumerate(pattern)]
        processor = Mock()
        processor.clean.side_effect = lambda samples, at: (samples, 1.0, False)
        began, accepted = [], Mock()
        microphone = SpeechInput(device, processor, Mock())
        complete = microphone.capture(CallInput(finish=0.4), Event(), Mock(), accepted,
                                      speech_started=lambda: began.append(device.read.call_count),
                                      reply_pending=lambda: True)
        self.assertTrue(complete)
        self.assertEqual(began, [300])
        self.assertEqual(sum(np.count_nonzero(args.args[0]) for args in accepted.call_args_list),
                         300 * FRAME_SAMPLES)

    def test_barge_in_keeps_weak_syllables_and_small_dropouts_during_the_speaking_window(self):
        voiced = np.full((FRAME_SAMPLES, 1), 4000, dtype=np.int16)
        weak = np.full_like(voiced, 60)
        pattern = [voiced] * 24 + [weak] * 36
        levels = pattern * 5 + [voiced] * 20 + [np.zeros_like(voiced)] * 150
        device, processor = Mock(), Mock()
        device.read.side_effect = [(pcm, i / 100, int(i in (65, 125, 180, 240)))
                                   for i, pcm in enumerate(levels)]
        processor.clean.side_effect = lambda samples, at: (samples, 0.75, True)
        began, accepted = [], Mock()
        microphone = SpeechInput(device, processor, Mock())
        self.assertTrue(microphone.capture(CallInput(finish=1.5), Event(), Mock(), accepted,
                                          speech_started=lambda: began.append(device.read.call_count),
                                          reply_pending=lambda: True))
        self.assertEqual(began, [301])
        frames = np.concatenate([args.args[0] for args in accepted.call_args_list])
        self.assertEqual(np.count_nonzero(frames == 4000), 140 * FRAME_SAMPLES)
        self.assertIn(60, frames)

    def test_only_active_playback_requires_vad_to_confirm_interrupting_speech(self):
        signal = np.full((FRAME_SAMPLES, 1), 4000, dtype=np.int16)
        for playing in (False, True):
            with self.subTest(playing=playing):
                device, processor = Mock(), Mock()
                device.read.side_effect = [(signal, i / 100, 0) for i in range(350)] + [None]
                processor.clean.return_value = (signal, 0.2, playing)
                started = Mock()
                SpeechInput(device, processor, Mock()).capture(
                    CallInput(), Event(), Mock(), Mock(), speech_started=started, reply_pending=lambda: True,
                )
                self.assertEqual(started.call_count, 0 if playing else 1)

    def test_short_overlap_is_discarded_and_live_speech_can_continue_when_reply_ends(self):
        voiced = np.full((FRAME_SAMPLES, 1), 3500, dtype=np.int16)
        quiet = np.zeros_like(voiced)
        short_overlap = 95
        for reply_end, expected in ((140, []), (35, [36])):
            with self.subTest(reply_end=reply_end):
                device, processor = Mock(), Mock()
                pattern = [voiced] * short_overlap + [quiet] * 55
                device.read.side_effect = [(pcm, i / 100, False) for i, pcm in enumerate(pattern)] + [None]
                processor.clean.side_effect = lambda samples, at: (samples, 1.0, False)
                began, accepted = [], Mock()
                microphone = SpeechInput(device, processor, Mock())
                complete = microphone.capture(CallInput(finish=0.4), Event(), Mock(), accepted,
                                              speech_started=lambda: began.append(device.read.call_count),
                                              reply_pending=lambda: device.read.call_count <= reply_end)
                self.assertEqual(complete, bool(expected))
                self.assertEqual(began, expected)
                self.assertEqual(accepted.called, bool(expected))

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

    def test_device_counts_queue_losses_and_estimates_flagged_driver_overflows(self):
        microphone = WindowsMicrophone()
        audio = np.zeros((FRAME_SAMPLES, 1), dtype=np.int16)
        stop = Event()

        def enqueue(adc, overflow=False):
            microphone._capture(audio, FRAME_SAMPLES, SimpleNamespace(inputBufferAdcTime=adc, currentTime=adc),
                                SimpleNamespace(input_overflow=overflow))

        enqueue(1.00)
        self.assertEqual(microphone.read(stop)[2], 0)
        enqueue(1.04, overflow=True)
        self.assertEqual(microphone.read(stop)[2], 3)
        enqueue(1.05, overflow=True)
        self.assertEqual(microphone.read(stop)[2], 1)
        for i in range(microphone._frames.maxlen + 4):
            enqueue(1.06 + i / 100)
        self.assertEqual(microphone.read(stop)[2], 4)
        microphone.discard_buffered()
        enqueue(4.0)
        self.assertEqual(microphone.read(stop)[2], 0)
        microphone._finished()
        with self.assertRaisesRegex(VoiceError, "Windows microphone stopped"):
            microphone.read(stop)

    def test_small_dropout_keeps_the_valid_frame_and_requires_a_clean_finish_pause(self):
        device, processor = Mock(), Mock()
        voice = np.full((FRAME_SAMPLES, 1), 4000, dtype=np.int16)
        quiet = np.zeros_like(voice)
        recovered = np.full_like(voice, 6000)
        # The missing frames and the preceding quiet would cross the finish threshold.
        pattern = ([(voice, 0)] * 12 + [(quiet, 0)] * 9 + [(recovered, 2)] +
                   [(quiet, 0)] * 10)
        device.read.side_effect = [(pcm, index / 100, missing)
                                   for index, (pcm, missing) in enumerate(pattern)]
        processor.clean.side_effect = lambda samples, at: (samples, 1.0, False)
        accepted = Mock()
        microphone = SpeechInput(device, processor, Mock())
        self.assertTrue(microphone.capture(CallInput(finish=0.1), Event(), Mock(), accepted,
                                           speech_started=Mock(), reply_pending=lambda: False))
        self.assertEqual(device.read.call_count, len(pattern))
        self.assertIn(6000, np.concatenate([call.args[0] for call in accepted.call_args_list]))
        self.assertAlmostEqual(microphone.last_speech_at, 0.22)

    def test_playback_fades_last_sample_and_discards_queued_audio(self):
        token = Event()
        reference = Mock()
        timing = SimpleNamespace(currentTime=2.0, outputBufferDacTime=2.02)
        buffer = bytearray(240 * 4)
        heard = Mock()
        with patch("sounddevice.RawOutputStream"), patch("voice_harness.render_device.time.monotonic", return_value=30):
            device = ReferencedOutput(24_000, reference, token, first_audio=heard)
            device._render(buffer, 240, timing, None)
            heard.assert_not_called()
            first = np.full(240, 0.5, dtype="<f4").tobytes()
            second = np.full(240, 0.9, dtype="<f4").tobytes()
            device.write(first + second)
            device._render(buffer, 240, timing, None)
            self.assertEqual(bytes(buffer), first)
            self.assertAlmostEqual(heard.call_args.args[0], 30.02)
            token.set()
            with self.assertRaises(CallbackStop):
                device._render(buffer, 240, timing, None)
        heard.assert_called_once()
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
