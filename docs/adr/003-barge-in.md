---
kind: decision
scope: windows-voice
status: accepted
reviewed: 2026-11-25
---

# ADR-003 Voice interruption during a Kindroid reply

Accepted on 2026-11-25 for Marc's Windows voice call workflow.

## Context

The initial call closed the microphone during the Kindroid round trip and spoken
reply. Continuing to speak should start the next turn immediately. The capture
device can hear the app's speech, and closing a client reply stream gives no
assurance that Kindroid canceled a delivery.

## Decision

The call keeps one capture device open and allows a single next draft in
parallel with the older reply. The Windows audio adapters timestamp incoming
and actual submitted output frames. A local WebRTC adapter performs acoustic
echo cancellation and noise suppression using their aligned signals.
Speech detection requires sustained activity during a reply and preserves a
short pre-roll for transcription. The speaker cancels queued synthesis and
fades current audio. The network stream remains available for confirmation
until the first valid reply data. If the original send remains uncertain,
the workflow ends with the new draft visible and the original delivery claim
intact. The Settings tab can disable speech interruption.

## Alternatives considered

Microphone volume alone works during silent waits but can mistake speaker echo
for the operator. End-of-sentence interruption preserves phrasing but can keep
speaking over the operator for seconds. Canceling the network stream at the first
speech detection leaves a send unconfirmed and can block the very next turn.
Windows endpoint AEC is an alternative adapter whose availability depends on
the capture device and platform.

## Consequences

One foreground slot still excludes other speech workflows. A call now uses
continuous input, one capture worker and a local audio processor alongside
synthesis when applicable. Capture and render queues are bounded and private
audio is transient. Physical echo rejection and audible stop latency vary by
endpoint and need operator acceptance with the intended devices.

Related [ADR-001](001-single-host-call.md).
