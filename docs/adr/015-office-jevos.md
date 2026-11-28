# ADR-015 Office-only Jevos scoring

Accepted 2026-11-27 for the first delegated processing capability.

## Context

Marc wanted the office PC’s existing Jevos runtime to perform scoring over the
tailnet. The first deployment needs to preserve SSH access to the office PC.

## Decision

We exposed one scoring capability through a separate loopback worker behind
an authorized SSH tunnel. It attaches to an existing Jevos v5 API and
reports readiness, runtime identity and available resource readings. The
laptop owns work claims, pending snapshots, cancellation and result history.
The worker admits one score at a time. Office only waits for a ready office
worker and uses the existing laptop processing buffers.

## Alternatives considered

A shared distributed queue and automatic device discovery would add
coordination before the first remote capability is proven. Replacing the
running office model would introduce a second deployment and require
lifecycle control over that installation.

## Consequences

The operator must provide an authorized tunnel. An unavailable office worker
leaves the newest snapshot pending. Turning Jevos off invalidates outstanding
results while the independent office runtime can finish an accepted request.
The trial worker can run with a bounded lifetime.
