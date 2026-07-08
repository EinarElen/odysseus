"""Developer-mode API routes."""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException, Request

from core.middleware import require_admin
from src import dev_mode

OPTIONAL_JSON_BODY = Body(default=None)


def setup_dev_routes() -> APIRouter:
    router = APIRouter(prefix="/api/dev", tags=["developer"])

    def _status(request: Request) -> Dict[str, Any]:
        require_admin(request)
        dev_mode.mark_client_seen(request.headers.get("X-Odysseus-Dev-Client"))
        return dev_mode.repo_status()

    def _require_enabled(request: Request) -> Dict[str, Any]:
        status = _status(request)
        if not status.get("enabled"):
            raise HTTPException(status_code=403, detail=status.get("reason") or "Developer mode is disabled")
        return status

    def _require_dev_action(request: Request, action: str) -> None:
        if request.headers.get("X-Odysseus-Dev-Action") != action:
            raise HTTPException(status_code=403, detail="Developer action header required")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}".rstrip("/")
        for header_name in ("origin", "referer"):
            raw = request.headers.get(header_name)
            if not raw:
                continue
            parsed = urlparse(raw)
            actual = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            if actual and actual != expected:
                raise HTTPException(status_code=403, detail="Cross-origin developer action rejected")

    @router.get("/status")
    async def status(request: Request) -> Dict[str, Any]:
        return _status(request)

    @router.get("/revision")
    async def revision(request: Request) -> Dict[str, Any]:
        status = _require_enabled(request)
        data = dev_mode.revision(status["root"])
        data["status"] = {
            "branch": status.get("branch"),
            "commit": status.get("commit"),
            "dirty_count": status.get("dirty_count"),
            "reload_active": status.get("reload_active"),
            "reload_requested": status.get("reload_requested"),
            "reload_mode": status.get("reload_mode"),
            "manual_reload_supported": status.get("manual_reload_supported"),
            "client_count": status.get("client_count"),
        }
        return data

    @router.post("/server/reload")
    async def server_reload(request: Request) -> Dict[str, Any]:
        status = _require_enabled(request)
        _require_dev_action(request, "server-reload")
        return dev_mode.request_server_reload(status["root"])

    @router.get("/git")
    async def git(request: Request) -> Dict[str, Any]:
        status = _require_enabled(request)
        return dev_mode.git_snapshot(status["root"])

    @router.get("/tests/suggest")
    async def suggest_tests(request: Request) -> Dict[str, Any]:
        status = _require_enabled(request)
        return dev_mode.test_suggestions(status["root"])

    @router.post("/tests/run")
    async def run_test(
        request: Request,
        body: Optional[Dict[str, Any]] = OPTIONAL_JSON_BODY,
    ) -> Dict[str, Any]:
        status = _require_enabled(request)
        kind = str((body or {}).get("kind") or "").strip()
        if not kind:
            raise HTTPException(status_code=400, detail="Missing check kind")
        return dev_mode.run_dev_check(kind, status["root"])

    @router.get("/introspection")
    async def introspection(request: Request) -> Dict[str, Any]:
        _require_enabled(request)
        return dev_mode.app_introspection(request.app)

    @router.get("/github/cache")
    async def github_cache(request: Request) -> Dict[str, Any]:
        _require_enabled(request)
        return dev_mode.read_github_cache()

    @router.post("/github/refresh")
    async def github_refresh(
        request: Request,
        body: Optional[Dict[str, Any]] = OPTIONAL_JSON_BODY,
    ) -> Dict[str, Any]:
        status = _require_enabled(request)
        kinds = (body or {}).get("kinds") or ["prs", "issues"]
        if not isinstance(kinds, list):
            kinds = ["prs", "issues"]
        return dev_mode.refresh_github_cache([str(k) for k in kinds], root=status["root"])

    return router
