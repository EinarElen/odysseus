# CodexBar and ccusage: usage-accounting study

Research date: 2026-07-11. Source snapshots: CodexBar `917a2165`; ccusage `997ad7f9`.

## Executive conclusion

CodexBar and ccusage solve two different problems that Odysseus should keep separate:

1. **Measured workload accounting**: tokens, cache tokens, model, estimated USD cost, session/project, and time. CodexBar and ccusage derive this from local agent JSONL. Odysseus already has a stronger first-party source for its own activity: its server-side usage ledger.
2. **Subscription-quota state**: the provider's authoritative 5-hour/session and weekly percentages and exact resets. CodexBar retrieves this from OpenAI's authenticated usage API (or Codex app-server fallback). This cannot be reconstructed reliably from tokens or USD.

The provider quota is account-wide. It does not identify which client consumed it. Therefore an exact “Odysseus vs external” split is possible only for Odysseus-measured facts; the external share must be explicitly labelled a **reconciled residual** between provider snapshots, not a measured client attribution.

## What CodexBar actually does

### Subscription windows

For Codex, the native app's automatic source order is OAuth first, falling back to Codex CLI app-server only for missing/invalid OAuth credentials. Its documented CLI RPC uses `account/read` and `account/rateLimits/read`; the optional browser dashboard is off by default. [Provider source policy](https://github.com/steipete/CodexBar/blob/917a2165229df89191d8fab41aedbfa0a1019f17/docs/providers.md#codex)

