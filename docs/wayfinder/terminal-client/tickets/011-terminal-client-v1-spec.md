# Terminal Client V1 Spec

Status: implemented
Type: spec
Labels: implemented
Blocked by: none
Assignee: Codex

## Problem Statement

Odysseus has a dense graphical application shell, but development, debugging, automation, and future Odysseus-driven self-improvement workflows need a reliable non-graphical control surface. Today the available HTTP/API surface is broad enough to observe and operate chat, agent, harness, diagnostics, task, service, and session behavior, but it is browser-shaped, split across product areas, and lacks a stable Terminal Client contract.

Users and agents need one Terminal Client that can support both direct terminal use and machine-driven automation. It must let a human watch and control live Runs in a TUI, let scripts start and attach to Runs through stable structured output, expose Session, Run, Harness Session, Lifecycle Target, auth, profile, and event state consistently, and do so without becoming an auth bypass or an arbitrary host process manager.

## Solution

Build `ody-term`, the Odysseus Terminal Client. `ody-term` is the umbrella non-graphical client surface, with scriptable CLI commands as the default mode and `ody-term tui` as the full-screen interactive mode.

The v1 Terminal Client is HTTP/API-first. It uses a narrow terminal-client API layer over existing Odysseus routes, while preserving a future path for privileged inspection by keeping raw/debug/event/capability boundaries explicit. It is live-control first: v1 optimizes for development and debugging of chat, agent, harness, and service workflows, while replay, experiment, and self-improvement workflows are supported through ordinary scriptable primitives rather than special product surfaces.

The core product model is:

- Session: the durable Odysseus conversation/workspace identity.
- Run: one active or recent execution with lifecycle, status, event stream, heartbeat, and replay cursor.
- Harness Session: the external runtime session/control surface linked to an Odysseus Session and active Runs.
- Event Envelope: the stable normalized event wrapper consumed by CLI renderers, TUI panes, automation, and future replay workflows.
- Lifecycle Target: a named Odysseus-managed target that can be inspected or controlled when ownership and capability rules allow.
- Terminal Client profile/config/runtime state: client-local targeting, credential references, and local server bootstrap state, separate from Odysseus application settings.

## User Stories

