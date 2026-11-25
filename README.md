# Voice Harness

A local Windows desktop app for talking to a Kindroid. It transcribes your microphone
with local Dictate models, sends the completed text turn to Kindroid, and plays the
streamed reply with local Pocket TTS. The chat window shows both sides as they arrive.

## Start on Windows

1. Keep the existing Windows speech bundle in the sibling `kindroid/var` directory
   or restore it in this repository's `var` directory. Double-click
   **Start Voice Harness.bat**, or run `.\scripts\start.ps1 -Assets 'D:\voice-bundle'`
   to specify a different bundle. See [setup and operation](docs/operations.md) for
   the expected layout.
2. Create a local `.env` from [.env.example](.env.example) and enter your Kindroid AI ID
   and API key. Opening the app with credentials requests one recent history page.
   Without credentials the window opens locally and **Call** shows the setup message.
3. Click **Call** and speak when the status shows **Listening**. Pause to send the turn.
   The microphone reopens after the reply audio finishes. **Stop** ends the call.
   If the app asks you to check delivery, inspect the Kindroid conversation and then
   click **I checked delivery** to enable a new call.

The chat tab has live speed and EQ controls, microphone/output meters, and mute for
the next call. The Settings tab configures the recognizer and pause timings. **Save
settings** keeps those choices in this repository. On first run the window uses the
existing bundle's saved chat settings when available.

## Develop

Read [AGENTS.md](AGENTS.md) and the [documentation map](docs/README.md). From PowerShell,
run `.\scripts\test.ps1` for the offline tests. Add `-LocalModels` to also run synthetic
inputs through the installed Parakeet and Pocket TTS adapters. The tests use an
offscreen Qt window and a loopback fixture for the Kindroid transport.

The first workflow is the call window. Standalone STT experiments, voice cloning,
MP3 export, Archive, and Relay remain in the original Kindroid tools repository.
