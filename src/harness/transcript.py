from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional


MAX_EVENTS = 200
MAX_DETAIL_CHARS = 4000
MAX_OUTPUT_CHARS = 12000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, *, limit: int = MAX_DETAIL_CHARS) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
    text = text.strip()
    if len(text) > limit:
        return text[:limit] + "\n..."
    return text


def _visible_detail(data: Dict[str, Any]) -> Dict[str, Any]:
    allowed = (
        "phase",
        "kind",
        "detail",
        "idle_seconds",
        "workspace",
        "request_id",
        "session_id",
        "event_type",
        "payload",
        "result",
    )
    return {
        key: data[key]
        for key in allowed
        if data.get(key) not in (None, "", [], {})
    }


def start_harness_run(
    *,
    harness_id: str,
    label: str,
    mode: str,
    workspace: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": harness_id,
        "label": label or harness_id,
        "mode": mode or "observe",
        "workspace": workspace or "",
        "session_id": session_id or "",
        "status": "running",
        "started_at": _now_iso(),
        "events": [],
    }


def finish_harness_run(run: Dict[str, Any], *, status: str, duration_seconds: float | None = None) -> Dict[str, Any]:
    run["status"] = status or "completed"
    run["completed_at"] = _now_iso()
    if duration_seconds is not None:
        run["duration_seconds"] = round(float(duration_seconds), 2)
    tool_count = sum(1 for event in run.get("events", []) if event.get("type") == "tool")
    failed_count = sum(1 for event in run.get("events", []) if event.get("status") in ("failed", "error"))
    if failed_count:
        run["summary"] = f"{tool_count} actions, {failed_count} failed"
    elif tool_count:
        run["summary"] = f"{tool_count} actions"
    else:
        run["summary"] = "No tool actions"
    return run


def update_harness_ref(run: Dict[str, Any], *, workspace: Optional[str] = None, session_id: Optional[str] = None) -> None:
    if workspace:
        run["workspace"] = workspace
    if session_id:
        run["session_id"] = session_id


def _append_event(run: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    events = run.setdefault("events", [])
    event.setdefault("timestamp", _now_iso())
    events.append(event)
    if len(events) > MAX_EVENTS:
        del events[: len(events) - MAX_EVENTS]
        run["truncated"] = True
    return event


def _find_tool(run: Dict[str, Any], tool_id: Any) -> Optional[Dict[str, Any]]:
    if not tool_id:
        return None
    for event in reversed(run.get("events", [])):
        if event.get("type") == "tool" and event.get("id") == tool_id:
            return event
    return None


def record_harness_status(run: Dict[str, Any], data: Dict[str, Any]) -> None:
    label = _clean_text(data.get("label") or "Harness activity", limit=160)
    _append_event(run, {
        "type": "status",
        "label": label,
        "status": _clean_text(data.get("status") or "running", limit=40),
        "phase": _clean_text(data.get("phase"), limit=80),
        "detail": _clean_text(data.get("detail"), limit=1000),
    })


def record_harness_event(run: Dict[str, Any], data: Dict[str, Any]) -> None:
    label = _clean_text(data.get("label") or data.get("event_type") or "Harness event", limit=160)
    _append_event(run, {
        "type": "event",
        "label": label,
        "status": _clean_text(data.get("status") or "done", limit=40),
        "detail": _visible_detail(data),
    })


def record_harness_tool_start(run: Dict[str, Any], data: Dict[str, Any]) -> None:
    tool_name = _clean_text(data.get("name") or "harness_tool", limit=120)
    _append_event(run, {
        "type": "tool",
        "id": data.get("id") or data.get("tool_call_id"),
        "label": tool_name,
        "tool": tool_name,
        "status": "running",
        "input": _clean_text(data.get("input") or data.get("arguments"), limit=1000),
    })


def record_harness_tool_update(run: Dict[str, Any], data: Dict[str, Any]) -> None:
    event = _find_tool(run, data.get("id") or data.get("tool_call_id"))
    if not event:
        return
    partial = data.get("partial")
    event["progress"] = _clean_text(partial, limit=MAX_DETAIL_CHARS)


def record_harness_tool_end(run: Dict[str, Any], data: Dict[str, Any]) -> None:
    event = _find_tool(run, data.get("id") or data.get("tool_call_id"))
    if not event:
        record_harness_tool_start(run, data)
        event = run["events"][-1]
    result = data.get("result")
    event["status"] = "failed" if data.get("is_error") else "completed"
    event["output"] = _clean_text(result, limit=MAX_OUTPUT_CHARS)
    if data.get("diff"):
        event["diff"] = data["diff"]
    event.pop("progress", None)


def record_harness_control(run: Dict[str, Any], data: Dict[str, Any], *, result: bool = False) -> None:
    kind = _clean_text(data.get("kind") or "control", limit=80)
    status = _clean_text(data.get("status") or ("completed" if result else "waiting"), limit=40)
    _append_event(run, {
        "type": "control",
        "id": data.get("id") or data.get("request_id"),
        "label": f"{kind.replace('_', ' ').title()}",
        "status": status,
        "blocking": bool(data.get("blocking")),
        "detail": _visible_detail(data),
    })
