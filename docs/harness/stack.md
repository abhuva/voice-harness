---
title: Stack proposal
description: Proposed technology choices and tradeoffs for the Windows-first harness and a later Linux workstation.
kind: proposal
scope: harness
status: proposed
reviewed: 2026-11-25
---

# Stack proposal

Start with a single-host Python runtime, local persistence, and small worker
contracts. Keep CPU inference as the first Windows baseline. Model startup,
resident memory, and turn latency drive the first measurements. Equivalent
workflows on Linux can use different native adapters and models.

| Area | Proposed choice | Why and tradeoff |
|---|---|---|
| Orchestration | Python 3.13 with `asyncio` | Async I/O, timers, and bounded queues suit the coordination layer. Supervise blocking native inference separately when it can delay cancellation or affect interactive latency. Keep process controls at the platform boundary. |
| Coordination | Local dispatcher, typed messages, private child-process pipes | Keep initial deployment small. Use a versioned protocol at process boundaries and direct binary streams for audio. Add transport across hosts when a workflow requires it. |
| Desktop and audio | Qt Widgets with PySide6, sounddevice, NumPy, soundfile | The stack keeps desktop and array processing familiar. Qt belongs at the desktop boundary, and audio devices need platform acceptance. |
| Models and connectors | sherpa-onnx STT, a resident Pocket TTS worker, HTTPX for HTTP connectors | Use the established speech baseline. Keep model names, device choices, and provider lifecycle details in adapters. Confirm the native Pocket TTS build and audio device behavior on Linux. |
| Durable state | SQLite with WAL and a local filesystem artifact store | Keep job, claim, outcome, provenance, and measurement metadata in short transactions. Store large inputs and outputs as files and reference repos and vaults in place. Revisit the database when multiple hosts need shared write ownership. |
| Build and acceptance | uv, a committed application lockfile, and pytest on Windows and Linux | A common headless suite checks workflow behavior. Native binaries, models, drivers, and target-device smoke tests still need per-platform setup. |
| Resources | Independent profiling with psutil and platform-specific enforcement | Measure warm idle footprint, peak activity, and CPU time before setting admission policy. Windows Job Objects and Linux cgroups have different control and permission requirements. Keep scheduler priorities distinct from quotas. |

Rust with Tokio is an alternative for the harness. It offers stricter compile-time
contracts and more direct control of memory and concurrency. A second application
toolchain adds integration cost while capability boundaries are still being tested.
A small worker protocol leaves room to move a measured bottleneck into Rust.

The initial Windows code uses PyQt6, Qt networking and requests, and Python
threads. It uses SQLite WAL, a filesystem catalog, independent process
sampling, a uv lockfile, and Windows and Linux headless contract tests.
The [implementation review](review.md) records the current choices and
platform acceptance limits.
