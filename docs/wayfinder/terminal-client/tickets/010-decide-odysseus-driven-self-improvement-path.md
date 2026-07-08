# Decide Odysseus-Driven Self-Improvement Path

Status: closed
Type: grilling
Blocked by: Specify Event Envelope And Renderers, Specify Session, Run, And Harness Flows, Specify Auth, Capability, And Safety Contract, Specify Server, Profile, And Config Runtime State
Assignee: Codex

## Question

How should the v1 Terminal Client architecture leave a credible path toward Odysseus-driven self-improvement workflows without adding a special self-improvement product surface or implementing the production client during this wayfinder map?

## Resolution

Treat Odysseus-driven self-improvement as an integration pattern built from ordinary `ody-term` primitives, not as a dedicated v1 feature area.

The v1 architecture should preserve a path for self-improvement by making the Terminal Client:

- scriptable through stable `clanker` JSON/JSONL output
- inspectable through Event Envelopes, raw/debug modes, run status, and event cursors
- steerable through authenticated Run, Harness Session, and Lifecycle Target commands
- repeatable through ordinary shell/client composition rather than a special experiment subsystem
- explicit about auth mode, capability scopes, confirmation posture, and elevated friction
- able to target local or remote Odysseus instances through profiles and server/runtime resolution

The first useful self-improvement loop is external orchestration:

1. Use `ody-term server --ensure-server` and profile resolution to select or start a target Odysseus instance.
2. Use `ody-term run start`, `run attach`, `inspect events`, and `session history|export` to execute and observe a behavior under test.
3. Use `clanker` output and Event Envelopes as machine-readable evidence.
4. Use ordinary scripts, agents, or harnesses to compare outcomes and decide changes.
5. Use existing development tools outside `ody-term` to edit code.
6. Use `ody-term service`, `server`, and `run` commands to restart, re-run, inspect, and compare.

Do not add a v1 `self-improve`, `experiment`, or autonomous edit/apply command family. Those would blur the boundary between the Terminal Client as a reliable control/observation surface and higher-level automation policy. Future self-improvement tools can wrap `ody-term`, but they should depend on the same session/run/event/service/auth contracts as every other automation client.

The Terminal Client should also avoid any privileged bypass for self-improvement. An Odysseus-driven workflow must use explicit profiles, owner-attributed API tokens, Terminal Client capability scopes, and the same confirmation or `--yolo` rules as a human or external automation caller.

This satisfies the destination's "path toward Odysseus-driven self-improvement" requirement without expanding v1 beyond the live-control and scriptable automation architecture already decided.
