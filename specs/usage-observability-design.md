# Usage Observability: Design

Status: implemented except for the intentionally deferred `ody-term usage` domain

Scope: persistent model/cache/cost/activity observability, web dashboard, and Terminal Client interface

Non-goal: implementation in this document

Implementation note (2026-07): the persistent ledger, owner-scoped query API,
migration/backfill jobs, retention/deletion, web dashboard suite, workload
instrumentation, and local maintenance CLI (`scripts/usage.py`) are complete.

## 1. Outcome

Odysseus should maintain an authoritative, server-side account of resource use while the application runs. A user must be able to answer:

- What used tokens, time, and money?
- Which session, Run, turn, model invocation, agent round, tool, or background task caused it?
- How much input was fresh, read from cache, or written to cache?
- Which model and provider actually served the request, including fallbacks?
- Which activities made a Run slow or expensive?
- How did usage change over time, and where are anomalies?

The browser dashboard and `ody-term usage` commands are projections over the same database. Browser local storage is not an accounting source.

## 2. Design principles

1. **Append facts; derive totals.** Raw observations are immutable. Session totals, charts, and costs are projections that can be rebuilt.
2. **Preserve provider truth.** Store raw provider usage fields and normalized fields. Never force unlike cache semantics into a lossy common number.
3. **Attribute without pretending.** Direct causality is stored explicitly. Shared prompt overhead is not arbitrarily assigned to tools.
4. **One observability seam.** Chat, agents, research, compare, document AI, scheduled tasks, email AI, image generation, harnesses, and Terminal Client Runs record through one module.
5. **Owner isolation by construction.** Every root Run is owner-scoped; all descendants inherit that owner.
6. **Queries, not stored counters, are authoritative.** Existing session token columns remain compatibility projections during migration.
7. **Local-first operation.** SQLite remains supported. Query shapes and indexes must also be suitable for a future PostgreSQL adapter.

## 3. Deep module and seam

Create a `usage_observability` module. Callers learn only this interface:

```python
run = usage.begin_run(RunContext(...))
span = run.begin_span(SpanContext(...))
span.record_usage(UsageObservation(...))
span.finish(SpanOutcome(...))
run.finish(RunOutcome(...))

usage.query(UsageQuery(...)) -> UsageReport
usage.export(UsageQuery(...), format="jsonl|csv") -> iterator[bytes]
```

The implementation owns IDs, timestamps, inheritance, normalization, transactions, provider-specific fields, cost resolution, rollups, retention, and redaction. Callers must not write ORM rows or update totals directly.

There are two real adapters at the persistence seam:

- SQL adapter for production.
- In-memory adapter for tests.

HTTP routes, dashboard code, and Terminal Client code are read adapters over `usage.query`; none contain accounting logic.

## 4. Identity and attribution hierarchy

```text
owner
└── run                         durable execution: chat, agent, research, task, email, compare...
    ├── span: turn              user-visible request/response
    │   ├── span: preparation   prompt build, retrieval, context trim
    │   ├── span: model         one provider request / fallback attempt
    │   ├── span: tool          one tool execution
    │   ├── span: model         next agent round
    │   └── span: verification  teacher/verifier/escalation call
    └── usage observations      tokens/cache/cost attached to the responsible model span
```

`run_id` is the observability root. It should reuse the canonical Odysseus Run identity when one exists. It may reference a durable chat `session_id`, message, scheduled task, comparison, research session, harness session, or document, but none of those substitutes for `run_id`.

Spans form a tree with `parent_span_id`. A span represents an activity and supplies timing and causality. A usage observation represents metered resources reported for that activity. Keeping these separate avoids stuffing tool timing, model tokens, images, and provider payloads into one sparse table.

## 5. Database schema

All timestamps are UTC. IDs are application-generated UUID-like strings. Monetary values use integer micros of the configured currency; token counts use integers.

### 5.1 `usage_runs`

One row per execution root.

