# Terminal Client Wayfinder Map

## Destination

A concrete architecture/spec for a non-graphical Odysseus Terminal Client that supports both human terminal use and machine-driven automation, including interactive TUI use, scriptable CLI use, structured/raw introspection output, repeatable experiment runs, and a path toward Odysseus-driven self-improvement workflows.

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
- Product posture accepted — v1 should be live-control first, with replay/experiment support treated as a first-class design constraint through stable event and transcript formats.
- Event model accepted — `ody-term` should be built around a structured event stream, using a thin normalized envelope for routing/filtering/rendering/replay while preserving subsystem-native details in a payload escape hatch.
- Session vocabulary accepted — keep Odysseus Session for durable conversation/workspace identity and use Run for active execution; Harness Session remains the external runtime's own session identity linked where applicable.
- Mutation/auth stance accepted — `ody-term` is a powerful utility rather than a restrictive product surface, but it must respect Odysseus auth/capabilities and add explicit friction for extreme operations such as forceful kills or broad restarts.
- [Inventory HTTP/API Gaps For `ody-term` V1](tickets/001-inventory-http-api-gaps.md) — existing HTTP routes are sufficient substrate, but `ody-term` needs a narrow terminal-client API layer for run identity, stable events, replay, harness inspection/control, lifecycle operations, and capability scopes.

## Not yet specified

- What future privileged inspection mode is allowed to inspect or mutate beyond the HTTP/API-first v1.
- How Odysseus-driven self-improvement should eventually use `ody-term` once the v1 live-control surface and event schema exist.

## Out of scope

- Building the production Terminal Client during this wayfinder charting session.
- Replacing the graphical Odysseus client.
- Broad feature parity for calendar, email, gallery, documents, cookbook, and other non-session product areas in v1.
