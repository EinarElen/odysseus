# HTTP/API Inventory For `ody-term` V1

Ticket: [Inventory HTTP/API Gaps For `ody-term` V1](../tickets/001-inventory-http-api-gaps.md)

## Question

Which existing Odysseus HTTP/API routes already support the `ody-term` v1 target surface, and what missing or unsuitable endpoints must be designed before an HTTP/API-first Terminal Client can cover chat sessions, agent runs, harness sessions, stream inspection, service/process lifecycle controls, and authenticated machine use?

## Answer

Odysseus already has enough HTTP surface to build a thin proof-of-concept `ody-term` around existing sessions, chat/agent sends, detached stream resume/stop/status, harness-backed sessions, scoped bearer-token auth, diagnostics, dev status, and cookbook model-serving lifecycle. It does not yet have a clean terminal-client API contract: current streams are UI-shaped SSE, run identity is effectively the Odysseus session id, replay buffers are transient, process/service lifecycle is split across admin-only UI routes and Codex cookbook routes, and API-token scopes do not cover the Terminal Client's intended session/run/harness/process operations.

The next Wayfinder tickets should assume an HTTP/API-first v1 is feasible, but must specify a small compatibility layer rather than exposing `ody-term` directly to every existing UI route.

## Existing Coverage

### Sessions

- `GET /api/sessions` returns owner-scoped, non-archived sessions with ids, names, model/endpoint fields, timestamps, mode, message count, token count, and provider options. Source: [routes/session_routes.py](../../../../routes/session_routes.py:251).
- `POST /api/session` creates a session. It already accepts `endpoint_url`, `model`, `endpoint_id`, `api_key`, `rag`, and `provider_options`; when `provider_options.harness.id` is present it validates the harness and uses `harness://<id>` as the endpoint marker. Source: [routes/session_routes.py](../../../../routes/session_routes.py:360).
- `PATCH /api/session/{sid}` updates session name/folder/model/endpoint/provider options, including harness provider options. Source: [routes/session_routes.py](../../../../routes/session_routes.py:512).
- `GET /api/history/{sid}` and `GET /api/session/{sid}/export` provide persisted conversation inspection/export, but these are history-level rather than run/event-level APIs. Source: [routes/session_routes.py](../../../../routes/session_routes.py:828).
- `GET /api/history/{session_id}` has paged history reads, and the history router also exposes message/session mutation helpers such as truncate, add message, delete messages, edit message, and fork. Sources: [routes/history/history_routes.py](../../../../routes/history/history_routes.py:118), [routes/history/history_routes.py](../../../../routes/history/history_routes.py:230), [routes/history/history_routes.py](../../../../routes/history/history_routes.py:244), [routes/history/history_routes.py](../../../../routes/history/history_routes.py:260), [routes/history/history_routes.py](../../../../routes/history/history_routes.py:323), and [routes/history/history_routes.py](../../../../routes/history/history_routes.py:571).
- `POST /api/session/{session_id}/compact` and `GET /api/session/{session_id}/context_info` expose context-management operations that a terminal inspector may want, though they are not run-control primitives. Source: [routes/session_routes.py](../../../../routes/session_routes.py:997) and [routes/session_routes.py](../../../../routes/session_routes.py:1363).

Gap: there is no terminal-oriented session detail route that joins session metadata, active run status, last heartbeat/progress, harness identity, and recent structured events in one stable shape.

### Chat And Agent Runs

- `POST /api/chat` supports non-streaming chat against an existing session and enforces session ownership and chat privileges. Source: [routes/chat_routes.py](../../../../routes/chat_routes.py:467).
- `POST /api/chat_stream` is the main streaming chat/agent endpoint. It accepts both form and JSON-adjacent fields for message/session, attachments, mode, web/research flags, capability domains/tools, disabled tools, workspace, and related UI context. Source: [routes/chat_routes.py](../../../../routes/chat_routes.py:698).
- In normal non-compare streams, `chat_stream` starts a detached server-side run and returns a subscription to it. Source: [routes/chat_routes.py](../../../../routes/chat_routes.py:2062).
- `GET /api/chat/resume/{session_id}`, `POST /api/chat/stop/{session_id}`, and `GET /api/chat/stream_status/{session_id}` provide active-run reconnect, cancellation, and status using the session id as the run key. Source: [routes/chat_routes.py](../../../../routes/chat_routes.py:2069).
- The detached run manager buffers SSE events in memory, fans them to subscribers, persists only status to `agent_runs.json`, and explicitly notes that replay buffers do not survive server restart. Source: [src/agent_runs.py](../../../../src/agent_runs.py:1), [src/agent_runs.py](../../../../src/agent_runs.py:31), and [src/agent_runs.py](../../../../src/agent_runs.py:51).

Gap: there is no distinct run resource. A new request replaces any in-flight run for the same session, run status is minimal, replay is process-local, and there is no cursor/sequence API for deterministic terminal reconnect or later experiment replay.