1. As an Odysseus developer, I want to start `ody-term` from a terminal, so that I can work with Odysseus without opening the graphical shell.
2. As an Odysseus developer, I want `ody-term` to support both CLI and TUI modes, so that I can choose between scripts and live interactive control.
3. As an Odysseus developer, I want `ody-term tui` to be the canonical interactive entry point, so that I have a memorable way to open the live terminal experience.
4. As an automation author, I want CLI commands to be the default command surface, so that scripted use does not depend on a full-screen terminal.
5. As an automation author, I want a noun-first command grammar, so that command discovery stays consistent across sessions, runs, harnesses, services, and inspection.
6. As an automation author, I want global target, output, format, verbosity, and confirmation options, so that I can control behavior uniformly across commands.
7. As a human terminal user, I want readable human output by default in a TTY, so that ordinary commands are useful without extra flags.
8. As an LLM-driven automation user, I want clanker output by default when stdout is not a TTY, so that command output is stable and parseable.
9. As a low-token reviewer, I want grug output, so that I can scan Odysseus state quickly.
10. As a script author, I want JSON and JSONL output for machine workflows, so that parsing never depends on decorative terminal text.
11. As a debugger, I want raw and debug output modes, so that I can inspect source-native transport, payload, and rendering details when needed.
12. As an Odysseus user, I want Session commands to operate on durable workspace identity, so that history and exports remain tied to the long-lived conversation.
13. As an Odysseus user, I want Run commands to operate on active or recent execution identity, so that start, attach, status, stop, and replay are not confused with Session history.
14. As an Odysseus user, I want `run start` to create or target a Session, so that live execution and durable history stay linked.
15. As an Odysseus user, I want `run attach` to follow a Run event stream, so that I can observe execution after it starts.
16. As an Odysseus user, I want attaching by Session to fail when multiple active Runs are possible, so that the client does not silently attach to the wrong work.
17. As an Odysseus user, I want `run list` to show active and recent Runs, so that I can find what is running, waiting, done, errored, or interrupted.
18. As an Odysseus user, I want Run status to include heartbeat and last activity summaries, so that stalled or waiting work is visible.
19. As an Odysseus user, I want `run stop` to be the canonical cancellation command, so that execution lifecycle is controlled from one model.
20. As an Odysseus user, I want Session history and Run events to be separate concepts, so that persisted conversation artifacts are not mistaken for execution traces.
21. As a harness user, I want Harness Session identity to remain visible, so that external runtime operations can be correlated with Odysseus Sessions and Runs.
22. As a harness user, I want harness commands to expose Odysseus and harness-native identifiers, so that I can debug linkage across systems.
23. As a harness user, I want harness operations to be capability-gated by adapter support, so that unavailable steering, abort, resume, branch, or open actions are explicit.
24. As a TUI user, I want a live merged Event Envelope timeline, so that chat, agent, harness, service, heartbeat, tool, and log events can be observed in one place.
25. As a TUI user, I want selected event detail, so that I can inspect payloads without losing the event timeline.
26. As a TUI user, I want a local control log, so that attempted stops, restarts, harness commands, and failed controls are visible.
27. As a TUI user, I want focused tab-like views, so that live monitoring, REPL-style commands, browse/tree navigation, and inspection are not crammed into one dashboard.
28. As a TUI user, I want mouse support as a first-class interaction path, so that I can select tabs, rows, tree nodes, and controls where the terminal supports it.
29. As a keyboard user, I want the TUI to remain fully operable by keyboard, so that terminal ergonomics and accessibility are preserved.
30. As an automation author, I want all TUI panes to use the same underlying Event Envelope stream as CLI output, so that interactive and scripted modes do not diverge.
31. As a developer, I want stable Event Envelopes for chat, agent, harness, service, process, server, and system events, so that filtering and rendering can be uniform.
32. As a developer, I want source-native event details preserved in payload or raw fields, so that normalization does not destroy debugging evidence.
33. As a script author, I want one JSONL Event Envelope per line, so that event streams can be piped, stored, and replayed with ordinary tools.
34. As a script author, I want bounded JSON responses to include cursor metadata where relevant, so that polling and continuation are reliable.
35. As a future replay workflow author, I want normalized events rather than raw browser-shaped SSE as the replay contract, so that experiments can survive backend route changes.
36. As an Odysseus operator, I want `service list` to show known Lifecycle Targets, so that managed server, run, harness, model-serving, MCP, and dependency state is visible.
37. As an Odysseus operator, I want Lifecycle Target capability flags, so that I know which targets can show logs, stop, restart, or be forcefully killed.
38. As an Odysseus operator, I want service status to normalize target state while preserving raw details, so that command output is consistent without hiding source evidence.
39. As an Odysseus operator, I want service logs as Event Envelopes, so that logs can be filtered and consumed like other runtime events.
40. As an Odysseus operator, I want stop and restart limited to known managed targets, so that `ody-term` does not become a vague host process manager.
41. As an Odysseus operator, I want forceful or broad operations to require elevated friction, so that dangerous actions are deliberate.
42. As an Odysseus operator, I want arbitrary PID mutation outside ordinary service commands, so that normal lifecycle commands stay ownership-aware.
43. As a local developer, I want `server` commands to bootstrap and inspect a local Odysseus server before API auth is available, so that first contact is ergonomic.
44. As a local developer, I want server bootstrap to reuse existing Odysseus launch machinery, so that `ody-term` does not grow a second launch grammar.
45. As a local developer, I want runtime state to record local server ownership evidence, so that stop and restart can avoid stale or ambiguous process mutation.
46. As a local developer, I want `--start` and `--ensure-server`, so that commands can launch a local target when no reachable server exists.
47. As a profile user, I want Terminal Client profiles for target URLs, repo paths, output posture, and token references, so that I can switch between Odysseus instances.
48. As a profile user, I want profiles to avoid storing mutable process facts, so that target config and runtime state do not become confused.
49. As a security-conscious user, I want raw token values stored in OS secret storage where available, so that durable config does not contain secrets.
50. As a security-conscious user, I want file fallback token storage to be visible, so that weaker storage modes are not hidden.
51. As a security-conscious user, I want auth status to show localhost bypass or auth-disabled modes, so that powerful access is visible.
52. As an admin, I want Terminal Client capability scopes added to existing owner-attributed API tokens, so that `ody-term` does not need a separate credential family.
53. As an admin, I want capability scopes expressed as resource/action pairs, so that aliases and command spellings do not change authorization.
54. As an owner-attributed token user, I want scoped access to my own Sessions, Runs, and events, so that normal terminal use does not require admin access.
55. As an admin, I want cross-owner visibility and extreme service operations to remain admin-only, so that `ody-term` respects the existing trust model.
56. As a non-interactive script author, I want confirmation-required commands to fail with structured output unless confirmation flags are supplied, so that automation can handle safety gates predictably.
57. As a human user, I want ordinary confirmations to be satisfiable with `--yes`, so that expected mutations can run without repeated prompts.
58. As a human or automation user, I want elevated operations to require `--yolo` or typed confirmation, so that forceful actions are visually and operationally distinct.
59. As an automation author, I want `auth capabilities` to report raw auth facts and resolved policy facts, so that agents can decide which operations are available.
60. As a help reader, I want canonical commands documented before aliases, so that shortcuts never become the conceptual model.
61. As an alias user, I want aliases to preserve command semantics, output defaults, fields, exit codes, confirmation behavior, and capability requirements, so that shortcuts are safe to use.
62. As a future self-improvement workflow author, I want no special v1 self-improvement command family, so that higher-level automation composes ordinary inspectable primitives.
63. As a future experiment workflow author, I want repeatable commands with stable structured output, so that repeated runs and comparisons can be built externally.
64. As an Odysseus maintainer, I want v1 to exclude broad feature parity for calendar, email, gallery, documents, cookbook, and other non-session areas, so that the Terminal Client can ship a coherent first slice.
65. As an Odysseus maintainer, I want a future path to privileged inspection kept open, so that the v1 HTTP/API-first design does not block deeper local debugging later.

