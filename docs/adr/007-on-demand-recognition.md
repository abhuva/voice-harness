---
kind: decision
scope: windows-voice
status: accepted
reviewed: 2026-11-26
---

# ADR-007 On-demand recognition with timed residency

Accepted 2026-11-26 for the desktop call workflow.

## Context

Holding the desktop recognizer for a whole call kept about 700 MiB resident
during speech-free periods. The microphone and echo cancellation already run
independently of recognition. A short overlap can also be a cough, noise or
an acknowledgment that should let the previous reply finish.

## Decision

We made the call borrow recognition for an accepted utterance and release it
after the last decode settles. The harness keeps the model warm through the
configured silence window, 60 seconds by default. Expiry is measured from the
last accepted speech, works across Stop, and only evicts a released model. Cold
recognition takes foreground priority over preemptible work such as Jevos.

Dynamic residency is the default. The operator can select warm residency in
the SST tab. Warm residency loads STT during call startup, holds the lease for
the call, and retains the released model without a silence deadline. Saving
the mode updates the deadline on an idle model. Both modes use the same
resident slot, and admission calibrates startup separately for each mode.

Capture keeps a rolling two-second buffer of cleaned mic audio in memory. It
keeps recording during cold startup and recognizer admission. An overlap
becomes an interruption after a three-second sustained speaking span by default.
The detector requires speech evidence throughout the span, tolerates natural gaps
and brief input loss, and requires echo-aware voice evidence during playback.
Capture discards short overlaps that end under a reply
and continues an utterance that is still active when the reply ends. The
existing one-shot delivery and confirmation rules govern when a turn can send.

A short gap in an accepted utterance can be filled with silence within a
100 ms per-turn budget. A larger loss drops the local draft and returns to
listening after a full pause. An open microphone and any older delivery claim
retain their own lifetimes. A device that stops still reports a call error.

## Alternatives considered

A call-long STT lease avoids cold starts during a call but leaves the model
resident for the full conversation. Releasing it after every turn frees memory
quickly but adds cold startup to rapid turns. A dedicated STT process would make
release independent of the Python host. The installed model returned most of its
measured working set when unloaded in place, so an extra worker process was
unnecessary for this change.

## Consequences

A cold start can extend turn completion when loading takes longer than recording
and the finish pause. The Monitor reports cold STT load time and the observed
change in the Python working set at idle expiry. A short utterance over playback
may be ignored. Both timing values are configurable in SST.

This refines recognition residency in [ADR-005](005-warm-speech-models.md)
and interruption detection in [ADR-003](003-barge-in.md).
