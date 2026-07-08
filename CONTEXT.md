# Odysseus

Odysseus is a self-hosted AI workspace for chat, agent workflows, research, documents, notes, email, calendar, and local model operations.

## Language

**Terminal Client**:
The non-graphical Odysseus client surface covering both interactive terminal use and scriptable command use.
_Avoid_: non-graphical client, CLI-only client

**TUI**:
The full-screen or interactive terminal mode of the Terminal Client.
_Avoid_: terminal UI when referring to the broader Terminal Client

**CLI**:
The command-oriented automation mode of the Terminal Client, intended for scripting, repeatable runs, and structured output.
_Avoid_: using CLI as the umbrella term for both CLI and TUI behavior

**ody-term**:
The preferred command name and binary entrypoint for the Terminal Client.
_Avoid_: odysseus term, odysseusctl

**Run**:
One active execution within or against an Odysseus Session, such as a chat completion, agent loop, harness turn, repeated scripted invocation, or service operation.
_Avoid_: using session for both durable workspace identity and active execution

**Harness Session**:
The external runtime session/control surface linked to an Odysseus Session, such as a Pi session that can be opened, resumed, branched, steered, or inspected through harness-specific controls.
_Avoid_: treating harness session behavior as service lifecycle merely because a harness bridge has process state

**Service**:
An operational process or infrastructure target managed or inspected by Odysseus, such as the main server, model-serving process, harness bridge process, or supporting dependency.
_Avoid_: using service for durable conversational/session concepts

**Lifecycle Target**:
A named Odysseus-managed thing that the Terminal Client can inspect or control, such as the main server, a Run, a harness bridge, a model-serving task, an MCP server, or a supporting service health check.
_Avoid_: treating arbitrary host processes as ordinary Lifecycle Targets

**human output**:
The default Terminal Client output profile for direct human terminal use, with readable tables, concise text, and TTY-aware formatting.
_Avoid_: making human output the stable automation contract

**grug output**:
The ultra-compact Terminal Client output profile for fast scanning and low-token use.
_Avoid_: treating grug output as a schema-stable machine interface

**clanker output**:
The stable machine-oriented Terminal Client output profile, using documented structured formats such as JSON, JSONL, or raw transport output where appropriate.
_Avoid_: decorative formatting or undocumented prose in clanker output

**Event Envelope**:
The normalized Terminal Client event wrapper consumed by default renderers for routing, filtering, rendering, and replay, while preserving source-native details in its payload.
_Avoid_: exposing raw SSE, harness, or log bodies as the default Terminal Client event contract

**Terminal Client config**:
Configuration owned by `ody-term` itself, such as profiles, default output profile, server URLs, repo paths, runtime-state pointers, and token references.
_Avoid_: using Terminal Client config for Odysseus application settings

**Terminal Client profile**:
A named local `ody-term` target configuration that resolves to an Odysseus base URL plus optional repo path, default output posture, and credential/token reference.
_Avoid_: storing mutable process facts or Odysseus application settings in a profile

**Terminal Client runtime state**:
Ephemeral local `ody-term` state about a bootstrapped server process, such as pid, bind URL, launch method, log path, repo path, and health/ownership evidence.
_Avoid_: treating runtime state as a durable profile or as authoritative proof that an arbitrary process is safe to control

**Terminal Client server**:
The local Odysseus server process as bootstrapped, discovered, or stopped by `ody-term` before authenticated API-backed commands are available.
_Avoid_: conflating Terminal Client server bootstrap with Odysseus API-backed service lifecycle

**Terminal Client capability scope**:
An Odysseus API-token scope that authorizes a specific Terminal Client resource/action pair, such as reading Runs, following Event Envelopes, controlling harness state, or mutating Lifecycle Targets.
_Avoid_: tying authorization to command spellings or creating a separate Terminal Client credential model when owner-attributed Odysseus API tokens can carry the capability

**Odysseus-driven self-improvement workflow**:
A future workflow where Odysseus uses `ody-term` as an inspectable and scriptable control surface to run, observe, compare, and iterate on its own behavior through normal Terminal Client primitives.
_Avoid_: treating self-improvement as a special v1 command family or as permission to bypass Terminal Client auth, event, run, and service contracts
