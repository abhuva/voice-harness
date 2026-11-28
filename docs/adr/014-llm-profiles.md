# ADR-014 Named LLM reply profiles

Accepted 2026-11-27 for the Voice Harness desktop and phone workflows.

## Context

Marc requested switchable reply providers and editable named OpenRouter
settings. The call already consumes provider events and owns a durable
one-shot delivery claim for each outgoing turn.

## Decision

We added a profile store and a chat selector that fixes the provider for the
next call. A compatible chat completions connector translates streamed or
complete replies into the existing events. Each client keeps a small
completed-turn context in memory for its own call. Kindroid remains the
default and owns its own history. Credentials are referenced by name and
resolved from the laptop’s local environment file at call startup.

## Alternatives considered

An application-wide conversation store could persist context across calls and
would require an explicit history and privacy policy. Reusing Kindroid
transcripts in the new request would send history to another provider.

## Consequences

Calls can switch providers without changing recognition or playback. An
unknown outcome uses the same operator review guard. OpenRouter context expires
when the call ends.
