"""Terminal Client Run compatibility layer.

This module gives ody-term a distinct Run resource while the existing chat
streaming internals are still keyed by durable Odysseus Session id.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncGenerator

from src import agent_runs


RUN_ACTIVE_STATUSES = {"queued", "starting", "running", "waiting", "stopping"}


@dataclass
class TerminalRun:
    run_id: str
    session_id: str
    kind: str = "chat"
    status: str = "running"
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    message: str = ""


_RUNS: dict[str, TerminalRun] = {}
_SESSION_ACTIVE: dict[str, list[str]] = {}


def reset_for_tests() -> None:
    _RUNS.clear()
    _SESSION_ACTIVE.clear()


def _new_identity(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _session_run_ids(session_id: str) -> list[str]:
    return [rid for rid in _SESSION_ACTIVE.get(session_id, []) if rid in _RUNS]


def _sync_run_status(run: TerminalRun) -> None:
    live = agent_runs.get_status(run.session_id)
    if live in {"running", "done", "error", "stopped"}:
        run.status = live
    elif run.status == "running" and agent_runs.get_persisted_status(run.session_id):
        run.status = str(agent_runs.get_persisted_status(run.session_id))
    if run.status not in RUN_ACTIVE_STATUSES and run.finished_at is None:
        run.finished_at = _utc_now()
    run.updated_at = _utc_now()


def _parse_sse_event(raw: str) -> tuple[str, Any]:
    event_type = "message"
    data_lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
    data_text = "\n".join(data_lines)
    if data_text == "[DONE]":
        return "done", {"done": True}
    if not data_text:
        return event_type, {}
    try:
        return event_type, json.loads(data_text)
    except json.JSONDecodeError:
        return event_type, {"text": data_text}


def _event_kind(event_type: str, payload: Any) -> str:
    if event_type == "done":
        return "run.status"
    if event_type == "error":
        return "error"
    if isinstance(payload, dict):
        if payload.get("type"):
            return str(payload["type"])
        if payload.get("status"):
            return "run.status"
        if payload.get("token") or payload.get("content") or payload.get("text"):
            return "message.delta"
    return "message.delta"


def _event_level(event_type: str, payload: Any) -> str:
    if event_type == "error":
        return "error"
    if isinstance(payload, dict) and str(payload.get("level") or "").lower() in {"trace", "debug", "info", "warn", "error"}:
        return str(payload["level"]).lower()
    return "info"


def _summary(kind: str, payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("summary", "message", "text", "content", "status"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value[:160]
    return kind


async def _collect_events(run: TerminalRun, *, cursor: int | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seq = 0
    async for raw in agent_runs.subscribe(run.session_id):
        if not isinstance(raw, str) or raw.startswith(":"):
            continue
        seq += 1
        if cursor is not None and seq <= cursor:
            continue
        event_type, payload = _parse_sse_event(raw)
        kind = _event_kind(event_type, payload)
        events.append(
            {
                "schema": "ody.event.v1",
                "id": f"evt_{run.run_id}_{seq}",
                "seq": seq,
                "time": run.updated_at,
                "session_id": run.session_id,
                "run_id": run.run_id,
                "source": "chat",
                "kind": kind,
                "level": _event_level(event_type, payload),
                "summary": _summary(kind, payload),
                "payload": payload if isinstance(payload, dict) else {"value": payload},
                "raw": {"transport": "sse", "type": event_type, "body": raw},
            }
        )
    return events


def run_summary(run: TerminalRun, *, event_count: int | None = None) -> dict[str, Any]:
    _sync_run_status(run)
    count = event_count
    if count is None:
        count = agent_runs.buffered_event_count(run.session_id)
    last_activity = None
    if count:
        last_activity = {
            "time": run.updated_at,
            "kind": "run.status" if run.status != "running" else "message.delta",
            "level": "info",
            "summary": run.status,
        }
    return {
        "run_id": run.run_id,
        "session_id": run.session_id,
        "kind": run.kind,
        "status": run.status,
        "started_at": run.started_at,
        "updated_at": run.updated_at,
        "finished_at": run.finished_at,
        "events_available": count > 0,
        "replay_available": count > 0,
        "cursor_available": count > 0,
        "event_count": count,
        "last_activity": last_activity,
        "heartbeat": last_activity,
    }


def create_chat_run(
    *,
    session_id: str | None,
    message: str,
    stream: AsyncGenerator[str, None],
) -> dict[str, Any]:
    resolved_session_id = session_id or _new_identity("ses")
    run = TerminalRun(run_id=_new_identity("run"), session_id=resolved_session_id, message=message)
    _RUNS[run.run_id] = run
    _SESSION_ACTIVE.setdefault(resolved_session_id, []).append(run.run_id)
    agent_runs.start(resolved_session_id, stream)
    return {"run": run_summary(run), "cursor": {"after": None, "next": "0", "count": 0}}


def list_runs(*, kind: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    summaries = []
    for run in _RUNS.values():
        if kind and run.kind != kind:
            continue
        summary = run_summary(run)
        if status and summary["status"] != status:
            continue
        summaries.append(summary)
    summaries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return summaries


def resolve_run(*, run_id: str | None = None, session_id: str | None = None) -> TerminalRun:
    if run_id:
        run = _RUNS.get(run_id)
        if run is None:
            raise KeyError(run_id)
        return run
    if not session_id:
        raise KeyError("missing")
    runs = [_RUNS[rid] for rid in _session_run_ids(session_id)]
    for run in runs:
        _sync_run_status(run)
    active = [run for run in runs if run.status in RUN_ACTIVE_STATUSES]
    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        raise ValueError(active)
    if not runs:
        raise KeyError(session_id)
    runs.sort(key=lambda run: run.updated_at, reverse=True)
    return runs[0]


async def attach_run(*, run_id: str | None = None, session_id: str | None = None, cursor: int | None = None) -> dict[str, Any]:
    run = resolve_run(run_id=run_id, session_id=session_id)
    events = await _collect_events(run, cursor=cursor)
    _sync_run_status(run)
    next_cursor = str(events[-1]["seq"]) if events else (str(cursor) if cursor is not None else None)
    return {
        "run": run_summary(run, event_count=(cursor or 0) + len(events)),
        "events": events,
        "cursor": {"after": str(cursor) if cursor is not None else None, "next": next_cursor, "count": len(events)},
    }


async def stop_run(*, run_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    run = resolve_run(run_id=run_id, session_id=session_id)
    stopped = agent_runs.stop(run.session_id)
    if stopped:
        await asyncio.sleep(0)
    _sync_run_status(run)
    return {"run": run_summary(run), "stopped": stopped}


def make_single_event_stream(message: str) -> AsyncGenerator[str, None]:
    async def _stream() -> AsyncGenerator[str, None]:
        yield "event: status\n" f"data: {json.dumps({'status': 'running', 'message': message})}\n\n"
        yield "data: [DONE]\n\n"

    return _stream()
