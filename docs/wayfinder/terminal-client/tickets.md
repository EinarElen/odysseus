# Tickets: Terminal Client V1

These tickets build the `ody-term` Terminal Client from the [Terminal Client V1 Spec](tickets/011-terminal-client-v1-spec.md).

Work the **frontier**: any ticket whose blockers are all done. The two survey tickets can start immediately; after that, work the command spine first and then follow the dependency edges.

## Implementation Evidence Gates

Before marking an implementation ticket complete, state the evidence that proves
the user-facing behavior works. Do not count scaffold, local fixture state,
static renderer output, or command-shape tests as production behavior unless the
ticket is explicitly labelled scaffold-only.

- [x] Real backend/API behavior is verified when the ticket promises live
      Odysseus Sessions, Runs, events, lifecycle state, auth, or capabilities.
- [x] Interactive behavior is verified against an interactive terminal harness
      when the ticket promises TUI keyboard, mouse, or full-screen behavior.
- [x] CLI contract tests are paired with integration-style checks whenever the
      command claims to control or observe real Odysseus state.
- [x] Any remaining prototype/scaffold behavior is labelled open in this file
      and in user-facing docs.

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
- [x] Event filtering and cursor metadata have a stable initial behavior over real Odysseus activity streams, not only local server logs.

Correction note: the current implementation normalizes local server logs,
real API-backed chat/agent/harness Run events, and managed service, process,
and system runtime snapshots. See [Terminal Client Implementation
Review](implementation-review.md).

## Create Run Identity Compatibility Layer For Chat Runs

**What to build:** Users can start, list, attach to, check status for, and stop a chat Run while preserving durable Session history. This creates the first real vertical Run slice without requiring every agent and harness flow at once.

**Blocked by:** Introduce Event Envelope Inspection Over Existing Streams.

- [x] A chat Run has distinct Run identity linked to durable Session identity in the backend/API layer.
- [x] Starting a Run can create a new Session or target an existing Session.
- [x] Listing and status show active/recent Runs with status, timestamps, heartbeat/activity summary, and event availability.
- [x] Attach follows the Run Event Envelope stream.
- [x] Attach-by-Session fails with a structured ambiguity response when more than one active Run can match.
- [x] Stop targets Run lifecycle rather than hiding cancellation under Session commands.

Correction note: chat, agent, and harness Runs use the real terminal-client API
and persisted Run identity. Production commands no longer read or write the
retired client-local Run JSON format.

## Extend Run Surface To Agent And Harness Workflows

**What to build:** Users can observe and control agent Runs and Harness Session-linked Runs with Odysseus Session, Run, and Harness Session identifiers visible in command output and Event Envelopes.

**Blocked by:** Create Run Identity Compatibility Layer For Chat Runs.

- [x] Agent Runs use the same real API-backed Run list, status, attach, and stop model as chat Runs.
- [x] Harness-linked Runs include Odysseus Session, Run, harness adapter, and Harness Session identity where known from backend state.
- [x] Harness operations are capability-gated by adapter support and report unsupported actions clearly.
- [x] Heartbeats and activity updates are visible as Event Envelopes from real execution.
- [x] Session history and Run events remain separate user-facing concepts.

## Expose Managed Lifecycle Targets

**What to build:** Users can inspect and control known Odysseus-managed Lifecycle Targets with capability flags, structured denial reasons, logs rendered as Event Envelopes, and elevated friction for forceful operations.

**Blocked by:** Add Terminal Client Auth And Capability Reporting; Introduce Event Envelope Inspection Over Existing Streams.

- [x] Lifecycle target inventory includes managed server, Run, harness bridge, model-serving, MCP, and supporting service-health targets where available.
- [x] Each target reports status, ownership/source metadata, last activity where available, and action capability flags.
- [x] Logs are exposed through Event Envelopes with raw/debug access where useful.
- [x] Stop and restart are available only for known managed targets with bounded semantics.
- [x] Forceful or broad operations require elevated friction and never bypass auth, scope, ownership, or admin-only policy.
- [x] Arbitrary host process mutation is not exposed as an ordinary service command.

