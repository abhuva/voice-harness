---
title: Implementation review
description: Which parts of the planned harness the Windows voice client implements and which still need proof.
kind: assessment
scope: windows-voice
status: current
reviewed: 2026-11-25
---

# Implementation review

The Windows voice client is a sound first workflow for the planned harness.
It validates call ownership, replaceable capabilities, bounded streaming, and
delivery safety. It partially implements harness coordination and resource
awareness. Independent background work and delayed result consumption remain
unproven. This review uses the implementation at
[revision b1b00ce](https://github.com/abhuva/voice-harness/tree/b1b00ce282a989585bb9c863185cf84e9a13be40).

## Fitness scorecard

| Concern | Fit | Evidence and remaining gap |
|---|---|---|
| Workflow ownership | Fits the first call | `call.py` owns the call and turn sequence. It accepts microphone, recognizer, speaker, and provider dependencies. `worker.py` composes the Windows and Qt adapters. The replacement fixtures drive the call without those adapters. |
| Reusable worker boundaries | Partial | Capture, chunking, and recognition already have separate responsibilities. `engine.py` owns the resident native synthesizer and `render_device.py` owns timed output. `PocketSpeaker` still combines synthesis, live controls, and playback, and the reply code segments streamed text. Another PCM consumer would need a synthesis interface it can use independently. |
| Provider delivery | Safe for the current connector, partial as a reusable contract | `provider.py` sends one cancellable POST. A durable claim survives timeout, cancellation, and restart until confirmed or reviewed. `Provider.send()` yields text, and `call.py` uses first reply text as confirmation. A connector that acknowledges separately would need a contract for explicit delivery and terminal outcomes. |
| Events and eligible work | Partial | The harness gives each top-level job a stable ID, one owner, cancellation, and a terminal status. The UI attaches an operation before execution. Qt signals carry progress and messages for that operation. The harness has one foreground slot. A general event dispatcher, capability matching, and claimable queue are open. Turn IDs and job IDs exist separately. A common envelope is needed to identify and discard late results across workflows. |
| Streams and shared data | Partial | Text, microphone PCM, and playback PCM use bounded paths. Voice presets save a recipe and profile under a stable ID and can be selected later. The catalog covers voice presets, and SQLite stores one operation status and the unresolved delivery claim. A shared versioned catalog, scoped cloud grants, and late work products remain future work. |
| Resource policy | First-version requirement remains open | `resources.py` samples the Python host and TTS process at call readiness. The UI reports working sets, load elapsed time, and CPU time. The sample includes other work in the host process and stays in memory. The slot prevents competing speech jobs. Resident and per-job estimates, independent cold and warm profiles, peak measurements, memory reservation, and call priority under contention all remain open. |
| Windows and Linux acceptance | Windows fixtures pass | The portable Windows bundle uses CPython 3.13 and PyQt6. Threads and futures coordinate work. Qt handles the streamed send and requests handles history. SQLite uses short local transactions with its default journal settings. The PowerShell runner uses unittest. A locked application environment and Linux device and native worker acceptance remain open. |

## What the tests prove

- `test_call.py` uses replacement workers and verifies sequential sends,
  barge-in while a reply is pending or audible, and a draft held when earlier
  delivery is uncertain.
- `test_state.py` checks the exclusive slot and recovery of an unresolved send.
  `test_provider.py` checks the one-shot POST and redirect handling through a
  loopback HTTP fixture.
- `test_barge_audio.py` checks reference alignment, pre-roll and the fade.
  `test_local_models.py` exercises the installed speech models on synthetic
  inputs. `test_cloning.py` checks preset publication and recovery from failure.

All 32 tests passed with the Windows runner and its installed-model fixtures.
Recognition runs in the host process, so stopping can wait for an in-flight
native decode. The fixture run uses synthetic inputs and replaces physical
devices and Kindroid. See [operator acceptance](../operations.md#verification)
for those checks.

## Priorities for the next increment

1. **Make delivery and playback contracts explicit.** Move delivery interpretation
   into the connector. Exercise acknowledgment before text, uncertainty, and
   cancellation with fixture connectors. Expose PCM as the synthesis output and
   let playback own drain and stop when a second consumer needs the boundary.
2. **Turn resource observations into admission evidence.** Add worker estimates
   with explicit unknowns and a repeatable cold and warm profile on Windows.
   Use measurements to reserve call memory and give calls priority before
   allowing competing model work.
3. **Prove independent work with a small local fixture.** Publish a turn event
   that several observers can receive, give its follow-up job one eligible
   owner, and save a versioned result. Verify that a later turn can consume it
   and a stopped call cannot accept stale completions. Run the same headless
   contract test on Windows and a later Linux target.

The [harness design](README.md) is the target for these checks.