| Column | Type | Notes |
|---|---|---|
| `id` | string PK | Canonical Run ID |
| `owner` | string, indexed | Required after legacy migration |
| `kind` | string, indexed | `chat`, `agent`, `research`, `compare`, `document`, `email`, `task`, `image`, `embedding`, `harness`, `other` |
| `status` | string, indexed | `running`, `succeeded`, `failed`, `cancelled`, `interrupted` |
| `source_surface` | string | `web`, `terminal`, `scheduler`, `api`, `internal` |
| `session_id` | string nullable, indexed | Durable Odysseus Session |
| `task_id` | string nullable, indexed | Scheduled/background task |
| `research_session_id` | string nullable | Optional domain correlation |
| `comparison_id` | string nullable | Optional domain correlation |
| `harness_session_id` | string nullable | Optional harness correlation |
| `started_at` | datetime, indexed | |
| `finished_at` | datetime nullable | |
| `duration_ms` | integer nullable | Finalized duration |
| `error_code` | string nullable | Stable category, not exception text |
| `attributes_json` | JSON | Small, indexed-independent dimensions only |
| `created_at` | datetime | |

Do not store prompts, model replies, tool outputs, API keys, headers, or exception stacks here.

### 5.2 `usage_spans`

One row per timed activity.

| Column | Type | Notes |
|---|---|---|
| `id` | string PK | |
| `run_id` | FK, indexed | Delete with Run only through retention |
| `parent_span_id` | self-FK nullable, indexed | Activity tree |
| `owner` | string, indexed | Denormalized deliberately for safe/query-efficient scoping |
| `kind` | string, indexed | `turn`, `model`, `tool`, `retrieval`, `prompt_build`, `context_trim`, `compaction`, `verification`, `fallback`, `other` |
| `name` | string | Stable operation name, e.g. `model.generate`, `tool.web_search` |
| `status` | string | Same terminal vocabulary as Runs |
| `sequence` | integer | Stable ordering within Run |
| `agent_round` | integer nullable | Explicit agent round |
| `started_at` | datetime, indexed | |
| `finished_at` | datetime nullable | |
| `duration_ms` | integer nullable | |
| `provider` | string nullable, indexed | Serving provider |
| `endpoint_id` | string nullable | Prefer endpoint ID over sensitive URL |
| `requested_model` | string nullable, indexed | |
| `actual_model` | string nullable, indexed | Records fallback/routing truth |
| `tool_name` | string nullable, indexed | Qualified tool name |
| `outcome_code` | string nullable | Stable outcome category |
| `attributes_json` | JSON | Sanitized dimensions and provider request ID if safe |

Activity content is referenced, not copied. For example, a tool span may carry a message metadata event ID, document ID, or search ID, but not its command output.

### 5.3 `usage_observations`

One row per provider usage report or local estimate. Multiple observations may belong to a model span when a provider emits incremental and final reports; `is_final` distinguishes them. Only final observations contribute to ordinary totals unless explicitly requested.

| Column | Type | Notes |
|---|---|---|
| `id` | string PK | |
| `run_id` | FK, indexed | Denormalized query key |
| `span_id` | FK, indexed | Normally a `model` span |
| `owner` | string, indexed | Owner scope |
| `observed_at` | datetime, indexed | |
| `sequence` | integer | Stable provider-event order |
| `is_final` | boolean, indexed | Authoritative observation for the span |
| `source` | string | `provider`, `backend`, `estimated`, `reconciled` |
| `input_tokens` | integer | Total provider input tokens under its semantics |
| `output_tokens` | integer | |
| `reasoning_tokens` | integer nullable | When separately reported |
| `cache_read_tokens` | integer nullable | Tokens served from prompt cache |
| `cache_write_tokens` | integer nullable | Tokens written/created in prompt cache |
| `fresh_input_tokens` | integer nullable | Stored explicitly only when provider supplies or semantics permit exact derivation |
| `audio_input_tokens` | integer nullable | Future-safe multimodal accounting |
| `audio_output_tokens` | integer nullable | |
| `image_input_units` | integer nullable | Provider-defined normalized count |
| `image_output_units` | integer nullable | |
| `request_count` | integer | Usually 1 |
| `currency` | string nullable | ISO code, initially `USD` |
| `input_cost_micros` | integer nullable | Resolved component |
| `output_cost_micros` | integer nullable | |
| `cache_read_cost_micros` | integer nullable | |
| `cache_write_cost_micros` | integer nullable | |
| `other_cost_micros` | integer nullable | Images, searches, provider fees, etc. |
| `total_cost_micros` | integer nullable | Sum of known components |
| `cost_source` | string nullable | `provider`, `price_catalog`, `manual`, `unknown` |
| `price_snapshot_id` | FK nullable | Exact pricing rules used |
| `raw_usage_json` | JSON nullable | Allowlisted provider usage fields only |

