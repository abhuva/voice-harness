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
2. The local `.env` holds `KINDROID_AI_ID` and `KINDROID_API_KEY`. Opening the app with
   credentials requests one recent history page. The TTS and cloning tools also run
   locally without Kindroid credentials.
3. Click **Call** and speak when the status shows **Listening**. Pause to send the turn.
   The microphone stays open for the call. Start speaking while Kindroid is replying
   to interrupt it and begin a new turn. **Stop** ends the call.
   If the app asks you to check delivery, inspect the Kindroid conversation and then
   click **I checked delivery** to enable a new call.

Expand **Playback** in the chat tab for live speed and EQ controls and mute for
the next call. The microphone and output meters stay visible. The **Settings** tab
configures the recognizer and pause timings. **Save settings** keeps those choices
in this repository. On first run the window uses the
existing bundle's saved chat settings when available. Run
`.\scripts\setup-barge-in.ps1` once for speaker echo cancellation with the portable
Windows bundle. The **Settings** tab can turn interruptions off.

In **TTS**, choose the English 18M REFL or 7M REFL model, select a compatible voice,
and click **Speak preview**. Click **Use for Kindroid calls** to save the choice.
In **Voice cloning**, import recordings, choose one to eight clips, and click
**Clone and save new preset**. Your clone appears in the 18M voice list.
See [setup and operation](docs/operations.md) for the recording formats and storage paths.

## Develop

Read [AGENTS.md](AGENTS.md) and the [documentation map](docs/README.md). From PowerShell,
run `.\scripts\test.ps1` for the offline tests. Add `-LocalModels` to also run synthetic
inputs through the installed Parakeet and Pocket TTS adapters. The tests use an
offscreen Qt window and a loopback fixture for the Kindroid transport.

Standalone STT experiments, MP3 export, Archive, and Relay remain in the original
Kindroid tools repository.

## Build the documentation

The Markdown under [docs/](docs/README.md) is the source for the Zensical site.
From PowerShell, create a documentation environment and build the site.

```powershell
py -3.13 -m venv var/docs-venv
.\var\docs-venv\Scripts\python.exe -m pip install -r requirements-docs.txt
.\var\docs-venv\Scripts\zensical.exe build --clean
```

Run `zensical serve` from that environment for a local preview at
`http://127.0.0.1:8767/`. The GitHub Actions workflow builds and deploys the
site on pushes to `main`.
