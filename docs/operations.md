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

- **Call** opens the microphone after preparing the saved Pocket TTS voice when
  unmuted and the recognizer when warm mode is selected. Speak after **Listening**.
  In dynamic mode, capture keeps up to two seconds of cleaned mic lead-in and
  continues recording while speech recognition loads. The
  default finish pause is four seconds. Chunk pauses can add words before the
  turn is sent. The microphone stays open until the call ends.
- Speak for about three seconds by default to interrupt a pending or playing
  reply. The detector measures the elapsed span of sustained speech, allowing
  gaps between words and brief microphone dropouts. If the overlap ends
  early while Kindroid is still replying, it is discarded. If the reply ends while
  you are still speaking, the captured audio becomes your next normal turn.
  When interrupted, the app drops queued speech and fades the current output.
  If the prior
  POST has no reply yet, the new draft stays local until the previous delivery
  is confirmed. If the earlier delivery fails or stays uncertain, the app keeps
  the draft in the window and stops. Review the Kindroid conversation before a
  new call.
- Brief microphone gaps during dictation are filled with silence, up to 100 ms
  per utterance. The finish-pause timer uses captured audio. If the loss exceeds
  that limit, the app marks the draft **Not sent · microphone dropout** and
  keeps the call open. Pause for the configured finish interval, then speak
  again when the status returns to **Listening**. If the capture device stops,
  the app reports the device error.
- Run `.\scripts\setup-barge-in.ps1` once for the portable Windows x64 CPython 3.13
  bundle. It installs the pinned WebRTC package into this checkout's ignored
  `var/` directory. With the package installed, local echo cancellation uses
  the actual audio sent to the speaker. Without it, interruption is available
  during silent reply waits and muted calls. The detector waits through recent
  speaker output. Use **SST → Interrupt Kindroid replies by speaking** to
  disable interruption if your audio setup triggers it unexpectedly.
- Expand **Playback** in the chat tab to adjust speed, EQ and mute. Speed and EQ
  affect playback as it runs. Mute applies to the next call. Click **Save playback
  settings** to persist those controls. Click **Save SST settings** in **SST**
  to apply recognizer and dictation changes to the next call.
- The app tries one recent history request at startup when `.env` is present. It shows
  up to ten displayable messages from a page of up to 100 records within the
  forward 24-hour range and flags when newer records are outside that page. There is
  no automatic pagination.
- **Stop** cancels capture, remote waiting and queued playback. Recognition follows
  the selected residency mode after the call ends. A different recognition model
  or thread count takes effect when the next call loads the recognizer. A different TTS
  model, voice or temperature reloads synthesis. A preview also uses the synthesis
  slot. App exit and foreground memory pressure can release idle models.
- During native recognition load or decode the status can remain **Stopping**
  until that operation returns. If delivery is unresolved, check the Kindroid
  conversation and mark it reviewed before starting a new call.

## Memory settings

In **SST**, **Load recognizer on demand and unload after silence** is enabled by
default. Recognition loads when you speak. **Unload after silence** defaults to
60 seconds from the last accepted speech frame. Unloading waits for any ongoing
decode to finish. The microphone and echo cancellation stay active during a call.
Cold recognition has priority over background scoring.

Clear the checkbox to load recognition when the next call starts and keep it
warm through pauses and between calls. App exit, a model or thread-count change,
and memory pressure from other foreground work can still release the cached
model. **Save SST settings** persists the choice and cancels the countdown on an
already warm recognizer. Saving dynamic mode starts a countdown for a cached
recognizer that has no deadline. **Speech needed to interrupt** sets the minimum
duration of sustained speech during a reply.

A call can start when Voice Harness is idle, even if a previous call measured
a larger working set than the currently available RAM. The harness still checks
estimates when it would run work concurrently.

Enable **SST → Wait for estimated RAM before starting speech work**
to require known memory estimates to fit before starting an operation.
When available RAM is the blocker, the wait status shows the estimate and
available RAM. Click **Stop** to change the setting while waiting, then click
**Save SST settings**. The tab shows the saved recognizer and device and flags
unsaved changes.

## Fluid Speech Pro on Vulkan

