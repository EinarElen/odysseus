# Tickets: Terminal Client V1

These tickets build the `ody-term` Terminal Client from the [Terminal Client V1 Spec](tickets/011-terminal-client-v1-spec.md).

Work the **frontier**: any ticket whose blockers are all done. The two survey tickets can start immediately; after that, work the command spine first and then follow the dependency edges.

## Survey Odysseus Terminal-Relevant Technology

**What to build:** A concise, decision-oriented inventory of technologies already present in Odysseus that should shape `ody-term`, including CLI patterns, config and secret storage, HTTP/API clients, streaming/event code, auth/token helpers, launch machinery, harness integration, packaging, and test prior art. The survey should avoid excessive dependency depth and end with concrete recommendations for the first implementation tickets. Neither backwards compatibility with awkward existing shapes nor rigid avoidance of new dependencies is a goal; recommendations should fit Odysseus' pragmatic engineering posture.

**Blocked by:** None — can start immediately.

- [x] Existing Odysseus CLI, launch, auth, config, streaming, harness, lifecycle, and test patterns are summarized.
- [x] Reusable internal seams and risky coupling points are identified.
- [x] Recommendations are specific enough to guide the command spine, profiles, auth, events, and server bootstrap tickets.
- [x] The survey explicitly rejects rabbit holes that are not relevant to `ody-term` v1.
- [x] The survey does not preserve old interfaces or avoid dependencies unless doing so materially improves the Terminal Client.

## Survey Relevant External Terminal Client Technologies

**What to build:** A bounded comparison of external implementation options for the Terminal Client, including CLI frameworks, TUI frameworks, HTTP streaming support, secret storage, packaging, and test approaches across plausible Python, Node, and Rust choices. The result should recommend a substrate for `ody-term` and explain rejected options without becoming a broad technology research project. Neither backwards compatibility with awkward existing shapes nor rigid dependency avoidance is a goal; a well-justified dependency is acceptable when it gives the Terminal Client a materially better foundation.

**Blocked by:** None — can start immediately.

- [x] Candidate CLI/TUI stacks are compared against Odysseus v1 needs: command grammar, JSON/JSONL output, streaming, mouse-capable TUI, packaging, and tests.
- [x] Secret storage and local config/runtime-state options are compared.
- [x] The recommendation accounts for `uv` integration without treating `uv` as a required implementation language choice.
- [x] Rejected options include short, concrete reasons.
- [x] The recommendation is allowed to choose a new dependency or break from legacy internal shapes when that is the simpler, better path.

## Establish `ody-term` Command Spine And Output Contracts

**What to build:** A runnable `ody-term` entrypoint with canonical domain/verb parsing, global options, help, output-profile selection, structured format handling, alias metadata, and behavior-focused CLI tests. This gives every later ticket one stable command seam to extend.

**Blocked by:** Survey Odysseus Terminal-Relevant Technology; Survey Relevant External Terminal Client Technologies.

- [x] `ody-term` exposes the accepted top-level domains and help shape.
- [x] Global target, output, format, color, verbosity, quiet, and confirmation options are parsed consistently.
- [x] TTY and non-TTY defaults select the accepted human and clanker output profiles.
- [x] Human, grug, JSON, JSONL, raw, and debug output contracts have a testable baseline.
- [x] Aliases, if present, are discoverable metadata over canonical commands and do not change semantics.

## Add Terminal Client Profiles, Config, And Target Resolution

**What to build:** Users can inspect and manage Terminal Client-local config and profiles, resolve a target Odysseus URL through the accepted precedence order, and get structured diagnostics when no target is reachable.

**Blocked by:** Establish `ody-term` Command Spine And Output Contracts.

- [x] Profiles store target configuration, output posture, repo hints, and credential references without storing mutable process facts.
- [x] Config commands operate only on Terminal Client config, not Odysseus application settings.
- [x] Target resolution follows the accepted precedence order and reports how the target was selected.
- [x] Human/TUI localhost fallback and non-interactive structured failure behavior are covered by tests.

## Implement Local Server Bootstrap Runtime State

**What to build:** Users can inspect, start, stop, and read logs for a local Odysseus server using existing launch machinery and runtime-state ownership evidence, without treating arbitrary host processes as safe targets.

**Blocked by:** Add Terminal Client Profiles, Config, And Target Resolution.

- [x] Local server commands can report status before authenticated API access exists.
- [x] Server start delegates to existing Odysseus launch behavior rather than introducing a second launch grammar.
- [x] Runtime state records enough ownership evidence to make stop and logs safe.
- [x] Ambiguous or stale process evidence fails with structured diagnostics instead of killing by broad process or port matching.
- [x] `--start` and `--ensure-server` behavior is covered at the command seam.

## Add Terminal Client Auth And Capability Reporting

**What to build:** Users can log in, log out, inspect auth status, see bypass modes, and query resolved Terminal Client capabilities from owner-attributed Odysseus API tokens with resource/action scopes.

**Blocked by:** Establish `ody-term` Command Spine And Output Contracts; Add Terminal Client Profiles, Config, And Target Resolution.