## Build Shared Live TUI MVP

**What to build:** Users can open `ody-term tui` and use focused Live, REPL, Browse/tree, and Inspect views over the same Session, Run, Event Envelope, lifecycle, and capability state used by the CLI.

**Blocked by:** Create Run Identity Compatibility Layer For Chat Runs; Expose Managed Lifecycle Targets.

- [x] The TUI has focused Live, REPL, Browse/tree, and Inspect views in a full-screen interactive renderer.
- [x] TUI panes consume the same Event Envelope, Session, Run, Lifecycle Target, and capability state as CLI commands.
- [x] Live view shows merged runtime events with selected-event detail and a local control log.
- [x] REPL view can perform status, tail, filter, stop, harness, and lifecycle control attempts within capability limits.
- [x] Browse/tree and Inspect views expose structure and current model state without depending on private internals.
- [x] Keyboard and mouse interaction paths are both covered against an interactive terminal renderer.

Implementation note: the harnessable `ody.tui.v1` state model feeds the
production Textual renderer. Human TTYs open the full-screen renderer;
structured and non-interactive output retain the deterministic text fallback.

## Harden Automation, Replay Primitives, And Final Docs

**What to build:** Automation users get stable clanker JSON/JSONL contracts, event cursors, reconnect behavior, raw/debug capture, documented aliases, exit codes, examples, and a final verification pass that the v1 surface supports future replay and self-improvement orchestration without special command families.

**Blocked by:** Extend Run Surface To Agent And Harness Workflows; Build Shared Live TUI MVP.

- [x] Clanker JSON/JSONL output is documented and covered by compatibility-style tests.
- [x] Event cursor and reconnect behavior is verified across Run attach and inspect flows.
- [x] Exit codes and structured error shapes are documented for auth, target resolution, confirmation, capability, and runtime failures.
- [x] Raw/debug capture is documented as diagnostic support, not the replay contract.
- [x] Examples demonstrate scriptable repeated runs and inspection using ordinary Terminal Client primitives.
- [x] Final documentation makes clear that replay, experiment, and self-improvement workflows are external orchestration patterns in v1.

Implementation note: finalized in `docs/ody-term.md`, with compatibility metadata exposed by `ody-term inspect contracts` and focused regression coverage for clanker contracts, cursor continuation, raw/debug capture gating, and Event Envelope replay posture.

# Tickets: Terminal Client Production Recovery

These tickets repair the gap between the implemented `ody-term` command
scaffold and the production behavior required by the
[Terminal Client V1 Spec](tickets/011-terminal-client-v1-spec.md). Source
review: [Terminal Client Implementation Review](implementation-review.md).

Work the **frontier**: any ticket whose blockers are all done. A ticket is not
done until its acceptance criteria are proven against the implementation
evidence gates above.

## Add Wayfinder Implementation Evidence Gates

**What to build:** The wayfinder tracker makes it hard to mistake scaffolded
command shape for production behavior. Future implementation tickets must say
what evidence closes them and must label scaffold/prototype behavior explicitly
when it is not the final user-facing behavior.

**Blocked by:** None — can start immediately.

- [x] The tracker distinguishes scaffold, prototype, contract shape, and
      production behavior.
- [x] Tickets that promise real Odysseus state require backend/API evidence
      before they can be marked complete.
- [x] Tickets that promise TUI interaction require interactive terminal
      evidence before they can be marked complete.
- [x] User-facing docs identify any remaining scaffold/prototype surfaces as
      open work.

## Build API-Backed Chat Run Vertical Slice

**What to build:** `ody-term run start/list/status/attach/stop --kind chat`
controls and observes real Odysseus chat execution instead of client-local run
fixtures. A user can start a chat Run, see its distinct Run identity linked to
the durable Session, attach to normalized Event Envelopes, reconnect with a
cursor, and stop the Run through the same Run model.

**Blocked by:** Add Wayfinder Implementation Evidence Gates.