Invariant: unknown is `NULL`, never zero. Zero means the provider explicitly reported none or exact derivation produced zero.

### 5.4 `usage_price_snapshots`

Pricing must be reproducible instead of depending on the current frontend table.

| Column | Type | Notes |
|---|---|---|
| `id` | string PK | |
| `provider` | string, indexed | |
| `model_pattern` | string | Explicit match rule |
| `currency` | string | |
| `input_per_million_micros` | integer nullable | |
| `output_per_million_micros` | integer nullable | |
| `cache_read_per_million_micros` | integer nullable | |
| `cache_write_per_million_micros` | integer nullable | |
| `reasoning_per_million_micros` | integer nullable | If separately priced |
| `unit_prices_json` | JSON | Image/search/other unit prices |
| `effective_from` | datetime, indexed | |
| `effective_to` | datetime nullable | |
| `source_url` | string nullable | Human audit trail |
| `created_at` | datetime | |

Cost is calculated at ingestion using the effective snapshot and stored on the observation. Later catalog edits do not rewrite history. A separate explicit reconciliation job may append a `reconciled` observation/version when desired.

### 5.5 `usage_daily_rollups`

Optional derived table for long-range dashboard speed. Grain:

`owner + UTC date + kind + provider + actual_model + source_surface`

It stores final observation totals, Run/span counts, failures, duration percentiles or sufficient histogram data, cache totals, and costs. Raw facts remain authoritative. Rollups are rebuildable and never accepted from clients.

### 5.6 Indexes

Minimum composite indexes:

- Runs: `(owner, started_at)`, `(owner, kind, started_at)`, `(owner, session_id, started_at)`.
- Spans: `(owner, run_id, sequence)`, `(owner, kind, started_at)`, `(owner, actual_model, started_at)`, `(owner, tool_name, started_at)`.
- Observations: `(owner, observed_at)`, `(owner, run_id)`, `(span_id, is_final)`, `(owner, is_final, observed_at)`.
- Rollups: unique grain index and `(owner, date)`.

## 6. Cache semantics

Provider adapters map usage into normalized fields but retain an allowlisted raw object. Important rules:

- Anthropic `cache_read_input_tokens` maps to `cache_read_tokens`; `cache_creation_input_tokens` maps to `cache_write_tokens`.
- OpenAI-compatible cached prompt details map to `cache_read_tokens` only when the response explicitly reports them.
- A provider's `input_tokens` may include or exclude cached tokens. Store its documented total unchanged and set a semantic marker in `raw_usage_json`/adapter version.
- `fresh_input_tokens` is calculated only where the provider contract makes that exact. It otherwise remains null.
- Application response-cache hits are spans with `kind=cache` and an explicit `cache_layer=response`; avoided provider tokens are not invented. They can be reported separately as estimates if a future estimator supplies them.

Dashboard cache rate defaults to:

`cache_read_tokens / (cache_read_tokens + fresh_input_tokens)`

and displays “unavailable” when exact fresh input is unknown.

## 7. Attribution model

### Exact attribution

