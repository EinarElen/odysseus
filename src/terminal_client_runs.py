"""Terminal Client Run compatibility layer.

This module gives ody-term a distinct Run resource while the existing chat
streaming internals are still keyed by durable Odysseus Session id.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncGenerator

from core.atomic_io import atomic_write_json
from src import agent_runs
from src.constants import TERMINAL_CLIENT_RUNS_FILE


RUN_ACTIVE_STATUSES = {"queued", "starting", "running", "waiting", "stopping"}
STOP_STATUS_WAIT_ATTEMPTS = 20
STOP_STATUS_POLL_INTERVAL_S = 0.05
DEFAULT_EVENT_QUERY_LIMIT = 80
MAX_EVENT_QUERY_LIMIT = 500
_TERMINAL_RUN_STORE = Path(TERMINAL_CLIENT_RUNS_FILE)
logger = logging.getLogger(__name__)


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
    events: list[dict[str, Any]] = field(default_factory=list)


_RUNS: dict[str, TerminalRun] = {}
_SESSION_ACTIVE: dict[str, list[str]] = {}
_LIVE_RUN_BY_SESSION: dict[str, str] = {}
_LOADED = False


def reset_for_tests(*, clear_persisted: bool = False) -> None:
    global _LOADED
    _RUNS.clear()
    _SESSION_ACTIVE.clear()
    _LIVE_RUN_BY_SESSION.clear()
    _LOADED = True
    if clear_persisted:
        try:
            _TERMINAL_RUN_STORE.unlink(missing_ok=True)
        except Exception:
            logger.debug("[terminal-client-run] test store cleanup failed", exc_info=True)


def _new_identity(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _session_run_ids(session_id: str) -> list[str]:
    return [rid for rid in _SESSION_ACTIVE.get(session_id, []) if rid in _RUNS]


def _run_from_record(record: Any) -> TerminalRun | None:
    if not isinstance(record, dict):
        return None
    run_id = record.get("run_id")
    session_id = record.get("session_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    return TerminalRun(
        run_id=run_id,
        session_id=session_id,
        kind=str(record.get("kind") or "chat"),
        status=str(record.get("status") or "running"),
        started_at=str(record.get("started_at") or _utc_now()),
        updated_at=str(record.get("updated_at") or _utc_now()),
        finished_at=str(record["finished_at"]) if isinstance(record.get("finished_at"), str) else None,
        message=str(record.get("message") or ""),
        events=[event for event in record.get("events", []) if isinstance(event, dict)]
        if isinstance(record.get("events"), list)
        else [],
    )


def _load_persisted_runs() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        data = json.loads(_TERMINAL_RUN_STORE.read_text(encoding="utf-8")) if _TERMINAL_RUN_STORE.exists() else {}
    except Exception:
        logger.debug("[terminal-client-run] state load failed", exc_info=True)
        return
    raw_runs = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(raw_runs, list):
        return
    for raw in raw_runs:
        run = _run_from_record(raw)
        if run is None:
            continue
        _RUNS.setdefault(run.run_id, run)
        _SESSION_ACTIVE.setdefault(run.session_id, [])
        if run.run_id not in _SESSION_ACTIVE[run.session_id]:
            _SESSION_ACTIVE[run.session_id].append(run.run_id)


def _save_persisted_runs() -> None:
    try:
        _TERMINAL_RUN_STORE.parent.mkdir(parents=True, exist_ok=True)
        runs = sorted(_RUNS.values(), key=lambda run: run.updated_at, reverse=True)
        atomic_write_json(str(_TERMINAL_RUN_STORE), {"version": 1, "runs": [asdict(run) for run in runs]}, indent=2)
    except Exception:
        logger.debug("[terminal-client-run] state save failed", exc_info=True)


def _sync_run_status(run: TerminalRun) -> None:
    if _LIVE_RUN_BY_SESSION.get(run.session_id) != run.run_id:
        if run.status in RUN_ACTIVE_STATUSES:
            run.status = "interrupted"
            run.finished_at = run.finished_at or _utc_now()
            run.updated_at = _utc_now()
            _save_persisted_runs()
        return
    live = agent_runs.get_status(run.session_id)
    persisted = agent_runs.get_persisted_status(run.session_id)
    original = (run.status, run.finished_at)
    if live in {"running", "done", "error", "stopped"}:
        run.status = live
    elif run.status in RUN_ACTIVE_STATUSES and persisted:
        run.status = str(persisted)
    elif run.status in RUN_ACTIVE_STATUSES:
        run.status = "interrupted"
    if run.status not in RUN_ACTIVE_STATUSES and run.finished_at is None:
        run.finished_at = _utc_now()
    if (run.status, run.finished_at) != original:
        run.updated_at = _utc_now()
        _save_persisted_runs()


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


def _event_envelope(run: TerminalRun, *, seq: int, raw: str) -> dict[str, Any]:
    event_type, payload = _parse_sse_event(raw)
    kind = _event_kind(event_type, payload)
    return {
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


def _persist_raw_event(run: TerminalRun, seq: int, raw: str) -> None:
    event = _event_envelope(run, seq=seq, raw=raw)
    event_type = str(event["raw"]["type"])
    if event_type == "done":
        run.status = "done"
        run.finished_at = _utc_now()
    elif event_type == "error":
        run.status = "error"
        run.finished_at = _utc_now()
    run.updated_at = _utc_now()
    _remember_events(run, [event])


async def _collect_events(run: TerminalRun, *, cursor: int | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seq = 0
    async for raw in agent_runs.subscribe(run.session_id):
        if not isinstance(raw, str) or raw.startswith(":"):
            continue
        seq += 1
        if cursor is not None and seq <= cursor:
            continue
        events.append(_event_envelope(run, seq=seq, raw=raw))
    return events


def _stored_events_after(run: TerminalRun, *, cursor: int | None = None) -> list[dict[str, Any]]:
    return [event for event in run.events if cursor is None or int(event.get("seq", 0)) > cursor]


def _remember_events(run: TerminalRun, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    seen = {int(event.get("seq", 0)) for event in run.events}
    added = False
    for event in events:
        seq = int(event.get("seq", 0))
        if seq in seen:
            continue
        run.events.append(event)
        seen.add(seq)
        added = True
    if added:
        run.events.sort(key=lambda event: int(event.get("seq", 0)))
        _save_persisted_runs()


def run_summary(run: TerminalRun, *, event_count: int | None = None) -> dict[str, Any]:
    _sync_run_status(run)
    count = event_count
    if count is None:
        buffered_count = (
            agent_runs.buffered_event_count(run.session_id)
            if _LIVE_RUN_BY_SESSION.get(run.session_id) == run.run_id
            else 0
        )
        count = max(buffered_count, len(run.events))
    last_activity = None
    if count:
        last_event = run.events[-1] if run.events else None
        last_activity = {
            "time": str(last_event.get("time") if last_event else run.updated_at),
            "kind": str(last_event.get("kind") if last_event else "run.status"),
            "level": str(last_event.get("level") if last_event else "info"),
            "summary": str(last_event.get("summary") if last_event else run.status),
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
    _load_persisted_runs()
    resolved_session_id = session_id or _new_identity("ses")
    run = TerminalRun(run_id=_new_identity("run"), session_id=resolved_session_id, message=message)
    _RUNS[run.run_id] = run
    _SESSION_ACTIVE.setdefault(resolved_session_id, []).append(run.run_id)
    _LIVE_RUN_BY_SESSION[resolved_session_id] = run.run_id
    _save_persisted_runs()
    agent_runs.start(
        resolved_session_id,
        stream,
        on_event=lambda seq, raw: _persist_raw_event(run, seq, raw),
    )
    return {"run": run_summary(run), "cursor": {"after": None, "next": "0", "count": 0}}


def list_runs(*, kind: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    _load_persisted_runs()
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
    _load_persisted_runs()
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
    if _LIVE_RUN_BY_SESSION.get(run.session_id) == run.run_id:
        events = await _collect_events(run, cursor=cursor)
        if events:
            _remember_events(run, events)
        else:
            events = _stored_events_after(run, cursor=cursor)
    else:
        events = _stored_events_after(run, cursor=cursor)
    _sync_run_status(run)
    next_cursor = str(events[-1]["seq"]) if events else (str(cursor) if cursor is not None else None)
    return {
        "run": run_summary(run),
        "events": events,
        "cursor": {"after": str(cursor) if cursor is not None else None, "next": next_cursor, "count": len(events)},
    }


async def query_events(
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    cursor: int | None = None,
    source: str | None = None,
    kind: str | None = None,
    level: str | None = None,
    limit: int = DEFAULT_EVENT_QUERY_LIMIT,
) -> dict[str, Any]:
    """Query a bounded Run snapshot without waiting for live execution to end."""
    run = resolve_run(run_id=run_id, session_id=session_id)
    bounded_limit = min(max(limit, 0), MAX_EVENT_QUERY_LIMIT)
    scanned = _stored_events_after(run, cursor=cursor)[:bounded_limit]
    filters = {"source": source, "kind": kind, "level": level}
    events = [
        event
        for event in scanned
        if all(expected is None or event.get(field) == expected for field, expected in filters.items())
    ]
    _sync_run_status(run)
    next_cursor = str(scanned[-1]["seq"]) if scanned else (str(cursor) if cursor is not None else None)
    return {
        "run": run_summary(run),
        "events": events,
        "cursor": {"after": str(cursor) if cursor is not None else None, "next": next_cursor, "count": len(events)},
        "filters": filters,
    }


async def stop_run(*, run_id: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    run = resolve_run(run_id=run_id, session_id=session_id)
    stopped = _LIVE_RUN_BY_SESSION.get(run.session_id) == run.run_id and agent_runs.stop(run.session_id)
    if stopped:
        for _ in range(STOP_STATUS_WAIT_ATTEMPTS):
            await asyncio.sleep(STOP_STATUS_POLL_INTERVAL_S)
            _sync_run_status(run)
            if run.status not in RUN_ACTIVE_STATUSES:
                break
    _sync_run_status(run)
    return {"run": run_summary(run), "stopped": stopped}


def make_single_event_stream(message: str) -> AsyncGenerator[str, None]:
    async def _stream() -> AsyncGenerator[str, None]:
        yield "event: status\n" f"data: {json.dumps({'status': 'running', 'message': message})}\n\n"
        yield "data: [DONE]\n\n"

    return _stream()
