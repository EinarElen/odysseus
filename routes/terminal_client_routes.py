"""Terminal Client API routes."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src import terminal_client_runs
from src.auth_helpers import effective_user


class RunStartRequest(BaseModel):
    kind: str = "chat"
    session_id: str | None = None
    message: str = Field(default="")


def setup_terminal_client_routes(session_manager=None, **_deps: Any) -> APIRouter:
    router = APIRouter(prefix="/api/terminal", tags=["terminal_client"])

    @router.post("/runs")
    async def start_run(request: Request, payload: RunStartRequest) -> dict[str, Any]:
        if payload.kind != "chat":
            raise HTTPException(400, "Terminal Client run start currently supports kind=chat")
        session_id = payload.session_id
        if not session_id:
            session_id = f"ses_{uuid.uuid4().hex[:16]}"
            if session_manager is not None and hasattr(session_manager, "create_session"):
                try:
                    session_manager.create_session(
                        session_id=session_id,
                        name="ody-term chat",
                        model="",
                        endpoint_url="",
                        owner=effective_user(request),
                    )
                except TypeError:
                    try:
                        created = session_manager.create_session("ody-term chat", "", "")
                        session_id = str(getattr(created, "id", session_id))
                    except Exception:
                        pass
                except Exception:
                    pass
        stream = terminal_client_runs.make_single_event_stream(payload.message)
        return terminal_client_runs.create_chat_run(session_id=session_id, message=payload.message, stream=stream)

    @router.get("/runs")
    async def list_runs(kind: str | None = None, status: str | None = None) -> dict[str, Any]:
        return {"runs": terminal_client_runs.list_runs(kind=kind, status=status)}

    @router.get("/runs/{run_id}")
    async def run_status(run_id: str) -> dict[str, Any]:
        try:
            run = terminal_client_runs.resolve_run(run_id=run_id)
        except KeyError:
            raise HTTPException(404, "Run not found") from None
        return {"run": terminal_client_runs.run_summary(run)}

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str, cursor: int | None = None) -> dict[str, Any]:
        try:
            return await terminal_client_runs.attach_run(run_id=run_id, cursor=cursor)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    @router.post("/runs/{run_id}/stop")
    async def stop_run(run_id: str) -> dict[str, Any]:
        try:
            return await terminal_client_runs.stop_run(run_id=run_id)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    return router
