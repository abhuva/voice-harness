---
kind: decision
scope: harness
status: accepted
reviewed: 2026-11-26
---

# ADR-004 Scoped work alongside the foreground call

## Context

The foreground gate gave the first voice workflow one owner. A delayed result
needs an independently claimable job, a durable versioned output, and a way to
reach an eligible later turn. The call must retain operator control over
external delivery and remain responsive when resources are limited.

## Decision

We retained one foreground speech slot and added a local SQLite queue for
capability-based claims. A foreground call submits a separate reply job for each
turn to its configured connector. A background worker can claim eligible work
alongside a running call when estimates fit the host budget. A waiting
foreground job takes priority over additional background claims.

Notifications carry workflow, job, and turn identifiers. The dispatcher fans
out small bounded events. Durable job state and catalog entries support lookup
when consumers miss a notification. Inputs are granted by artifact version.
A connector can opt into scoped local results for later turns. Kindroid
continues to receive the operator's dictated turn.

A repeatable local job can return to the queue after restart. Other interrupted
claims require explicit handling. The original unresolved Kindroid delivery rule
continues to apply. The profiler samples the host and native process separately
and persists load, idle, peak, CPU, and elapsed observations. Admission uses
estimates and available memory, with a call reservation and conservative
handling of unknown demand. Earlier working-set measurements provide advisory
estimates for a sole foreground job. The operator can opt into strict memory
checking. Concurrent work still requires admission against the budget.

## Alternatives considered

Keeping direct callbacks for all work would leave delayed results coupled to the
call's lifetime. An external broker and a shared database server would extend
deployment and recovery requirements before there is a second host. The local
dispatcher, SQLite queue, and filesystem catalog support the first independent
worker with the same process model.

## Consequences

The headless fixture can run a second worker while the call waits for its
connector and use its saved result once in a later turn. Physical audio and
native Linux workers need platform acceptance. Admission controls declared
demand and scheduling priority. Host CPU quota and accelerator enforcement can
be supplied by a platform adapter when required.

Related [ADR-001](001-single-host-call.md) and [ADR-003](003-barge-in.md).
