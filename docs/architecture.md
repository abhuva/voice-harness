---
kind: reference
scope: voice-harness
status: current
reviewed: 2026-11-26
---

# Voice runtime

This page describes the shipped Windows implementation. See the
[Harness design](harness/README.md) for the intended architecture and the
[implementation review](harness/review.md) for the gaps.

The call keeps one microphone open while Kindroid replies. The call owns one
foreground slot through shutdown. Within it, one capture worker can collect the
next draft while the previous turn receives text or plays audio.

| Module | Contract |
|---|---|
| `window.py`, `worker.py` | Show local controls, deliver stage and message updates via Qt, and run submitted operations on one Qt worker thread. |
| `harness.py`, `work_store.py`, `events.py` | Admit, claim, cancel, and finish durable jobs. Fan out scoped lifecycle notifications. |
| `admission.py`, `profiler.py`, `resources.py` | Reserve call memory, assess worker estimates, and sample and persist process measurements. |
| `residency.py` | Own the app's resident STT, TTS, and decision slots, grant exclusive model access, expire idle STT leases, and publish model lifecycle events. |
| `telemetry.py`, `hardware.py`, `monitor_window.py` | Collect text-free call timings and module state, sample host resources, and show models and durable work in a read-only window. |
| `decisions.py`, `jevos.py`, `decision_view.py` | Scope complete-message snapshots, run local resource-admitted scoring, and render durable scores in the chat heatmap. |
| `catalog.py` | Record versioned snapshots or external source revisions. Resolve the inputs granted to an active job. |
| `call.py` | Prepare the workers, advance the turn, claim one delivery, and keep operator and Kin messages separate. Depends on the capabilities in `contracts.py`. |
| `microphone.py`, `render_device.py` | Capture and play timestamped 10 ms frames on the Windows audio devices. Bound both queues. Report missing capture blocks from queue sequence and flagged driver overflow. |
| `echo.py`, `speech_input.py` | Align the actual post-EQ and post-speed render frames with capture time, enhance the microphone signal, confirm incoming speech with a separate interruption threshold, and retain a two-second mic pre-roll. |
| `recognizer.py`, `dictation.py` | Group enhanced 16 kHz mono audio into pause-delimited chunks and decode with one local recognizer. |
| `provider.py` | Translate Kindroid's one-shot streaming request into explicit delivery, text, and terminal events. Read one guarded recent-history page. |
| `reply.py`, `segments.py` | Coordinate typed reply events and bounded synthesis segments. |
| `synthesis.py`, `engine.py` | Feed the resident Pocket TTS process and return sample-rate-tagged PCM. |
| `speaker.py`, `playback.py` | Connect synthesis and output with one segment rendering ahead. The output adapter owns tuning, levels, drain, and device teardown. |
| `voice_controls.py`, `voice_jobs.py` | Collect local TTS selections and recipes. Submit import, clone and preview operations through the foreground gate and deliver their progress and saved preset IDs. |
| `recordings.py`, `cloning.py`, `encoder.py`, `presets.py` | Prepare bounded local references, export with the native Pocket TTS process, and publish and resolve model-compatible voice profiles. |
| `state.py`, `settings.py` | Keep a versioned local job status, unresolved turn ID and explicitly saved preferences. Keep model paths and credentials scoped to their adapters. |

## Events, streams and results

The active job's Qt signals carry stage and UI message updates. Turn and reply IDs
keep incremental text in the same bubble. Pending dictation is capped at 32 chunks
during capture, plus the final flush. Each decode chunk is at most eight seconds.
The dictation worker borrows STT on qualifying speech and keeps recording
through model preparation. Capture buffers overlap audio until the speaking span
has enough sustained speech to interrupt, or the reply ends with speech still active.
Brief input loss can be repaired within the same audio budget used for dictation.
Brief capture losses use a bounded silence fill while timing waits for a clean
finish pause. Larger losses discard the current turn and keep the capture stream
open. The next attempt waits for a full pause and gets a fresh turn ID. Any prior
Kindroid delivery retains its original claim and confirmation path.
Live replies are limited to 512 KiB at the provider boundary and eight queued
speech segments of at most 480 characters in the player. Backpressure slows reply
consumption when playback falls behind. The playback worker owns render-ahead
while the microphone stays open. The call advances after the previous exchange
settles, including playback completion or interruption. After Stop, the harness holds admission until the active job actually settles.
Playback interruption cancels local synthesis and queued PCM independently of
the provider stream. The render adapter fades to silence in one 10 ms frame.
Already submitted device buffers drain, so audible stop time depends on the
device. The next draft stays local while the older send awaits confirmation.
On confirmation the previous reply stream can close and the next complete turn
can claim delivery. On uncertain delivery the draft remains visible in memory
and the call stops. If a completed draft waited for delivery, stale mic frames
are discarded before collecting the following turn.