## Implementation Decisions

- Build `ody-term` as the Terminal Client binary/entrypoint.
- Use scriptable CLI commands as the default surface and `tui` as the canonical full-screen interactive entry point.
- Use canonical command shape `ody-term [global-options] <domain> <verb> [command-options]`.
- Accept global options before or after the domain path for ergonomics, while documenting globals-first form.
- Support global options for target selection, output profile, structured format, color, verbosity, quiet mode, and confirmation posture.
- Use top-level domains: `auth`, `config`, `server`, `session`, `run`, `harness`, `service`, `inspect`, and `tui`.
- Do not make `chat` and `agent` canonical top-level domains. Treat them as Run kinds, with later aliases allowed only as spelling shortcuts.
- Keep Harness Session commands under `harness`; keep harness bridge process lifecycle under `service`.
- Use output profiles `human`, `grug`, and `clanker`.
- Default to `human` output when stdout is a TTY and `clanker` output when stdout is not a TTY.
- Treat JSON/JSONL as refinements of `clanker` output rather than a replacement for the output-profile model.
- Implement aliases only as documented secondary spelling shortcuts that do not alter semantics, output, fields, exit codes, confirmation behavior, or capability requirements.
- Use HTTP/API-first implementation for v1, backed by a narrow terminal-client API layer over existing Odysseus routes.
- Define distinct Session, Run, and Harness Session identity in the Terminal Client contract.
- Treat current backend session-keyed execution internals as a transition substrate, not the Terminal Client contract.
- Start Runs against either a new Session or an existing Session.
- Require Run commands for active lifecycle operations such as attach, status, stop, and event replay.
- Let Session commands expose durable history and exports, plus active Run summaries where useful.
- Use a thin normalized Event Envelope as the default event contract across chat, agent, harness, service, process, server, and system sources.
- Require Event Envelope fields for schema, id, sequence, time, source, kind, level, and payload.
- Include Session, Run, and Harness Session identifiers in Event Envelopes whenever known.
- Preserve source-native bodies in payload and optional raw fields.
- Use raw/debug modes for source-native transport dumps and expanded inspection; do not make raw source events the default automation contract.
- Use Event Envelopes for heartbeat and activity visibility instead of a separate terminal-only status channel.
- Build TUI panes over the same Event Envelope, Session, Run, Harness Session, and capability state used by CLI commands.
- Organize the TUI as focused tab-like views: Live, REPL, Browse/tree, and Inspect.
- Treat mouse support as first-class in the TUI while preserving keyboard operation.
- Treat lifecycle as an Odysseus-managed target surface, not a general host process manager.
- Expose Lifecycle Targets for the local/main server, active and recent Runs, harness bridge processes and harness-linked runtime, model-serving and cookbook tasks, MCP servers, and supporting service health checks.
- Normalize lifecycle target list, status, logs, stop, restart, and forceful-operation availability through target identity and capability flags.
- Keep arbitrary PID kill and broad host mutation outside ordinary service commands and behind explicit elevated friction if supported at all.
- Use existing owner-attributed Odysseus API tokens as the auth base.
- Add Terminal Client capability scopes to the existing API-token model rather than creating a separate credential family.
- Use resource/action capability scopes such as Session read/write, Run read/start/stop, Event read/raw, Harness read/control, Service read/restart/kill, and auth capabilities.
- Make auth-disabled and localhost-bypass modes explicit in auth status, structured output, and TUI chrome.
- Use `--yes` for ordinary confirmations and `--yolo` or typed confirmation for elevated-friction operations.
- Ensure confirmation never bypasses auth, missing scope, ownership, admin-only policy, server policy, or unavailable operations.
- Keep Terminal Client durable config, secret storage, and ephemeral runtime state separate.
- Store profiles as target configuration, not mutable process records.
- Store token references in config and token values in OS secret storage where available, with permission-locked file fallback for dev and unsupported platforms.
- Use runtime state as hint and safety evidence for locally bootstrapped server processes, not as authority to kill arbitrary processes.
- Delegate server bootstrap to existing Odysseus launch machinery.
- Keep local `server` bootstrap commands distinct from API-backed `service` lifecycle commands.
- Resolve targets in order: explicit server URL, explicit profile URL, environment URL, default profile URL, live runtime-state URL, localhost fallback for human/TUI use, and optional local bootstrap.
- Interpret `--start` as starting a local server when no target is selected or reachable.
- Interpret `--ensure-server` as requiring a reachable target and starting a local server when resolution fails and local bootstrap is possible.
- Treat replay, experiment, and self-improvement workflows as future orchestration over ordinary Terminal Client primitives, not as special v1 command families.