### Stream Events And Introspection

- The stream currently emits UI-oriented SSE JSON envelopes such as `model_info`, `attachments`, `rag_sources`, `web_sources`, `memories_used`, `research_progress`, `research_sources`, `research_findings`, `research_done`, `metrics`, `message_saved`, `tool_start`, `tool_progress`, `tool_output`, `agent_step`, `doc_*`, `ui_control`, `ask_user`, and `plan_update`. Sources: [routes/chat_routes.py](../../../../routes/chat_routes.py:1204), [routes/chat_routes.py](../../../../routes/chat_routes.py:1231), [routes/chat_routes.py](../../../../routes/chat_routes.py:1339), [routes/chat_routes.py](../../../../routes/chat_routes.py:1390), [routes/chat_routes.py](../../../../routes/chat_routes.py:1930).
- `GET /api/chat/resume/{session_id}` replays the current in-memory event buffer from the beginning, then follows live output. Source: [src/agent_runs.py](../../../../src/agent_runs.py:235).
- `GET /api/diagnostics/logs` provides admin-only application log tailing. Source: [routes/diagnostics_routes.py](../../../../routes/diagnostics_routes.py:32).
- `GET /api/dev/introspection` exposes FastAPI app introspection only when dev mode is enabled. Source: [routes/dev_routes.py](../../../../routes/dev_routes.py:89).
- The scheduled-task API has a separate event-trigger catalog, including `session_created`, `message_sent`, `document_created`, `memory_added`, `research_completed`, `email_received`, and `skill_added`; this is automation metadata, not a session/run event log. Source: [routes/task_routes.py](../../../../routes/task_routes.py:1031).

Gap: stream events are not specified as a stable versioned envelope with event ids, timestamps, source/subsystem, severity, run id, session id, and raw payload preservation. There is also no route for raw transport dump, JSONL replay, bounded event querying, or "tail from event N" independent of an active SSE connection.

### Harness Sessions

- `GET /api/harnesses` publishes registered harness adapter capabilities. Source: [routes/chat_routes.py](../../../../routes/chat_routes.py:648).
- Harness sessions are ordinary sessions whose endpoint marker is `harness://<adapter>` and whose provider options include a `harness` block. Source: [docs/harnesses.md](../../../harnesses.md:9).
- `POST /api/harnesses/{session_id}/command` sends a harness-specific command after verifying Odysseus session ownership and reconstructing a `HarnessSessionRef`. Source: [routes/chat_routes.py](../../../../routes/chat_routes.py:654).
- The harness adapter protocol has normalized types for capabilities, session refs, events, control requests/results, `start`, `send`, `command`, and `close`. Source: [src/harness/base.py](../../../../src/harness/base.py:17) and [src/harness/base.py](../../../../src/harness/base.py:101).
- The registered `pi` adapter advertises resume, branching, abort, steering, follow-up, tool bridging, file events, model changes, cooperative control, and heartbeat/activity timeout defaults. Source: [src/harness/registry.py](../../../../src/harness/registry.py:19).
- Chat streaming maps harness events into the current SSE vocabulary: `harness_status`, `harness_start`, `harness_event`, `harness_ui_request`, `harness_control_request`, `harness_control_result`, plus tool events and deltas. Source: [routes/chat_routes.py](../../../../routes/chat_routes.py:1392), [routes/chat_routes.py](../../../../routes/chat_routes.py:1497), and [routes/chat_routes.py](../../../../routes/chat_routes.py:1550).

Gap: harness lifecycle exists only through session creation plus `chat_stream` plus a generic command route. There is no first-class route for listing active harness sessions, inspecting harness state, following harness-native events, opening/resuming/branching by harness session id, or translating harness capabilities into terminal commands.

### Service And Process Lifecycle

- Admin diagnostics cover service health for ChromaDB, SearXNG, email, ntfy, and provider endpoints. Source: [routes/diagnostics_routes.py](../../../../routes/diagnostics_routes.py:24).
- Dev routes cover repo/dev-server status, revision, reload, git snapshot, suggested tests, test runs, and app introspection when dev mode is enabled. Source: [routes/dev_routes.py](../../../../routes/dev_routes.py:43).
- Shell execution routes exist for admin-only command execution and streaming shell execution. Source: [routes/shell_routes.py](../../../../routes/shell_routes.py:883) and [routes/shell_routes.py](../../../../routes/shell_routes.py:901).
- Cookbook/model serving has admin-only routes for model serving, cached models, GPU/process state, kill by PID, cookbook state, and task status. Sources: [routes/cookbook_routes.py](../../../../routes/cookbook_routes.py:1880), [routes/cookbook_routes.py](../../../../routes/cookbook_routes.py:3122), [routes/cookbook_routes.py](../../../../routes/cookbook_routes.py:3173), and [routes/cookbook_routes.py](../../../../routes/cookbook_routes.py:4002).
- The Codex integration exposes a narrower scoped machine API for cookbook tasks, servers, output tailing, serve, stop, cached models, presets, and adopt. Source: [routes/codex_routes.py](../../../../routes/codex_routes.py:568).
- Scheduled tasks have their own run/stop and run-history API, which may be relevant for later automation workflows but is separate from chat/agent runs. Sources: [routes/task_routes.py](../../../../routes/task_routes.py:857), [routes/task_routes.py](../../../../routes/task_routes.py:875), [routes/task_routes.py](../../../../routes/task_routes.py:892), and [routes/task_routes.py](../../../../routes/task_routes.py:964).

