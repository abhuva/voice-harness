---
title: Implementation review
description: Architecture fitness of the voice workflow, shared jobs, resource admission, and delayed results.
kind: assessment
scope: harness
status: current
reviewed: 2026-11-26
---

# Implementation review

The harness covers the first voice workflow, a desktop Jevos scoring workflow,
and a headless delayed-result fixture. The desktop continues to use Kindroid and the Windows speech bundle.
The cross-platform fixture exercises the same orchestration, claims, permission
checks, and resource policy using replacement devices and connectors.

## Fitness scorecard

| Concern | Fit | Evidence and remaining acceptance |
|---|---|---|
| Workflow ownership | Implemented | `call.py` still owns call and turn state. It uses a distinct reply job for each turn. Worker composition stays in `worker.py`. |
| Reusable worker boundaries | Implemented | Capture, chunking, and STT keep separate responsibilities. App-owned STT and TTS slots can stay ready after a call. `synthesis.py` produces PCM, `playback.py` owns device operations, and `speaker.py` connects them with one segment rendering ahead. A fixture replaces the PCM sink. |
| Provider delivery | Implemented | A connector reports confirmation, text, and terminal outcomes explicitly. Only the Kindroid adapter translates its first valid reply bytes into confirmation. A fixture confirms before any text. A failed or uncertain send retains the delivery guard until explicit review. |
| Events and eligible work | Implemented locally | The dispatcher supports several observers. Durable jobs have one eligible claimant, scoped cancellation, and terminal status. Work identity connects workflows, turns, and result versions. The desktop has a foreground slot, with an additional resource-admitted background claim. |
| Streams and shared data | Implemented locally | Bounded text and PCM paths remain separate from events. The catalog stores hashed file versions or external source revisions. Grants apply to specific versions and expire with the claim. A fixture saves a delayed result, uses it once on a later turn, and checks that a different call cannot adopt a late result. |
| Resource policy | Implemented with conservative limits | Workers declare estimates, including unknowns. An independent sampler persists startup, warm idle, and active peaks with CPU and elapsed time. Memory admission reserves capacity for a call and credits the warm memory of matching models. Waiting foreground work takes priority. Background CPU estimates and optional remote request and cost budgets are admission controls. OS-level CPU quotas and accelerator enforcement remain platform work. |
| Decision workflow and observability | Implemented on Windows | Jevos can be enabled independently of its CPU or Vulkan device choice. Vulkan jobs also require VRAM headroom and yield to live speech activity. Up to 100 durable scores feed the chat heatmap. |
| Windows and Linux acceptance | Headless contract suite | The application environment is locked in `uv.lock`. A Windows and Linux CI matrix runs the same headless contracts. The Windows speech bundle remains the supported desktop runtime. Physical Linux audio and a native Linux Pocket TTS build need acceptance. |

## Evidence

- `test_harness_contract.py` proves event fan-out, one claimant, restart recovery,
  cloud grants and budgets, version integrity, call priority, independent
  profiling, replaceable PCM consumption, and delayed result scope.
- `test_call.py`, `test_state.py`, and `test_provider.py` cover barge-in,
  serialized one-shot delivery, and unresolved delivery across restart.
- `test_barge_audio.py` and `test_local_models.py` exercise synthetic audio and
  the installed Windows speech adapters. Recognition is resident in the host
  process, so cancellation can wait for a native decode.
- `test_decisions.py` checks VRAM admission and preemption. The installed
  `test_jevos_vulkan.py` fixture compares binary scores from both devices.

The delayed-result fixture exercises how a local connector can consume an older
result during a later turn. The desktop Jevos workflow feeds the operator's heatmap.
Using decision results to change replies, scanning vaults, or taking the floor
requires an explicit workflow and operator policy.

See the [harness design](README.md), [architecture diagrams](diagrams.md),
[voice runtime](../architecture.md), and
[operator acceptance](../operations.md#verification).
