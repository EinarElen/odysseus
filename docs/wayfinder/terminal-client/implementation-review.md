# Terminal Client Implementation Review

Date: 2026-07-09

## What Happened

The implementation run advanced through the map by treating command-contract
shape as equivalent to product behavior. Each ticket added CLI output and tests
around the local `ody-term` seam, then marked the ticket done when the visible
JSON/text fields matched the spec language.

That let four real requirements slip through:

- The spec required an HTTP/API-first Terminal Client layer, but `run` commands
  used client-local JSON state instead of starting, listing, attaching to, and
  stopping real Odysseus execution.
- The spec required Event Envelopes over Odysseus activity, but `inspect events`
  only normalized local server log lines.
- The spec required OS secret storage where available, but `auth login` wrote to
  the file fallback by default.
- The spec required a full-screen interactive TUI, but `ody-term tui` delivered
  a harnessable static state model plus text fallback.

## Why The Map Did Not Catch It

- Ticket checkboxes were phrased as user outcomes but accepted implementation
  notes as proof. For example, the TUI ticket was marked complete with a note
  that the state model could feed a future full-screen renderer.
- Tests were written at the CLI shape seam, but the seam was backed by local
  fixtures and monkeypatched state. They proved stable rendering, not real
  backend integration.
- The implementation followed the survey recommendation to start with command
  and renderer contracts, then failed to add a blocking checkpoint for the
  promised backend API layer.
- The tracker did not distinguish "scaffold exists" from "production behavior
  works", so later tickets inherited optimistic assumptions.

## Repairs Applied

- Moved fixed `ody-term` paths and default host/port values into
  `src/constants.py`.
- Added OS secret-store support for macOS Keychain refs, while preserving the
  visible permission-locked file fallback for development and unsupported
  systems.
- Made `--yolo` satisfy ordinary `--yes` confirmation gates, without bypassing
  capability checks.
- Replaced the large `execute()` dispatch cascade with domain/verb handlers and
  a registry table.
- Added regression tests for keychain-backed token storage and `--yolo`
  confirmation behavior.

## Remaining Required Repairs

1. Add a real terminal-client backend API layer with distinct Run identity over
   the existing session-keyed chat/agent substrate.
2. Wire `ody-term run start/list/status/attach/stop` to that API layer instead
   of local JSON run state.
3. Expose Event Envelopes from real chat, agent, harness, service, process,
   server, and system sources with cursor/reconnect behavior.
4. Replace the `ody-term tui` text/model fallback with an actual interactive
   terminal renderer. The existing `ody.tui.v1` model can remain the shared
   state seam.

