# Specify Event Envelope And Renderers

Status: closed
Type: grilling
Blocked by: Inventory HTTP/API Gaps For `ody-term` V1
Assignee: Codex

## Question

What thin normalized event envelope should `ody-term` use across chat, agent, harness, service, and process sources, and which v1 renderers must exist on top of it for human live view, compact logs, pretty debug output, JSONL, raw transport dumps, TUI panes, and future replay?

## Resolution

Use one canonical normalized Event Envelope as the default contract for every `ody-term` event renderer. Raw SSE frames, harness-native events, application log lines, process output, and service lifecycle records should not be the primary terminal-client contract; they remain available through explicit raw/debug renderers and are preserved inside the envelope payload.

The v1 envelope should be intentionally thin:

```json
{
  "schema": "ody.event.v1",
  "id": "evt_...",
  "seq": 123,
  "time": "2026-07-08T12:34:56.789Z",
  "session_id": "ses_...",
  "run_id": "run_...",
  "harness_session_id": "optional external id",
  "source": "chat|agent|harness|service|process|server|system",
  "kind": "message.delta|tool.start|tool.output|agent.step|harness.status|heartbeat|log|service.status|run.status|error|ui.request|control.result",
  "level": "trace|debug|info|warn|error",
  "summary": "short human/debug summary when useful",
  "span_id": "optional correlation id",
  "parent_id": "optional parent event/span id",
  "tags": ["optional", "filter", "hints"],
  "payload": {},
  "raw": {
    "transport": "sse|json|log|stdout|stderr|harness",
    "type": "source-native event type",
    "body": {}
  }
}
```

Required fields are `schema`, `id`, `seq`, `time`, `source`, `kind`, `level`, and `payload`. `session_id`, `run_id`, and `harness_session_id` are required when known and absent only for pre-session server/config/service bootstrap events. `raw` is optional but should be retained wherever available so future debugging does not require a second capture path.

Renderer behavior:

- `human`: default TTY live view. Show concise text, progress, tool/status boundaries, warnings/errors, and final result-oriented events. Collapse noisy payload detail unless `--verbose` is set.
- `grug`: ultra-compact scan view. One short line per important event, stable enough for low-token inspection but not a machine schema.
- `clanker --format jsonl`: one Event Envelope per line. This is the primary stable automation and replay stream.
- `clanker --format json`: bounded query responses may return arrays or objects containing Event Envelopes plus cursor metadata.
- `raw`: explicit raw transport dump for debugging current backend behavior. It may expose source-native SSE/log/harness frames and is not the default replay contract.
- `debug`: pretty expanded Event Envelope view, including payload/raw, correlation ids, capability/safety hints, and renderer decisions where useful.
- `tui`: pane-oriented renderer over the same Event Envelope stream. Panes filter by `source`, `kind`, `level`, `run_id`, and correlation ids rather than subscribing to separate event contracts.

Replay and experiments should consume `clanker --format jsonl` Event Envelopes rather than raw backend transports. Raw output can be captured alongside replay artifacts as diagnostic evidence, but replay correctness should depend on the normalized envelope, stable sequence ordering, timestamps, run/session identity, source/kind classification, and preserved payload.

The backend compatibility layer may translate today's browser-shaped SSE event types into these envelopes, but current SSE names should be treated as source-native payload/raw detail unless promoted into documented `kind` values. Adding new `kind` values is allowed within `ody.event.v1` if old fields remain compatible; breaking field changes require a new `schema` value.
