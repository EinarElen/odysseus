# Throwaway Live TUI Scope Prototype

Question: what is the smallest throwaway TUI prototype that clarifies the v1 live-control experience without committing to production implementation details?

Run:

```bash
uv run --with textual python docs/wayfinder/terminal-client/prototypes/live-tui-scope/prototype.py
```

This is intentionally not production `ody-term` code. It is a Textual app for reviewing live-control scope: tab-like focused views, pane layout within each view, focus behavior, event priority, compact event wording, filters, command affordances, REPL interaction, tree-like chat navigation, and visible normalized event data.

Language/library choice: Python plus Textual. Python matches the existing Odysseus runner/backend ecosystem, and Textual is a serious TUI framework with panes, keyboard bindings, reactive state, tables, logs, and rich rendering. The prototype still avoids adding a production dependency by using `uv run --with textual`.

## Proposed Scope

The smallest useful prototype should simulate one active Run with linked Session and Harness Session identity, one normalized Event Envelope stream, and multiple focused views rather than a single one-size-fits-all pane layout:

- Live view: merged timeline, selected event detail, control rail, and local control log.
- REPL view: command-shaped interaction for status, event tailing, filtering, run stop, harness steering, and service restart attempts.
- Browse view: tree-like navigation through a chat/run structure, including turns, messages, tool calls, harness state, operations, and raw event envelopes.
- Inspect view: current model, capability posture, and envelope sample.

It should include sample chat, agent, harness, heartbeat, tool, log, service, error, and control-result events. It should not connect to Odysseus, persist state, implement auth, manage real processes, or choose the final TUI library.

Navigation sketch: number keys switch tabs when focus is not inside the REPL input. F1-F4 switch Live, REPL, Browse, and Inspect globally, including while the REPL input has focus.

## Review Prompts

- Does tab-like navigation between Live, REPL, Browse, and Inspect match the way `ody-term tui` should organize focused workflows?
- Is the REPL-like view important enough to be a first-class v1 TUI tab, or should it stay as a CLI-only affordance?
- Should the Browse tree be a first-class v1 tab for navigating chat/run structure, or should tree navigation live inside the Live/Inspect views?
- Are heartbeats and service events visible enough without overwhelming the Run timeline?
- Should harness commands appear as first-class controls in Live and REPL, or behind a focused Harness tab?
- Does the Event Envelope carry enough information for the TUI to avoid source-specific subscriptions?

## User Reaction So Far

The prototype must be substantial enough to judge, should use a proper TUI library and sound implementation language, and should explore multiple tab-like views rather than a single four-pane layout. One tab should be REPL-like. REPL focus breaking navigation is not a blocker for the wayfinder decision, but the prototype should sketch tree-like navigation through different chat elements.

## Pending Verdict

Open until reviewed. After reaction, record the actual decision in ticket 007 and delete or absorb this prototype.
