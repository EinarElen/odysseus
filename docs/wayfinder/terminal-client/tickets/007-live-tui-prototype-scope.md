# Decide Live TUI Prototype Scope

Status: closed
Type: prototype
Blocked by: Decide `ody-term` Command Grammar And Mode Split, Specify Event Envelope And Renderers, Specify Session, Run, And Harness Flows
Assignee: Codex

## Question

What is the smallest throwaway TUI prototype that would clarify the live-control experience for watching chat, agent, harness, heartbeat, tool, log, and service events without committing to production implementation details?

## Prototype

- [Throwaway Live TUI Scope Prototype](../prototypes/live-tui-scope/prototype.py)
- [Prototype notes](../prototypes/live-tui-scope/NOTES.md)

## Resolution

Use a real TUI prototype to validate focused interaction modes rather than a single dense dashboard. The smallest useful v1 live-control prototype should be tab-like, with multiple focused views over the same Session, Run, Harness Session, and Event Envelope model.

The accepted prototype scope is:

- Live view: merged normalized Event Envelope timeline, selected event detail, control rail, and local control log.
- REPL view: command-shaped interaction for status, event tailing, filtering, run stop, harness steering, and service restart attempts.
- Browse view: tree-like navigation through chat/run structure, including turns, messages, tool calls, harness state, operations, and raw event envelopes.
- Inspect view: current visible model, capability posture, and envelope sample.

Mouse support should be treated as first-class for the TUI, not as a nice-to-have. Keyboard operation remains required for terminal and automation ergonomics, but the interactive TUI should assume users can select tabs, rows, tree nodes, and controls with the mouse where the terminal supports it.

The prototype can remain throwaway. Its durable decision is that `ody-term tui` should organize live control as focused, tab-like views with shared underlying event/session/run state, including a first-class REPL-like view and a first-class tree/navigation view.
