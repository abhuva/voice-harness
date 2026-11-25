---
title: Harness design
description: Design and responsibility boundaries for the local harness and its first voice workflow.
kind: design
scope: harness
status: target
reviewed: 2026-11-25
---

# Harness design

Voice Harness is a local runtime for independent modules. The Windows voice call is
its first workflow. The design should let a conversation continue while another
worker takes longer, then use that worker's result after the originating turn.
The same contracts should support a future Linux workstation.

Start on one host. Keep each boundary small and document the inputs, outputs, side
effects, and resource needs that callers depend on. Add a process boundary when
it buys supervision, isolation, or replaceability. Define platform parity by
what the operator can do and by the contracts modules provide.

## Responsibilities

| Responsibility | Owns |
|---|---|
| Modules | Scoped capabilities and their declared inputs, outputs, side effects, data needs, and expected resources. Workers perform work. Decision models can publish findings or propose follow-up work within their scope. |
| Orchestrators | The state and decisions of a workflow. A call orchestrator owns a call, advances its turns, and decides when audio plays or a delayed result belongs in the conversation. |
| Harness | Event delivery, work claims, cancellation, resource allocation, and enforcement of operator policies. |
| Shared data | Versioned inputs and results, with permissions and provenance. Results can outlive the worker and workflow that produced them. |

A decision model informs an orchestrator. The orchestrator decides which
workflow step to take, and the harness enforces the authority it requires.
A recommendation can guide the current discussion. A lasting behavior change
needs an explicit activation decision, provenance, and a way to reverse it.

## Voice as the first workflow

| Capability | Inputs and outputs | Reuse |
|---|---|---|
| Capture and chunking | Read microphone PCM. Detect speech and pauses. Produce bounded audio chunks. | Dictation and recording tests. |
| Speech recognition | Convert an audio chunk to text with a warm recognizer. | Files and other audio sources. |
| Reply connector | Accept a turn and report delivery confirmation or uncertainty, streamed text, and completion or failure. Each adapter interprets its provider's behavior. | Kindroid, an API-backed model, or direct local inference. |
| Speech synthesis | Accept complete or streamed text, find usable breaks, and produce audio chunks. | Voice calls, text previews, and audio exports. |
| Playback | Accept PCM and own the output device, live controls, levels, drain, stop, and failure. | Generated speech and existing audio. |
| Call orchestration | Own the turn sequence, including when to capture, submit, play, interrupt, and advance. | The same workflow with different workers and connectors. |

Capture and playback can share an app process. STT and TTS can run as resident
child workers where supervision or independent resource measurement warrants it.
Those deployment choices preserve the capability contracts.

## Events, jobs, streams, and saved results

| Mechanism | Contract |
|---|---|
| Event | Announce something that happened. Several listeners can observe `UserTurnReady`. The call orchestrator can respond by submitting `GenerateReply`. |
| Job | Assign work to one eligible owner at a time. The harness owns the claim. Reclaim interrupted work when its effects are known to be safe to repeat. Preserve an uncertain external delivery claim for explicit review. |
| Stream | Carry ordered, bounded text or audio while work runs. Consumers can start on partial output. Completion, failure, interruption, and backpressure have explicit meaning. |
| Saved result | Record a completed, versioned output. Commit the result before announcing it so a consumer can resolve it later, including after a restart. |

Carry the originating workflow or call ID, turn ID where applicable, and job ID
through coordination and result delivery. Consumers can discard late results
that no longer belong to the work they are handling. The dispatcher carries
coordination and small results. High-rate PCM travels through a direct bounded
stream, with a reference and lifecycle events available to the relevant modules.
Observers gain access to an event within their scope. A separate claim grants
authority to execute the work.

## Resource policy

Resource awareness belongs in the first version. Keep estimates, observations,
and admission decisions separate.

| Role | Owns |
|---|---|
| Worker | Report resident and additional job memory by location, CPU and accelerator needs, and the input size and settings those estimates assume. Use a range or an explicit unknown when evidence is limited. Remote workers can declare rate limits and cost. |
| Independent profiler | Measure load time, warm idle memory, peak memory during work, CPU time, and elapsed time on known inputs. Track cold and warm runs separately. Start with observed ranges and fit formulas when measurements justify them. |
| Resource manager | Combine capacity, current use, estimates, and operator policy. Reserve room for the call, give it priority under contention, and decide whether idle models can stay resident. |

Priority decides the order in which work runs. A CPU cap needs a defined unit
and platform enforcement. Memory admission accounts for models that remain in
RAM or accelerator memory while idle. Start with a memory reservation for the
call and priority over competing background work.

## Shared references and delayed work

Repositories and vaults can stay where they live. A catalog records how to find
a particular version, which worker may access it, and where a result came from.
A job can carry a scoped recording reference, repository revision, or deliberately
selected context snapshot. Grant explicit permission before a cloud worker
receives data. A result records which input versions and job produced it.

A slow-analysis example tests the design. A fast reply proceeds while a decision
worker observes authorized context across turns. A finding can lead an
orchestrator to commission deeper analysis on a frozen snapshot. The worker saves
a report and emits its versioned reference. The conversation orchestrator
decides whether to use it in the current topic, a later reply, or a follow-up at a
natural pause. It can defer the result and record when it has already been used.

The example motivates the boundaries. Prove the architecture with a replaceable
model, a lightweight background worker, and a delayed result that the conversation
can use without rewriting the call sequence. A local dispatcher and a synthetic
saved result can test this before a real decision model, research worker, or
proactive follow-up policy is in scope.

See the [stack proposal](stack.md), [implementation review](review.md), and
[current voice runtime](../architecture.md).
