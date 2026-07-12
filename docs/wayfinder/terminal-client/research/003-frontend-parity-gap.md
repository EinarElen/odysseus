# Frontend Parity Gap Analysis — Terminal API → full web-app experience

_What a new frontend can build on today, and what the API still needs for
someone to replicate the web app "-ish" without the browser client._

---

## STATUS UPDATE — most of this is now implemented

The gap analysis below was the pre-implementation baseline. Since then:

- **Owner-token unlock** — a bearer token now authenticates as its owner across
  the *entire* app (`app.py` middleware + `auth_helpers.require_user`), so a
  token-based frontend reaches every domain (email, calendar, tasks, notes,
  memory, skills, presets, gallery, research, compare, cookbook, search,
  uploads, settings) read+write, owner-attributed, via the real routes.
  _Dev/wild-west posture: any valid token = full owner access (incl. admin)._
- **Normalized `/api/terminal` contract** now covers: runs (chat/agent/harness),
  events (query + NDJSON stream), sessions (read/history/export), usage, models,
  **documents (full CRUD + live editing stream)**, **notes (read)**,
  **tasks (read)**, and **capabilities**.
- **Run inputs completed:** `active_doc_id` (iterative doc editing), `plan_mode`
  + `approved_plan` (the propose→approve→execute plan loop), `attachments`
  (multimodal / doc-from-file).
- **Interactive channel:** `ask_user` answered via follow-up run (by design —
  it ends the turn); plan via `plan_mode`/`approved_plan`; harness steering via
  the now-reachable `/api/harnesses/{sid}/command`.

**What deliberately was NOT normalized:** writes with side effects (note
reminders, task scheduling, calendar recurrence, IMAP) go through the real
routes rather than thin — and buggy — terminal duplicates.

**Remaining refinement (not a capability gap):** `response_model`/OpenAPI typing
on the terminal routes so clients can be generated rather than reverse-
engineered. Applying it requires *complete* models per endpoint (FastAPI filters
responses to declared fields), so it's a deliberate, test-backed pass.

The rest of this document is the original analysis, kept for reference.

---

## TL;DR

Odysseus effectively has **two API surfaces**:

1. **The cookie-session web routes** — everything the browser uses. Untyped
   ad-hoc JSON + SSE, ~450 routes, ~132k lines of client JS on top. A new
   *browser* frontend on the same origin *can* reach all of it (after
   `/api/auth/login`), but has no typed contract and must reimplement the whole
   client.
2. **The normalized `/api/terminal` contract** — token-scoped, owner-attributed,
   with the stable `ody.event.v1` NDJSON event envelope. This is the reusable
   seam for a *non-browser* frontend.

`/api/terminal` now covers the **conversational core** well enough that a new
frontend can replicate chat + agents + live document editing (proven by the
`odysseus.nvim` client). **Full web parity is not there yet** — email, calendar,
tasks, notes, research, compare, cookbook, gallery, settings, search, uploads,
and (critically) *interactive run control* are still browser/cookie-only with no
normalized contract.

---

## 1. What `/api/terminal` supports today

| Domain | Endpoints | Enables |
| --- | --- | --- |
| **Runs** | `POST /runs`, `GET /runs`, `GET /runs/{id}`, `POST /runs/{id}/stop`, `…/by-session/{sid}` variants | Start/stop/list chat, agent, and harness runs; reuse `session_id` for continuity; agent runs can target a document (`active_doc_id`) |
| **Events** | `GET /events`, `GET /events/stream` (NDJSON), `GET /runs/{id}/events` | Bounded replay + live tail of the normalized `ody.event.v1` stream, cursor-resumable |
| **Sessions** | `GET /sessions`, `/{id}`, `/{id}/history`, `/{id}/export` | Browse/resume conversations, read persisted history, export md/txt/json |
| **Usage** | `GET /usage/{summary,breakdown,timeseries,runs,runs/{id},cache,subscription,export,live}` | Full owner-scoped workload accounting + live updates |
| **Models** | `GET /models` | List available models + the configured default |
| **Documents** | `GET /documents`, `GET/POST /documents`, `PUT /documents/{id}` | List / open / create / update the writing surface, with version history shared with the web route |

**Run inputs** (`RunStartRequest`): `kind` (chat/agent/harness), `session_id`,
`message`, `model`/`endpoint_url`, `preset_id`, harness fields, `workspace`,
`active_doc_id`.

