# Terminal Client Technology Surveys

## Odysseus Internal Survey

### CLI, Launch, And Packaging

- `pyproject.toml` already exposes Python console scripts through Hatch/`uv`; `src/odysseus_run_cli.py` is the main local task runner and is dependency-light.
- `src/odysseus_run_cli.py` centralizes launch selection, port choice, dry-run behavior, `uv` invocation, Docker/native launch variants, and check commands. `ody-term server start` should delegate to `uv run ody launch select` instead of growing a second launch grammar.
- Existing script-style CLIs under `scripts/` are pragmatic, but they are product-area specific. `ody-term` should not copy those command shapes into the Terminal Client contract.
- Recommendation: ship `ody-term` as another Python console script and keep the public command seam testable through `main(argv, stdout, stderr)`.

### Config, Runtime State, And Secret Storage

- Application settings live in Odysseus data/config files such as settings and integrations; Terminal Client profiles must stay separate from those app settings.
- `core/atomic_io.py` documents the project preference for atomic JSON config writes. The initial Terminal Client config can follow the same temp-and-replace posture and should later move to the shared helper if the module joins the wider app package.
- Existing account integrations encrypt service credentials with `src.secret_storage` where appropriate. Terminal Client profiles should store credential references only; raw tokens belong in OS secret storage or a clearly marked fallback selected by the auth ticket.
- Runtime state should be ephemeral evidence for local server processes: PID, process group, repo path, command, URL, log path, and timestamps. It must not become a profile or general host process registry.
- Recommendation: use client-local JSON config/runtime files now, add keyring/file-fallback token storage in the auth ticket, and keep mutable process facts out of profiles.

### HTTP, Streaming, Events, And Routes

- Existing HTTP routes are broad but browser-shaped. Chat, session, agent, harness, service-health, task, and admin routes are enough substrate, but the terminal should introduce a narrow API contract instead of binding automation to current UI route names.
- Streaming exists in the app through chat/monitoring flows and frontend consumers; the Terminal Client should normalize those into Event Envelopes before exposing JSONL.
- `src/service_health.py` already models bounded, secret-free service diagnostics. Lifecycle target work should reuse that posture for status metadata and failure language.
- Recommendation: start with renderer and command contracts, then build a small terminal-client API layer that adapts existing routes into Session, Run, Event Envelope, Capability, and Lifecycle Target resources.

### Auth, Tokens, And Safety

- `core/middleware.py` distinguishes configured auth, disabled auth, and internal loopback/tool tokens. Terminal Client auth status must make bypass and auth-disabled modes visible rather than treating them as success without context.
- Existing admin/token management surfaces are owner-attributed. Terminal Client scopes should extend that model with resource/action permissions, not command-spelling permissions.
- Dangerous operations should combine auth/capability checks with local confirmation posture. Confirmation flags are not authorization and should never bypass server policy.
- Recommendation: defer token storage and capability resolution to the auth ticket, but make the command spine carry `--yes` and `--yolo` from the beginning.

### Harness, Runs, Sessions, And Lifecycle

- Existing harness code under `src/harness/` and related tests is useful prior art, but harness-native identity is distinct from durable Odysseus Session identity.
- Current chat/session internals are still transition substrate; the Terminal Client should present a Run compatibility layer rather than exposing session-keyed execution as the public model.
- Managed lifecycle should cover Odysseus-owned targets only: local server, runs, harness bridge/runtime, MCP, model-serving/cookbook tasks, and service health.
- Recommendation: command spine first, then profiles/server bootstrap, then auth/capabilities, then Event Envelope and Run compatibility. Do not attempt broad calendar/email/gallery parity in v1.

### Relevant Test Prior Art

- `tests/test_odysseus_run_cli.py` tests console-script metadata and behavior through subprocess/direct command seams.
- The test standard prefers deterministic, behavior-first assertions and isolated env/CWD/module state.
- Route/auth/harness tests provide later patterns for API contract tests, but the command spine should remain lightweight and direct.
- Recommendation: keep early `ody-term` tests at the command/output seam; add route-contract tests only when the terminal API layer exists.

## External Technology Survey

| Area | Python Standard Library | Python With Typer/Click/Textual | Node | Rust |
|---|---|---|---|---|
| CLI grammar | Enough for noun-first parsing and global options; no dependency | Better help/completion once command count grows | Strong CLI ecosystem, second runtime | Strong CLI ecosystem, new build toolchain |
| JSON/JSONL output | Straightforward and testable | Same | Straightforward | Strong |
| HTTP streaming | Needs `urllib`/third-party client for comfort later | `httpx`/async clients fit well | Native fetch/streams are good | Excellent with reqwest, more setup |
| Mouse-capable TUI | Not realistic alone | Textual is the strongest Python option | Blessed/Ink possible, less aligned with backend | Ratatui is strong, but larger stack shift |
| Secret storage | Needs dependency for OS keyring | `keyring` fits Python auth ticket | OS keychain packages vary | OS keyring crates viable |
| Packaging with `uv` | Already native to this repo | Same | Requires Node package path | Requires Rust build/distribution path |
| Test fit | Matches existing pytest style | Matches existing pytest style | Adds Node test seam | Adds Rust test seam |

### Recommendation

- Use Python for v1 because it reuses the existing `uv`/Hatch console-script path, launch code, tests, and backend-adjacent helpers.
- Use the standard library for the command spine. It is sufficient for global options, output profiles, JSON/JSONL, and stable command registration.
- Consider Textual at the TUI MVP ticket because that is the first point where a dependency materially improves the product.
- Consider `keyring` at the auth ticket because token storage needs OS integration; keep file fallback explicit and visible.
- Use a Python HTTP client in the event/API tickets after the exact streaming contract is pinned. Do not choose an HTTP dependency in the command-spine ticket.

### Rejected Rabbit Holes For V1

- Rewriting `ody-term` in Rust before the API contract is proven.
- A Node TUI stack just because terminal UI libraries exist; it would split implementation from the Python backend and runner.
- Exposing browser-shaped SSE or raw route payloads as the automation contract.
- Preserving awkward internal session/run shapes for compatibility when the Terminal Client spec deliberately introduces a cleaner Run model.
- Avoiding dependencies categorically; add Textual/keyring/http client dependencies when their ticket needs them and tests justify them.