Run `.\scripts\setup-fluid-vulkan.ps1` once in PowerShell to install the pinned
Windows worker and Q8 model under `var/fluid-vulkan/`. The download is about
930 MiB and the script verifies both assets before publishing the installation.
The baseline speech bundle still supplies the Windows app runtime.
[Windows worker release](https://github.com/mudler/parakeet.cpp/releases/tag/v0.6.1) ·
[Fluid Speech Pro GGUF](https://huggingface.co/FluidVoice-Labs/fluid-speech-pro-gguf)

In **SST**, select **Fluid Speech Pro** as the recognizer and **GPU (Vulkan)**
under **Recognition device**. Choose **CPU** to use the existing Sherpa-ONNX
model. Click **Save SST settings** to apply the selection to the next call.
With **Load recognizer on demand and unload after silence** cleared, the GPU
worker warms up before **Listening** and stays resident between matching calls.
In dynamic mode, the first utterance can wait while the worker warms up.

Open **Monitor → Models** to see the selected device, state and host working
set at load. NVIDIA telemetry in the Monitor shows free dedicated VRAM. On the
tested GTX 1050 Ti, the worker peaked near 980 MiB of VRAM and about 975 MiB of
host working set during cold load. These observations vary with the machine
and input. A GPU startup or decode failure appears in the call status. Select
**CPU** before calling again if the Vulkan device is unavailable.

## Monitoring and Jevos

Click **Monitor** from any tab to open a separate read-only window. It samples
host CPU, Python CPU, available RAM, and NVIDIA GPU utilization and VRAM when
`nvidia-smi` is available. The **Models** tab shows each model's device, state,
host RAM, CPU usage, GPU usage and dedicated VRAM. CPU is a percentage of the
machine's total CPU capacity. GPU shows the worker's busiest Windows GPU engine.
The CPU recognizer shares the Python process, so its CPU column shows **Shared**
and its host RAM is the working-set increase observed at load. An em dash marks
an unavailable reading. The tab also shows the last cold STT load time and the
observed host working-set decrease at idle unload.
**Modules** shows current activity. **Jobs** shows recent durable claims and
terminal outcomes. **Call timings** keeps up to 100 turns from the running app.

| Interval | Start | End |
|---|---|---|
| Last speech → STT ready | End of the last detected speech frame | The full transcript is ready, including the configured finish pause |
| Send → first Kindroid text | Submission of the one-shot POST | The first decoded reply text |
| First text → first audio | The first decoded reply text | Estimated DAC time of the first non-silent output frame |

A blank milestone appears as an em dash. Muted or interrupted turns can leave
the audio interval blank. The hardware readings are observations and the
configured CPU and memory figures for admission are estimates.

In **Jevos**, choose a **Scoring device**, set the yes/no questions, and click
**Save Jevos settings**. The app starts its own loopback worker when a scoring
job is admitted.

- **CPU (OpenVINO)** uses the existing Jevos v5 INT8 installation. With no
  saved settings, the app detects and enables it if it finds the default folder
  at `<assets>/archive-models/jevos-v5-openvino-int8-v1`. To use a separate
  installation, choose the folder containing `jev.exe` (or `jev/jev.exe`) and
  `model/openvino_model.xml`. [Jevos v5 Windows and OpenVINO assets](https://github.com/feder-cr/jev/releases/tag/jevos-v5)
- **GPU (Vulkan)** uses the Jevos v5 Q8 GGUF on Windows x64 with a single NVIDIA
  adapter and working `nvidia-smi` VRAM readings. Run
  `.\scripts\setup-jevos-vulkan.ps1` from the repository once, then select
  **GPU (Vulkan)** and save. Setup verifies the model and runner and installs
  them under `var/jevos-vulkan/`. GPU scores can differ from CPU scores.
  If GPU initialization fails, use the footer to diagnose it or save the CPU device.

To turn scoring off, clear **Enable Jevos decision scoring** and click
**Save Jevos settings**. This applies during a call and persists across restarts.
The footer shows when active or queued scoring has stopped and the local model
has unloaded. With scoring off, the heatmap is hidden and Jevos collects no
conversation context. Your current call continues. When you re-enable scoring,
the heatmap shows saved scores and new samples start with three complete messages
received after re-enabling. You can save a different scoring device with Jevos off.
Your last saved CPU folder and questions are kept.

After an outgoing message is confirmed or a Kindroid stream completes, Jevos
can score the latest three complete messages with their full paragraph breaks.
Displayed history can supply context. If scoring waits for resources, the
newest pending snapshot replaces older waiting snapshots. The footer shows
waiting, loading, scoring, or the outcome across tabs. Foreground jobs take
priority. Jevos GPU scoring waits for dictation or the current reply to finish.
When speech preempts an active GPU score, Jevos queues the snapshot again
unless a newer one arrived. A dropped or empty utterance releases the speech
gate when capture returns to listening.
After a score, the model can stay warm for up to 30 seconds between requests.
When memory blocks a score, the footer shows the amount Jevos needs and the
budget available for that job after reservations. The Monitor shows system-wide
free memory. Scoring can wait while speech models stay loaded.
The GPU worker has a 4,096-token context and reports an error if the full
evidence and question exceed it.

The heatmap beside the chat shows questions vertically and sample time
horizontally. Scroll right for the newest samples. Each cell gives Jevos's
estimated probability of **yes**. Hover for the full question, timestamp,
score, and model. Changing a question preserves the wording of earlier samples.
SQLite retains 100 scored samples across launches. The scoring workflow keeps
its message snapshots in memory and feeds the heatmap. Conversation replies
continue through the existing Kindroid path.

## Phone calls

The phone supplies the microphone and plays the reply. The laptop runs the
call, uses the saved SST and TTS choices, and contacts Kindroid. The phone page
shows the current call and its messages. The laptop and phone share one call
slot, and either can end the phone call. Unmute **Playback** on the laptop
to hear replies on the phone.

1. Put the laptop and phone on your Tailscale network. Keep the laptop awake
   with Voice Harness running while using the phone.
2. Install the optional runtime once from the repository with
   `.\scripts\setup-phone.ps1`. This uses uv and CPython 3.13.
3. In an administrator PowerShell window, run `tailscale serve 8768`.
   Leave it running and copy the private HTTPS address it prints.
   [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
4. Restart Voice Harness from a regular PowerShell window with
   `.\scripts\start.ps1 -Phone -PhoneOrigin 'https://your-laptop.your-tailnet.ts.net'`.
   Open the URL shown on the desktop **Phone** tab, or open the plain HTTPS
   address and enter the six-digit **Pairing PIN** shown there. To choose your
   own, enter 4 to 8 digits in the laptop’s **Phone** tab and click **Set pairing PIN**.
   The pairing link updates immediately. Restarting Voice Harness generates a new PIN.
5. Choose a mode, tap **Start call**, and grant microphone access. In
   **Push to talk**, hold the talk button and release to submit. In
   **Hands-free · WebRTC**, speak and pause as you would at the laptop.
   Use **End call** when finished.

Push-to-talk streams the reply over the same secure connection that carries
the phone controls. WebRTC uses the HTTPS connection for setup and a separate
UDP audio connection over Tailscale. The launcher discovers the laptop's
Tailscale IPv4 address when it can run `tailscale ip -4`. Supply
`-PhoneIceAddress` with that address if discovery is unavailable. WebRTC
requires the phone to reach the laptop on UDP through the tailnet and host
firewall. Push-to-talk also works when the network only allows HTTPS.

Keep the browser page in the foreground during the first phone test. WebRTC
uses the phone browser's echo cancellation. Tap **Play reply audio** if the
browser asks you to start playback. The laptop preserves unresolved send
claims when the connection drops and displays its usual delivery check.
For a same-laptop test, launch with `-Phone` and open the loopback address in
the **Phone** tab.

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
   settings. Unmute playback in **Kindroid chat** to hear it. **Save TTS settings**
   saves the model, voice, temperature, and device for future calls. The tab shows
   which choice is saved and flags unsaved changes.

**Stop** cancels queued playback and encoding. An interrupted multi-file import
keeps completed recordings and reports the count. Interrupted encoding leaves
the last saved presets available and requires a new explicit submission.
Back up `samples/` and `presets/` privately to preserve references and saved voices.

### Pocket TTS on CUDA

Run `.\scripts\setup-pocket-cuda.ps1` once in PowerShell on an NVIDIA Windows
system with uv and Python 3.13. Setup downloads the isolated Python runtime
dependencies, including a roughly 2.7 GiB CUDA wheel. The CUDA worker uses the
existing 18M or 7M model, local voice preset, and audio settings. Voice cloning
continues to encode with the CPU worker. [Pocket TTS GPU notes](https://github.com/kyutai-labs/pocket-tts/blob/v4.0.1/README.md#running-on-gpu)

In **TTS**, pick **GPU (CUDA)** under **Synthesis device**. **Speak preview** uses
the selected device immediately. **Save TTS settings** saves the device and
voice for the next call. Pick **CPU** any time between jobs to use the native
playback worker. Switching devices replaces the resident TTS worker when the
next preview or audible call starts. Recognition keeps its own device selection.

## Verification

The application dependencies are locked in `uv.lock`. On Windows or Linux
with Python 3.13 and uv, install the application with `uv sync --locked`. Run
the portable checks with the two commands below. They use fixture audio and
connectors.

```text
uv run --locked python -m unittest discover -s tests -p test_harness_contract.py -v
uv run --locked python -m unittest discover -s tests -p test_call.py -v
```

On the supported Windows speech bundle, run `.\scripts\test.ps1` for the offline suite, or use `-Pattern test_window.py
-TimeoutSeconds 30` for a targeted check. `-LocalModels` also loads the installed
multilingual recognizer, 18M Alba, the 18M clone encoder, 7M Alba, and Jevos if
its OpenVINO bundle is installed. It decodes
synthetic silence, builds a preset from a generated waveform, renders fixture
sentences to PCM, checks resident-process measurements, and scores synthetic chat messages. These checks use
temporary clone files and run without real audio devices or Kindroid.

Audio fixtures also exercise two seconds of pre-roll, capture during a cold load,
short overlaps, and sustained speech with brief microphone loss during a reply.
Recognition fixtures check expiry and reuse across calls in both residency modes.
Settings fixtures check module-specific saves, failed writes, migration from
version 1, and the saved model choices after a restart.
After Vulkan setup, run `.\scripts\test.ps1 -Pattern test_fluid_vulkan.py -LocalModels`
to verify GPU selection, silent PCM warm-up, decoding and worker exit offline.
After CUDA setup, run `.\scripts\test.ps1 -Pattern test_pocket_cuda.py -LocalModels`
to verify the resident CUDA worker with synthetic preview text.
The offline Jevos fixtures check cancellation of queued and active scoring,
RAM and VRAM admission, GPU scoring after speech preemption and microphone
dropout, warm-model unload, and saved choices across restarts.
After Jevos Vulkan setup, run
`.\scripts\test.ps1 -Pattern test_jevos_vulkan.py -LocalModels` to compare CPU
and GPU scores on synthetic messages. Check **Models** for the Jevos worker's
host working set and dedicated VRAM while it is warm.

After phone runtime setup, run `.\scripts\test.ps1 -Pattern test_phone.py`.
The fixture checks pairing, a single phone session, push-to-talk release, and
bidirectional WebRTC audio through a synthetic recognizer, connector, and
synthesizer. It also checks that disconnect releases the call.

Physical acceptance requires a normal Windows desktop with the intended microphone
and output device. With explicit approval and a disposable Kin, launch with that
Kin configured and permit one recent history read. Use two planned utterances
to test interruption of an in-flight reply. Check that the second starts with
its first words, playback fades promptly, and both utterances are delivered at
most once. Compare headphones and speakers and check that speaker-only playback
does not trigger a new turn. Offline tests use generated and mocked audio.

For live Vulkan acceptance, use an approved disposable conversation. Observe
**GPU (Vulkan)** in the Monitor, speak a short utterance, verify the transcript
and reply, and click **Stop**. Switch back to CPU before the next call and verify
that the old GPU worker releases its VRAM. Check the chosen residency mode after Stop.

Offline CUDA preview acceptance uses generated speech and a fixture playback
sink. For live CUDA playback acceptance, use an approved disposable conversation.
Verify **GPU (CUDA)** in the Monitor, hear a complete reply, and check that an
interruption cancels stale playback. Switch to CPU and check the next reply.

For live Jevos GPU acceptance, use an approved disposable conversation. Enable
Jevos with **GPU (Vulkan)** and save, complete enough messages to generate a sample,
and check its model label in the heatmap. Turn Jevos off and save during the call.
Check that the worker disappears from **Models** and the call can continue.

For live phone acceptance, use an approved disposable conversation. Place one
call in each mode from the phone on Wi-Fi and mobile data. Check phone capture,
streamed playback, stop, and disconnect behavior. Test speaking over a reply
in WebRTC with the handset speaker and headphones. Check the laptop delivery
guard if the network is intentionally cut during a send.
