---
kind: decision
scope: voice-harness
status: accepted
reviewed: 2026-11-25
---

# ADR-001 Single-host voice call with local capability workers

Accepted on 2026-11-25 for Marc's Windows voice-call prototype.

## Context

The existing desktop window combines the Kindroid call with speech experiments
and voice cloning. Its working local speech adapters and direct Kindroid transport
provide a starting point for a smaller first workflow. The new repository needs
a platform-independent call sequence that can use the established Windows bundle.

## Decision

The first workflow used an in-process harness with one interactive job and a
half-duplex call orchestrator. [ADR-003](003-barge-in.md) extends the call to
continuous microphone capture while preserving the foreground slot. Microphone, recognition, provider and playback
have replaceable contracts. The Pocket TTS adapter keeps its existing native
process boundary. The local database owns the unresolved send claim and terminal
operation status. The first delivery focuses on the chat window and settings
used directly by the call.

## Alternatives considered

Moving the whole prototype would bring along unrelated experiments and leave
workflow ownership coupled to the window. A process per capability with a general
broker would isolate native faults, at the cost of wider startup, IPC and
scheduling contracts for a single-host call. The existing native synthesis
process already supplies isolation at that boundary.

## Consequences

The desktop can replace a speech or provider adapter without changing turn
order. One foreground slot disallows overlapping speech jobs. Recognition
continues in the current host, so cancellation may wait for native loading or
decode to finish. Further platforms can reuse the workflow and its acceptance
tests while supplying their own adapters.