- [x] Starting a chat Run creates or targets a real Odysseus Session and starts
      real chat execution through a terminal-client API path.
- [x] The terminal-client API exposes distinct Run identity linked to Session
      identity, even if existing internals remain session-keyed during the
      compatibility phase.
- [x] `run list` and `run status` report real active/recent chat Runs with
      status, timestamps, heartbeat/activity summary, and event availability.
- [x] `run attach` emits one normalized `ody.event.v1` Event Envelope per real
      stream event in JSONL mode and returns cursor metadata in JSON mode.
- [x] `run stop` stops real execution through bounded server-side behavior and
      does not mutate only client-local JSON state.
- [x] Tests fail if the command reports successful chat Run behavior without
      using the real terminal-client API path.

Implementation note: a first terminal-client API seam now exists at
`/api/terminal/runs`, and default/explicit chat starts plus API-created chat Run
status/attach/stop calls go through that seam instead of client-local JSON run
state. This is not enough to close the ticket: the backend route currently
provides a minimal compatibility stream over the existing session-keyed
detached-run substrate and still must be wired into full Odysseus chat
execution with real session/model validation, chat processing, and durable
history updates.

2026-07-09 update: `POST /api/terminal/runs` now requires the real Odysseus
chat runtime, can create a real Session when `endpoint_url` and `model` are
provided, targets existing Sessions through `SessionManager`, drains
`stream_llm_with_fallback` through the existing detached run buffer, persists
user/assistant turns, and refuses to fake success when the chat runtime is not
registered. `ody-term run start` forwards `--endpoint-url`, `--model`, and
`--preset-id` to that API path. Focused route/CLI tests prove the API no longer
uses the synthetic single-event stream, but the ticket remains open until this
is verified against a live configured model/backend rather than a test-patched
LLM stream.

2026-07-09 follow-up: API-backed chat Run status, attach, and stop now resolve
by Run id or by Session id through the terminal-client API. Session-id
resolution reports ambiguity when multiple active Runs match, but still permits
reconnect/attach to a single recent completed Run. `ody-term run
status/attach/stop --kind chat --session-id ...` uses these API paths, and a
stale client-local JSON chat Run no longer satisfies chat status, attach, or
stop. The ticket remains open because live configured-backend verification is
still not proven.

2026-07-09 durability follow-up: terminal-client Run identity is now persisted
server-side and reloaded after process memory is cleared. Route tests prove
`run list`, run-id status, Session-id status, event availability, and replay of
previously observed normalized events can recover recent completed chat Runs,
including latest-completed selection, after the in-memory terminal Run registry
is reset. Reloaded active Run metadata is marked interrupted when no detached
execution or persisted detached-run status exists. The ticket remains open only
for live configured-backend verification against a real model/backend outside
the patched test stream.

2026-07-09 live verification: with the backend running on
`http://127.0.0.1:7860`, a scoped API token, the configured ChatGPT
Subscription endpoint `https://chatgpt.com/backend-api/codex/responses`, and
model `gpt-5.4-mini`, `ody-term run start --kind chat` created
`run_704b9dc7c0a7486b` linked to durable Session `ses_16f1dce291444318`.
`run attach` emitted seven real `ody.event.v1` envelopes from the live stream,
including message deltas, metrics, `message_saved`, and `[DONE]`; the assistant
reply was `ody-term live ok`. `run status` reported `done`, `event_count: 7`,
and event availability. `run attach --cursor 4 --format=json` returned three
events with cursor `{after: "4", next: "7"}`. `run list --kind chat` reported
the real recent Run with heartbeat/activity metadata. A second live Run
`run_20ccaae46c3f4bbf` was stopped through `ody-term run stop --yes`; follow-up
status reported `stopped` with a server-side `finished_at`. This closes the
ticket.

