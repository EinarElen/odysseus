# `ody-term` Terminal Client

`ody-term` is the non-graphical Odysseus control surface. The v1 surface is live-control first and automation-friendly: use ordinary commands for sessions, runs, harnesses, lifecycle targets, inspection, and the TUI. Replay, experiment, and self-improvement workflows should wrap these primitives instead of depending on a special command family.

Current implementation status: the command surface, renderers, local server
bootstrap, capability shapes, file/keychain auth storage, lifecycle inventory
scaffold, a first `/api/terminal/runs` chat-run compatibility seam, and
`ody.tui.v1` state model exist. Full chat execution through the terminal-client
API, Event Envelopes over live Odysseus activity streams, and a full-screen
interactive TUI are still open work tracked in
`docs/wayfinder/terminal-client/implementation-review.md`.

## Command Shape

```bash
ody-term [global-options] <domain> <verb> [command-options]
```

Canonical domains are `auth`, `config`, `server`, `session`, `run`, `harness`, `service`, `inspect`, and `tui`.

Global options include:

- `--target URL`, `--profile NAME`
- `--output human|grug|clanker`
- `--format text|json|jsonl|raw|debug`
- `--color auto|always|never`
- `--quiet`, `--verbose`
- `--yes`, `--yolo`
- `--start`, `--ensure-server`

Aliases are metadata, not a separate semantic surface. Discover them with:

```bash
ody-term inspect aliases --format=json
```

## Automation Contracts

Use `--output clanker --format json` for bounded command responses. `clanker` means stable machine-oriented output; `grug` means compact human scan output. Non-TTY stdout defaults to the `clanker` profile, so scripts can usually request only `--format=json`.

Bounded JSON responses have this outer shape:

```json
{
  "ok": true,
  "command": ["run", "status"],
  "message": "Run run_... is running",
  "profile": "clanker",
  "format": "json",
  "data": {}
}
```

Use `--format jsonl` for event streams. Event-stream commands emit one `ody.event.v1` envelope per line:

```bash
ody-term run attach run_abc --format=jsonl
ody-term inspect events --format=jsonl
ody-term service logs main-server --format=jsonl
```

The stable replay contract is the normalized Event Envelope, not raw backend transport:

```json
{
  "schema": "ody.event.v1",
  "id": "evt_run_abc_1",
  "seq": 1,
  "time": "2026-07-08T12:00:00+00:00",
  "session_id": "ses_...",
  "run_id": "run_...",
  "source": "chat",
  "kind": "run.status",
  "level": "info",
  "payload": {}
}
```

## Cursors And Reconnect

`run attach` and `inspect events` return cursor metadata in JSON responses:

```bash
ody-term run attach run_abc --format=json
```

Read `data.cursor.next`, then reconnect by passing that value back as `--cursor`:

```bash
ody-term run attach run_abc --cursor 42 --format=jsonl
ody-term inspect events --cursor 42 --format=jsonl
```

The cursor is an event sequence continuation point. Events with `seq <= cursor` are skipped.

## Raw And Debug Capture

`--format raw` and `--format debug` are diagnostic capture modes. They may expose source-native SSE, log, harness, or compatibility payloads and require raw-event capability where applicable. Do not treat raw/debug output as the replay contract; use `ody.event.v1` JSONL for automation and replay.

Examples:

```bash
ody-term inspect events --format=debug
ody-term run attach run_abc --format=raw
```

## Exit Codes

- `0`: command succeeded.
- `1`: known runtime or target failure, such as unknown run, stale runtime state, or unsupported lifecycle action.
- `2`: usage, auth, capability, confirmation, or policy failure.

Structured errors are written to stderr in `clanker`, `json`, or `jsonl` modes:

```json
{
  "ok": false,
  "error": {
    "code": "confirmation_required",
    "message": "run:stop requires --yes"
  }
}
```

Common structured error categories:

```json
{"ok":false,"error":{"code":"missing_token","message":"auth login requires --token"}}
```

Auth errors use codes such as `missing_token` or `capability_denied` and exit `2`.

```json
{"ok":false,"error":{"code":"capability_denied","message":"event:raw is not allowed: server_capabilities_unavailable"}}
```

Capability and confirmation errors use codes such as `capability_denied`, `confirmation_required`, or `elevated_confirmation_required` and exit `2`.

```json
{"ok":true,"command":["config","resolve-target"],"message":"Target resolution failed","profile":"clanker","format":"json","data":{"target":{"ok":false,"source":"unresolved","reason":"no target URL found for non-interactive command"}}}
```

Target resolution failures are command responses with `ok:false` inside `data.target` and exit `1`.

```json
{"ok":true,"command":["server","stop"],"message":"Local server runtime state is stale","profile":"clanker","format":"json","data":{"server":{"status":"stale"}}}
```

Runtime failures that are expected operational states, such as stale local server runtime evidence or unsupported lifecycle actions, exit `1`; unsupported or policy-blocked lifecycle mutations may be stderr errors when the command cannot produce a valid target response.

## Scriptable Repeated Runs

Use normal shell composition for repeated execution and inspection:

```bash
run_json="$(ody-term run start --kind chat --message 'Summarize the failing test' --format=json)"
run_id="$(printf '%s' "$run_json" | jq -r '.data.run.run_id')"
ody-term run attach "$run_id" --format=jsonl > run-events.jsonl
ody-term run status "$run_id" --format=json
```

For future self-improvement orchestration, keep the loop external: start or select a target with profiles/server flags, run work through `run` commands, collect Event Envelopes, compare outcomes with ordinary tools, edit code outside `ody-term`, then restart and inspect through `server`, `service`, and `run` commands.
