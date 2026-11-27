from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from voice_harness.contracts import CallInput, VoiceError
from voice_harness.presets import OutputSettings, Playback, SynthesisSettings, VoiceLibrary
from voice_harness.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_legacy_profile_is_read_and_new_settings_are_saved_locally(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "installed-assets"
            preferences = bundle / "voice/chat-stt.json"
            preferences.parent.mkdir(parents=True)
            legacy = CallInput(model="german", threads=2, finish=3.5)
            options = asdict(legacy)
            options.pop("stt_idle")
            options.pop("barge_in")
            options.pop("stt_on_demand")
            options.pop("stt_backend")
            original = json.dumps(options)
            preferences.write_text(original, encoding="utf-8")
            settings = Settings(root, bundle)
            self.assertEqual(settings.load_sst(), (legacy, False))
            updated = CallInput(model="multilingual", finish=2.0, stt_idle=45, barge_in=2.5, stt_on_demand=False)
            voice = SynthesisSettings("7m", "alba", 0.5)
            settings.save_sst(updated, strict_memory=False)
            settings.save_tts(voice)
            self.assertEqual(settings.load_sst(), (updated, False))
            self.assertEqual(settings.load_tts(), voice)
            self.assertEqual(preferences.read_text(encoding="utf-8"), original)
            self.assertFalse((bundle / "pocket-tts/4.0.1/chat-voice.json").exists())

    def test_strict_memory_is_opt_in_and_survives_saving_a_new_voice(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(root, root / "assets")
            self.assertFalse(settings.load_sst()[1])
            settings.save_sst(CallInput(), strict_memory=True)
            self.assertTrue(Settings(root, root / "assets").load_sst()[1])
            settings.save_tts(SynthesisSettings("7m", "alba", 0.3))
            self.assertTrue(settings.load_sst()[1])
            settings.save_sst(CallInput(), strict_memory=False)
            self.assertFalse(settings.load_sst()[1])

    def test_version_one_preferences_upgrade_on_scoped_save(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(root, root / "assets")
            settings.local.mkdir()
            voice = Playback("18m", "alba", 0.4, speed=0, bass=3, backend="cuda")
            original = json.dumps({"version": 1, "dictation": asdict(CallInput(threads=2)),
                                   "playback": asdict(voice), "strict_memory": True})
            settings.preferences.write_text(original, encoding="utf-8")
            self.assertEqual(settings.load_tts(), voice.synthesis)
            self.assertEqual(settings.load_output(), OutputSettings(bass=3, muted=True))
            self.assertEqual(settings.preferences.read_text("utf-8"), original)
            settings.save_sst(CallInput(threads=5), strict_memory=True)
            stored = json.loads(settings.preferences.read_text("utf-8"))
            self.assertEqual(stored["version"], 2)
            self.assertEqual(settings.load_tts(), voice.synthesis)
            output = OutputSettings(speed=1.1, treble=1, muted=True)
            settings.save_output(output)
            self.assertEqual(settings.load_output(), output)
            self.assertEqual(settings.load_sst(), (CallInput(threads=5), True))
            self.assertEqual(settings.load_tts(), voice.synthesis)

    def test_missing_saved_tts_voice_cannot_block_saving_other_sections(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(root, root / "assets")
            settings.pocket.mkdir(parents=True)
            preset = settings.pocket / "custom.safetensors"
            preset.touch()
            settings.save_tts(SynthesisSettings(preset="original", backend="cuda"))
            preset.unlink()
            with self.assertRaisesRegex(VoiceError, "saved voice file is missing"):
                VoiceLibrary(settings.pocket).resolve(settings.load_tts())
            settings.save_sst(CallInput(threads=2), strict_memory=True)
            settings.save_output(OutputSettings(mid=-2))
            self.assertEqual(settings.load_sst(), (CallInput(threads=2), True))
            self.assertEqual(settings.load_output(), OutputSettings(mid=-2))
            self.assertEqual(settings.load_tts(), SynthesisSettings(preset="original", backend="cuda"))

    def test_missing_credentials_report_only_local_filenames(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(VoiceError, r"local \.env"):
                Settings(root, root / "assets").credentials()
