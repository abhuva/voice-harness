---
kind: decision
scope: windows-voice
status: accepted
reviewed: 2026-11-26
---

# ADR-010 Scoped saves for speech settings

Accepted 2026-11-26 by the operator and maintainers for the desktop workflow.

## Context

The TTS preview used the current device selection while calls used the saved voice.
The SST tab's generic save action saved a full profile with the previously active
TTS device. It also validated the saved voice, so a missing preset could block
an unrelated SST change. The chat playback controls included live adjustments
that could be accidentally persisted by either save action.

## Decision

We gave SST, TTS and chat playback distinct settings and save actions. The desktop
uses the saved recognizer and synthesis settings for calls. A preview can use the
current TTS form. Playback speed and EQ apply live, and mute applies to the next
call. The playback save action persists those control values across launches.

The preferences store replaces the file atomically and updates only the section
owned by the save action. It reads version 1 and shared-bundle defaults and writes
version 2 on the next explicit save. The synthesis factory supplies both previews
and calls with the same model key and backend.

## Alternatives considered

Autosaving model edits would apply accidental selections to the next call and
remove the chance to try a draft voice first. Keeping a global save would require
reviewing unrelated changes as one transaction. Separate files would add storage
locations without improving the existing single-window save workflow.

## Consequences

The save buttons are explicit activation points for model changes. The tab shows
which model and device a call will use. Version 1 stored mute as zero speed, so
migration uses normal speed when that prior speed is unknown. Version 2 keeps
mute and speed independently. A missing preset leaves its saved device choice
intact and reports a synthesis error when the preset is requested.

Related [ADR-007](007-on-demand-recognition.md) and [ADR-009](009-pocket-tts-cuda.md).