- [x] Auth status reports token-backed, auth-disabled, and localhost-bypass modes explicitly.
- [x] Token values are stored through the selected secret-storage path, with weaker fallback modes visible.
- [x] Terminal Client capability scopes are represented as resource/action permissions rather than command spellings.
- [x] `auth capabilities` reports raw auth facts and resolved policy facts in stable structured output.
- [x] Ordinary confirmation and elevated-friction behavior are enforced without bypassing auth, ownership, or server policy.

## Introduce Event Envelope Inspection Over Existing Streams

**What to build:** Users can inspect existing Odysseus activity as normalized Event Envelopes in human, grug, JSON, JSONL, raw, and debug modes, with source-native details preserved for troubleshooting.

**Blocked by:** Add Terminal Client Auth And Capability Reporting.

- [x] Event Envelopes include required schema, identity, sequence, time, source, kind, level, and payload fields.
- [x] Source-native details are preserved in payload or raw fields where available.
- [x] JSONL emits one Event Envelope per line for streaming and automation.
- [x] Raw and debug modes expose source details without becoming the default automation contract.
- [x] Event filtering and cursor metadata have a stable initial behavior.

## Create Run Identity Compatibility Layer For Chat Runs

**What to build:** Users can start, list, attach to, check status for, and stop a chat Run while preserving durable Session history. This creates the first real vertical Run slice without requiring every agent and harness flow at once.

**Blocked by:** Introduce Event Envelope Inspection Over Existing Streams.

- [x] A chat Run has distinct Run identity linked to durable Session identity.
- [x] Starting a Run can create a new Session or target an existing Session.
- [x] Listing and status show active/recent Runs with status, timestamps, heartbeat/activity summary, and event availability.
- [x] Attach follows the Run Event Envelope stream.
- [x] Attach-by-Session fails with a structured ambiguity response when more than one active Run can match.
- [x] Stop targets Run lifecycle rather than hiding cancellation under Session commands.

## Extend Run Surface To Agent And Harness Workflows

**What to build:** Users can observe and control agent Runs and Harness Session-linked Runs with Odysseus Session, Run, and Harness Session identifiers visible in command output and Event Envelopes.

**Blocked by:** Create Run Identity Compatibility Layer For Chat Runs.

- [x] Agent Runs use the same Run list, status, attach, and stop model as chat Runs.
- [x] Harness-linked Runs include Odysseus Session, Run, harness adapter, and Harness Session identity where known.
- [x] Harness operations are capability-gated by adapter support and report unsupported actions clearly.
- [x] Heartbeats and activity updates are visible as Event Envelopes.
- [x] Session history and Run events remain separate user-facing concepts.

## Expose Managed Lifecycle Targets

**What to build:** Users can inspect and control known Odysseus-managed Lifecycle Targets with capability flags, structured denial reasons, logs rendered as Event Envelopes, and elevated friction for forceful operations.

**Blocked by:** Add Terminal Client Auth And Capability Reporting; Introduce Event Envelope Inspection Over Existing Streams.

- [ ] Lifecycle target inventory includes managed server, Run, harness bridge, model-serving, MCP, and supporting service-health targets where available.
- [ ] Each target reports status, ownership/source metadata, last activity where available, and action capability flags.
- [ ] Logs are exposed through Event Envelopes with raw/debug access where useful.
- [ ] Stop and restart are available only for known managed targets with bounded semantics.
- [ ] Forceful or broad operations require elevated friction and never bypass auth, scope, ownership, or admin-only policy.
- [ ] Arbitrary host process mutation is not exposed as an ordinary service command.

## Build Shared Live TUI MVP

**What to build:** Users can open `ody-term tui` and use focused Live, REPL, Browse/tree, and Inspect views over the same Session, Run, Event Envelope, lifecycle, and capability state used by the CLI.

**Blocked by:** Create Run Identity Compatibility Layer For Chat Runs; Expose Managed Lifecycle Targets.

- [ ] The TUI has focused Live, REPL, Browse/tree, and Inspect views.
- [ ] TUI panes consume the same Event Envelope, Session, Run, Lifecycle Target, and capability state as CLI commands.
- [ ] Live view shows merged runtime events with selected-event detail and a local control log.
- [ ] REPL view can perform status, tail, filter, stop, harness, and lifecycle control attempts within capability limits.
- [ ] Browse/tree and Inspect views expose structure and current model state without depending on private internals.
- [ ] Keyboard and mouse interaction paths are both covered by tests or harnessed verification.

## Harden Automation, Replay Primitives, And Final Docs

**What to build:** Automation users get stable clanker JSON/JSONL contracts, event cursors, reconnect behavior, raw/debug capture, documented aliases, exit codes, examples, and a final verification pass that the v1 surface supports future replay and self-improvement orchestration without special command families.

**Blocked by:** Extend Run Surface To Agent And Harness Workflows; Build Shared Live TUI MVP.

- [ ] Clanker JSON/JSONL output is documented and covered by compatibility-style tests.
- [ ] Event cursor and reconnect behavior is verified across Run attach and inspect flows.
- [ ] Exit codes and structured error shapes are documented for auth, target resolution, confirmation, capability, and runtime failures.
- [ ] Raw/debug capture is documented as diagnostic support, not the replay contract.
- [ ] Examples demonstrate scriptable repeated runs and inspection using ordinary Terminal Client primitives.
- [ ] Final documentation makes clear that replay, experiment, and self-improvement workflows are external orchestration patterns in v1.
