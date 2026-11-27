---
kind: decision
scope: windows-voice
status: accepted
reviewed: 2026-11-27
---

# ADR-012 Phone audio over a private connection

## Context

On 2026-11-27 Marc requested push-to-talk and hands-free phone calls while
his laptop runs Voice Harness and the local models. The workflow already
separates microphone capture, recognition, delivery and playback.

## Decision

We added an opt-in browser client behind a loopback server suitable for
Tailscale Serve. The page starts a call explicitly and pairs using a
per-launch numeric PIN with rate-limited attempts. The operator can replace
the PIN in the desktop Phone tab. Each connection uses the desktop's foreground slot, saved
settings, model leases and durable delivery guard. Provider credentials stay
on the laptop. Disconnect cancels the matching call.

Push-to-talk sends microphone PCM over a WebSocket only while held and uses
release to finish capture. The same connection streams synthesized PCM to
the phone. Hands-free uses WebRTC audio in both directions and the existing
pause-based capture and interruption rules. Echo cancellation takes place in
the phone browser. The HTTPS proxy carries control traffic and the phone
initiates WebRTC connectivity checks to the laptop's offered address.

## Alternatives considered

Desktop remoting could expose a whole session and would depend on each mobile
client's microphone redirection. A separate native phone app could support
more background and device behavior but would require mobile distribution.
Using WebSocket PCM for both modes would require implementing real-time audio
packet-loss and jitter handling in the app.

## Consequences

The laptop needs to stay awake. Phone mode adds optional local dependencies.
WebRTC requires a UDP path between the phone and laptop. Push-to-talk uses
the HTTPS proxy alone. Transport and provider fixtures can verify the
workflow offline. Mobile device behavior and a live provider call have
separate operator acceptance.

Related [ADR-001 Single-host voice call](001-single-host-call.md)
and [ADR-003 Voice interruption](003-barge-in.md).
