# Specify Session, Run, And Harness Flows

Status: closed
Type: grilling
Blocked by: Inventory HTTP/API Gaps For `ody-term` V1, Specify Event Envelope And Renderers
Assignee: Codex

## Question

How should `ody-term` create, attach to, list, inspect, steer, cancel, and resume Odysseus Sessions, Runs, and Harness Sessions, including heartbeat visibility and the relationship between durable session identity and active execution?

## Resolution

Use three linked but separate concepts in the v1 Terminal Client surface:

- `Session`: the durable Odysseus conversation/workspace identity.
- `Run`: one active or recent execution, with its own `run_id`, status, event stream, lifecycle, and replay cursor.
- `Harness Session`: the external runtime session/control surface, linked to an Odysseus Session and to a Run while a harness turn is active.

Do not preserve the current backend habit of using `session_id` as the active run key as a Terminal Client contract. The current `agent_runs.py` shape is acceptable as an implementation substrate during transition, but the spec should define distinct Run identity because it better matches the direction of chat, agent, harness, replay, experiment, and service-operation workflows. Core refactoring is in scope for the later implementation plan where it clarifies the model and removes awkward compatibility constraints.

Canonical command behavior:

- `ody-term session list|show|create|history|export` operates on durable Odysseus Sessions.
- `ody-term run list|start|attach|status|stop` operates on Runs.
- `ody-term harness list|show|command|open` operates on harness adapters, harness capabilities, and harness-linked session state.

`run start` should either create a new Session or target an existing Session. Starting a Run returns at least `run_id`, `session_id`, `kind`, `status`, and initial event/cursor metadata. The Session is the durable place where final persisted chat/history artifacts live; the Run is the live/recent execution handle used for attach, stop, status, event tailing, and replay.

`run attach` follows the normalized Event Envelope stream for a `run_id`. It may accept a `session_id` convenience target only when there is exactly one active Run for that Session; ambiguous cases must fail with a structured choice rather than silently attaching to the wrong execution.

`run list` should expose active and recent Runs with status values such as `queued`, `starting`, `running`, `waiting`, `stopping`, `stopped`, `done`, `error`, and `interrupted`. It should include `session_id`, Session name when cheap, `kind`, `started_at`, `updated_at`, `finished_at`, last heartbeat/activity summary, and whether replay/events are still available.

Stop/cancel behavior belongs primarily to Run. `run stop <run_id>` is the canonical cancellation command. `session` commands may expose convenience affordances such as showing active Runs for a Session, but they should not hide the Run model.

Harness flows:

- A Harness Session remains the external runtime identity, such as a Pi session file/id.
- A harness-backed Odysseus Session records adapter/config/linkage.
- Each harness prompt/turn is a Run while active.
- Harness commands should include both Odysseus identity and harness-native identity wherever available: `session_id`, `run_id`, `harness_adapter_id`, and `harness_session_id`.
- Harness steering, abort, resume, branch, follow-up, and command operations should be capability-gated using the adapter manifest. If an operation affects active execution, the resulting events should attach to the relevant Run.

Heartbeat and activity should be surfaced as Event Envelopes rather than a separate terminal-only status channel. The status APIs can summarize the latest heartbeat/activity, but live views, TUI panes, and replay should all consume the same event stream.

Session history and Run replay are different things. `session history` reads persisted conversation history. `inspect events` or `run attach` reads Run events. Experiment/replay workflows should rely on Run event/transcript contracts and may use Session history as a durable artifact, but should not treat browser-shaped session history as the full execution trace.