## Testing Decisions

- Tests should assert external behavior at the highest useful seam: the `ody-term` command surface and the terminal-client API contracts it consumes.
- CLI tests should exercise command inputs, exit codes, output profile defaults, structured JSON/JSONL shape, confirmation failures, and capability-denied responses without binding to internal parser or renderer implementation details.
- Event tests should validate Event Envelope schema, required fields, sequence ordering, identity linkage, raw preservation, and renderer behavior through public outputs.
- Session/Run/Harness tests should cover the distinction between durable Session history, active Run lifecycle, and Harness Session linkage through command/API behavior.
- Auth and capability tests should cover owner-attributed tokens, Terminal Client scopes, bypass visibility, admin-only controls, ordinary confirmation, and elevated-friction confirmation.
- Lifecycle tests should verify that known managed Lifecycle Targets expose expected actions and that unsupported or ambiguous process mutation fails with structured diagnostics.
- Server/profile/config tests should verify target resolution order, client-local config behavior, secret-reference handling, runtime-state safety checks, and bootstrap delegation behavior.
- TUI tests should focus on state model and rendered behavior at a harnessable boundary: panes consuming shared Session, Run, Event Envelope, and capability state. The throwaway prototype is prior art for the expected Live, REPL, Browse/tree, and Inspect views.
- Prior art exists in the repository's route, CLI, auth, session, harness, service health, shell, MCP, cookbook lifecycle, and JavaScript UI tests. New tests should follow those existing behavior-focused patterns.
- Avoid tests that assert private module structure, exact helper names, or implementation-only intermediate data.

## Out of Scope

- Building broad production feature parity with the graphical Odysseus shell.
- Replacing the graphical Odysseus client.
- Dedicated v1 command families for replay, experiments, or self-improvement.
- Broad v1 support for calendar, email, gallery, documents, cookbook, and other non-session product areas beyond lifecycle visibility where already part of managed targets.
- Treating `ody-term` as an arbitrary host process manager.
- Adding a separate Terminal Client credential family.
- Using Terminal Client config for Odysseus application settings.
- Implementing privileged host-internal inspection in v1, beyond preserving clear future extension boundaries.

## Further Notes

- The resolved wayfinder map already contains the architectural decisions that feed this spec.
- `uv` remains the main project/task runner, but it does not determine the implementation language or technology for `ody-term`.
- The first implementation plan should be split into tracer-bullet work around the highest seams: terminal-client API contract, CLI command surface, Event Envelope renderers, auth/capability enforcement, server/profile runtime state, lifecycle target surface, and TUI state model.
