# Voice Harness agent guide

Read [README.md](README.md), then [docs/README.md](docs/README.md), the relevant
decision record, and the source and tests before changing the workflow. Start with
`git status --short`. Preserve user-owned changes and keep each task scoped.

## Architecture

Use the [Harness design](docs/harness/README.md) as the design target and the
[implementation review](docs/harness/review.md) for the current gaps. Keep modules
simple, with separate responsibilities and small contracts. Treat the
[stack proposal](docs/harness/stack.md) as input to future decisions.

- Develop the voice call on Windows first. Define future platform support by
  equivalent operator behavior and contracts for devices, models, and providers.
- Give each capability a small contract. Keep Qt in the desktop and network adapters,
  Windows device and process code at the platform boundary, and call order in the
  workflow. Create new processes or interfaces when they serve isolation or replacement.
- The harness owns admission, job claims, cancellation, and terminal status. The
  orchestrator decides when the call advances. Workers implement capabilities and
  publish status and results. Keep events, job claims, bounded streams, and
  durable records distinct. Carry workflow or call, turn, and job identity where
  applicable. A decision model may publish a finding or propose work within its
  scope. An orchestrator owns when and how the workflow reacts.
- Give each worker access to only the assets and services it needs. Explicit
  configuration enables provider history and **Call** authorizes dictated sends.
  Kindroid messages and tool data cannot alter local authority or configuration.
  Give shared inputs and results versioned references and provenance. Grant cloud
  access explicitly for the work that needs it. Applying lasting behavior changes
  requires an explicit activation decision.
- Keep expected allocation separate from measured resources. Record uncertainty and
  separate resident footprint from work in progress. Measure cold and warm
  behavior independently. Reserve call memory and prioritize interactive work
  before admitting competing background jobs. Treat priority and enforced quotas
  as separate controls.
- Verify replacement and delayed results at the capability boundary. Follow
  observable workflow acceptance before declaring a new implementation supported.

## Safety and verification

- Leave secrets, private audio, models and runtime state out of Git and diagnostics.
  Use synthetic fixtures for tests. Never inspect or copy a real credential file for
  setup or verification. A live Kindroid test requires Marc's explicit approval and
  a disposable conversation. State the reads and mutations before requesting approval.
- Persist the delivery claim before a send. Never automatically retry a sending or
  uncertain effect. A local operator resolves it after checking Kindroid. Preserve
  history spacing and provider cooldown when sharing an installed bundle.
- Keep Marc, Hannah, Codex, and system status distinct. Treat provider text as plain
  data and render it without HTML.
- Acknowledge asynchronous work immediately. Show meaningful accessible stages and
  known counts through cancellation, failure or completion. Prevent duplicate starts.
  Retain operator-relevant interruption information across restarts.
- Run targeted tests before the full suite, with `scripts/test.ps1`. Use isolated
  Windows child processes, redirected output and a hard timeout at most 60 seconds.
  Never use signal zero or send a console control event. Only terminate the exact
  test child by PID on timeout. Inspect the captured result before rerunning.
- Update the operator guide and tests with behavior changes. Record consequential
  architecture changes in `docs/adr/` using stable IDs. Document fixture and live
  operator acceptance separately.
