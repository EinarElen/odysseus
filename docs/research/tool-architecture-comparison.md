# Tool Architecture Comparison: OpenAI, Codex, opencode, Pi, and Odysseus

Date: 2026-07-08

## Question

How does Odysseus's current tool architecture compare to the canonical OpenAI Responses API tool model and to current first-party implementations in OpenAI Codex CLI, opencode, and Pi? What gaps matter for a web/agent app like Odysseus?

## Answer

Odysseus already has a broad local tool surface, but its core contract is still a legacy `ToolBlock` pipeline wrapped with OpenAI-compatible function schemas. That works for chat-completions style function calls, Anthropic tool use, fenced calls, and Pi SDK bridging, but it does not match the current OpenAI Responses API item model. The largest gap is not the number of tools; it is the lack of a typed, provider-neutral tool runtime where the model-visible spec, executable handler, permission policy, concurrency policy, and model-facing result item are one registered unit.

The modern baselines all move in that direction:

- OpenAI Responses treats tool calls and tool outputs as structured items correlated by `call_id`, with built-in hosted tools, remote MCP, custom free-form tools, local shell/shell/apply_patch item types, and computer-use/deep-research flows.
- Codex CLI has a dedicated tools crate and runtime registry that serializes `ToolSpec` values directly as Responses API tools, preserves distinct payload/output item variants, supports deferred tool loading and tool search, and gates parallel execution per tool.
- opencode centralizes tool definitions in a registry, adapts schemas per provider, wraps execution in plugin/permission/truncation hooks, and has first-class permission requests.
- Pi keeps a minimal default tool set but exposes a clean SDK custom-tool contract and a core loop that can execute tool batches in parallel while preserving transcript order.

Odysseus should not copy any one implementation wholesale. It should split the existing monolithic schema plus dispatcher into a registry/runtime contract, then add a true Responses adapter alongside the existing chat-completions adapter.

## Sources

Primary sources only:

- OpenAI official docs:
  - Using tools: <https://developers.openai.com/api/docs/guides/tools>
  - Function calling: <https://developers.openai.com/api/docs/guides/function-calling>
  - MCP and connectors: <https://developers.openai.com/api/docs/guides/tools-connectors-mcp>
  - Deep research: <https://developers.openai.com/api/docs/guides/deep-research>
  - Computer use: <https://developers.openai.com/api/docs/guides/tools-computer-use>
  - Migration note for Responses tool-call item threading: <https://developers.openai.com/api/docs/guides/migrate-to-responses>
  - API reference item types, including function outputs, MCP calls, local shell, shell, apply_patch, custom tool calls, code interpreter, web search, and image generation: <https://developers.openai.com/api/reference/python/resources/conversations/subresources/items/methods/retrieve>
- OpenAI Codex CLI first-party repo, cloned at `bdaad68` from <https://github.com/openai/codex>.
- opencode first-party repo, cloned at `fe96d4a` from <https://github.com/sst/opencode>.
- Pi installed package source:
  - `@earendil-works/pi-coding-agent` version `0.78.1`, repository metadata in [package.json](../../node_modules/@earendil-works/pi-coding-agent/package.json:2).
  - Nested `@earendil-works/pi-agent-core` package source under [node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-agent-core](../../node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-agent-core/README.md:76).
- Odysseus files requested in scope:
  - [src/tool_schemas.py](../../src/tool_schemas.py:33)
  - [src/tool_execution.py](../../src/tool_execution.py:43)
  - [src/agent_loop.py](../../src/agent_loop.py:2210)
  - [src/llm_core.py](../../src/llm_core.py:1070)
  - [src/chatgpt_subscription.py](../../src/chatgpt_subscription.py:1)
  - [src/harness/sdk.py](../../src/harness/sdk.py:40)
  - [src/harness/bridges/pi_sdk_bridge.mjs](../../src/harness/bridges/pi_sdk_bridge.mjs:27)

## OpenAI Canonical Tool Model

OpenAI's current docs present tools as a Responses API surface covering built-in tools, function calling, tool search, and remote MCP servers. The `tools` guide explicitly groups web search, file search, tool search, function calling, and remote MCP as model capabilities, with examples using `responses.create(... tools: [{ type: "web_search" }])`.

Function calling is no longer just "JSON args in, text out". The function-calling guide covers JSON-schema function tools and custom tools with free-form text inputs and outputs. The migration guide states the important Responses invariant: tool calls and their outputs are distinct Items correlated by `call_id`.

