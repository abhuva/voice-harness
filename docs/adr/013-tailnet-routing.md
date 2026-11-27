# ADR-013 Tailnet presence and routed reply playback

## Decision

Voice Harness reads presence from the local Tailscale client. The host overlays
its processing capabilities, the active phone session, and the playback endpoints
paired through the existing private phone gateway. The proxy client address
associates endpoints with devices in the display. Pairing uses the origin and
PIN checks already enforced by the gateway.

The host retains the call, model leases, and delivery guard. An operator chooses
one reply destination for the next phone call. A separate browser receiver plays
bounded PCM frames and receives cancellation flushes through its WebSocket.
The call owns the chosen receiver connection until it finishes. A receiver
disconnect cancels that call and requires an explicit new call before playback
can resume.

## Consequences

Additional tailnet devices appear as they become visible to the host. Their
audio capability becomes available when they connect a receiver. A browser can
receive audio without sending microphone data or running speech models.
Hands-free phone calls wait through audible replies sent to a remote receiver,
where the phone browser has no playback reference for echo cancellation.

The loopback fixture verifies routing and disconnect. The host, phone, and
receiver browser require a physical acceptance check on the tailnet.