- Provider token/cache/cost observations attach to the exact model span.
- Model spans attach to the turn, agent round, verifier, fallback, or background activity that initiated them.
- Tool duration and outcome attach to the tool span.
- Retrieval/prompt construction/compaction durations attach to their own spans.

### Derived contribution

The query layer may calculate deltas between consecutive agent model calls:

- `context_growth_tokens = current_input_tokens - previous_input_tokens`
- Label the delta with intervening tool/retrieval spans.
- Present it as “context growth after these activities,” not “tokens caused by tool X.”

When exactly one activity occurred between model calls, the UI may show a high-confidence association. With multiple activities it shows a grouped association. Negative deltas indicate trimming/compaction and are displayed as such.

### No false allocation

System prompts, tool schemas, conversation history, and reused context are shared overhead. The first version must not split them across tools using arbitrary percentages.

## 8. Ingestion lifecycle

1. Begin Run before processing user/background input.
2. Begin a turn or operation span.
3. Create preparation/retrieval/tool/model child spans as activities occur.
4. Provider adapter emits a normalized usage observation, including cache detail and safe raw fields.
5. Finalize spans and Run even on cancellation/error through `finally` paths.
6. Commit observations independently enough that a process crash does not erase already reported provider usage.
7. Asynchronously update rollups; query raw facts for recent live data.

For streaming, partial observations may power live UI but do not enter ordinary totals. A provider's final usage replaces neither history nor earlier rows; it is a new final fact. A uniqueness rule permits at most one active final observation per `(span_id, reconciliation_version)`.

## 9. Query interface and HTTP routes

All queries require owner scope server-side. There is no administrative cross-owner mode: administrators see only their own usage through this interface.

Suggested routes:

| Route | Purpose |
|---|---|
| `GET /api/usage/summary` | KPI totals and comparisons for a time/filter range |
| `GET /api/usage/timeseries` | Bucketed tokens, cache, cost, Runs, errors, duration |
| `GET /api/usage/breakdown` | Group by model/provider/kind/surface/session/tool/status |
| `GET /api/usage/runs` | Paginated Run table |
| `GET /api/usage/runs/{run_id}` | Run waterfall, spans, observations, links |
| `GET /api/usage/live` | SSE stream of sanitized Run/span/usage updates |
| `GET /api/usage/anomalies` | Deterministic anomaly results |
| `GET /api/usage/export` | Streaming CSV or JSONL |
| `GET /api/usage/schema` | Dimensions, measures, enums, capability metadata |

Common filters: `from`, `to`, `timezone`, `session_id`, `run_id`, `kind`, `surface`, `provider`, `model`, `tool`, `status`, `usage_source`, `cache_status`, and `group_by`.

Time buckets are computed in the requested display timezone but stored in UTC. Responses include `generated_at`, filter echo, currency, completeness flags, and whether values are exact, estimated, or mixed.

## 10. Dashboard suite

Add a first-class **Usage** workspace, not a modal under chat. Every chart supports hover details, click-to-filter, brush/zoom where meaningful, keyboard navigation, accessible tabular fallback, permalinkable filters, and CSV/JSONL export.

### 10.1 Overview

- KPI cards: input, output, cache read/write, exact cache hit rate, estimated/provider cost, Runs, failures, median and p95 duration.
- Stacked time series: fresh input, cache read, cache write, output.
- Cost time series split by provider/model.
- “Top contributors” ranked bars for models, sessions, Run kinds, tools, and surfaces.
- Exact/estimated/mixed quality badge on every aggregate.

### 10.2 Runs explorer

- Filterable virtualized table: time, Run, session, source, status, model route, rounds, tools, tokens, cache, duration, cost.
- Row expansion opens the Run detail view.
- Saved views are local UI preferences; accounting facts remain server-side.

### 10.3 Run detail

