# Inventory HTTP/API Gaps For `ody-term` V1

Status: closed
Type: research
Blocked by: none
Assignee: Codex

## Question

Which existing Odysseus HTTP/API routes already support the `ody-term` v1 target surface, and what missing or unsuitable endpoints must be designed before an HTTP/API-first Terminal Client can cover chat sessions, agent runs, harness sessions, stream inspection, service/process lifecycle controls, and authenticated machine use?

## Resolution

Resolved in [HTTP/API Inventory For `ody-term` V1](../research/001-http-api-gaps.md).

Odysseus already has enough first-party HTTP surface to build an HTTP/API-first `ody-term` substrate around sessions, chat/agent streams, detached run resume/stop/status, harness-backed sessions, diagnostics, dev state, API tokens, and cookbook model-serving lifecycle. The gaps are not "no backend exists"; they are that the current backend is browser/UI-shaped and split across session, chat, harness, cookbook, dev, task, and Codex routes.

Next design work should specify a narrow terminal-client API layer over the existing routes: distinct run resources, stable versioned event envelopes, durable event/replay access, harness session inspection/control, unified service/process lifecycle operations, and Terminal Client-specific capability scopes.
