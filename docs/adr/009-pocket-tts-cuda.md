---
kind: decision
scope: windows-voice
status: accepted
reviewed: 2026-11-26
---

# ADR-009 Selectable CUDA synthesis with model resource readings

Accepted 2026-11-26 by the operator and maintainers for Windows playback.

## Context

The operator wanted to allocate synthesis to the GPU independently of recognition.
The CPU Pocket TTS worker already uses a bounded local pipe for warm-up,
synthesis, cancellation and playback. The GTX 1050 Ti requires a PyTorch build
that includes CUDA kernels for Pascal.

## Decision

We added a CUDA Pocket TTS worker using Kyutai's Python runtime and the same
pipe protocol. It loads the local model and voice preset, warms up on CUDA, and
shares the existing resident slot with the CPU worker. Backend identity is part
of the model key and of call calibration. TTS previews use the selected device.
The operator saves the call device through the ordinary voice settings.

The Monitor uses worker PIDs for host working set, CPU time and Windows WDDM
counters. GPU load is the busiest engine serving that PID. Dedicated VRAM is
the sum of that PID's adapter allocations. CPU recognizers hosted in Python
share the app's CPU reading. An unavailable sample appears as an em dash.

## Alternatives considered

A CUDA build of the native Rust worker would preserve one deployment and
requires replacing or extending its CPU tensor backend. Running Python and
PyTorch inside the desktop process would combine application and worker metrics
and require restarting the desktop process to recover CUDA allocations.

## Consequences

The CUDA environment is an optional local install. The CPU executable and
voice encoding remain available. The display measures resource use. Backend
selection is manual. The Windows driver manages concurrent GPU allocation.

Related [ADR-005](005-warm-speech-models.md) and [ADR-008](008-fluid-vulkan.md).