Gap: there is no unified service/process lifecycle API for `ody-term`. V1 needs a deliberate list/status/log/stop/restart/kill contract across main server, active runs, harness bridge processes, model-serving tasks, and supporting services. Existing cookbook/dev/shell routes are too UI-specific or too privileged to expose directly as the terminal surface.

### Auth, API Tokens, And Capabilities

- API token management supports listing, profiles, create, patch, and delete under `/api/tokens`. It is admin-gated. Source: [routes/api_token_routes.py](../../../../routes/api_token_routes.py:76).
- Allowed token scopes are currently `chat`, todos, documents, email, calendar, memory, and cookbook scopes. There are no explicit session/run/harness/diagnostics/process scopes. Source: [routes/api_token_routes.py](../../../../routes/api_token_routes.py:15).
- The auth middleware accepts `Authorization: Bearer ody_...`, maps valid tokens to `request.state.current_user = "api"`, records owner/scopes separately, and rejects invalid bearer tokens before cookie auth. Source: [app.py](../../../../app.py:405).
- `effective_user()` maps bearer-token requests back to the token owner for routes that intentionally support owner attribution, while `require_user()` rejects API-token callers and tells them to use scope-aware API routes. Source: [src/auth_helpers.py](../../../../src/auth_helpers.py:13) and [src/auth_helpers.py](../../../../src/auth_helpers.py:62).
- Normal route helpers often use cookie/current-user auth and owner checks; Codex routes explicitly translate API token scopes to an owner before calling underlying route handlers. Source: [routes/codex_routes.py](../../../../routes/codex_routes.py:85).
- Admin-only route protection also has an internal loopback bypass token for in-process tools, trusted only on direct loopback. Source: [core/middleware.py](../../../../core/middleware.py:12) and [app.py](../../../../app.py:370).
- `GET /api/codex/capabilities` exposes token-aware capability information, but only for Codex integration tools, not the terminal client. Source: [routes/codex_routes.py](../../../../routes/codex_routes.py:167).
- The Codex helper script refuses non-`/api/codex/` paths, which reinforces that scoped machine use is currently intended to go through integration-specific APIs rather than arbitrary browser routes. Source: [integrations/codex/scripts/odysseus_api.py](../../../../integrations/codex/scripts/odysseus_api.py:178).

Gap: `ody-term` needs its own capability contract. At minimum, the design needs scopes for session read/write, run start/stop/read, harness read/control, diagnostics read, service read/control, process kill, and maybe raw event/log access. It also needs a terminal-friendly capabilities endpoint so scripts can discover permitted commands without probing forbidden routes.

## Missing Or Unsuitable Endpoints To Design

1. `ody-term` capability endpoint: return the authenticated caller, owner, auth mode, allowed terminal-client operations, safety requirements, and versioned event schema support.
2. Session detail endpoint: return one session with model/endpoint/provider options, mode, message count, recent history pointers, harness info, active run summary, and relevant timestamps.
3. Run resource endpoints: create/start a run, list active/recent runs, inspect one run, stop/cancel one run, and expose terminal-safe status fields. Do not key only by session id.
4. Stable event stream endpoint: stream normalized versioned envelopes with sequence ids, timestamps, run id, session id, source, type, severity, renderer hints, and raw payload.
5. Event query/replay endpoint: return JSONL or paginated events from persisted or bounded replay storage, with cursor support and raw-SSE/debug render modes.
6. Harness session endpoints: list/inspect harness sessions, open/resume/branch, issue typed controls, expose heartbeat/activity status, and map harness-native ids to Odysseus session/run ids.
7. Service/process inventory endpoint: list main server, harness bridges, model-serving tasks, supporting services, and relevant subprocesses with safe status/log metadata.
8. Service/process lifecycle endpoint: typed stop/restart/kill actions with capability checks and explicit force-friction, rather than handing `ody-term` raw shell/cookbook/dev routes.
9. API-token scope expansion: add Terminal Client scopes and decide whether existing `chat` can start runs or only read basic chat state.

## Decision

Use the existing HTTP/API surface as the v1 backend substrate, but design a narrow `ody-term` API layer over it. The layer should normalize session/run/harness/process concepts and event envelopes instead of baking current browser SSE and admin UI route shapes into the Terminal Client contract.
