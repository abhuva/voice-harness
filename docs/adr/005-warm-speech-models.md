---
kind: decision
scope: voice-harness
status: accepted
reviewed: 2026-11-26
---

# ADR-005 App-owned speech model residency

## Context

Each call used to create and close both speech models. Repeated calls with the
same settings paid the loading cost again. The foreground harness already owns
job admission, and audio devices and turn state have clear call lifetimes.

## Decision

We gave the app separate resident slots for speech recognition and synthesis.
The first use loads each requested configuration. Calls borrow both where
needed, and a speech preview borrows synthesis. Stop closes devices, settles
pending work, and releases the model claims. A matching ready model remains
available for the next job.

Recognition is keyed by bundle, model, and thread count. Synthesis is keyed by
bundle, model, voice preset, and temperature. Playback tuning belongs to each
job's audio output. A different model configuration replaces only its own slot.
A failed model is released. Idle slots can be evicted to admit a foreground
job with a different resource need, and app exit releases all idle slots.

The owners publish lifecycle events after each transition. The work contract
uses explicit acquire and release. Admission credits measured resident bytes
for matching models and continues to check available host memory and additional
job demand. Measurements remain advisory. STT uses the observed increase in
the host working set, and TTS uses the child working set.

[ADR-007](007-on-demand-recognition.md) adds the default per-utterance STT lease
and its idle deadline. Warm mode uses the call-long lease described here.

## Alternatives considered

A model process surviving application exit would add IPC and service management.
Eagerly loading both models at app launch would consume memory before any speech
operation. Retaining several configurations would increase resident memory and
need a replacement policy. One lazily loaded slot per capability serves the
current workflow.

## Consequences

The initial call still includes loading. A matching second call opens its audio
devices using the warm models. A preview that selects a different voice replaces
the resident TTS configuration. STT remains in process, and a cold load can take
time to settle after Stop.

Related [ADR-001](001-single-host-call.md) and [ADR-004](004-scoped-work.md).
