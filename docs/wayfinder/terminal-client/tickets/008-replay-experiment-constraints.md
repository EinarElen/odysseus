# Specify Replay And Experiment Constraints

Status: closed
Type: grilling
Blocked by: Specify Event Envelope And Renderers, Specify Session, Run, And Harness Flows
Assignee: Codex

## Question

What stable transcript, event, configuration, and result contracts must v1 preserve so later `ody-term` workflows can repeat prompts or interactions across fresh sessions many times and compare outcomes without redesigning the live-control surface?

## Resolution

This ticket was mis-scoped during charting. Replay and experiment workflows are a real motivation and design driver for `ody-term`, but v1 does not need specialized replay or experiment support as a separate Terminal Client feature area.

The intended capability is ordinary client composability: users and agents should be able to run repeated prompts, harness commands, inspections, and comparisons by scripting `ody-term` commands and consuming stable structured output. That motivation should keep pressure on useful primitives such as scriptable commands, Event Envelopes, clanker JSON/JSONL output, raw/debug inspection, run/session identity, event history/cursors where needed for attach and reconnect, and exports where already needed for live control and automation.

Future replay or experiment tooling can be built on top of those general primitives if it becomes valuable. It should not create a dedicated v1 command family, backend subsystem, or product workflow beyond keeping the client automatable and its structured outputs documented.
