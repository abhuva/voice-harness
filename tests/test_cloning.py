from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock

import numpy as np
import soundfile as sf

from voice_harness.cloning import clone_voice
from voice_harness.contracts import VoiceError
from voice_harness.presets import Playback, Recipe, VoiceLibrary
from voice_harness.recordings import import_recording


class CloningTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.library = VoiceLibrary(self.root / "bundle")
        self.stop = Event()
        self.audio = self.root / "speaker.wav"
        sf.write(self.audio, np.repeat([0.1, 0.5], 16_000 * 4), 16_000)
        self.sample = import_recording(self.library.samples, self.audio, self.stop)
        self.recipe = Recipe("Fixture voice", (self.sample,), 0, 3, 0.45)

    def test_import_preserves_colliding_files_and_reuses_identical_audio(self):
        original = (self.library.samples / self.sample).read_bytes()
        sf.write(self.audio, np.full(16_000, 0.4), 16_000)
        self.assertEqual(import_recording(self.library.samples, self.audio, self.stop), "speaker (2).wav")
        self.assertEqual(import_recording(self.library.samples, self.audio, self.stop), "speaker (2).wav")
        self.assertEqual((self.library.samples / self.sample).read_bytes(), original)
        self.stop.set()
        self.assertIsNone(import_recording(self.library.samples, self.audio, self.stop))
        self.assertEqual(set(self.library.recordings()), {"speaker.wav", "speaker (2).wav"})

    def test_recipe_progress_and_atomic_presets_are_compatible_with_model_selection(self):
        encoder = Mock()

        def encode(reference, profile, stop):
            data, rate = sf.read(reference)
            self.assertEqual(rate, 24_000)
            profile.write_text(str(round(data.max(), 1)), encoding="ascii")
            self.assertEqual(len(self.library.recipes()), encoder.export.call_count - 1)
            return True

        encoder.export.side_effect = encode
        second = Recipe("Fixture voice", (self.sample,), 4, 3, 0.85)
        progress = Mock()
        ids = [clone_voice(self.library, encoder, recipe, self.stop, Mock(), progress)
               for recipe in (self.recipe, second)]
        self.assertNotEqual(*ids)
        self.assertEqual(self.library.recipes()[ids[1]], second)
        self.assertEqual(progress.call_args_list[0].args, (0, 1, "reference clips prepared"))
        self.assertEqual(progress.call_args_list[1].args, (1, 1, "reference clips prepared"))
        for ident, expected in zip(ids, ("0.1", "0.5")):
            config, path = self.library.resolve(Playback("18m", ident, 0.45))
            self.assertEqual(config, "cloning.yaml")
            self.assertEqual((self.library.root / path).read_text(encoding="ascii"), expected)
        with self.assertRaisesRegex(VoiceError, "18M"):
            self.library.resolve(Playback("7m", ids[0], 0.45))
        self.assertEqual(self.library.resolve(Playback("7m", "alba", 0.3)),
                         ("english-7m.yaml", "alba-7m.safetensors"))

    def test_cancel_or_failure_during_encoder_leaves_no_partial_preset(self):
        encoder = Mock()

        def partial(_reference, profile, stop):
            profile.write_text("partial", encoding="ascii")
            if encoder.export.call_count == 1:
                raise VoiceError("Encoder failed.")
            stop.set()
            return True

        encoder.export.side_effect = partial
        with self.assertRaisesRegex(VoiceError, "Encoder failed"):
            clone_voice(self.library, encoder, self.recipe, self.stop, Mock(), Mock())
        self.assertIsNone(clone_voice(self.library, encoder, self.recipe, self.stop, Mock(), Mock()))
        self.assertEqual(list(self.library.presets.iterdir()), [])

    def test_bad_reference_is_rejected_before_encoding(self):
        encoder = Mock()
        with self.assertRaises(VoiceError):
            clone_voice(self.library, encoder, Recipe("invalid", ("../speaker.wav",)),
                        self.stop, Mock(), Mock())
        sf.write(self.library.samples / self.sample, np.zeros(16_000 * 4), 16_000)
        with self.assertRaisesRegex(VoiceError, "silent"):
            clone_voice(self.library, encoder, self.recipe, self.stop, Mock(), Mock())
        encoder.export.assert_not_called()
