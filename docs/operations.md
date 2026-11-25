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
| `voice/runtime/` | Established Windows Python installation with PyQt6, NumPy, requests, sherpa-onnx and sounddevice. |
| `voice/dictate/` | NICA Dictate's local recognizer adapter, model registry and installed ONNX models. |
| `pocket-tts/4.0.1/` | `voice-engine.exe`, tokenizer, model configuration, weights and the voice profile you use. |

The existing Kindroid tools checkout documents restoration and rebuilding its
Windows speech bundle. For a standalone installation, restore that bundle to the
paths above or install the Python package into a compatible local environment and
run `voice-harness --root <settings-directory> --assets <bundle-directory>`.
The package contains the app but the portable interpreter, native executable,
models and voice recordings are installed separately under their own terms.
The launcher reuses the bundle in place. Only the voice process lock and history
spacing/cooldown timestamp are shared with the original voice frontend. Close
that frontend before starting the new one. Each repository has its own credentials
and preferences. Set the new `.env` locally.

## During a call

- **Call** prepares speech recognition and, if unmuted, the saved Pocket TTS voice.
  Speak after **Listening**. The default finish pause is four seconds. Each chunk
  pause can add words before the microphone finishes. Muted calls display replies
  and reopen the microphone when the reply finishes.
- Speed and EQ changes affect playback as it runs. Mute and dictation settings take
  effect at the next call. **Save settings** writes the selected settings for later launches.
- The app tries one recent history request at startup when `.env` is present. It shows
  up to ten displayable messages from a page of up to 100 records within the
  forward 24-hour range and flags when newer records are outside that page. There is
  no automatic pagination.
- **Stop** cancels capture, remote waiting and queued playback. During native
  recognition load or decode the status can remain **Stopping** until the current
  native operation returns. If delivery is unresolved, check the Kindroid
  conversation and mark it reviewed before starting a new call.

## Verification

Run `.\scripts\test.ps1` for the offline suite, or use `-Pattern test_window.py
-TimeoutSeconds 30` for a targeted check. `-LocalModels` also loads the installed
multilingual recognizer and 18M Alba voice, decodes synthetic silence, synthesizes
one fixture sentence to PCM, and checks resident-process measurements. This test
opens neither audio device and contacts no service.

Physical acceptance requires a normal Windows desktop with the intended microphone
and output device. With explicit approval and a disposable Kin, launch with that
Kin configured, permit one recent history read, and say one planned test utterance.
Verify its partial transcript, one delivery, streamed reply, audible playback and
the return to listening. Stop while listening so there is no second send.