- Waterfall/Gantt of nested spans.
- Model-call cards with requested/actual model, input/output/cache tokens, cost, TTFT, generation speed, provider request identifier, and exact/estimated state.
- Agent-round lane interleaving model, retrieval, tool, compaction, and verification spans.
- Context-growth chart between model rounds with attribution-confidence labels.
- Links back to chat message, research report, comparison, task, document, or harness session.
- Sanitized error categories; no prompt/tool output leakage.

### 10.4 Cache laboratory

- Cache reads/writes/fresh input over time.
- Cache rate and monetary savings where prices and semantics are exact.
- Breakdown by provider/model and prompt workload kind.
- Cold-start versus warm-call comparison.
- “Unknown semantics” bucket rather than silently treating missing fields as zero.

### 10.5 Cost and capacity

- Spend by day/provider/model/workload.
- Cost per successful Run and per output token.
- Context utilization distribution and near-limit Runs.
- Throughput, TTFT, generation speed, and p95 latency.
- Optional user-configured budgets and local alerts are a later layer, not part of accounting correctness.

### 10.6 Activity contribution

- Sankey or hierarchical flow only when filtered to a manageable scope: Run kind → model/provider → activity → tokens/cost.
- Tool table: invocations, failure rate, total duration, median duration, context growth after tool use.
- Retrieval/compaction table: executions, duration, tokens added/removed where measurable.

Do not use pie charts for high-cardinality dimensions. Default to line, stacked area, ranked bar, histogram, heatmap, and waterfall views.

### Frontend implementation choice

Use **Apache ECharts** behind a small Usage-specific chart adapter. Load it lazily only when the Usage workspace opens so the normal chat/application path does not pay its bundle and initialization cost. ECharts supplies the required line, stacked-area, ranked-bar, histogram, heatmap, custom waterfall, and Sankey views; Canvas/SVG rendering; data zoom/brush; responsive resize; export; and accessibility support in one library.

The adapter owns theme mapping, resize lifecycle, filter events, tooltip formatting, exact/estimated quality indicators, empty states, and destruction. Individual views receive chart-ready series from the Usage query client rather than importing ECharts directly. Every visualization retains an accessible HTML table equivalent. Backend query contracts remain independent of ECharts.

## 11. Terminal Client interface

Add `usage` as a top-level Terminal Client domain and `usage:read` / `usage:export` capabilities.

```text
ody-term usage summary [--from 24h] [filters]
ody-term usage top <models|providers|sessions|tools|runs> [--by tokens|cost|duration]
ody-term usage timeline [--bucket hour|day|week] [--metric tokens|cost|cache|runs|latency]
ody-term usage runs [filters]
ody-term usage show <run-id>
ody-term usage cache [filters]
ody-term usage live [filters]
ody-term usage export --format jsonl|csv [filters]
```

Output behavior:

- `human`: aligned KPIs/tables and compact Unicode sparklines; Run detail renders an indented span timeline.
- `grug`: one-line totals and terse ranked rows.
- `clanker`: versioned JSON/JSONL with stable field names and nulls preserved.
- `usage live`: NDJSON event envelopes, suitable for pipes; no terminal decoration in clanker output.

Example stable JSON envelope:

```json
{
  "schema_version": 1,
  "query": {"from": "...", "to": "..."},
  "quality": "mixed",
  "currency": "USD",
  "totals": {
    "input_tokens": 1200,
    "output_tokens": 340,
    "cache_read_tokens": null,
    "total_cost_micros": 4200
  }
}
```

## 12. Privacy, security, and retention

- Usage tables contain metadata, not content.
- Allowlist raw provider usage keys; never persist headers, credentials, prompts, responses, tool arguments, tool outputs, file contents, or raw exception text.
- Owner predicates are mandatory inside the query module, not left to route callers.
- Run links are resolved only after ownership checks on both resources.
- Raw spans and observations are retained indefinitely by default. Configurable retention may be added later, but must remain opt-in. Rollups may outlive raw rows if retention is enabled.
- Deletion modes: delete one Run, delete by time range, delete all owner usage. Session deletion should default to retaining accounting with `session_id` nulled; offer an explicit “delete linked usage” option.
- Incognito Runs record no usage records of any kind. They do not create Runs, spans, observations, rollups, or anonymous aggregates. In-memory metrics may still drive the current message footer while the incognito response is open, but they must not enter persistent accounting or browser storage.
- Exports include metadata only and are always restricted to the effective owner.

