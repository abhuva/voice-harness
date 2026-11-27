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
   The microphone stays open, and speech recognition loads on demand by default. During a
   reply, about three seconds of sustained speech interrupts Kindroid and keeps audio from
   the start of your utterance. If a dropout drops the current draft, pause and
   speak again in the same call. **Stop** ends the call.
   If the app asks you to check delivery, inspect the Kindroid conversation and then
   click **I checked delivery** to enable a new call.

Expand **Playback** in the chat tab for live speed and EQ controls and mute for
the next call. **Save playback settings** keeps those choices across restarts.
The microphone and output meters stay visible. In the **SST** tab,
clear **Load recognizer on demand and unload after silence** to load at call
startup and keep the model warm after **Stop**. The tab also controls pause timing
and interruption. Click **Save SST settings** to use the selected recognizer and
timing for the next call and across restarts.
On first run the window uses the
existing bundle's saved chat settings when available. Run
`.\scripts\setup-barge-in.ps1` once for speaker echo cancellation with the portable
Windows bundle. The **SST** tab can turn interruptions off or require
estimated RAM checks before speech work starts.

Fluid Speech Pro also offers **CPU** and **GPU (Vulkan)** in **SST → Recognition
device**. The GPU option uses the optional local Windows Vulkan bundle.
See [Fluid Speech Pro on Vulkan](docs/operations.md#fluid-speech-pro-on-vulkan).

In **TTS**, choose the English 18M REFL or 7M REFL model, a compatible voice,
and a **Synthesis device**. **Speak preview** uses the current selection. Click
**Save TTS settings** to use it for calls and across restarts. The tab shows
the saved call voice and device alongside any unsaved changes. See the
[CUDA setup and per-model readings](docs/operations.md#pocket-tts-on-cuda).
In **Voice cloning**, import recordings, choose one to eight clips, and click
**Clone and save new preset**. Your clone appears in the 18M voice list.
See [setup and operation](docs/operations.md) for the recording formats and storage paths.

Click **Monitor** to open a separate window for CPU, GPU, RAM, loaded models,
module activity, recent jobs, and per-turn call timings. In **Jevos**, choose
**CPU (OpenVINO)** or **GPU (Vulkan)**, set the yes/no questions, and click
**Save Jevos settings**. The independent **Enable Jevos decision scoring** switch
can turn the workflow off while keeping the selected device. When enabled,
the chat heatmap shows up to 100 scored samples from three-message windows. See
[monitoring and Jevos](docs/operations.md#monitoring-and-jevos) for setup and timing definitions.

Open the optional **Phone** tab to pair a browser on your Tailscale network.
The phone can use **Push to talk** or **Hands-free · WebRTC** while the laptop
does the processing. See [Phone calls](docs/operations.md#phone-calls).

## Develop

Read [AGENTS.md](AGENTS.md) and the [documentation map](docs/README.md). From PowerShell,
run `.\scripts\test.ps1` for the offline tests. Add `-LocalModels` to also run synthetic
inputs through the installed Parakeet, Pocket TTS, and Jevos adapters. The tests use an
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