2026-07-10 default-resolution follow-up: a new chat Run with no Session,
`endpoint_url`, or `model` now resolves the API-token owner's configured
Default Model through the shared owner-scoped endpoint resolver. Explicit
endpoint/model pairs still win, incomplete explicit pairs fail, and a missing
default returns a bounded error without creating a Session. Live verification
created `run_2bea4044edfe49b2` and new Session `ses_7823b73aac534194`
without runtime flags, streamed seven persisted envelopes, selected configured
model `gpt-5.5`, and returned `ODY_TERM_DEFAULT_OK`.

## Promote Event Inspection To Real Odysseus Activity

**What to build:** `ody-term inspect events` reads normalized Event Envelopes
from real Odysseus activity, starting with chat Runs and local server logs, so
automation can inspect live/recent behavior without depending on raw backend
transport shapes.

**Blocked by:** Build API-Backed Chat Run Vertical Slice.

- [x] `inspect events` can read real chat Run events by Run, Session, source,
      kind, level, and cursor.
- [x] Local server logs remain available as Event Envelopes, but are no longer
      the only implemented source.
- [x] Raw/debug modes preserve source-native event details without replacing
      the normalized Event Envelope contract.
- [x] Cursor behavior is verified across bounded JSON responses, JSONL streams,
      and reconnect after a previous cursor.
- [x] Tests include at least one real backend/API-backed event source and fail
      if only local fixture/log state is queried.

2026-07-10 foundation note: `GET /api/terminal/events` now queries one bounded,
non-blocking snapshot of a real Run by Run or Session identity, filters
normalized envelopes by source, kind, and level, and preserves the Run replay
cursor after filtering. `--lines` supplies the bounded page size, capped
server-side. API-token callers require owner-attributed `event:read` or
`event:raw` scope, Session ownership is checked server-side, and raw transport
details are removed unless explicitly authorized. Normalized events are
persisted under distinct Run identity as the detached stream drains, so a
later Run on the same Session cannot alias its buffer and recent replay does
not depend on a client having attached before eviction. Existing Run API
routes now enforce `run:start`, `run:read`, and `run:stop` scopes as well.
`ody-term inspect events --run-id/--session-id` consumes that API in JSON,
JSONL, raw, and debug modes, while identity-free inspection and explicit
`--source server` retain local server-log envelopes. Route tests start a real
terminal chat Run through the API seam before querying its events; CLI tests
verify Run and Session selection, filtering, bounded cursor reconnect across
JSON and JSONL invocations, and source-native debug details.

2026-07-10 streaming follow-up: `GET /api/terminal/events/stream` tails the
same persisted per-Run Event Envelopes as newline-delimited JSON, replays only
events after `cursor`, applies source/kind/level filters, and closes after the
Run reaches a terminal state. `ody-term inspect events
--format=jsonl --run-id/--session-id` consumes that response incrementally and
flushes each envelope as it arrives. Tests prove replay after a cursor, live
delivery before an active Run finishes, and incremental CLI writes. This closes
the recovery ticket; the next frontier is API-backed agent and harness Runs.

2026-07-10 agent follow-up: `ody-term run start --kind agent` now uses the same
owner-scoped terminal-client Run API as chat instead of writing client-local
Run state. The backend builds the normal Odysseus agent context, applies the
same resolved user privileges and globally disabled-tool policy as the browser
adapter, executes `stream_agent_loop`, persists user and assistant Session
history, and records real agent SSE activity as `ody.event.v1` envelopes with
`source=agent`. The native `agent_prep` liveness signal is normalized to the
stable `heartbeat` kind while its native payload/raw details remain available.
CLI and route tests cover start, filtered list, status, attach, cursor
continuation, Session ambiguity, stop, ownership policy, and durable history.
Final live evidence used Run `run_299df42899ca4c6a`, Session
`ses_e5183fcc5ba84575`, configured default model `gpt-5.5`, twelve real events,
a normalized `heartbeat` retaining native `agent_prep` identity, and the
persisted exact response `ODY_TERM_AGENT_HEARTBEAT_OK`. Harness execution
remains the open half of this frontier.

