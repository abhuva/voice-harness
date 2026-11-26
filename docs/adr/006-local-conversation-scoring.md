---
kind: decision
scope: voice-harness
status: accepted
reviewed: 2026-11-26
---

# ADR-006 Local conversation scoring with Jevos

Accepted 2026-11-26 by the operator and maintainers for the first desktop decision workflow.

## Context

The speech call needs to remain responsive while a second model evaluates
conversation context. This first workflow needs a visible result over time
and a small storage footprint. The Windows archive already has a local
Jevos v5 OpenVINO installation.

## Decision

We added a background scoring workflow with an independent native Jevos
process behind an authenticated loopback adapter. The call supplies completed
message snapshots through a separate observer. A snapshot holds three full
speaker-attributed messages in memory. History already displayed at startup
can supply the first context. A new complete message can request scoring.

The harness admits the job with a one-thread CPU profile and a memory estimate.
New foreground work can cancel active scoring. The decision workflow can retain
one pending snapshot and replaces it with the newest available context when
resources are scarce. It keeps Jevos warm for 30 seconds after scoring while
the app has no competing foreground startup.

SQLite keeps the last 100 successful samples with time, question wording,
probabilities, model name, and the originating job IDs. The chat heatmap reads
those records after their jobs complete. Results feed the operator's view.
A future workflow can consume a durable result under its own explicit authority.

The saved off setting detaches the desktop observer and stops score polling.
Turning it off invalidates active and queued snapshots, clears the in-memory
message context, and unloads the owned model. Re-enabling starts a new message
window and can display the earlier durable scores. The call keeps its own lifetime.

## Alternatives considered

Calling Jevos synchronously from the call would keep scoring close to the
conversation but would directly add model startup and inference time to the
voice path. Starting a fresh process for every message would free its memory
sooner at the cost of repeated cold loads. A permanent service would keep it
ready across app launches but would need its own authentication, resource
accounting and lifecycle. A transcript store would allow replay after a restart
but would increase the amount of saved conversation data.

## Consequences

Scoring can finish after the call advances or ends. Deferred work can skip
intermediate snapshots when several messages arrive during a memory wait.
An app restart restores saved scores and discards pending text. Jevos scores
are model estimates. Question wording changes remain visible in older rows,
and the heatmap can show gaps where different questions were asked.

Related [ADR-004 Scoped work](004-scoped-work.md) and
[ADR-005 Warm speech models](005-warm-speech-models.md).
