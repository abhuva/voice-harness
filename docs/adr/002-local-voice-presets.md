---
kind: decision
scope: windows-voice
status: accepted
reviewed: 2026-11-25
---

# ADR-002 Local voice presets in the installed speech bundle

Accepted on 2026-11-25 for Marc's Windows voice cloning and TTS selection workflow.

## Context

The installed Pocket TTS helper restricts model and voice paths to its working
directory. The original desktop stores imported samples, editable recipes and
exported voice states in that bundle. Voice Harness already takes the bundle's
process lock and saves call preferences in its own state directory.

## Decision

Voice Harness submits imports, clone generation and voice previews through
the existing foreground harness. It preserves the installed bundle's sample and
preset formats. A new clone is published by renaming a complete directory into
`presets/<id>/`. The call consumes a preset only after the operator explicitly
saves a compatible model, preset and temperature.

## Alternatives considered

Keeping a fully separate preset store would require staging voice states into
the model bundle for playback, or changing and rebuilding the native helper.
A second speech worker alongside the call would require separate admission rules
and could compete for CPU, memory and the output device.

## Consequences

Both desktop apps can use the recordings and presets when pointed at the same
bundle. Imports and new presets require write access to that bundle. The preset
picker distinguishes the clone-compatible 18M model from the 7M model's Alba
voice. Call settings remain local to Voice Harness. The single foreground slot
holds until import, encoding or preview releases its resources.
