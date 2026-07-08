# Harness Integrations

Odysseus treats a harness as a cooperative agent runtime. A harness may own an
internal turn scheme, model calls, native tools, and workspace state for part of
a turn, but it can yield control back to Odysseus for tools, policy, approval,
UI, persistence, and other product-owned decisions. Odysseus owns the user
session shell, permissions, streaming shape, and UI rendering.

Harness sessions are ordinary sessions with a non-network endpoint marker and a
sanitized provider option block:

```json
{
  "endpoint_url": "harness://pi",
  "model": "codex-mini",
  "provider_options": {
    "harness": {
      "id": "pi",
      "mode": "observe",
      "model_provider": "openai-codex-responses",
      "model": "codex-mini",
      "thinking_level": "high"
    }
  }
}
```

`GET /api/harnesses` publishes the available adapters and their integration
capabilities. The manifest is the stable UI-facing contract; adapter internals
can change without route or frontend rewrites.

## Python Mini-SDK

`src/harness/sdk.py` is the backend-owned mini-SDK for SDK-hosted harnesses. It
is intentionally not Pi-specific.

Core types:

- `HarnessToolDefinition`: serializable tool schema Odysseus can provide to a
  harness.
- `HarnessToolBroker`: Python protocol for listing and executing tools.
- `OdysseusToolBroker`: broker implementation backed by Odysseus native tool
  schemas and `execute_tool_block()`.
- `HarnessControlRequest` / `HarnessControlResult`: cooperative yield points
  and resume payloads for approval, user-input, policy, UI, and state handoff.
- `HarnessControlBroker`: Python protocol for handling harness yield points.
- `HarnessBridgeProcess`: reusable JSONL subprocess transport.
- `SdkHarnessAdapter`: generic `HarnessAdapter` implementation for harnesses
  hosted through an SDK bridge process.

The SDK bridge protocol is LF-delimited JSON on stdin/stdout. Odysseus sends:

- `start_session`: config plus optional Odysseus tool definitions.
- `prompt`: user message and attachments.
- `command`: harness-specific control command.
- `tool_result`: result for a harness-requested Odysseus tool call.
- `control_result`: result for a harness-requested control-yield point.

A bridge sends:

- `response`: acknowledgement or command response.
- normalized stream events such as `text_delta`, `thinking_delta`,
  `tool_start`, `tool_end`, `harness_event`, `done`, and `error`.
- `tool_call`: request for Odysseus to execute a provided tool.
- cooperative requests such as `approval_request`, `user_input_request`,
  `policy_check`, `ui_request`, `state_update`, or generic `control_yield`.

Bridge payload fields are snake_case. A successful `start_session` response may
return `harness_session_id`, `session_id`, `session_file`, and `session_dir`.
Tool definitions use fields such as `prompt_snippet`, `prompt_guidelines`, and
`execution_mode`. Tool calls use `tool_call_id`; tool results use `is_error`.
Runtime state commands should return JSON-safe summaries using fields such as
`session_id`, `session_file`, `thinking_level`, `is_streaming`, `active_tools`,
and `all_tools`. Harness SDK names can be camelCase internally, but adapters
must translate them at the bridge boundary.

Harness runs are activity-driven, not duration-driven. `heartbeat_interval_seconds`
controls how often Odysseus emits a visible "still running" status while a
bridge is quiet. `activity_timeout_seconds` is the no-activity stall guard for a
prompt turn, and `startup_activity_timeout_seconds` is the equivalent guard for
SDK/session startup.

This is the long-term path for bidirectional integration: the harness SDK host
receives Odysseus tools at construction/start time, exposes them to its model
loop, calls back through `tool_call` when the model invokes one, and yields
control through the same bridge when Odysseus needs to make a product-level
decision.

## Pi Adapter

`pi` is registered through `SdkHarnessAdapter` and the Node bridge at
`src/harness/bridges/pi_sdk_bridge.mjs`. The bridge imports
`@earendil-works/pi-coding-agent`, creates a Pi session with
`createAgentSession()`, and maps Odysseus-provided tools into Pi
`customTools`. The bridge uses Pi's SDK session manager: by default it creates a
file-backed Pi session, can continue the most recent session with
`resume_mode: "continue"` or `resume: true`, can open an explicit
`session_file`, and can opt into in-memory mode with `persist: false`.

Set `ODYSSEUS_PI_SDK_COMMAND` to override the bridge command when Odysseus is
packaged with a different Node entry point or package manager layout. Runtime
integration should resolve the installed `@earendil-works/pi-coding-agent`
package, not a local source checkout. The command must speak the mini-SDK JSONL
protocol described above. The published Pi SDK currently requires Node
`>=22.19.0`.

## Capability Model

Adapters expose:

- `session`: whether the harness supports resume, branching, abort, steering,
  and follow-up turns.
- `tools`: whether Odysseus can list harness tools, provide Odysseus tools to
  the harness, observe/intercept native harness tool calls, or disable native
  Odysseus tools for that session.
- `files`: whether workspace, read, write, and diff events are available.
- `models`: whether the adapter supports model and thinking-level changes.
- `control_flow`: whether the adapter supports cooperative yield/resume
  requests for approvals, user input, policy, UI, and state updates.
- `modes`: supported integration modes.

Pi currently advertises `observe` and `bridged`. `bridged` provides Odysseus
tools to Pi's SDK runtime; tool calls cross back into Python through
`OdysseusToolBroker`, preserving Odysseus owner, workspace, and disabled-tool
policy.

## Stream Mapping

Harness events are normalized before reaching chat routes:

- `text_delta` -> assistant stream delta
- `thinking_delta` -> thinking stream delta
- `tool_start`, `tool_update`, `tool_end` -> Odysseus tool events
- `harness_ui_request` -> harness-specific UI request event
- `control_request` / `control_result` -> harness yield/resume events
- `harness_event` -> lifecycle or adapter-specific event
- `final_text` / `done` -> response completion
- `error` -> stream error

This keeps the frontend consuming the same event vocabulary as native agent
sessions while preserving harness-specific details under `harness_*` fields.
