"""Persistent local owner for chat and agent execution.

The public web process may be replaced while this service keeps provider,
harness, tool, and asyncio state alive. Public run endpoints proxy here; the
existing durable agent-run buffers remain authoritative inside this process.
"""
from __future__ import annotations

import json
import asyncio
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from typing import Any
from contextlib import contextmanager
from urllib.request import Request as UrlRequest, urlopen

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from core.atomic_io import atomic_write_json
from src.constants import DATA_DIR
from src.runtime_paths import get_app_root

STATE_FILE = Path(DATA_DIR) / "execution-service.json"
LOG_FILE = Path(DATA_DIR) / "execution-service.log"
LOCK_FILE = Path(DATA_DIR) / "execution-service.lock"
WORKER_ENV = "ODYSSEUS_EXECUTION_WORKER"
SECRET_ENV = "ODYSSEUS_EXECUTION_SECRET"
HEADER = "x-odysseus-execution-secret"
OWNER_HEADER = "x-odysseus-execution-owner"
SCOPES_HEADER = "x-odysseus-execution-scopes"
API_TOKEN_HEADER = "x-odysseus-execution-api-token"
_PROXY_STREAM_TASKS: set[asyncio.Task] = set()
_START_LOCK = threading.Lock()


@contextmanager
def _process_lock():
    """Serialize worker discovery/spawn across overlapping frontend processes."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(LOCK_FILE, 0o600)
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def is_worker() -> bool:
    return os.getenv(WORKER_ENV, "").lower() in {"1", "true", "yes", "on"}


def _read_state() -> dict[str, Any] | None:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _write_state(state: dict[str, Any]) -> None:
    atomic_write_json(str(STATE_FILE), state, indent=2)
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


def _healthy(state: dict[str, Any], timeout: float = 0.5) -> bool:
    try:
        req = UrlRequest(
            f"http://127.0.0.1:{int(state['port'])}/api/execution/health",
            headers={HEADER: str(state["secret"])},
        )
        with urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _control(state: dict[str, Any], action: str, timeout: float = 1.0) -> dict[str, Any] | None:
    try:
        req = UrlRequest(
            f"http://127.0.0.1:{int(state['port'])}/api/execution/{action}",
            data=b"{}",
            method="POST",
            headers={HEADER: str(state["secret"]), "content-type": "application/json"},
        )
        with urlopen(req, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else None
    except Exception:
        return None


def current_state(*, require_healthy: bool = True) -> dict[str, Any] | None:
    state = _read_state()
    if not state or not state.get("port") or not state.get("secret"):
        return None
    return state if not require_healthy or _healthy(state) else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def ensure_running(*, startup_timeout_s: float = 60.0) -> dict[str, Any]:
    """Return the live worker state, starting a detached worker if necessary."""
    if is_worker():
        raise RuntimeError("execution worker cannot start another execution worker")
    with _START_LOCK:
        with _process_lock():
            return _ensure_running_locked(startup_timeout_s=startup_timeout_s)


def _ensure_running_locked(*, startup_timeout_s: float) -> dict[str, Any]:
    existing = current_state()
    if existing:
        existing["reused"] = True
        return existing

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1,
        "port": _free_port(),
        "secret": secrets.token_urlsafe(32),
        "started_at": time.time(),
    }
    env = os.environ.copy()
    env[WORKER_ENV] = "1"
    env[SECRET_ENV] = state["secret"]
    env.pop("ODYSSEUS_RELOAD", None)
    env.pop("ODYSSEUS_DEV_RELOAD", None)
    env.pop("ODYSSEUS_RELOAD_ACTIVE", None)
    log_fd = os.open(LOG_FILE, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    os.chmod(LOG_FILE, 0o600)
    log = os.fdopen(log_fd, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(state["port"])],
            cwd=get_app_root(),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"),
            creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0,
        )
    finally:
        log.close()
    state["pid"] = proc.pid
    _write_state(state)
    deadline = time.monotonic() + startup_timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"execution service exited during startup ({proc.returncode})")
        if _healthy(state):
            state["reused"] = False
            return state
        time.sleep(0.1)
    raise TimeoutError("execution service did not become ready")


def should_proxy() -> bool:
    if is_worker() or os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return current_state(require_healthy=False) is not None


def disconnect_proxy_streams() -> int:
    """Drop frontend relay tasks without affecting detached worker runs."""
    tasks = [task for task in _PROXY_STREAM_TASKS if not task.done()]
    for task in tasks:
        task.cancel()
    return len(tasks)


async def proxy(request: Request, path: str, *, streaming: bool, _retry: bool = True) -> Response:
    state = current_state(require_healthy=False)
    if not state:
        raise HTTPException(503, "Execution service is unavailable", headers={"Retry-After": "2"})
    body = await request.body()
    headers = {
        key: value for key, value in request.headers.items()
        if key.lower() not in {
            "host", "content-length", HEADER, OWNER_HEADER, SCOPES_HEADER,
            API_TOKEN_HEADER,
        }
    }
    headers[HEADER] = str(state["secret"])
    owner = (
        getattr(request.state, "api_token_owner", None)
        if getattr(request.state, "api_token", False)
        else getattr(request.state, "current_user", None)
    )
    if owner:
        headers[OWNER_HEADER] = str(owner)
    if getattr(request.state, "api_token", False):
        headers[API_TOKEN_HEADER] = "1"
        headers[SCOPES_HEADER] = ",".join(getattr(request.state, "api_token_scopes", []) or [])
    url = f"http://127.0.0.1:{int(state['port'])}{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    client = httpx.AsyncClient(timeout=None)
    try:
        upstream = await client.send(
            client.build_request(request.method, url, headers=headers, content=body),
            stream=streaming,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        if _retry:
            await asyncio.to_thread(ensure_running)
            return await proxy(request, path, streaming=streaming, _retry=False)
        raise HTTPException(503, "Execution service is reconnecting", headers={"Retry-After": "1"}) from exc
    response_headers = {
        key: value for key, value in upstream.headers.items()
        if key.lower() in {
            "content-type", "cache-control", "retry-after",
            "x-odysseus-run-id",
        }
    }
    if not streaming:
        content = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        return Response(content, status_code=upstream.status_code, headers=response_headers)

    async def _body():
        task = asyncio.current_task()
        if task is not None:
            _PROXY_STREAM_TASKS.add(task)
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            if task is not None:
                _PROXY_STREAM_TASKS.discard(task)
            await upstream.aclose()
            await client.aclose()

    async def _close() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        _body(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(_close),
    )


def router() -> APIRouter:
    api = APIRouter()

    @api.get("/api/execution/health", include_in_schema=False)
    async def health(request: Request) -> dict[str, Any]:
        expected = os.getenv(SECRET_ENV, "")
        if not is_worker() or not expected or not secrets.compare_digest(request.headers.get(HEADER, ""), expected):
            raise HTTPException(404)
        from src import agent_runs
        return {"ok": True, "pid": os.getpid(), "active_runs": agent_runs.active_run_count()}

    def _authorize(request: Request) -> None:
        expected = os.getenv(SECRET_ENV, "")
        if not is_worker() or not expected or not secrets.compare_digest(request.headers.get(HEADER, ""), expected):
            raise HTTPException(404)

    @api.post("/api/execution/drain", include_in_schema=False)
    async def drain(request: Request) -> dict[str, Any]:
        _authorize(request)
        from src import agent_runs
        return {"ok": True, "active_runs": agent_runs.begin_drain()}

    @api.post("/api/execution/status", include_in_schema=False)
    async def status(request: Request) -> dict[str, Any]:
        _authorize(request)
        from src import agent_runs
        return {"ok": True, "active_runs": agent_runs.active_run_count(), "draining": agent_runs.is_draining()}

    if os.getenv("ODYSSEUS_EXECUTION_TEST_PROBE") == "1":
        @api.post("/api/execution/probe/{run_id}", include_in_schema=False)
        async def probe_start(request: Request, run_id: str, delay: float = 1.0) -> StreamingResponse:
            _authorize(request)
            from src import agent_runs
            import asyncio

            async def _events():
                yield 'data: {"delta":"started"}\n\n'
                await asyncio.sleep(max(0.0, min(delay, 30.0)))
                yield 'data: {"delta":"finished"}\n\n'
                yield "data: [DONE]\n\n"

            agent_runs.start(run_id, _events())
            return StreamingResponse(agent_runs.subscribe(run_id), media_type="text/event-stream")

        @api.get("/api/execution/probe/{run_id}", include_in_schema=False)
        async def probe_resume(request: Request, run_id: str) -> StreamingResponse:
            _authorize(request)
            from src import agent_runs
            if agent_runs.get_status(run_id) is None:
                raise HTTPException(404)
            return StreamingResponse(agent_runs.subscribe(run_id), media_type="text/event-stream")

    return api
