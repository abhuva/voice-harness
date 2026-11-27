---
kind: decision
scope: windows-voice
status: accepted
reviewed: 2026-11-26
---

# ADR-008 Selectable Vulkan backend for Fluid Speech Pro

Accepted 2026-11-26 by the operator and maintainers for the Windows voice call.

## Context

Fluid Speech Pro was the most costly speech recognizer in the installed CPU
bundle. The operator's GTX 1050 Ti had about 4 GiB of dedicated VRAM available.
On two public speech clips, the publisher's Q8 GGUF export with `parakeet.cpp`
on Vulkan retained about 770 MiB less host memory and decoded faster than the
installed Sherpa-ONNX INT8 build. Sampled host RSS during loading still peaked
near 975 MiB. The first request in a fresh GPU run took longer than later requests.

## Decision

We added a device choice for Fluid Speech Pro in SST and kept CPU as the default.
The Vulkan adapter starts the pinned Windows `parakeet.cpp` server as an owned
child process. It binds to loopback, confirms the selected device, and warms up
on silent PCM before use. Call capture and dictation keep the same bounded chunk
contract. The model key includes backend, assets and threads. Both residency
modes from [ADR-007](007-on-demand-recognition.md) work with either backend.

We retained foreground priority during cold model loading. A provisional host
memory estimate covers the GPU worker's cold load. The job profiler samples the
STT and TTS children separately and aggregates their host footprints. The
Monitor reports observed NVIDIA VRAM separately from host working set. Windows
and the Vulkan driver manage GPU allocations. Scheduling concurrent GPU jobs
requires an accelerator budget and measurements for those jobs.

## Alternatives considered

A CUDA build of Sherpa-ONNX could reuse the installed CPU export and would need
Pascal-compatible Windows CUDA libraries and model operator verification. An
in-process `parakeet.cpp` binding could remove loopback HTTP and would put the
Vulkan runtime inside the desktop process. The separate Vulkan worker uses the
runtime tested on the target machine and releases GPU allocations at process exit.

## Consequences

The optional install stores the pinned runtime and GGUF weights in ignored local
state. A different recognition configuration releases the prior worker at the
next call. A Vulkan startup or decode error surfaces in the call status so the
operator can choose CPU. The two export formats can differ in recognition output.
Offline verification exercises switching, warm-up and release with synthetic
audio. Live conversation acceptance is tracked in the operator guide.
