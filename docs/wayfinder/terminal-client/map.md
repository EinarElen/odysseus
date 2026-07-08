# Terminal Client Wayfinder Map

## Destination

A concrete architecture/spec for a non-graphical Odysseus Terminal Client that supports both human terminal use and machine-driven automation, including interactive TUI use, scriptable CLI use, structured/raw introspection output, repeated scripted runs motivated by experiment workflows, and a path toward Odysseus-driven self-improvement workflows.

## Notes

- Local Markdown tracker for this effort; do not create or update GitHub issues.
- Local child tickets live in `docs/wayfinder/terminal-client/tickets/`; each ticket records status, type, and blockers in its metadata because there is no native tracker relationship.
- This map is planning/spec work by default. Implementation is out of scope unless a small prototype is explicitly needed to clarify a decision.
- Use `wayfinder`, `grilling`, and `domain-modeling` while charting or resolving tickets.
- Backend posture decided so far: HTTP/API-first for the first version, with a future privileged inspection mode kept in mind.
- `uv` is the main project/task runner, but it does not determine `ody-term` implementation technology.

## Decisions so far

- Destination accepted — the effort ends with an architecture/spec for the Terminal Client, not an implemented client.
- Terminal Client terminology accepted — Terminal Client is the umbrella term; TUI and CLI name the interactive and scriptable modes.
- Backend posture accepted — first version should be HTTP/API-first, while preserving a future path for privileged inspection of local internals.
- V1 scope accepted — optimize for development/debugging of chat, agent, and harness sessions, with minimum support for the main server, harness sessions, relevant session details such as heartbeats, and service/process listing plus stop/restart controls.
- Command identity accepted — the Terminal Client binary/entrypoint should be `ody-term`.
- Tooling posture accepted — `uv` remains the main project/task runner; `ody-term` can be implemented in whichever technology best fits the client, as long as invocation/distribution integrates cleanly with that runner.
- Product posture accepted — v1 should be live-control first; replay and experiment workflows remain a design driver for stable events, cursors, and structured output, but repeated or comparative runs should be handled through ordinary scriptable client primitives rather than a specialized feature surface.
- Event model accepted — `ody-term` should be built around a structured event stream, using a thin normalized envelope for routing/filtering/rendering/replay while preserving subsystem-native details in a payload escape hatch.
- Session vocabulary accepted — keep Odysseus Session for durable conversation/workspace identity and use Run for active execution; Harness Session remains the external runtime's own session identity linked where applicable.
- Mutation/auth stance accepted — `ody-term` is a powerful utility rather than a restrictive product surface, but it must respect Odysseus auth/capabilities and add explicit friction for extreme operations such as forceful kills or broad restarts.
- [Inventory HTTP/API Gaps For `ody-term` V1](tickets/001-inventory-http-api-gaps.md) — existing HTTP routes are sufficient substrate, but `ody-term` needs a narrow terminal-client API layer for run identity, stable events, replay, harness inspection/control, lifecycle operations, and capability scopes.
- [Decide `ody-term` Command Grammar And Mode Split](tickets/002-command-grammar-and-mode-split.md) — use noun-first `ody-term <domain> <verb>` grammar with CLI commands as the default, canonical `tui`, human/grug/clanker output profiles, documented aliases, global confirmation posture, and top-level local `server`/`config` domains.
- [Specify Event Envelope And Renderers](tickets/003-event-envelope-and-renderers.md) — use one thin normalized Event Envelope as the default renderer/replay contract, preserving raw source-native bodies only in payload/raw fields and explicit raw/debug modes.
- [Specify Session, Run, And Harness Flows](tickets/004-session-run-harness-flows.md) — use distinct Run identity for active/recent execution while keeping Session as durable workspace identity and Harness Session as external runtime identity; later implementation may refactor core session-keyed run internals to match this model.
- [Specify Service And Process Lifecycle Surface](tickets/005-service-process-lifecycle-surface.md) — treat lifecycle as an Odysseus-managed target surface with broad list/status/logs, bounded stop/restart for owned targets, and raw PID/arbitrary process mutation only behind elevated friction.
- [Specify Auth, Capability, And Safety Contract](tickets/006-auth-capability-and-safety-contract.md) — use existing owner-attributed Odysseus API tokens with explicit Terminal Client resource/action scopes, visible bypass modes, resolved capability reporting, and elevated friction for forceful or broad operations.
- [Decide Live TUI Prototype Scope](tickets/007-live-tui-prototype-scope.md) — validate `ody-term tui` as focused tab-like views over shared session/run/event state, with first-class Live, REPL, Browse/tree, Inspect, keyboard, and mouse interaction.
- [Specify Server, Profile, And Config Runtime State](tickets/009-server-profile-config-runtime-state.md) — keep `ody-term` profiles/config and runtime state client-local, delegate server bootstrap to existing `uv run ody launch` machinery, and separate local server control from API-backed service lifecycle.
- [Decide Odysseus-Driven Self-Improvement Path](tickets/010-decide-odysseus-driven-self-improvement-path.md) — treat self-improvement as future orchestration over ordinary `ody-term` run/event/service/profile/auth primitives rather than a special v1 command family or bypass path.
- [Terminal Client V1 Spec](tickets/011-terminal-client-v1-spec.md) — ready-for-agent consolidated product/API contract for implementing `ody-term` from the resolved wayfinder decisions.
- [Terminal Client V1 Implementation Tickets](tickets.md) — ready-for-agent tracer-bullet implementation sequence, starting with internal and external technology surveys.

## Not yet specified

None.

## Out of scope

- Building the production Terminal Client during this wayfinder charting session.
- Replacing the graphical Odysseus client.
- Broad feature parity for calendar, email, gallery, documents, cookbook, and other non-session product areas in v1.
- [Specify Replay And Experiment Constraints](tickets/008-replay-experiment-constraints.md) — a dedicated replay/experiment feature surface is out of scope for v1; replay and experiment workflows remain a motivation for scriptable commands, stable structured output, and event/history primitives.
- Future privileged inspection mode beyond the HTTP/API-first v1 — preserve a path for it by keeping raw/debug/event/capability boundaries explicit, but do not decide host-internal mutation or inspection powers in this v1 Terminal Client architecture map.