The OAuth implementation sends an authenticated `GET` to the ChatGPT backend usage endpoint, with `Authorization: Bearer …` and, when available, `ChatGPT-Account-Id`. The default path resolves to `/backend-api/wham/usage`; an alternate Codex API path is also supported. [OAuth request implementation](https://github.com/steipete/CodexBar/blob/917a2165229df89191d8fab41aedbfa0a1019f17/Sources/CodexBarCore/Providers/Codex/CodexOAuth/CodexOAuthUsageFetcher.swift#L340-L399)

The response is decoded as:

- `plan_type`;
- `rate_limit.primary_window` and `secondary_window`;
- for each window, `used_percent`, Unix `reset_at`, and `limit_window_seconds`;
- optional credits/balance and spend controls;
- `additional_rate_limits` for named/model-specific limits.

Malformed optional/additional windows are decoded lossily so they do not discard valid primary/weekly data. [Response model](https://github.com/steipete/CodexBar/blob/917a2165229df89191d8fab41aedbfa0a1019f17/Sources/CodexBarCore/Providers/Codex/CodexOAuth/CodexOAuthUsageFetcher.swift#L6-L237)

This is the key lesson: **CodexBar displays the server's percentage and reset timestamp; it does not infer subscription consumption from token counts.** It can also fetch reset-credit state separately from `/wham/rate-limit-reset-credits`. [Fetcher endpoints](https://github.com/steipete/CodexBar/blob/917a2165229df89191d8fab41aedbfa0a1019f17/Sources/CodexBarCore/Providers/Codex/CodexOAuth/CodexOAuthUsageFetcher.swift#L340-L344)

### Tokens and dollar-equivalent cost

CodexBar separately scans `CODEX_HOME`/`~/.codex/sessions` and `archived_sessions` JSONL for local cost history. [Provider documentation](https://github.com/steipete/CodexBar/blob/917a2165229df89191d8fab41aedbfa0a1019f17/docs/providers.md#L81-L91)

Its vendored scanner treats token-count events as cumulative snapshots and derives stable rows/deltas, tracks input, cached input, output and reasoning output, normalizes model identities, and caches scan state. Cost is a **retail API-price equivalent**, calculated per model from token categories; it is not proof of an amount billed under a subscription. The scanner preserves rows and stable turn IDs to avoid double counting copied/forked histories. [Codex cache/delta machinery](https://github.com/steipete/CodexBar/blob/917a2165229df89191d8fab41aedbfa0a1019f17/Sources/CodexBarCore/Vendored/CostUsage/CostUsageScanner%2BCacheHelpers.swift) [pricing implementation](https://github.com/steipete/CodexBar/blob/917a2165229df89191d8fab41aedbfa0a1019f17/Sources/CodexBarCore/Vendored/CostUsage/CostUsagePricing.swift)

CodexBar therefore intentionally presents quota windows and local dollar/token history as different datasets.

## What ccusage actually does

### Local-log accounting

ccusage is fundamentally a local log analyzer. Its Codex adapter finds session sources, reads session/headless JSONL, accepts `event_msg/token_count` records, follows current model context, and converts cumulative usage into events. [Codex parser](https://github.com/ryoppippi/ccusage/blob/997ad7f90189867d9f218aa0e7401586e3b9fde8/rust/crates/ccusage/src/adapter/codex/parser.rs) [loader](https://github.com/ryoppippi/ccusage/blob/997ad7f90189867d9f218aa0e7401586e3b9fde8/rust/crates/ccusage/src/adapter/codex/loader.rs)

It deduplicates events by timestamp, normalized model, and token fields across copied/branched session files. It aggregates in the selected timezone into daily, weekly, monthly, or session groups, with configurable week start. [Codex aggregation](https://github.com/ryoppippi/ccusage/blob/997ad7f90189867d9f218aa0e7401586e3b9fde8/rust/crates/ccusage/src/adapter/codex/aggregate.rs) [weekly behavior](https://github.com/ryoppippi/ccusage/blob/997ad7f90189867d9f218aa0e7401586e3b9fde8/docs/guide/weekly-reports.md)

For costs, ccusage supports three semantics: use reported `costUSD` where present, calculate from tokens, or automatic preference/fallback. Model pricing comes from its bundled/cached models.dev catalog with runtime overrides and offline support. This is again dollar-equivalent workload analysis, not subscription billing. [Pricing implementation](https://github.com/ryoppippi/ccusage/blob/997ad7f90189867d9f218aa0e7401586e3b9fde8/rust/crates/ccusage/src/pricing.rs)

### “Session blocks” are estimates, not authoritative quota

ccusage's block view groups activity into inferred windows. It floors the first event to the hour, starts a new block after the configured duration (default five hours), inserts visible gaps, sums token/cost categories, and regards a block as active when recent activity and current time are within the inferred end. [Block construction](https://github.com/ryoppippi/ccusage/blob/997ad7f90189867d9f218aa0e7401586e3b9fde8/rust/crates/ccusage/src/blocks.rs#L18-L126)

Burn rate is elapsed tokens/cost per minute/hour; projected totals extrapolate that rate through the inferred block end. A token “limit” is user supplied or the maximum historical block, not a provider quota. [Burn rate and projection](https://github.com/ryoppippi/ccusage/blob/997ad7f90189867d9f218aa0e7401586e3b9fde8/rust/crates/ccusage/src/blocks.rs#L535-L569) [block documentation](https://github.com/ryoppippi/ccusage/blob/997ad7f90189867d9f218aa0e7401586e3b9fde8/docs/guide/blocks-reports.md)

Thus ccusage's weekly report means calendar-week local token/cost aggregation. It is **not** the subscription's rolling weekly percent/reset. Its inferred five-hour blocks are useful workload analytics but must not be presented as equivalent to OpenAI's authoritative subscription windows.

## Odysseus vs external activity

There are three truth levels:

| Value | Truth source | Attribution quality |
|---|---|---|
| Odysseus tokens/cache/cost/runs | Odysseus server ledger | Exact for instrumented Odysseus calls |
| Account subscription percent/reset | OpenAI authenticated quota snapshot | Exact account total, no client identity |
| External consumption | Difference between successive account snapshots after accounting for Odysseus activity | Inferred residual only |

Do not subtract token percentages. OpenAI does not publish the conversion from heterogeneous requests/models/reasoning effort to subscription percentage, and the mapping can vary. The defensible algorithm is snapshot-based:

1. Persist every provider quota snapshot with account, window identity/duration, `used_percent`, `reset_at`, fetch source, timestamp, and raw payload/schema version.
2. Assign Odysseus runs to the interval between snapshots by actual provider completion time and credential/account identity.
3. Calculate `provider_delta_percent`, handling a changed `reset_at` as a new epoch and treating downward jumps without an epoch change as correction/uncertainty.
4. If the interval contains only Odysseus activity, it can calibrate an empirical Odysseus contribution. If external clients may run concurrently, attribution is underdetermined.
5. Show `account total`, `Odysseus measured workload`, and `unattributed/external residual` side by side. Never label the residual “external exact.” Include confidence and snapshot age.

A stronger operational technique is to fetch a quota snapshot immediately before and shortly after an Odysseus run/batch. That narrows the attribution interval, but cannot eliminate concurrent external use or provider reporting lag.

Local Codex JSONL can supplement the residual: if Odysseus launches Codex sessions with a dedicated `CODEX_HOME` or durable origin marker, those files can be classified exactly as Odysseus, while other known Codex homes are external. Files without a trustworthy marker remain `unknown`; path/process heuristics must not be called exact attribution.

## Recommended Odysseus design extension

Keep the existing usage ledger authoritative for workload facts and add a separate subscription domain:

- `subscription_accounts`: owner, provider, credential/account identity (hashed/redacted), plan, enabled source.
- `subscription_windows`: account, provider window key (`primary`, `secondary`, named additional limit), duration, reset epoch, semantics.
- `subscription_snapshots`: window, observed time, used/remaining percent, reset time, source (`oauth`, `app_server`, `web`), freshness, status, raw-version/hash.
- `subscription_attribution_intervals`: pair of snapshots, Odysseus run/token/cost totals, account delta, attribution state (`odysseus_only`, `mixed`, `external_only`, `unknown`), confidence and reason.

UI/CLI should expose two coordinated but visibly distinct views:

- **Subscription**: session/weekly percent remaining, exact reset countdown, pace vs elapsed time, snapshot freshness, and history/step chart.
- **Attribution**: account delta vs Odysseus activity per interval, with external/unattributed shaded as an estimate.
- **Workload**: the already implemented token/cache/USD/run dashboard, with calendar-week and inferred five-hour views inspired by ccusage.

Important implementation rules:

- Use the same credential/account identity as each Odysseus model endpoint; never merge different ChatGPT accounts.
- Store percentages as fixed-point integers (for example basis points), timestamps in UTC, and preserve provider reset timestamps rather than locally guessing them.
- Poll conservatively and on meaningful events; cache the last good snapshot and show staleness on auth/network failure.
- Make OAuth/API the preferred source and app-server a fallback, following CodexBar's approach. Browser scraping should be optional and lowest priority.
- Keep “USD equivalent” distinct from “subscription spend”; subscription plans do not imply per-call dollar charges.
- Incognito must produce neither workload records nor attribution linkage, while account-wide quota snapshots may still change because they describe provider state. This creates an intentionally unattributed residual.

## What not to copy

- Do not present ccusage's historical-maximum token block as a real subscription limit.
- Do not infer the provider's weekly reset from calendar-week boundaries.
- Do not combine local JSONL totals and Odysseus ledger totals without cross-source deduplication.
- Do not claim exact external-client attribution from an account-wide percentage.
- Do not make a scraped dashboard the only durable source; preserve source, freshness, and failure state for every snapshot.
