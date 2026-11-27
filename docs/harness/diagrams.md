---
title: Architecture diagrams
description: The voice workflow, independent jobs, and the lifetime of warm speech models.
kind: reference
scope: harness
status: current
reviewed: 2026-11-26
---

# Architecture diagrams

## Voice workflow

The harness admits and tracks work. The call owns turn order. Speech and audio
workers handle their own inputs and outputs. The reply connector reports
delivery, streamed text, and a terminal outcome.

```mermaid
flowchart TB
    UI["Desktop UI"]
    H["Harness"]
    C["Call orchestrator"]
    S["App-owned STT and TTS"]
    I["Capture and echo cancellation"]
    R["Reply connector"]
    K["Kindroid"]
    P["Playback"]
    D[("Local state and artifacts")]

    UI -->|"Submits and cancels"| H
    H -->|"Admits foreground job"| C
    H <-->|"Durable claims and outcomes"| D
    C -->|"Borrows TTS for the call and STT by residency mode"| S
    I -->|"Speech chunks"| S
    S -->|"Recognized text"| C
    C -->|"Claims reply work"| R
    R <-->|"One submitted turn and streamed reply"| K
    R -->|"Delivery, text and completion"| C
    C -->|"Text segments"| S
    S -->|"PCM"| P
    P -.->|"Render reference"| I
```

## Recognition after a long pause

In dynamic mode the microphone opens independently of STT. A call acquires the
selected Pocket voice when unmuted and borrows recognition for spoken turns.
Warm mode borrows STT before opening the microphone and releases it after the
call with no silence deadline.

```mermaid
sequenceDiagram
    participant C as Call and capture
    participant H as Harness admission
    participant M as STT residency

    C->>C: Listen and keep two seconds of cleaned audio
    C->>M: Borrow on confirmed speech
    M->>H: Request priority if a cold load is needed
    C->>C: Continue capturing during model load
    M-->>C: Ready to decode saved and incoming chunks
    C->>M: Release after the final decode settles
    Note over M: Keep warm until the silence deadline
    M->>M: Unload after the deadline while idle
    C->>M: Borrow again when speech resumes
```

Loading, readiness, reuse, failure, and unloading produce lifecycle events.
Those notifications describe completed transitions. The model owners handle
the explicit acquire and release operations.

## Work that finishes later

The headless fixture exercises a background capability with a local connector.
The connector can use the saved result on a later turn in the same call.

```mermaid
flowchart TB
    T["Call completes an operator turn"]
    E["Event dispatcher · turn ready"]
    O["Observer · subscribes to turn-ready events"]
    F["Configured connector streams the normal reply"]
    Q["Durable background work queue"]
    W["Eligible background worker"]
    A[("Versioned result in catalog")]
    L["Later turn · opted-in local connector"]

    T --> E
    E --> O
    T -->|"Claims the foreground reply job"| F
    O -->|"Submits follow-up work"| Q
    Q -->|"One owner claims the job"| W
    W -->|"Saves with job and input provenance"| A
    W -->|"Announces the saved reference"| E
    A -->|"Scoped to the originating call"| L
```

## Desktop scoring and monitoring

```mermaid
flowchart LR
    C["Call orchestrator"]
    O["Complete-message observer"]
    H["Harness admission and claims"]
    J["Local Jevos scorer"]
    D[("Rolling SQLite scores")]
    V["Chat-side heatmap"]
    M["Monitor window"]
    T["Module activity and call timings"]
    R["Host and model readings"]

    C -->|"Confirmed operator message or complete Kindroid text"| O
    O -->|"Three full messages in memory"| H
    H -->|"Admitted local claim"| J
    J -->|"Question probabilities and provenance"| D
    D -->|"Completed scores"| V
    H -->|"Job state"| M
    C --> T
    T --> M
    R --> M
```

See the [voice runtime](../architecture.md) for current adapters and the
[harness design](README.md) for the intended extension points.
