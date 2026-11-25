from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from voice_harness.contracts import CallInput, VoiceError
from voice_harness.presets import Playback
from voice_harness.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_legacy_profile_is_read_and_new_settings_are_saved_locally(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "installed-assets"
            preferences = bundle / "voice/chat-stt.json"
            preferences.parent.mkdir(parents=True)
            legacy = CallInput(model="german", threads=2, finish=3.5)
            original = json.dumps(asdict(legacy))
            preferences.write_text(original, encoding="utf-8")
            settings = Settings(root, bundle)
            self.assertEqual(settings.load()[0], legacy)
            updated = CallInput(model="multilingual", finish=2.0)
            voice = Playback("7m", "alba", 0.5, speed=0)
            settings.save(updated, voice)
            self.assertEqual(settings.load(), (updated, voice))
            self.assertEqual(preferences.read_text(encoding="utf-8"), original)
            self.assertFalse((bundle / "pocket-tts/4.0.1/chat-voice.json").exists())

    def test_missing_credentials_report_only_local_filenames(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(VoiceError, r"local \.env"):
                Settings(root, root / "assets").credentials()
