# Decide `ody-term` Command Grammar And Mode Split

Status: closed
Type: grilling
Blocked by: Inventory HTTP/API Gaps For `ody-term` V1
Assignee: Codex

## Question

What should the v1 `ody-term` command grammar be, including the split between scriptable CLI commands and interactive TUI entry points, so humans and LLMs can discover and use chat, agent, harness, services, inspect, and run workflows consistently while `uv` remains the main project/task runner without forcing `ody-term`'s implementation technology?

## Resolution

Use a noun-first command grammar with scriptable CLI commands as the default command surface and `tui` as the canonical full-screen interactive entry point.

The canonical shape is:

```bash
ody-term [global-options] <domain> <verb> [command-options]
```

Global options should be accepted before or after the domain path for ergonomics, while help documents globals-first form. Global options include target selection (`--server`, `--profile`), output profile (`--for human|grug|clanker`), format refinements (`--format json|jsonl|raw|table|text`), display controls (`--no-color`, `--verbose`, `--quiet`), and non-interactive confirmation posture (`--yes`, `--yolo`).

The v1 top-level domains are:

- `auth`: login, status, logout, capabilities, and authenticated/admin-gated token management.
- `config`: Terminal Client-local configuration only, not Odysseus application settings.
- `server`: local bootstrap/runtime management for the Odysseus server before API-backed commands are available.
- `session`: durable Odysseus Session list, show, create, history, and export operations.
- `run`: active execution list, start, attach, status, stop, and resume-oriented operations.
- `harness`: Harness Session behavior and harness-specific controls.
- `service`: authenticated Odysseus service/process lifecycle, including main server observation, model serving, harness bridge processes, and supporting dependencies.
- `inspect`: cross-cutting event, log, raw transport, schema, and debug introspection.
- `tui`: canonical full-screen interactive entry point.

Do not make `chat` and `agent` top-level domains in the canonical model. They are Run kinds, expressed through `run start --kind chat` and `run start --kind agent`. Ergonomic aliases such as `ody-term chat ...` or `ody-term agent ...` may exist later, but only as spelling shortcuts over the Run model.

Harness is not a subset of Service in the command grammar. Harness Session commands belong under `harness`; harness bridge process lifecycle belongs under `service`.

Output uses three explicit profiles:

- `human`: readable terminal output for direct use.
- `grug`: ultra-compact terminal output for fast scanning and low-token use.
- `clanker`: stable machine-oriented output using documented structured formats such as JSON, JSONL, or raw transport output.

Select output profile with `--for human|grug|clanker`. Default to `human` when stdout is a TTY and to `clanker` when stdout is not a TTY. Format-specific options such as `--format jsonl` refine clanker output rather than replacing the profile model.

Aliases are allowed as documented secondary spelling shortcuts, including aliases that compress multiple canonical path parts. They must not change command semantics, output defaults, fields, exit codes, confirmation behavior, or capability requirements. Help should show canonical commands first and aliases second; machine-readable capabilities should expose alias metadata so agents can discover aliases while preferring canonical forms.

`--yes` answers ordinary confirmation prompts for the exact requested operation. `--yolo` is a global "yes to everything" posture for the command: it implies `--yes` and suppresses interactive confirmation prompts, including elevated friction, but it does not bypass auth, ownership, capability scopes, server-side policy, unavailable operations, or explicit target requirements. In `clanker`, commands that require confirmation should fail with structured confirmation-required output unless `--yes` or `--yolo` is supplied as appropriate.

Server/profile target resolution should be:

1. Explicit `--server`.
2. Named `--profile`.
3. `ODYSSEUS_URL`.
4. Terminal Client default profile/runtime state.
5. Localhost fallback only for human/TUI use.
6. Optional `--start` or `--ensure-server` to launch a background server if nothing is reachable.

`server` is a top-level local bootstrap domain distinct from API-backed `service`, because `server start --background` must work before the Odysseus API exists. It should delegate to existing `uv run ody launch ...` machinery rather than reimplementing launch logic.

Authentication should support interactive credential login, token-paste login, and browser/device flow where available. Interactive credential login prompts securely, stores only the resulting token, refuses or strongly warns for non-loopback plain HTTP, and should not prompt in `clanker` mode. Auth tokens should be stored in OS keychain/secret storage where available, with Terminal Client config storing token references and a permission-locked file fallback.

Minimum v1 canonical skeleton:

```bash
ody-term help [domain]
ody-term auth login|status|logout|capabilities
ody-term config show|profiles|set|unset
ody-term server start|status|stop|logs
ody-term session list|show|create|history|export
ody-term run list|start|attach|status|stop
ody-term harness list|show|command|open
ody-term service list|show|status|logs|stop|restart|kill
ody-term inspect events|logs|raw|capabilities
ody-term tui
```

Later tickets should refine event flags, lifecycle targets, auth scopes, session/run/harness flows, and local server/profile/config runtime state.