SQLite in WAL mode stores desktop status, reply and background claims, measurements,
and artifact metadata. The delivery claim is committed before the Kindroid POST
and cleared on explicit confirmation. The Kindroid adapter issues confirmation
from its first valid reply text. An uncertain send retains the claim across restart.
Transcripts and PCM stay in memory by default. Provider history uses a per-bundle
spacing and cooldown timestamp with the original frontend's bundle lock.

A turn publishes its workflow, turn, and job IDs to the bounded event dispatcher.
A subscriber can submit a distinct follow-up job. The harness grants that job to
one eligible worker and attaches exact input versions. A completed result remains
in the local catalog. An opted-in local connector can use earlier results from
its own call, and confirmation marks those results consumed. Kindroid receives
the dictated message through the existing delivery path.

The Jevos observer receives completed messages separately from draft and stream
updates. It submits session-scoped work with the originating call and message
IDs. Its RAM-only input can expire while waiting for resources or on shutdown.
The native worker owns the local scoring connection. The decision store retains
100 completed samples with timestamps, questions and probabilities. The heatmap
reads those scores independently of the call. See [ADR-006](adr/006-local-conversation-scoring.md).

Import and reference preparation publish file counts. Native encoding publishes
its active stage. Preview reports segments queued for playback, with completion
after the device drains. Presets become discoverable when their complete profile
and recipe directory is renamed into place. Preset IDs and source recording names
link saved output to its input recipe. The call reads a new selection after the
operator explicitly saves it.

## Resources and extension

The foreground slot covers the call, history, local import, preview, and encoding.
A background capability can claim work when the declared memory and CPU demand
fit the budget. An awaiting foreground claim blocks additional background work.
The default reserve is 1 GiB or the observed call peak with headroom, whichever
is higher. Call estimates use recent profiles matching the selected recognizer,
thread count, recognition residency mode, and TTS class. The app borrows TTS
for a call. In dynamic mode it borrows STT for each spoken turn and keeps the
released model warm until its silence deadline. Cold STT can yield preemptible
background work and receives its own admission check. In warm mode the app
borrows STT at call startup, includes it in call admission, and retains the
released model without a silence deadline. Warm
working-set estimates remain part of admission. Available host memory already reflects loaded models,
so the harness credits their measured footprint against the next job's resident
estimate. For a sole foreground job, measurements from previous runs are advisory
by default. Operators can opt into stricter admission in SST. Cold
configurations with unknown memory demand run alone until the audio pipeline is
ready. Admission then holds a transient call reserve based on the current
working sets, available calibration and a 256 MiB floor. New foreground work
can cancel the preemptible Jevos job and release its idle slot. Requests to generic cloud workers need explicit version grants and
configured request and cost budgets.

An independent sampler records startup, idle and active working sets, sampled
peaks, time to ready, and CPU and elapsed time. The Python working set includes
the application and the CPU recognizer. The sampler measures the native TTS and
Vulkan STT workers separately as children and aggregates their host memory for
call admission. The Monitor reports dedicated NVIDIA VRAM separately. Runtime
CPU throttling and accelerator allocation remain platform-specific concerns.

The workflow tests replace the microphone, recognizer, provider and speaker with
fixtures and check barge-in, one-shot delivery, and consumption of a durable
background result on a later turn. Synthetic audio tests check reference
alignment, echo suppression, and fade. The [implementation review](harness/review.md)
records the boundaries and remaining platform acceptance.

See [ADR-001](adr/001-single-host-call.md) for runtime boundaries and
[ADR-002](adr/002-local-voice-presets.md) for local voice presets. See
[ADR-003](adr/003-barge-in.md) for interruption and delivery decisions, and
[ADR-004](adr/004-scoped-work.md) for scoped job admission and delayed results.
See [ADR-005](adr/005-warm-speech-models.md), [ADR-007](adr/007-on-demand-recognition.md),
[ADR-008](adr/008-fluid-vulkan.md), and the [architecture diagrams](harness/diagrams.md)
for model lifetimes.
