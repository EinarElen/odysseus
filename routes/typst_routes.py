"""Typst live-preview API routes."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from services.typst_service import typst_session_manager
from src.auth_helpers import get_current_user, require_privilege


class TypstSessionCreate(BaseModel):
    ownerType: str = "document"
    ownerId: Optional[str] = None
    source: str = ""
    backend: str = "tinymist"
    autoRefresh: bool = True


class TypstSourceUpdate(BaseModel):
    source: str
    revision: Optional[int] = None
    compile: bool = False


class TypstCompileRequest(BaseModel):
    revision: Optional[int] = None
    reason: Optional[str] = None


class TypstExportRequest(BaseModel):
    format: str = "pdf"


def setup_typst_routes() -> APIRouter:
    router = APIRouter(prefix="/api/typst", tags=["typst"])

    @router.post("/sessions")
    async def create_session(request: Request, req: TypstSessionCreate) -> Dict[str, Any]:
        user = require_privilege(request, "can_use_documents")
        sess = typst_session_manager.create(
            owner=user,
            owner_type=req.ownerType,
            owner_id=req.ownerId,
            source=req.source,
            backend=req.backend or "tinymist",
            auto_refresh=req.autoRefresh,
        )
        return {"ok": True, "session": typst_session_manager.snapshot(sess)}

    @router.get("/sessions/{session_id}")
    async def get_session(request: Request, session_id: str) -> Dict[str, Any]:
        user = get_current_user(request)
        try:
            sess = typst_session_manager.get(session_id, user)
        except KeyError:
            raise HTTPException(404, "Typst session not found")
        except PermissionError:
            raise HTTPException(404, "Typst session not found")
        return {"ok": True, "session": typst_session_manager.snapshot(sess)}

    @router.patch("/sessions/{session_id}/source")
    async def update_source(request: Request, session_id: str, req: TypstSourceUpdate) -> Dict[str, Any]:
        user = get_current_user(request)
        try:
            sess = typst_session_manager.get(session_id, user)
            revision = typst_session_manager.update_source(sess, req.source, req.revision)
            result = None
            if req.compile or sess.auto_refresh:
                result = await typst_session_manager.request_compile(sess, revision)
        except KeyError:
            raise HTTPException(404, "Typst session not found")
        except PermissionError:
            raise HTTPException(404, "Typst session not found")
        return {
            "ok": True,
            "revision": revision,
            "session": typst_session_manager.snapshot(sess),
            "compile": typst_session_manager._result_payload(sess, result) if result else None,
        }

    @router.post("/sessions/{session_id}/compile")
    async def compile_session(request: Request, session_id: str, req: TypstCompileRequest) -> Dict[str, Any]:
        user = get_current_user(request)
        try:
            sess = typst_session_manager.get(session_id, user)
            result = await typst_session_manager.request_compile(sess, req.revision)
        except KeyError:
            raise HTTPException(404, "Typst session not found")
        except PermissionError:
            raise HTTPException(404, "Typst session not found")
        return typst_session_manager._result_payload(sess, result)

    @router.get("/sessions/{session_id}/events")
    async def session_events(request: Request, session_id: str):
        user = get_current_user(request)
        try:
            sess = typst_session_manager.get(session_id, user)
        except KeyError:
            raise HTTPException(404, "Typst session not found")
        except PermissionError:
            raise HTTPException(404, "Typst session not found")
        return StreamingResponse(typst_session_manager.subscribe(sess), media_type="text/event-stream")

    @router.get("/sessions/{session_id}/pages")
    async def list_pages(request: Request, session_id: str) -> Dict[str, Any]:
        user = get_current_user(request)
        try:
            sess = typst_session_manager.get(session_id, user)
        except KeyError:
            raise HTTPException(404, "Typst session not found")
        except PermissionError:
            raise HTTPException(404, "Typst session not found")
        return {"ok": True, "pages": typst_session_manager.snapshot(sess)["pages"]}

    @router.get("/sessions/{session_id}/pages/{page}.svg")
    async def page_svg(request: Request, session_id: str, page: int) -> Response:
        user = get_current_user(request)
        try:
            sess = typst_session_manager.get(session_id, user)
            svg = typst_session_manager.page_svg(sess, page)
        except KeyError:
            raise HTTPException(404, "Typst page not found")
        except PermissionError:
            raise HTTPException(404, "Typst page not found")
        return Response(svg, media_type="image/svg+xml; charset=utf-8", headers={"Cache-Control": "no-store"})

    @router.post("/sessions/{session_id}/export")
    async def export_session(request: Request, session_id: str, req: TypstExportRequest) -> Response:
        user = get_current_user(request)
        fmt = (req.format or "pdf").lower()
        if fmt not in {"pdf", "svg", "typ"}:
            raise HTTPException(400, "format must be pdf, svg, or typ")
        try:
            sess = typst_session_manager.get(session_id, user)
            data = await typst_session_manager.export(sess, fmt)
        except KeyError:
            raise HTTPException(404, "Typst session not found")
        except PermissionError:
            raise HTTPException(404, "Typst session not found")
        except Exception as e:
            raise HTTPException(500, str(e))
        media = "application/pdf" if fmt == "pdf" else "image/svg+xml" if fmt == "svg" else "text/plain; charset=utf-8"
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="typst-export.{fmt}"'})

    @router.delete("/sessions/{session_id}")
    async def delete_session(request: Request, session_id: str) -> Dict[str, Any]:
        user = get_current_user(request)
        try:
            typst_session_manager.delete(session_id, user)
        except KeyError:
            raise HTTPException(404, "Typst session not found")
        except PermissionError:
            raise HTTPException(404, "Typst session not found")
        return {"ok": True}

    return router
