---
kind: reference
scope: voice-harness
status: current
reviewed: 2026-11-25
---

# Architecture

The call is half duplex. The microphone closes after dictation, and speech playback
drains before the microphone reopens. A new call owns a single foreground slot
until its workers return and release their devices and models.

| Module | Contract |
|---|---|
| `window.py`, `worker.py` | Show local controls, deliver stage and message updates via Qt, and run submitted operations on one Qt worker thread. |
| `harness.py` | Claim one interactive job by ID, accept cancellation from its owner, and persist its outcome. |
| `call.py` | Prepare the workers, advance the turn, claim one delivery, and keep operator and Kin messages separate. Depends on the capabilities in `contracts.py`. |
| `microphone.py`, `recognizer.py`, `dictation.py` | Read 100 ms, 16 kHz mono blocks, split them by pauses, and decode them with one local recognizer. |
| `provider.py` | Make one cancellable streaming delivery request and read a guarded recent-history page. Translate failures into bounded operator-facing messages. |
| `reply.py`, `speaker.py`, `engine.py` | Split the streamed text into bounded segments, feed a resident Pocket TTS process, and play PCM through a single speaker stream with one segment rendering ahead. |
| `state.py`, `settings.py` | Keep a versioned local job status, unresolved turn ID and explicitly saved preferences. Keep model paths and credentials scoped to their adapters. |

## Events, streams and results

The active job's Qt signals carry stage and UI message updates. Turn and reply IDs
keep incremental text in the same bubble. Pending dictation is capped at 32 chunks
during capture, plus the final flush. Each decode chunk is at most eight seconds.
Live replies are limited to 512 KiB at the provider boundary and eight queued
speech segments of at most 480 characters in the player. Backpressure slows reply
consumption when playback falls behind. The render-ahead future belongs to the
playback worker, and its completion controls when the call may listen again.
After Stop, the harness holds admission until the active job actually settles.

A numbered SQLite schema stores the most recent operation and a single delivery
claim. The sending claim is committed before the request. A reply confirms delivery
and clears that claim. Before confirmation, an interrupted or failed send keeps
the claim until operator review. Restart marks unfinished operations interrupted.
Transcripts and PCM are held in memory. Provider history holds a per-bundle spacing
and cooldown timestamp. The lock at that boundary is shared with the original
voice frontend when the same bundle is selected.

## Resources and extension

Admission reserves one interactive slot. A call requests the microphone, one
recognizer limited to the chosen thread count, and one native synthesis process
and output stream for an unmuted call. The current adapters use CPU inference.
The estimated peak memory and exact synthesis CPU demand are unknown. After
warmup the status tooltip reports the host and TTS working sets separately and
the host and native synthesis CPU time and the elapsed time observed during loading. Working sets include the
processes beyond the model allocations and are snapshots. The harness does not
enforce a RAM or CPU quota. The speech models stay resident during the call and
are released when it ends.

The workflow tests replace the microphone, recognizer, provider and speaker with
fixtures and check that delayed playback completes before capture resumes. History
is a separate job submitted through the same foreground gate. There is no general
background scheduler yet. Future jobs can add their own resource contracts and
saved results without adding those decisions to the call workflow.

See [ADR-001](adr/001-single-host-call.md) for the choice of runtime boundaries.
