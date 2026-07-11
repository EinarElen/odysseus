"""Owner-scoped HTTP read interface for the usage ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.auth_helpers import effective_user, require_user
from src.usage_observability import usage_store


def _owner(request: Request) -> str:
    require_user(request)
    return effective_user(request) or "local"


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    compact = value.strip().lower()
    units = {"h": "hours", "d": "days", "w": "weeks"}
    if compact[-1:] in units and compact[:-1].isdigit():
        return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**{units[compact[-1]]: int(compact[:-1])})
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid timestamp: {value}") from exc


def setup_usage_routes() -> APIRouter:
    router = APIRouter(prefix="/api/usage", tags=["usage"])

    @router.get("/summary")
    def summary(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None):
        return usage_store.query_summary(owner=_owner(request), start=_time(from_), end=_time(to))

    @router.get("/timeseries")
    def timeseries(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None, bucket: str = "day"):
        try:
            return usage_store.query_timeseries(owner=_owner(request), start=_time(from_), end=_time(to), bucket=bucket)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/breakdown")
    def breakdown(request: Request, group_by: str = "model", from_: str | None = Query(None, alias="from"), to: str | None = None):
        try:
            return usage_store.query_breakdown(owner=_owner(request), group_by=group_by, start=_time(from_), end=_time(to))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/runs")
    def runs(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None, limit: int = 100, offset: int = 0):
        return usage_store.list_runs(owner=_owner(request), start=_time(from_), end=_time(to), limit=limit, offset=max(offset, 0))

    @router.get("/runs/{run_id}")
    def run_detail(request: Request, run_id: str):
        result = usage_store.get_run(owner=_owner(request), run_id=run_id)
        if result is None:
            raise HTTPException(404, "Usage Run not found")
        return result

    @router.get("/export")
    def export(request: Request, format: str = "jsonl", from_: str | None = Query(None, alias="from"), to: str | None = None):
        try:
            body = usage_store.export(owner=_owner(request), format=format, start=_time(from_), end=_time(to))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        media = "application/x-ndjson" if format == "jsonl" else "text/csv"
        return StreamingResponse(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="odysseus-usage.{format}"'})

    @router.get("/schema")
    def schema(request: Request):
        _owner(request)
        return {
            "schema_version": 1,
            "currency": "USD",
            "dimensions": ["model", "provider", "kind", "surface", "session", "tool"],
            "measures": ["input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens", "cache_write_tokens", "fresh_input_tokens", "total_cost_micros", "runs", "duration_ms"],
            "buckets": ["hour", "day", "week"],
        }

    return router
