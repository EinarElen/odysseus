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
One active execution within or against an Odysseus Session, such as a chat completion, agent loop, harness turn, experiment repetition, or service operation.
_Avoid_: using session for both durable workspace identity and active execution

**Harness Session**:
The external runtime session/control surface linked to an Odysseus Session, such as a Pi session that can be opened, resumed, branched, steered, or inspected through harness-specific controls.
_Avoid_: treating harness session behavior as service lifecycle merely because a harness bridge has process state

**Service**:
An operational process or infrastructure target managed or inspected by Odysseus, such as the main server, model-serving process, harness bridge process, or supporting dependency.
_Avoid_: using service for durable conversational/session concepts

**human output**:
The default Terminal Client output profile for direct human terminal use, with readable tables, concise text, and TTY-aware formatting.
_Avoid_: making human output the stable automation contract

**grug output**:
The ultra-compact Terminal Client output profile for fast scanning and low-token use.
_Avoid_: treating grug output as a schema-stable machine interface

**clanker output**:
The stable machine-oriented Terminal Client output profile, using documented structured formats such as JSON, JSONL, or raw transport output where appropriate.
_Avoid_: decorative formatting or undocumented prose in clanker output
