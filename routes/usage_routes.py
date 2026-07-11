"""Owner-scoped HTTP read interface for the usage ledger."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.auth_helpers import effective_user, require_user
from src.usage_observability import usage_store
from src.subscription_usage import subscription_usage_store


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


class UsageFilters:
    def __init__(
        self, session_id: str | None = None, run_id: str | None = None,
        kind: str | None = None, surface: str | None = None,
        provider: str | None = None, model: str | None = None,
        tool: str | None = None, status: str | None = None,
        usage_source: str | None = None, cache_status: str | None = None,
    ):
        self.values = {key: value for key, value in locals().items() if key not in {"self"} and value is not None}


def setup_usage_routes() -> APIRouter:
    router = APIRouter(prefix="/api/usage", tags=["usage"])

    @router.get("/summary")
    def summary(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None, filters: UsageFilters = Depends()):
        return usage_store.query_summary(owner=_owner(request), start=_time(from_), end=_time(to), **filters.values)

    @router.get("/timeseries")
    def timeseries(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None, bucket: str = "day", timezone: str = "UTC", filters: UsageFilters = Depends()):
        try:
            return usage_store.query_timeseries(owner=_owner(request), start=_time(from_), end=_time(to), bucket=bucket, timezone_name=timezone, **filters.values)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/breakdown")
    def breakdown(request: Request, group_by: str = "model", from_: str | None = Query(None, alias="from"), to: str | None = None, filters: UsageFilters = Depends()):
        try:
            return usage_store.query_breakdown(owner=_owner(request), group_by=group_by, start=_time(from_), end=_time(to), **filters.values)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/runs")
    def runs(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None, limit: int = 100, offset: int = 0, filters: UsageFilters = Depends()):
        return usage_store.list_runs(owner=_owner(request), start=_time(from_), end=_time(to), limit=limit, offset=max(offset, 0), **filters.values)

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

    @router.get("/anomalies")
    def anomalies(request: Request, from_: str | None = Query(None, alias="from"), to: str | None = None):
        return usage_store.query_anomalies(owner=_owner(request), start=_time(from_), end=_time(to))

    @router.get("/subscription")
    def subscription(request: Request):
        return subscription_usage_store.refresh_if_stale(owner=_owner(request))

    @router.post("/subscription/refresh")
    def refresh_subscription(request: Request):
        return subscription_usage_store.refresh(owner=_owner(request))

    @router.get("/live")
    async def live(request: Request):
        owner = _owner(request)

        async def events():
            seen: set[str] = set()
            while not await request.is_disconnected():
                page = usage_store.list_runs(owner=owner, limit=50)
                current = {run["id"] for run in page["runs"]}
                for run in reversed(page["runs"]):
                    if run["id"] not in seen:
                        yield f"event: usage.run\ndata: {json.dumps(run)}\n\n"
                seen |= current
                yield ": heartbeat\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @router.post("/rollups/rebuild")
    def rebuild_rollups(request: Request):
        owner = _owner(request)
        return {"rebuilt": usage_store.rebuild_daily_rollups(owner=owner)}

    @router.post("/backfill")
    def backfill(request: Request):
        _owner(request)
        return usage_store.backfill_legacy_messages()

    @router.delete("/runs/{run_id}")
    def delete_run(request: Request, run_id: str):
        deleted = usage_store.delete_usage(owner=_owner(request), run_id=run_id)
        if not deleted:
            raise HTTPException(404, "Usage Run not found")
        return {"deleted": deleted}

    @router.delete("")
    def delete_usage(request: Request, before: str | None = None, all: bool = False):
        try:
            deleted = usage_store.delete_usage(owner=_owner(request), before=_time(before), all_usage=all)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"deleted": deleted}

    return router