2026-07-10 harness backbone: the terminal-client backend Run Interface now
accepts `kind=harness`, resolves a registered `HarnessAdapter`, validates its
mode capability, runs `start`/`send`, persists the returned Harness Session
identity, and places Odysseus Session, Run, adapter, and Harness Session
identity on normalized `source=harness` envelopes. Focused route tests exercise
the Adapter seam and durable identity update.

2026-07-11 harness completion: `ody-term run start --kind harness`, Run reads,
lifecycle inventory/logs/stop, and the TUI state model now consume the same
server-owned Run and Event APIs. Live Pi evidence used Run
`run_a41cc2d3d5424dea`, Odysseus Session `ses_c4ca8f9304c74cfb`, adapter `pi`,
adapter-confirmed Harness Session `ses_c4ca8f9304c74cfb`, workspace
`/Users/einarelen/junk/odysseus`, and observe mode. The completed Run persisted
19 `source=harness` Event Envelopes, including activity phases, deltas,
metrics, history persistence, and terminal status. The deltas joined to
`ODY_TERM_HARNESS_OK`; Run state and all events remained queryable after a
server restart.

## Extend Real Runs To Agent And Harness Workflows

**What to build:** Agent and harness-backed execution use the same real Run
model as chat. A user can observe and control agent Runs and Harness
Session-linked Runs with Odysseus Session, Run, harness adapter, and Harness
Session identity visible in command output and Event Envelopes.

**Blocked by:** Build API-Backed Chat Run Vertical Slice; Promote Event
Inspection To Real Odysseus Activity.

- [x] Agent Runs use the same real API-backed list, status, attach, and stop
      commands as chat Runs.
- [x] Harness-linked Runs include Odysseus Session identity, Run identity,
      harness adapter identity, and Harness Session identity where known.
- [x] Harness operations are capability-gated by adapter support and report
      unsupported actions clearly in structured output.
- [x] Heartbeats and activity updates from real execution are visible as Event
      Envelopes.
- [x] Session history and Run events remain separate user-facing concepts.

## Make Lifecycle Logs And Controls Consume Real Run/Event State

**What to build:** `ody-term service list/status/logs/stop/restart` consumes
real managed target evidence for Runs, harness bridge state, server runtime
state, cookbook/model-serving, and health targets. Unknown or placeholder
targets remain visible only as explicitly unavailable/unknown, not as completed
control surfaces.

**Blocked by:** Extend Real Runs To Agent And Harness Workflows.

- [x] Run lifecycle targets are populated from real terminal-client Run state,
      not client-local fixtures.
- [x] Harness bridge lifecycle targets reflect adapter/runtime capability and
      linked real Runs where available.
- [x] Service logs expose real Event Envelopes for supported targets and return
      structured unsupported/unavailable responses for targets without logs.
- [x] Stop and restart controls operate only on known managed targets with
      server-side ownership/capability checks.
- [x] Forceful or broad actions require elevated friction and still cannot
      bypass auth, scopes, ownership, or admin-only policy.

## Replace The Static TUI With An Interactive Renderer

**What to build:** `ody-term tui` opens a real full-screen interactive terminal
UI over the existing `ody.tui.v1` state model. The text fallback may remain for
limited terminals, but it no longer counts as completion evidence for the TUI
ticket.

**Blocked by:** Promote Event Inspection To Real Odysseus Activity; Make
Lifecycle Logs And Controls Consume Real Run/Event State.

- [x] The TUI renders focused Live, REPL, Browse/tree, and Inspect views in a
      full-screen terminal renderer.
- [x] Live view consumes the same real Event Envelope, Session, Run, Lifecycle
      Target, and capability state as CLI commands.
- [x] Keyboard navigation and control paths are verified in an interactive
      terminal harness.
- [x] Mouse selection/control paths are verified where the terminal backend
      supports mouse input.
- [x] The existing text renderer is documented and tested only as fallback, not
      as the primary interactive TUI.

