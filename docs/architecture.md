---
kind: reference
scope: voice-harness
status: current
reviewed: 2026-11-25
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
| `harness.py` | Claim one interactive job by ID, accept cancellation from its owner, and persist its outcome. |
| `call.py` | Prepare the workers, advance the turn, claim one delivery, and keep operator and Kin messages separate. Depends on the capabilities in `contracts.py`. |
| `microphone.py`, `render_device.py` | Capture and play timestamped 10 ms frames on the Windows audio devices. Keep both device queues bounded. |
| `echo.py`, `speech_input.py` | Align the actual post-EQ and post-speed render frames with capture time, enhance the microphone signal, confirm incoming speech and hand off a short pre-roll. |
| `recognizer.py`, `dictation.py` | Group enhanced 16 kHz mono audio into pause-delimited chunks and decode with one local recognizer. |
| `provider.py` | Make one cancellable streaming delivery request and read a guarded recent-history page. Translate failures into bounded operator-facing messages. |
| `reply.py`, `speaker.py`, `engine.py` | Split the streamed text into bounded segments, feed a resident Pocket TTS process, and play PCM through a single speaker stream with one segment rendering ahead. |
| `voice_controls.py`, `voice_jobs.py` | Collect local TTS selections and recipes. Submit import, clone and preview operations through the foreground gate and deliver their progress and saved preset IDs. |
| `recordings.py`, `cloning.py`, `encoder.py`, `presets.py` | Prepare bounded local references, export with the native Pocket TTS process, and publish and resolve model-compatible voice profiles. |
| `state.py`, `settings.py` | Keep a versioned local job status, unresolved turn ID and explicitly saved preferences. Keep model paths and credentials scoped to their adapters. |

## Events, streams and results

The active job's Qt signals carry stage and UI message updates. Turn and reply IDs
keep incremental text in the same bubble. Pending dictation is capped at 32 chunks
during capture, plus the final flush. Each decode chunk is at most eight seconds.
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

A numbered SQLite schema stores the most recent operation and a single delivery
claim. The sending claim is committed before the request. A reply confirms delivery
and clears that claim. Before confirmation, an interrupted or failed send keeps
the claim until operator review. Restart marks unfinished operations interrupted.
Transcripts and PCM are held in memory. Provider history holds a per-bundle spacing
and cooldown timestamp. The lock at that boundary is shared with the original
voice frontend when the same bundle is selected.

Import and reference preparation publish file counts. Native encoding publishes
its active stage. Preview reports segments queued for playback, with completion
after the device drains. Presets become discoverable when their complete profile
and recipe directory is renamed into place. Preset IDs and source recording names
link saved output to its input recipe. The call reads a new selection after the
operator explicitly saves it.

## Resources and extension

Admission reserves one interactive slot. A call requests the microphone, one
recognizer limited to the chosen thread count, and one native synthesis process
and output stream for an unmuted call. The current adapters use CPU inference.
The estimated peak memory and exact synthesis CPU demand are unknown. After
warmup the status tooltip reports the host and TTS working sets separately and
CPU time for the Python and native synthesis processes and elapsed startup time.
The working-set snapshots include memory beyond the model allocations. The harness does not
enforce a RAM or CPU quota. The speech models stay resident during the call and
are released when it ends. The same one-slot policy covers local import, preview
and encoding. Preview uses one synthesis process and the output device. Cloning
uses one short-lived CPU encoder and temporary disk space, with peak memory
dependent on the reference and model.

The workflow tests replace the microphone, recognizer, provider and speaker with
fixtures and check that a new draft can interrupt reply audio without overlapping
sends. Synthetic audio tests check reference alignment, echo suppression and
the cancellation fade without operating real devices. History
is a separate job submitted through the same foreground gate. There is no general
background scheduler yet. Future jobs can add their own resource contracts and
saved results without adding those decisions to the call workflow.

See [ADR-001](adr/001-single-host-call.md) for runtime boundaries and
[ADR-002](adr/002-local-voice-presets.md) for local voice presets. See
[ADR-003](adr/003-barge-in.md) for interruption and delivery decisions.
