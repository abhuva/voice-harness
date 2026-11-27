---
kind: decision
scope: windows-voice
status: accepted
reviewed: 2026-11-26
---

# ADR-011 Selectable Vulkan backend for Jevos

## Context

The operator wants to choose where Jevos runs independently of speech
recognition and synthesis. The current OpenVINO INT8 worker scores full
conversation snapshots locally and has an independent saved enable switch.
The GTX 1050 Ti has 4 GiB of dedicated VRAM shared with speech and desktop use.

## Decision

Keep OpenVINO as the default device and offer the published Jevos v5 Q8 GGUF
through an optional pinned llama.cpp Vulkan worker. The app starts its own
authenticated loopback process and confirms the NVIDIA device and full layer
offload. Vulkan uses the Jevos evidence prompt and computes the yes probability
from the relative scores of the two answer tokens. The model label in each new
heatmap sample identifies the Vulkan backend. [Jevos v5 release](https://github.com/feder-cr/jev/releases/tag/jevos-v5)
[Jevos prompt source](https://github.com/feder-cr/jev/blob/jevos-v5/src/prompt.hpp)
[llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/b12777/tools/server/README.md)

Save the device and enablement independently in Jevos settings. The existing
decision scheduler owns both backends and still unloads the model when scoring
is disabled. Vulkan admission checks host memory and free VRAM on one NVIDIA
adapter. GPU scoring yields to speech activity and uses the existing model
residency and monitoring paths. If speech cancels scoring, the workflow
retains that snapshot for the next idle period when no fresher one is pending.
The footer reports the scheduler's current blocker and shows the remaining
budget when memory prevents a claim.

## Alternatives considered

The existing OpenVINO CPU worker covers hosts without a supported GPU.
Extending the native Jevos server to support Vulkan would require a separate
native build. Using a generated answer from a general inference endpoint would
give a binary label without the probability needed by the heatmap.

## Consequences

The optional Vulkan installation adds a model and a runner. Q8 and OpenVINO
INT8 can produce different probabilities for the same question. Vulkan has a
fixed 4,096-token context. The app leaves scoring pending while headroom is
low, and device selection stays under operator control. The Models monitor
shows observed memory and utilization for the owned process.

Related [ADR-006 Local conversation scoring](006-local-conversation-scoring.md)
and [ADR-009 Model resource readings](009-pocket-tts-cuda.md).
