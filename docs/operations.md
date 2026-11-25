---
kind: guide
scope: windows-voice
status: current
reviewed: 2026-11-25
---

# Setup and operation

## Windows bundle

Point the launcher at the directory containing these subdirectories. On the
original installation that directory is `kindroid/var`. The launcher tries this
repository's `var` first, then the sibling bundle. `-Assets` overrides that choice.

| Location below the bundle root | Required contents |
|---|---|
| `voice/runtime/` | Established Windows Python installation with PyQt6, NumPy, requests, sherpa-onnx, sounddevice and soundfile. |
| `voice/dictate/` | NICA Dictate's local recognizer adapter, model registry and installed ONNX models. |
| `pocket-tts/4.0.1/` | `voice-engine.exe`, tokenizer, model configurations, weights and voice profiles. Cloning also uses `voice-clone.exe`, `cloning.yaml` and `cloning.safetensors`. |

The existing Kindroid tools checkout documents restoration and rebuilding its
Windows speech bundle. For a standalone installation, restore that bundle to the
paths above or install the Python package into a compatible local environment and
run `voice-harness --root <settings-directory> --assets <bundle-directory>`.
The package contains the app but the portable interpreter, native executable,
models and voice recordings are installed separately under their own terms.
The launcher reuses the bundle in place. The voice process lock, history
spacing/cooldown timestamp, imported recordings and saved clone presets are shared
with the original voice frontend. Close that frontend before starting the new one.
Each repository has its own credentials and preferences. Set `KINDROID_AI_ID` and
`KINDROID_API_KEY` in the local `.env` to enable live calls.

## During a call

- **Call** prepares speech recognition and, if unmuted, the saved Pocket TTS voice.
  Speak after **Listening**. The default finish pause is four seconds. Each chunk
  pause can add words before the turn is sent. The microphone stays open until the call ends.
- Speak again while Kindroid is replying to start the next draft. While audio is
  playing the app drops queued speech and fades the current output. If the prior
  POST has no reply yet, the new draft stays local until the previous delivery
  is confirmed. If the earlier delivery fails or stays uncertain, the app keeps
  the draft in the window and stops. Review the Kindroid conversation before a
  new call.
- Run `.\scripts\setup-barge-in.ps1` once for the portable Windows x64 CPython 3.13
  bundle. It installs the pinned WebRTC package into this checkout's ignored
  `var/` directory. With the package installed, local echo cancellation uses
  the actual audio sent to the speaker. Without it, interruption is available
  during silent reply waits and muted calls. The detector waits through recent
  speaker output. Use **Settings → Interrupt Kindroid replies by speaking** to
  disable interruption if your audio setup triggers it unexpectedly.
- Expand **Playback** in the chat tab to adjust speed, EQ and mute. Speed and EQ
  changes affect playback as it runs. Mute and dictation settings take effect at
  the next call. **Save settings** writes the selected settings for later launches.
- The app tries one recent history request at startup when `.env` is present. It shows
  up to ten displayable messages from a page of up to 100 records within the
  forward 24-hour range and flags when newer records are outside that page. There is
  no automatic pagination.
- **Stop** cancels capture, remote waiting and queued playback. During native
  recognition load or decode the status can remain **Stopping** until the current
  native operation returns. If delivery is unresolved, check the Kindroid
  conversation and mark it reviewed before starting a new call.

## TTS and voice cloning

1. In **Voice cloning**, click **Import audio files…** and select WAV, MP3, FLAC,
   or Ogg recordings of the same speaker. The import copies them into the bundle's
   `samples/` directory. Identical files with the same name reuse the existing copy.
   A different recording with the same name gets a numbered filename.
2. Check one to eight recordings and name the new preset. Set the start offset
   within each clip, the total reference duration (3–30 seconds), and the preset's
   default temperature. Use at least three seconds of audible speech across the
   clips. The worker divides the duration across the selected recordings.
3. Click **Clone and save new preset**. The footer reports clips prepared and then
   shows the encoding stage. Once complete, the recipe and voice state are stored
   together in the bundle's `presets/<id>/`. Use **Load clone settings** to start
   from an earlier recipe and create another version.
4. In **TTS**, choose a model and voice. English 18M REFL supports Alba, the
   original clone if present, and saved clones. English 7M REFL supports its
   own Alba voice. **Speak preview** plays locally with the chat speed and EQ
   settings. Unmute playback in **Kindroid chat** to hear it. **Use for Kindroid
   calls** saves the choice for future calls.

**Stop** cancels queued playback and encoding. An interrupted multi-file import
keeps completed recordings and reports the count. Interrupted encoding leaves
the last saved presets available and requires a new explicit submission.
Back up `samples/` and `presets/` privately to preserve references and saved voices.

## Verification

Run `.\scripts\test.ps1` for the offline suite, or use `-Pattern test_window.py
-TimeoutSeconds 30` for a targeted check. `-LocalModels` also loads the installed
multilingual recognizer, 18M Alba, the 18M clone encoder, and 7M Alba. It decodes
synthetic silence, builds a preset from a generated waveform, renders fixture
sentences to PCM, and checks resident-process measurements. These checks use
temporary clone files and run without real audio devices or Kindroid.

Physical acceptance requires a normal Windows desktop with the intended microphone
and output device. With explicit approval and a disposable Kin, launch with that
Kin configured and permit one recent history read. Use two planned utterances
to test interruption of an in-flight reply. Check that the second starts with
its first words, playback fades promptly, and both utterances are delivered at
most once. Compare headphones and speakers and check that speaker-only playback
does not trigger a new turn. Offline tests use generated and mocked audio.
