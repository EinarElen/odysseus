"""Owner-scoped, cursor-replayable application activity feed."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from src.auth_helpers import effective_user, require_user
from src.event_bus import query_application_events


def _owner(request: Request) -> str | None:
    require_user(request)
    return effective_user(request)


def setup_application_event_routes() -> APIRouter:
    router = APIRouter(prefix="/api/events", tags=["application_events"])

    @router.get("")
    async def query_events(
        request: Request,
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        events = query_application_events(owner=_owner(request), cursor=cursor, limit=limit)
        after = events[-1]["seq"] if events else cursor
        return {"events": events, "cursor": {"after": after}}

    @router.get("/stream")
    async def stream_events(
        request: Request,
        cursor: int = Query(default=0, ge=0),
        once: bool = False,
    ):
        owner = _owner(request)

        async def generate():
            after = cursor
            while True:
                events = query_application_events(owner=owner, cursor=after, limit=500)
                for event in events:
                    after = event["seq"]
                    yield json.dumps(event, separators=(",", ":")) + "\n"
                if once or await request.is_disconnected():
                    return
                await asyncio.sleep(0.25)

        return StreamingResponse(
            generate(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