The API reference shows why a Responses-native adapter needs multiple item variants, not just Chat Completions `tool` messages:

- `function_call_output` items carry the `call_id` and output.
- `mcp` tools have `server_label`, `server_url`/authorization configuration, `allowed_tools`, and approval state; MCP call items include `server_label`, `name`, `arguments`, `approval_request_id`, `output`, `error`, and `status`.
- `custom_tool_call` and `custom_tool_call_output` use free-form `input`/`output`, not JSON function arguments.
- `local_shell_call`, `shell_call`, and `apply_patch` are distinct item types.
- Built-in hosted tools include web search, file search, image generation, code interpreter, and computer use item shapes.

Remote MCP is also a built-in OpenAI tool type. The MCP/connectors guide says connectors and remote MCP servers are used through the `mcp` built-in tool type, with automatic or developer-required approval. This differs from merely expanding MCP tools into local function schemas.

Computer use is explicitly a harness loop: the model inspects screenshots, returns UI actions, and the application executes those actions in an isolated browser/VM or custom harness. Deep research uses Responses with deep research models and requires at least one data source: web search, remote MCP, or file search; it can also include code interpreter.

## Codex CLI Baseline

Codex's tool layer is structured around Responses-compatible specs. `ToolSpec` serializes directly as OpenAI Responses API tools and includes `function`, `namespace`, `tool_search`, `image_generation`, `web_search`, and `custom` free-form variants ([codex-rs/tools/src/tool_spec.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/tools/src/tool_spec.rs#L13-L53)). The helper explicitly creates JSON compatible with Responses API function calling ([tool_spec.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/tools/src/tool_spec.rs#L77-L91)).