**Event vocabulary already flowing through the stream** (normalized to
`ody.event.v1` kinds):

```
message.delta  run.status  metrics  message_saved  model_info
tool_start  tool_progress  tool_output  agent_step  rounds_exhausted
doc_stream_open  doc_stream_delta  doc_update  doc_suggestions
research_progress  research_sources  research_findings  research_done
harness_start  harness_status  harness_event
harness_ui_request  harness_control_request  harness_control_result
plan_update  ask_user  ui_control
attachments  rag_sources  web_sources  memories_used
compacted  context_trimmed  workspace_rejected  budget_exceeded  heartbeat
```

**Key observation:** the *event stream is already rich* — it carries research,
harness, plan, tool, doc, and interactive-prompt events. But the API is missing
the **endpoints to initiate** several of those flows and, crucially, the
**channel to respond** to the interactive ones (see §3.1).

---

## 2. The parity map (web feature → API status)

Auth model per web feature group (routes today are almost entirely
cookie-`require_user`; only a handful authenticate a bearer token):

| Feature | Web routes | Reachable by token now? | On the normalized contract? | Gap for parity |
| --- | --- | --- | --- | --- |
| Chat + Agents | chat_routes | ✅ via `/api/terminal/runs` | ✅ | Attachments, session write-ops, interactive replies |
| Sessions | session_routes (19) | 🔶 read-only via terminal | 🔶 read only | rename/delete/archive/fork/truncate, message edit/delete |
| Documents | document_routes (23) | ✅ via `/api/terminal/documents` | ✅ | versions/restore, suggestions accept, import/export |
| Models | model_routes (19) | 🔶 list only | 🔶 list only | endpoint CRUD, default-model set, probing |
| Usage | usage | ✅ | ✅ | — |
| Deep Research | research (stream only) | ❌ (no start/result endpoint) | 🔶 events only | research run kind + status/result endpoints |
| Compare | compare_routes (5) | ❌ | ❌ | compare run kind (blind multi-model + synthesis) |
| Email | email_routes (54) | ❌ cookie-only | ❌ | whole email domain (list/thread/read/draft/send/tags/triage/reminders) |
| Calendar | calendar_routes (19) | 🔶 partially open | ❌ normalized | owner-scoped terminal calendar domain (+ CalDAV) |
| Notes | note_routes (10) | ❌ cookie-only | ❌ | terminal notes domain |
| Tasks | task_routes (22) | 🔶 partially open | ❌ normalized | scheduled-task CRUD + run/history in terminal |
| Memory | memory_routes | 🔶 open list | ❌ normalized | owner-scoped memory CRUD |
| Skills | skills_routes (21) | 🔶 open list | ❌ normalized | skill CRUD/install |
| Gallery / images | gallery_routes | ❌ | ❌ | image-gen + gallery domain |
| Cookbook (serving) | cookbook_routes (17) | 🔶 via `service`/Codex | 🔶 partial | serve/stop/list model-serving in terminal |
| Presets | preset_routes (8) | 🔶 open | 🔶 run takes `preset_id` | list/CRUD presets in terminal |
| Search | search_routes (4) | ❌ | ❌ | global search (sessions/docs/memory) |
| Uploads / attachments | upload_routes (6) | ❌ | ❌ | upload channel feeding runs + docs |
| STT / TTS | stt/tts_routes | ❌ | ❌ | voice endpoints |
| Settings / prefs / 2FA | prefs/model/admin | ❌ cookie/admin | ❌ | user-pref read/write, default model, theme |

Legend: ✅ done · 🔶 partial · ❌ missing.

---

## 3. Cross-cutting contract gaps (the ones that matter most)

### 3.1 There is no response/steering channel (biggest gap)
The stream emits server→client **prompts** — `ask_user`, `ui_control`,
`plan_update` (approve/reject a plan), `harness_ui_request`,
`harness_control_request` — but `/api/terminal` has **no way to answer them**.
The only run mutation is `stop`. To replicate the web app's interactive agent
experience a frontend needs a typed **respond-to-run** endpoint, e.g.:

```
POST /api/terminal/runs/{id}/respond
  { "type": "ask_user",        "value": "yes" }
  { "type": "plan_decision",   "decision": "approve" | "reject", "edits": … }
  { "type": "ui_response",     "control_id": …, "value": … }
  { "type": "harness_control", "action": "steer"|"abort"|"branch"|"follow_up", … }
```