2026-07-11 interactive renderer: production `ody-term tui` now runs a Textual
full-screen application over the shared `ody.tui.v1` snapshot when stdout is a
human TTY. Textual pilot tests exercise F-key navigation, keyboard control
attempts, REPL submission and filtering, clickable view/control buttons, and
mouse selection of Event Envelope rows and Browse nodes. REPL and control
events delegate to the shared capability-aware attempt seam rather than
reimplementing policy in the renderer. A PTY run against the live dev server
rendered the persisted Run/Event inventory, switched to Browse with the `3`
binding, and exited cleanly through `q`. JSON, JSONL, and non-interactive human
output continue to use the deterministic fallback.

## Retire Local Run-State Fixtures From Production Commands

**What to build:** Production `ody-term` commands no longer report successful
Run behavior from client-local JSON fixtures. Any local run state that remains
is explicitly test-only, diagnostic-only, or runtime metadata that does not
claim to be the source of truth for Odysseus execution.

**Blocked by:** Extend Real Runs To Agent And Harness Workflows; Replace The
Static TUI With An Interactive Renderer.

- [x] Production Run commands use the terminal-client API source of truth for
      start, list, status, attach, and stop.
- [x] Local JSON run fixtures are removed from production success paths or
      renamed/documented as diagnostic/test-only state.
- [x] Tests cover the absence of fake-success behavior when the backend/API path
      is unavailable.
- [x] Docs and examples no longer demonstrate local-fixture-backed Run behavior
      as if it were production behavior.

2026-07-11 retirement evidence: local Run-state path/load/save, reference
resolution, event synthesis, and fallback branches were removed from
production. Run start/list/status/attach/stop and harness status/stop now use
the terminal-client API unconditionally. Regression tests place a plausible
legacy Run JSON file at the former environment override and prove status,
attach, and stop return the API-unavailable error without reading or mutating
that file. Harness status is separately verified against API-backed Run
inventory.

## Final Contract Audit And Docs Refresh

**What to build:** The wayfinder map, `docs/ody-term.md`, and implementation
tickets are reconciled with actual behavior. Every completed checkbox has
corresponding integration evidence, and remaining scaffold/prototype behavior is
explicitly labelled open.

**Blocked by:** Retire Local Run-State Fixtures From Production Commands; Make
Lifecycle Logs And Controls Consume Real Run/Event State.

- [x] All Terminal Client tickets are reviewed against the implementation
      evidence gates.
- [x] `docs/ody-term.md` describes only behavior that works, with unfinished
      behavior clearly labelled as open work.
- [x] The wayfinder map points to the production recovery tickets and no longer
      implies the real API/TUI requirements are complete.
- [x] Focused and full verification commands are recorded with the final status.

2026-07-11 managed snapshot completion: `inspect events --source
service|process|system` emits bounded `ody.event.v1` snapshots from the same
Lifecycle Target and owned runtime evidence consumed by service commands. The
TUI merges those sources with Run events and server logs. Focused tests cover
schema, kind, source, cursor count, real process ownership evidence, and the
shared TUI timeline. Live service/system inspection against the dev server
returned fourteen managed service snapshots and one system snapshot.

2026-07-11 final contract audit: every canonical command is implemented; the
stale generic `config set/unset` advertisements were removed. A narrow
owner-scoped Session API now backs `session list/show/history/export` with
`session:read`, keeping durable history and linked Run summaries separate.
Live verification listed sixteen Sessions and read the persisted harness
Session with one linked Run, two history messages, and a 96-character Markdown
export. Managed runtime snapshots use `cursor.mode=snapshot`, return
`next: null`, and reject stream cursors. Textual pilot tests and a real PTY run
cover keyboard, mouse, REPL, tree, event selection, and terminal restoration.
Final focused command: `uv run pytest -q tests/test_ody_term_cli.py
tests/test_ody_term_tui.py tests/test_terminal_client_routes.py` — 126 passed.
Final repository command: `uv run pytest -q` — 4771 passed, 3 skipped. Ruff
correctness checks and `git diff --check` passed; `ty` remained advisory with
known dynamic-boundary warnings. Independent Standards and Spec review axes
both passed after their findings were corrected.