Codex separates metadata from runtime. `ToolDefinition` contains name, description, input schema, optional output schema, and `defer_loading`; it can be renamed or converted into a deferred tool ([tool_definition.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/tools/src/tool_definition.rs#L4-L25)). MCP tools are parsed into that shape, including an output schema for MCP call results ([mcp_tool.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/tools/src/mcp_tool.rs#L6-L37)).

Codex also preserves distinct payload and output contracts. `ToolPayload` has `Function`, `ToolSearch`, and `Custom` variants ([tool_payload.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/tools/src/tool_payload.rs#L5-L20)). `ToolOutput` converts executable results back into typed Responses input items, including function outputs, custom tool outputs, and MCP tool call outputs ([tool_output.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/tools/src/tool_output.rs#L15-L28), [tool_output.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/tools/src/tool_output.rs#L135-L180)).

The router converts model output items into runtime calls for `FunctionCall`, `ToolSearchCall`, and `CustomToolCall` ([router.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/core/src/tools/router.rs#L112-L160)). Its prompt builder sends `router.model_visible_specs()` and `parallel_tool_calls` from model info ([turn.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/core/src/session/turn.rs#L1083-L1099)). The client request preserves those values in the Responses request body ([client.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/core/src/client.rs#L897-L913)).

Tool execution is concurrent when tools opt in. `ToolExecutor` has `supports_parallel_tool_calls()` ([tool_executor.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/tools/src/tool_executor.rs#L44-L68)); `ToolCallRuntime` uses a read/write lock so parallel-safe tools share a read lock and non-parallel tools take the write lock ([parallel.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/core/src/tools/parallel.rs#L93-L156)). Sampling keeps tool calls in `FuturesOrdered` and records each `ResponseInputItem` back to history ([turn.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/core/src/session/turn.rs#L1892-L1910), [turn.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/core/src/session/turn.rs#L1971-L2114)).

Shell and patch are first-class planned tools, not generic `bash` strings. Tool planning chooses unified exec vs shell command by model/features/environment and registers `exec_command`, `write_stdin`, legacy shell dispatch, and `apply_patch` when supported ([spec_plan.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/core/src/tools/spec_plan.rs#L640-L680), [spec_plan.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/core/src/tools/spec_plan.rs#L765-L769)). `exec_command` and `shell_command` opt into parallel calls ([exec_command.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs#L80-L101), [shell_command.rs](https://github.com/openai/codex/blob/bdaad68/codex-rs/core/src/tools/handlers/shell/shell_command.rs#L140-L158)). The TypeScript SDK exposes sandbox mode, network access, web search mode, and approval policy as exec options ([sdk/typescript/src/exec.ts](https://github.com/openai/codex/blob/bdaad68/sdk/typescript/src/exec.ts#L10-L41), [exec.ts](https://github.com/openai/codex/blob/bdaad68/sdk/typescript/src/exec.ts#L86-L149)).

## opencode Baseline

opencode's local model is a registry of typed tool definitions. `Tool.Def` includes an id, description, parameter decoder, optional JSON schema, executable function, and validation error formatter; runtime context includes session/message ids, abort signal, call id, messages, metadata updates, and `ask()` for permission requests ([packages/opencode/src/tool/tool.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/tool/tool.ts#L36-L65)). The wrapper decodes arguments, standardizes invalid-argument errors, traces execution, and truncates output through the agent's truncation policy ([tool.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/tool/tool.ts#L99-L149)).

The registry builds built-ins plus custom project/plugin tools. Built-ins include shell, read, glob, grep, edit, write, task, web fetch/search, skill, apply_patch, question, LSP, plan, and optional code mode ([registry.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/tool/registry.ts#L96-L247)). Project tools are discovered from `{tool,tools}/*.{js,ts}` under configured directories, and plugin tools are normalized into the same definition shape ([registry.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/tool/registry.ts#L120-L199)). The registry filters tools by provider/model, choosing `apply_patch` for newer GPT-style models and falling back to edit/write otherwise ([registry.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/tool/registry.ts#L286-L335)).

Session tools adapt registry definitions to AI SDK tools and provider-specific schemas, then execute through plugin hooks and complete tool calls in the session processor ([packages/opencode/src/session/tools.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/session/tools.ts#L41-L134)). MCP resource tools are added when a connected MCP server exposes resources, with permission checks and truncation ([session/tools.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/session/tools.ts#L136-L220)).

Permissions are first-class state. The core permission service evaluates rules, creates pending permission requests, publishes asked/replied events, waits on deferred replies for blocking assertions, persists "always" approvals, and exposes list/get/forSession APIs ([packages/core/src/permission.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/core/src/permission.ts#L76-L99), [permission.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/core/src/permission.ts#L176-L218), [permission.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/core/src/permission.ts#L220-L300)).

Shell and patch are notably richer than Odysseus's current generic shell/edit tools. Shell parses bash/PowerShell with tree-sitter, scans file-touching commands, asks for external-directory and shell permissions, and runs with timeout/output capture ([tool/shell.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/tool/shell.ts#L257-L291), [shell.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/tool/shell.ts#L338-L465)). `apply_patch` parses patch text, validates files, asks edit permission with diff metadata, applies add/update/delete/move operations, emits file watcher/LSP updates, and reports diagnostics ([tool/apply_patch.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/tool/apply_patch.ts#L18-L75), [apply_patch.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/opencode/src/tool/apply_patch.ts#L204-L303)).

opencode also has an OpenAI Responses preparation layer for hosted/provider tools. It maps AI SDK provider tools to Responses `file_search`, `local_shell`, `web_search_preview`, `web_search`, `code_interpreter`, and `image_generation`, and maps tool choice to hosted-tool or function selectors ([packages/core/src/github-copilot/responses/openai-responses-prepare-tools.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/core/src/github-copilot/responses/openai-responses-prepare-tools.ts#L9-L52), [openai-responses-prepare-tools.ts](https://github.com/sst/opencode/blob/fe96d4a/packages/core/src/github-copilot/responses/openai-responses-prepare-tools.ts#L53-L173)).

## Pi Baseline

Pi's installed package advertises a minimal default tool set: `read`, `write`, `edit`, and `bash` ([README.md](../../node_modules/@earendil-works/pi-coding-agent/README.md:96)). CLI options allow tools, exclude-tools, no-builtin-tools, and no-tools, and the documented built-ins include `read`, `bash`, `edit`, `write`, `grep`, `find`, and `ls` ([README.md](../../node_modules/@earendil-works/pi-coding-agent/README.md:554)).

Pi's extension model can add custom tools, permission gates, path protection, MCP integration, sandbox execution, and UI components ([README.md](../../node_modules/@earendil-works/pi-coding-agent/README.md:349)). Its philosophy is intentionally minimal: no built-in MCP, no permission popups, no plan mode, and no background bash by default ([README.md](../../node_modules/@earendil-works/pi-coding-agent/README.md:474)).

The SDK exposes `createAgentSession()` and accepts `noTools`, `tools`, `excludeTools`, and `customTools` ([README.md](../../node_modules/@earendil-works/pi-coding-agent/README.md:438), [dist/core/sdk.d.ts](../../node_modules/@earendil-works/pi-coding-agent/dist/core/sdk.d.ts:29)). Runtime registry refresh merges built-ins, extension-registered tools, SDK custom tools, allowlists, and denylists into active tool definitions ([dist/core/agent-session.js](../../node_modules/@earendil-works/pi-coding-agent/dist/core/agent-session.js:1818)).

The nested `pi-agent-core` package documents a clear tool-call event sequence and configurable tool execution modes. Parallel is the default: preflight sequentially, execute allowed tools concurrently, emit completion events as calls finish, then emit tool-result messages in assistant source order. Per-tool `executionMode: "sequential"` forces a sequential batch ([pi-agent-core/README.md](../../node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-agent-core/README.md:76), [README.md](../../node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-agent-core/README.md:102)). The implementation matches that: it chooses sequential vs parallel, uses `Promise.all` for parallel execution, emits result messages in ordered source order, and wraps validation/hook errors as tool errors ([dist/agent-loop.js](../../node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-agent-core/dist/agent-loop.js:254), [agent-loop.js](../../node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-agent-core/dist/agent-loop.js:299), [agent-loop.js](../../node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-agent-core/dist/agent-loop.js:360)).

Odysseus's Pi bridge maps Odysseus tools into Pi `customTools`, sends tool calls over JSONL, and returns text content plus details back to Pi ([src/harness/bridges/pi_sdk_bridge.mjs](../../src/harness/bridges/pi_sdk_bridge.mjs:233), [pi_sdk_bridge.mjs](../../src/harness/bridges/pi_sdk_bridge.mjs:289)). This is a strong integration point, but it currently exposes Odysseus's legacy tool contract rather than a richer registry contract.

## Odysseus Current Shape

Odysseus defines a large monolithic `FUNCTION_TOOL_SCHEMAS` list in OpenAI Chat Completions function-tool shape. Core examples include `bash`, `python`, web search/fetch, file read/write/edit, grep/glob/ls, MCP management, `app_api`, and `trigger_research` ([src/tool_schemas.py](../../src/tool_schemas.py:33), [tool_schemas.py](../../src/tool_schemas.py:709), [tool_schemas.py](../../src/tool_schemas.py:970), [tool_schemas.py](../../src/tool_schemas.py:1006)).

Native function calls are converted back into `ToolBlock` objects for the legacy execution pipeline. The converter maps names, parses JSON arguments, fails closed for malformed email MCP calls, accepts `mcp__...` names, and converts structured args back into text content for legacy tools ([src/tool_schemas.py](../../src/tool_schemas.py:1292)).

Execution is centralized in `tool_execution.py`. File tools have path confinement and sensitive path denial ([src/tool_execution.py](../../src/tool_execution.py:43), [tool_execution.py](../../src/tool_execution.py:154)). Legacy tool names can route through MCP or direct fallback; comments note several mapped tools currently do not have live MCP servers and fall back to direct handlers ([src/tool_execution.py](../../src/tool_execution.py:331), [tool_execution.py](../../src/tool_execution.py:408)). The dispatcher enforces disabled tools, guide-only policy, admin/public gates, background bash markers, mapped MCP tools, direct code-navigation fallbacks, arbitrary `mcp__` calls, and dynamic handlers in one long branch chain ([src/tool_execution.py](../../src/tool_execution.py:570), [tool_execution.py](../../src/tool_execution.py:680), [tool_execution.py](../../src/tool_execution.py:715), [tool_execution.py](../../src/tool_execution.py:742), [tool_execution.py](../../src/tool_execution.py:926)). Results are formatted into Markdown/text for the next model round, with extra JSON capped at 8000 characters ([src/tool_execution.py](../../src/tool_execution.py:978)).

The agent loop supports both native tool calls and fenced/textual tools, but native calls are still normalized to `ToolBlock`. It prefers native calls when present, otherwise parses fenced/textual blocks with special gating for API models ([src/agent_loop.py](../../src/agent_loop.py:2210)). For native chat-completions style models, it appends an assistant message with `tool_calls` and subsequent `role: "tool"` messages; for fenced tools it wraps tool output as untrusted context ([src/agent_loop.py](../../src/agent_loop.py:2261)). API models get OpenAI-compatible tool schemas, while local models mostly use text/fenced channels and only sometimes MCP schemas ([src/agent_loop.py](../../src/agent_loop.py:3332)). Tool blocks are executed sequentially in a `for` loop ([src/agent_loop.py](../../src/agent_loop.py:4010)).

`llm_core.py` passes tools to Anthropic, Ollama, and OpenAI-compatible chat-completions payloads, and parses Anthropic/OpenAI-compatible streaming tool calls ([src/llm_core.py](../../src/llm_core.py:2191), [llm_core.py](../../src/llm_core.py:2389), [llm_core.py](../../src/llm_core.py:2496)). The ChatGPT subscription Responses path is different: `_build_chatgpt_responses_payload()` builds a Responses payload without `tools` ([src/llm_core.py](../../src/llm_core.py:1070), [llm_core.py](../../src/llm_core.py:2204)), and its streaming parser handles only output text, completion, usage, and errors ([src/llm_core.py](../../src/llm_core.py:2261)).

`chatgpt_subscription.py` confirms this is a separate ChatGPT/Codex backend provider and converts history into simple Responses input text items; `role: "tool"` is coerced to `role: "user"` ([src/chatgpt_subscription.py](../../src/chatgpt_subscription.py:1), [chatgpt_subscription.py](../../src/chatgpt_subscription.py:302)).

The harness SDK exposes Odysseus tools to external runtimes by converting OpenAI function schemas into `HarnessToolDefinition`, then executing through `execute_tool_block()` ([src/harness/sdk.py](../../src/harness/sdk.py:40), [sdk.py](../../src/harness/sdk.py:170)). The Pi bridge imports `@earendil-works/pi-coding-agent`, converts Odysseus tool definitions into Pi custom tools, sends JSONL `tool_call` requests back to Odysseus, and starts Pi with `customTools`, `noTools`, `tools`, and `excludeTools` ([src/harness/bridges/pi_sdk_bridge.mjs](../../src/harness/bridges/pi_sdk_bridge.mjs:27), [pi_sdk_bridge.mjs](../../src/harness/bridges/pi_sdk_bridge.mjs:233), [pi_sdk_bridge.mjs](../../src/harness/bridges/pi_sdk_bridge.mjs:323)).

## Gaps

### 1. Responses API support is partial and lossy

Odysseus uses Responses only for the ChatGPT subscription provider, but that path omits `tools` and flattens message history into text input items. It cannot preserve `function_call`, `function_call_output`, `custom_tool_call_output`, `mcp_call`, `computer_call_output`, local shell, shell, or apply_patch item relationships. This is the highest-priority gap because it blocks canonical OpenAI tools and forces all tool-capable behavior through Chat Completions emulation.

Recommended direction: introduce a Responses-native adapter that carries provider-neutral `ToolCallItem` and `ToolOutputItem` values through the loop, with `call_id` threading and `previous_response_id`/response item preservation where supported.

### 2. Tool specs and tool handlers are split by convention, not contract

Odysseus has schemas in `tool_schemas.py`, handler dispatch in `tool_execution.py`, prompt selection in `agent_loop.py`, harness conversion in `harness/sdk.py`, and provider serialization in `llm_core.py`. This makes it hard to add properties Codex/opencode/Pi treat as part of the tool definition: output schema, exposure, permission policy, parallel safety, truncation, model/provider compatibility, and result serialization.

Recommended direction: create a `ToolRegistry` where each tool registers one definition object: model-visible specs per provider, handler, permission metadata, concurrency mode, result serializer, UI metadata, and harness adapter metadata. Keep the existing schemas as an adapter output initially.

### 3. Model-facing tool results are formatted Markdown instead of typed outputs

`format_tool_result()` is useful for humans but lossy for models and incompatible with Responses item types. It also mixes UI display, truncation, structured data, and model input into one string. Codex and opencode keep typed outputs until the provider adapter decides how to serialize them.

Recommended direction: return structured `ToolResult` objects from handlers, with separate serializers for model context, UI events, logs, and persistence. Preserve raw JSON/content items for model-visible outputs where possible, and cap/truncate in the serializer.

### 4. Shell and patch are under-modeled

Odysseus has `bash`, `python`, and exact-string `edit_file`, but no dedicated `apply_patch` grammar/tool, no persistent exec session/write-stdin model, and no structured shell permission model. Codex has unified exec plus apply_patch as planned tools. opencode parses shell commands for permission/path scanning and treats apply_patch as a rich patch/diff/LSP workflow.

Recommended direction: add a dedicated `apply_patch` tool and split shell into at least `exec_command` plus optional `write_stdin`/session id semantics. Keep `bash` as compatibility sugar, but model the real execution primitive separately.

### 5. MCP strategy is mixed

Odysseus supports local MCP management and arbitrary `mcp__server__tool` dispatch, but it expands MCP into function-like names and sometimes injects descriptions in prompt context. OpenAI's canonical MCP surface is also a hosted `type: "mcp"` tool with `server_label`, `allowed_tools`, and approval fields. Codex supports both local MCP-derived specs and deferred loading/tool search.

Recommended direction: decide per provider whether MCP is executed by Odysseus as local brokered tools or delegated to OpenAI Responses as hosted remote MCP. Represent both as registry entries with explicit source, approval mode, read-only metadata, and exposure/deferred-loading policy.

### 6. Parallel execution is missing in the main loop

Odysseus receives batches of native tool calls but converts and executes them sequentially. Codex and Pi both support concurrent execution while preserving ordered transcript output. opencode's execution path also supports concurrent registry/tool preparation.

Recommended direction: annotate tools with `execution_mode`: `parallel`, `sequential`, or `exclusive`. Execute batches concurrently when all calls are parallel-safe, but serialize mutations and shell/session tools unless they explicitly opt in.

### 7. Permissions are mostly gates, not first-class requests

Odysseus has disabled tools, guide-only policy, admin/public blocks, path confinement, and plan-mode blocks. Those are necessary, but not the same as a first-class permission flow. opencode has pending permission objects, events, replies, "always" persistence, and blocking assertions. Codex exposes approval/sandbox policy through core execution and SDK config.

Recommended direction: add a permission request service that can block, ask the UI, persist "always" grants, reject/correct calls, and produce audit events. Use it for shell, patch/edit/write, external directories, MCP writes, browser/computer use, app API mutation, and harness control requests.

### 8. Hosted OpenAI built-ins are not first-class provider tools

Odysseus implements local `web_search`/`web_fetch`, local file tools, and internal `trigger_research`, but it does not expose OpenAI hosted `web_search`, `file_search`, `code_interpreter`, `image_generation`, `computer`, remote MCP, tool_search, shell, local_shell, or apply_patch through a provider-aware adapter. Some local tools should remain local, but the registry should know when a provider can run hosted tools more safely or efficiently.

Recommended direction: add provider tool-capability mapping. For each requested tool, choose local execution, hosted OpenAI execution, remote MCP delegation, or unavailable, with explicit fallback behavior.

### 9. Deep research is an internal task, not the OpenAI deep-research contract

`trigger_research` starts an Odysseus research task, but OpenAI deep research is a Responses flow using deep research models plus at least one data source: web search, remote MCP, or file search, optionally code interpreter. If Odysseus wants to support OpenAI deep research, that should be a provider mode/tool bundle, not only a local background task.

Recommended direction: keep `trigger_research` for Odysseus jobs, but add a separate deep-research provider adapter that supplies the required data-source tools and records citations/artifacts as typed outputs.

### 10. Harness integration is good but inherits legacy limits

The Pi bridge is already close to a modern external-harness contract: Odysseus tools become Pi `customTools`, Pi events become Odysseus harness events, and tool calls round-trip over JSONL. The limitation is that the exported tools come from `FUNCTION_TOOL_SCHEMAS` and execute through `ToolBlock`, so Pi cannot see richer metadata like permission mode, execution mode, output schema, or hosted/local selection.

Recommended direction: make the harness SDK consume the same future `ToolRegistry`, not the legacy schema list. Then expose `execution_mode`, permission metadata, output shape, labels, prompt snippets, and progress semantics to Pi and future harnesses.

## Prioritized Implementation Plan

1. Build a registry layer without changing behavior: wrap every current schema and handler in a `RegisteredTool` object and generate `FUNCTION_TOOL_SCHEMAS` from it.
2. Add typed `ToolCall` and `ToolResult` objects under the agent loop, while still supporting `ToolBlock` as a compatibility adapter.
3. Implement provider serializers: Chat Completions function tools, Anthropic tool use, local/fenced fallback, and true OpenAI Responses items.
4. Move `format_tool_result()` behind a model-output serializer and keep structured results for UI/persistence.
5. Add `execution_mode` and batch execution for parallel-safe read/search/fetch tools.
6. Add a permission request service and route shell/edit/write/app_api/MCP mutations through it.
7. Add dedicated `apply_patch` and structured shell/exec tools.
8. Add provider capability mapping for OpenAI hosted tools, remote MCP, computer use, and deep research.

## Non-Goals For This Note

This note does not propose production code changes and did not modify production code. It also does not evaluate tool prompt wording, UI rendering, or security policy in depth beyond what affects the architecture gaps above.