Without this, agent runs that ask questions, request plan approval, or need
harness steering can only be watched, not driven.

### 3.2 No attachment / upload channel
`RunStartRequest` is text-only. The web chat supports image/file attachments and
"document from uploaded PDF". A frontend needs an upload endpoint whose ids can
be referenced from a run (and from document creation).

### 3.3 Read-heavy contract; most domains need CRUD
`/api/terminal` is run-centric + a few reads. Documents got full CRUD; every
other productivity domain (email, calendar, tasks, notes, memory, skills)
needs the same owner-scoped, token-gated CRUD treatment to reach parity. The
shared-helper pattern used for documents (extract logic → both web + terminal
call it) is the template.

### 3.4 No capabilities / discovery endpoint
The `auth:capabilities` scope exists but there's no `GET /api/terminal/capabilities`
returning who-am-I + allowed operations + supported event schema. A generated
client and graceful degradation both want this.

### 3.5 Untyped schema
Terminal responses are still `dict[str, Any]` with almost no `response_model`,
so `/openapi.json` can't generate a typed client. A frontend author reverse-
engineers shapes. Adding response models (or a maintained OpenAPI) is the single
biggest DX multiplier.

### 3.6 Notifications beyond a single run
Reminders firing, email arriving, a scheduled task finishing — the web app
surfaces these. There's no terminal subscribe/notifications stream; only
per-run events. A `GET /api/terminal/notifications/stream` (or a general event
bus) would be needed for a live, app-like frontend.

### 3.7 Native-client auth
Tokens are minted in the web UI. There *is* a pairing foundation already
(`remote_access/pairing.py`, `companion/pairing.py`) — a native frontend should
use a device-pairing/OAuth-style flow rather than manual token copy.

---

## 4. Effort tiers to reach "web-app-ish"

**Tier 1 — a complete *conversational* frontend (closest to done):**
- Interactive respond-to-run channel (§3.1) ← highest value
- Attachments/upload channel (§3.2)
- Session write-ops (rename/delete/archive/fork/truncate, message edit/delete)
- Presets list, memory read, skills list on the terminal contract
- Capabilities endpoint + response models for the existing terminal routes

**Tier 2 — productivity parity:**
- Terminal domains for **email**, **calendar**, **tasks**, **notes**, **memory**
  (owner-scoped CRUD, shared-helper pattern)
- Global **search**
- Notifications stream (§3.6)

**Tier 3 — rich / media parity:**
- **Deep Research** (run kind + status/result endpoints; events already stream)
- **Compare** (blind multi-model run kind + synthesis)
- **Cookbook** model-serving control
- **Gallery / image generation**, **STT/TTS**
- **Settings/prefs** read-write (default model, theme, endpoints)

---

## 5. Recommendation

Treat `/api/terminal` as **the** frontend contract and grow it domain-by-domain,
rather than exposing the raw cookie web routes to new clients. Two changes unlock
the most parity per unit effort:

1. **The respond-to-run channel** — turns the already-rich event stream from a
   one-way feed into a real interactive agent surface.
2. **Response models / OpenAPI on the terminal routes** — lets any frontend be
   generated instead of hand-reverse-engineered.

Everything else is mechanical domain build-out on the proven shared-helper +
`require_terminal_scope` pattern.

_Verified reference implementation: `clients/nvim/odysseus.nvim` replicates
chat, agents, sessions, live document editing (authoring + iterative edits +
manual save-back), model picking, and usage on exactly this contract._

_Second reference implementation (native GUI): `clients/rust/odysseus` (egui).
Replicates chat + agent streaming (answer/thinking/tool cards), model + kind
pickers, sessions + history, live document editing (streamed authoring +
edit + save-back + open existing), notes/tasks read views, **interactive plan
mode** (propose → approve → execute), and the **ask_user** choice channel — all
on `/api/terminal`. Building it drove the Tier-1 interactive channel (§3.1) to
done and surfaced/fixed a payload-shape wart: `plan_update` and `ask_user` SSE
chunks nested their fields under `data`, unlike every other kind; the terminal
event normalizer now flattens them so a client reads `payload[field]`
uniformly. Headless probes (`odysseus probe|docs|plan`) exercise the chat,
document, and plan flows against a live server._
