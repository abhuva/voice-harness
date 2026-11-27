---
title: Overview
description: Set up Voice Harness, make a Kindroid call, and learn how the Windows audio and delivery workers fit together.
kind: index
scope: voice-harness
status: current
reviewed: 2026-11-26
---

# Voice Harness

Voice Harness is a Windows desktop app for spoken Kindroid conversations.
It transcribes speech locally, sends the text to Kindroid, and plays the reply
with Pocket TTS. You can speak over a reply to start your next turn.

## Get started

- [Install and make a call](operations.md#windows-bundle) with the Windows speech bundle.
- [Choose a TTS model or clone a voice](operations.md#tts-and-voice-cloning) using local recordings.
- [Adjust voice interruption](operations.md#during-a-call) or recover an uncertain delivery.
- [Inspect runtime activity or configure Jevos](operations.md#monitoring-and-jevos).
- [Use a phone over Tailscale](operations.md#phone-calls).

## How it works

- [Harness design](harness/README.md) explains how modules, workflows, resources,
  and saved results fit together. The [architecture diagrams](harness/diagrams.md)
  show the voice workflow and speech model lifetimes. The [implementation review](harness/review.md)
  compares that design with the Windows client.
- [Voice runtime](architecture.md) describes the call workflow, audio adapters,
  and how the harness tracks work and delivery.
- [Decision records](adr/README.md) explain the single-host runtime, voice preset
  storage, and reply interruption.

See [verification](operations.md#verification) for the offline checks and physical
device acceptance steps.
