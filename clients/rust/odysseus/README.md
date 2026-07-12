# odysseus (native Rust GUI client)

A native **egui** desktop client for Odysseus, built to see how well the
`/api/terminal` + `ody.event.v1` contract supports building a *new* frontend
from scratch — in a different language than the reference nvim client — and
grown to feature/visual parity with the web app.

- `src/model.rs` — wire types (`ody.event.v1` envelope + run/session/doc responses)
- `src/api.rs` — blocking HTTP client (`ureq`); token, runs, streaming, docs, notes/tasks
- `src/gui.rs` — the egui app (background stream thread → UI via mpsc)
- `src/theme.rs` — web-app visual theme (One-Dark palette, vendored Fira Code)
- `src/main.rs` — `gui` (default) + headless `probe` / `docs` / `plan` checks

## Features

Mirrors the web app's core experience on the token contract:

- **Chat + agent** runs with live streaming, model + kind pickers
- **Chat bubbles** with role dots, **markdown** answers, and a collapsible
  **thinking** disclosure — styled to match the web app (charcoal/cyan, Fira Code)
- **Agent tool cards** — expandable per-call cards (status dot, command, output)
- **Documents** — live streamed authoring, a **syntax-highlighted** editor,
  edit + save-back (versioned), and open-existing from the sidebar
- **Interactive plan mode** — propose → approve → execute
- **ask_user** — the agent's mid-turn multiple-choice questions as buttons
- **Notes / Tasks** read views
- **Session reload** rebuilds the full agent trace (thinking + tool cards) from
  history metadata

## Run

```sh
cd clients/rust/odysseus
cargo run --release             # opens the GUI (release recommended)
cargo run -- probe "say hi"     # headless: chat round-trip
cargo run -- docs               # headless: agent-writes-doc → list → get → save → verify
cargo run -- plan               # headless: propose → approve → execute
```

Auth/URL resolve like the nvim client (zero-config if `ody-term` is logged in):

- token: `$ODY_NVIM_TOKEN` → `$ODY_TERM_TOKEN` → the `ody-term` macOS keychain entry
- url: `$ODY_TERM_URL` → `$ODYSSEUS_URL` → `http://127.0.0.1:7860`

## Verified

`probe` against a live server: `capabilities` (owner + auth), `sessions` count,
`POST /runs`, and the streamed `message.delta` reply — all green. GUI builds
clean.

## DX findings — does the suite support new frontends?

**Yes, well, for the conversational core.** ~300 lines of Rust, one session.

What was easy:
- **Auth** — one `Authorization: Bearer` header; reusing the `ody-term` keychain
  entry made it zero-config.
- **Streaming** — the normalized NDJSON stream is one JSON object per line, so
  `BufRead::lines()` + `serde_json` is the *entire* stream reader. No SSE
  framing, no partial-frame reassembly. This is the standout: the same mental
  model that worked in Lua worked unchanged in Rust.
- **Runs** — one `POST /runs`, typed `{run_id, session_id}` back; reuse
  `session_id` for continuity.
- **`GET /capabilities`** — a client can introspect owner/scopes/run-inputs
  instead of guessing.

Friction found — and what the API did about it:
1. **Untyped event payloads** (was: the client hand-picked `delta`/`thinking`/
   `tool` from loose JSON). **Fixed:** `GET /capabilities` now publishes an
   `events` map documenting each kind's payload fields (e.g.
   `message.delta → {delta: str, thinking: bool?}`,
   `doc_update → {doc_id, content, version, title, language}`), so a client (or
   codegen) has a machine-readable payload contract instead of guessing.
2. **`Accept-Encoding: identity` footgun** (was: the gzip middleware mangled the
   stream). **Fixed:** the ndjson stream is excluded from gzip and the exec
   proxy no longer forwards `Accept-Encoding`, so a gzip-accepting client gets a
   clean stream. This client still sends `identity` as harmless defense against
   older servers, but it's no longer required.
3. **Client generation.** Wire types are hand-written; the fixed-shape endpoints
   (`capabilities`, `models`, `documents`, `sessions`) now carry `response_model`
   schemas in OpenAPI, so those can be generated. Per-kind event payloads are
   documented (see #1) rather than statically typed on the stream.

**Verdict:** the `/api/terminal` + `ody.event.v1` contract is genuinely
portable across languages (Lua and Rust, same shape), and the two real gaps the
Rust build surfaced (payload discoverability + the gzip footgun) are now closed
in the API itself.