## 13. Migration and compatibility

1. Add tables and the usage module without changing existing UI behavior.
2. Instrument direct chat and agent-loop model calls first.
3. Dual-write authoritative observations and legacy session totals temporarily.
4. Backfill historical assistant metadata into synthetic Runs/observations with `source=estimated|legacy` and no invented cache data.
5. Change `/usage`, message stats, and session totals to query projections.
6. Instrument research, compare, scheduled tasks, email/document AI, images, embeddings, harnesses, and other model call sites.
7. Add dashboard and Terminal Client surfaces.
8. After reconciliation tests, stop writing legacy session counters; retain read compatibility for one release.

Historical browser-local costs should not be imported automatically: they lack trustworthy pricing provenance and may double-count history reload behavior. They may be shown once as “legacy browser estimate” during transition.

## 14. Verification requirements

- Provider adapter contract tests for OpenAI-compatible, Anthropic, Ollama/local, Codex subscription, missing usage, cache usage, fallbacks, and malformed fields.
- Database invariant tests: owner inheritance, null-versus-zero, final observation uniqueness, cancellation finalization, idempotent event replay, cascading retention.
- Query golden tests: totals equal raw facts, filters compose, timezone buckets are correct across DST, estimates are labeled, owner isolation holds.
- Cost tests pin exact price snapshots and cache rates; catalog updates never rewrite old costs.
- Crash tests preserve already committed provider observations.
- Dashboard tests verify filters and chart/table equivalence.
- CLI contract tests cover human, grug, JSON, JSONL, null fields, pagination, live events, and capability denial.
- Migration reconciliation: projected session totals equal legacy totals for a controlled fixture before cutover.

## 15. Decisions and open questions

### Recommended decisions

- Use Runs + spans + observations, not a single usage table.
- Store cost components and price snapshot provenance, not only a floating-point total.
- Keep raw facts indefinitely by default; any future retention policy is explicit opt-in configuration.
- Treat missing cache data as unknown.
- Build one server query interface shared by web and Terminal Client.
- Make content capture explicitly out of scope.

### Settled product decisions

1. Incognito produces no persistent or aggregate usage records.
2. Session deletion retains accounting facts and nulls the deleted `session_id`; usage is removed only through an explicit usage-deletion action.
3. Pricing and UI use USD. The schema retains a currency field for data clarity, but the first implementation rejects or marks unsupported non-USD price entries rather than performing conversion.
4. There is no cross-owner reporting, including for administrators. Every query and export is restricted to the effective owner.
5. The dashboard uses Apache ECharts through a lazy-loaded Usage-specific adapter with accessible table equivalents.

## 16. Suggested implementation slices

1. **Accounting foundation:** models, migrations, SQL/in-memory adapters, query types, provider normalization, price snapshots.
2. **Chat and agent instrumentation:** Run/span lifecycle, per-round model observations, cache capture, fallback attempts, legacy projections.
3. **Read interface:** summary/timeseries/breakdown/Run-detail routes, export, ownership and query tests.
4. **Terminal Client:** `usage` domain, capabilities, human/grug/clanker renderers, live stream.
5. **Dashboard foundation:** Usage workspace, filters, overview, Runs explorer, accessible tables.
6. **Deep exploration:** waterfall, cache laboratory, cost/capacity, activity contribution.
7. **Coverage expansion:** research, compare, scheduler, documents, email, images, embeddings, harnesses.
8. **Migration cutover:** historical backfill, reconciliation, legacy counter deprecation, browser-cost retirement.

Slices 1–3 establish correctness before visualization. Slice 7 should be delivered workload by workload rather than as one broad change.
