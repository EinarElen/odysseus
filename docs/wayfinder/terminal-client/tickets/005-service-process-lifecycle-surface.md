# Specify Service And Process Lifecycle Surface

Status: closed
Type: grilling
Blocked by: Inventory HTTP/API Gaps For `ody-term` V1
Assignee: Codex

## Question

Which main-server, harness, model-serving, and supporting service/process targets belong in the v1 lifecycle surface, and for each one what should `ody-term` support as list, status, log, stop, restart, kill, or read-only inspection behavior?

## Resolution

Treat lifecycle as an Odysseus-managed target surface, not as a general host process manager.

The v1 lifecycle surface should expose named Lifecycle Targets:

- Main Odysseus server as local bootstrap/runtime target.
- Active and recent Runs.
- Harness bridge processes and harness-linked runtime state.
- Model-serving and cookbook tasks.
- MCP servers.
- Supporting service health checks, including ChromaDB, SearXNG, email, ntfy, provider endpoints, and similar dependency probes.

`ody-term service list` should return a unified inventory of Lifecycle Targets with `target_id`, `kind`, `name`, `status`, ownership/source metadata, last activity/heartbeat where available, and capability flags such as `can_logs`, `can_stop`, `can_restart`, and `can_kill`.

`ody-term service status <target>` should provide structured status for one target. It may delegate to existing diagnostics, dev, cookbook, MCP, run, or harness APIs internally, but the Terminal Client contract should normalize status values and expose source-native details under payload/raw fields rather than leaking current route shapes.

`ody-term service logs <target>` should be broadly available for managed targets where logs exist. Logs should render as Event Envelopes with source/kind/level metadata. Raw log output remains available through explicit raw/debug modes.

`stop` and `restart` should be available only for known managed targets with clear ownership and bounded semantics:

- Main server: `server stop|restart` remains the local bootstrap/runtime command; API-backed `service` may inspect the running server but should not depend on the server being alive to stop itself unless a local runtime handle exists.
- Runs: canonical stop belongs under `run stop`; service inventory may link to Run lifecycle but should not create a second competing mutation model.
- Harness bridge: stop/restart only when Odysseus owns the bridge process or the adapter exposes a safe lifecycle command.
- Model-serving/cookbook task: stop/restart using cookbook task/session identity, not arbitrary PID matching.
- MCP server: reconnect/restart/disable only through MCP server identity and existing ownership/config checks.
- Supporting service health checks: read-only in v1 unless Odysseus clearly owns the process.

Raw PID kill and arbitrary host process mutation should not be ordinary `service` commands. They may exist behind an explicit elevated-friction path for exceptional debugging, requiring strong target specificity, capability checks, and confirmation posture such as `--yolo` for forceful operations. The current admin `kill-pid` capability is evidence that this need exists, not a model for the everyday lifecycle contract.

The lifecycle API should make safety visible in the response. For each target/action it should state whether the action is available, unavailable, requires confirmation, requires stronger capability, or is intentionally unsupported because the process is outside Odysseus ownership.

This decision keeps `ody-term` powerful for Odysseus development/debugging while avoiding a vague terminal process manager that duplicates shell access and weakens the auth/capability model.
