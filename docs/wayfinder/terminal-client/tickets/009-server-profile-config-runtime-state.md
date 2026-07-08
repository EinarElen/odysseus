# Specify Server, Profile, And Config Runtime State

Status: closed
Type: grilling
Blocked by: Decide `ody-term` Command Grammar And Mode Split
Assignee: Codex

## Question

How should `ody-term` manage Terminal Client-local server bootstrap, background server runtime state, profiles, config files, token references, repo paths, logs, pid/process ownership, target resolution, and `--start`/`--ensure-server` behavior while keeping this local state separate from Odysseus application settings?

## Resolution

Keep `ody-term` local state as a Terminal Client concern, split into durable config, secret storage, and ephemeral runtime state. Do not add profile/server bootstrap state to Odysseus application settings.

Durable Terminal Client config should live outside the repo by default under the user config directory, with an override for tests and portable development. It should contain only non-secret client state:

- schema version
- default profile name
- named profiles
- default output profile and format refinements
- optional repo paths per profile
- token references, not raw token values
- runtime-state pointer paths where needed

A profile is a named target, not a process record. A profile may include `url`, `repo`, `default_for`, TLS/plain-HTTP posture, output defaults, and `token_ref`. Profiles should not store pid, launch status, or mutable health facts.

Token values should use OS secret storage where available. Config stores references such as `keychain:ody-term/<profile>` or `file:<id>`. A permission-locked file fallback is acceptable for platforms or dev environments without keychain support, but `ody-term auth status` must make that storage mode visible. Inherited environment tokens are allowed for one command but should not be silently persisted.

Runtime state should be a separate ephemeral local record, preferably under the user state/cache directory rather than the config directory. Each locally bootstrapped server gets a runtime record containing at least:

- runtime id
- profile name when launched from a profile
- repo path
- launch method
- pid and process start evidence where available
- bind URL and resolved port
- log path
- environment-file path if used
- created/updated timestamps
- last health probe
- ownership evidence used before stop/restart

Treat runtime state as a hint plus safety evidence, not authority. `ody-term server stop` may stop only a process that `ody-term` launched or can strongly match by pid, start time/process identity, repo path, and bind URL. If that evidence is stale or ambiguous, it should fail with structured diagnostics rather than killing by port or command substring. Arbitrary PID/process mutation remains outside ordinary server bootstrap and belongs only behind the elevated lifecycle surface decided in [Specify Service And Process Lifecycle Surface](005-service-process-lifecycle-surface.md).

Server bootstrap should delegate to the existing Odysseus launch runner instead of reimplementing it. The implementation substrate is `uv run ody launch ...`, backed today by `src/odysseus_run_cli.py`, including launch method selection, macOS port handling, `.env` handling, uv/python options, Docker/native launch options, and dry-run command planning. `ody-term server start` should call that machinery or a shared launch-planning library extracted from it, then write the runtime record. It should not fork a second launch grammar.

Canonical local server commands:

```bash
ody-term server status
ody-term server start [--profile NAME] [--repo PATH] [--method auto|uv-dev|uv|launcher|macos|windows|docker|docker-dev|docker-gpu-nvidia|docker-gpu-amd] [--background] [--port N] [--host HOST]
ody-term server stop [--profile NAME|--runtime ID]
ody-term server logs [--profile NAME|--runtime ID]
ody-term server doctor
```

`server` commands are local bootstrap commands that can work before an authenticated Odysseus API is available. API-backed `service` commands may inspect or control managed Lifecycle Targets only after target resolution and auth/capability checks. The main server can appear in both surfaces, but with different authority: `server` uses local runtime evidence, while `service` uses authenticated Odysseus lifecycle APIs.

Target resolution should keep the order accepted by [Decide `ody-term` Command Grammar And Mode Split](002-command-grammar-and-mode-split.md), refined as:

1. Explicit `--server` URL.
2. Explicit `--profile` URL.
3. `ODYSSEUS_URL`.
4. Default profile URL.
5. Live runtime-state URL for the default or selected profile.
6. Localhost fallback only for human/TUI use.
7. `--start` or `--ensure-server` bootstrap when no reachable target exists.

`--start` means "start a local server if no target has been selected or reached." `--ensure-server` means "require a reachable target; if resolution fails and local bootstrap is possible, start one, then retry the original command." Neither flag should override an explicit remote `--server` unless the command says so. In `clanker` mode, failed target resolution should return structured diagnostics and not prompt. In human/TUI mode, it may offer an interactive start choice.

Config commands should operate only on Terminal Client config:

```bash
ody-term config show
ody-term config profiles
ody-term config profile add|set-default|remove
ody-term config set|unset
ody-term config paths
```

Do not expose Odysseus application settings under `ody-term config`; those remain API-backed product settings.

This keeps `ody-term` ergonomic for local development while preserving the prior boundaries: `uv` remains the project runner, profile/config state stays client-local, tokens stay owner-attributed Odysseus API tokens, and powerful service/process mutation stays capability-gated rather than becoming a side effect of local port discovery.
