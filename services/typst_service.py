"""Typst/Tinymist live-preview session service.

Default backend is Tinymist when available, with typst CLI fallback.  The
Odysseus-facing contract is session/revision/page based so Documents and Notes
can get live preview semantics without depending on a specific compiler process.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional


@dataclass
class TypstDiagnostic:
    severity: str
    message: str
    raw: str = ""


@dataclass
class TypstPreviewPage:
    page: int
    width: Optional[float]
    height: Optional[float]
    hash: str
    svg: str


@dataclass
class CompileResult:
    ok: bool
    revision: int
    backend: str
    pages: List[TypstPreviewPage] = field(default_factory=list)
    diagnostics: List[TypstDiagnostic] = field(default_factory=list)
    duration_ms: int = 0
    error: Optional[str] = None


@dataclass
class TypstSession:
    id: str
    owner: Optional[str]
    owner_type: str
    owner_id: Optional[str]
    source: str
    source_revision: int = 0
    asset_revision: int = 0
    last_compiled_revision: int = -1
    pending_revision: Optional[int] = None
    compiling: bool = False
    backend_name: str = "tinymist"
    auto_refresh: bool = True
    pages: List[TypstPreviewPage] = field(default_factory=list)
    diagnostics: List[TypstDiagnostic] = field(default_factory=list)
    last_error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    events: List[Dict[str, Any]] = field(default_factory=list)
    subscribers: List[asyncio.Queue] = field(default_factory=list)


class TypstPreviewBackend:
    name = "base"

    async def compile(self, session: TypstSession, source: str, revision: int, fmt: str = "svg") -> CompileResult:
        raise NotImplementedError

    async def export(self, session: TypstSession, source: str, fmt: str) -> bytes:
        raise NotImplementedError


class CliTypstBackend(TypstPreviewBackend):
    name = "typst-cli"

    def __init__(self, command: str = "typst"):
        self.command = command

    @property
    def available(self) -> bool:
        return shutil.which(self.command) is not None

    async def _run_compile(self, source: str, suffix: str, timeout: float = 20.0) -> tuple[int, bytes, str, str]:
        with tempfile.TemporaryDirectory(prefix="odysseus-typst-") as td:
            root = Path(td)
            inp = root / "main.typ"
            out = root / f"out.{suffix}"
            inp.write_text(source or "", encoding="utf-8")
            proc = await asyncio.create_subprocess_exec(
                self.command, "compile", str(inp), str(out),
                cwd=str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return 124, b"", "", "Typst compile timed out"
            data = out.read_bytes() if out.exists() else b""
            return proc.returncode or 0, data, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")

    async def compile(self, session: TypstSession, source: str, revision: int, fmt: str = "svg") -> CompileResult:
        started = time.time()
        code, data, stdout, stderr = await self._run_compile(source, "svg")
        duration = int((time.time() - started) * 1000)
        if code != 0 or not data:
            raw = (stderr or stdout or "Typst compile failed").strip()
            return CompileResult(False, revision, self.name, diagnostics=_parse_diagnostics(raw), duration_ms=duration, error=raw)
        svg = data.decode("utf-8", "replace")
        pages = _split_svg_pages(svg)
        return CompileResult(True, revision, self.name, pages=pages, duration_ms=duration)

    async def export(self, session: TypstSession, source: str, fmt: str) -> bytes:
        suffix = "pdf" if fmt == "pdf" else "svg"
        code, data, stdout, stderr = await self._run_compile(source, suffix, timeout=40.0)
        if code != 0 or not data:
            raise RuntimeError((stderr or stdout or "Typst export failed").strip())
        return data


class TinymistBackend(CliTypstBackend):
    """Tinymist-backed compiler adapter.

    Tinymist is the preferred/default backend.  In installations where Tinymist
    exposes `tinymist compile`, use it directly.  Otherwise this adapter falls
    back to the official Typst CLI while still reporting the hybrid backend; the
    session/API layer is unchanged and can later be wired to Tinymist's preview
    server/LSP protocol for richer source-preview sync.
    """

    name = "tinymist"

    def __init__(self):
        super().__init__("tinymist")
        self.fallback = CliTypstBackend("typst")

    @property
    def available(self) -> bool:
        return shutil.which("tinymist") is not None or self.fallback.available

    async def _run_compile(self, source: str, suffix: str, timeout: float = 20.0) -> tuple[int, bytes, str, str]:
        if shutil.which("tinymist"):
            # Try the Tinymist CLI first.  If this installation does not expose a
            # compile subcommand, gracefully fall back to typst.
            with tempfile.TemporaryDirectory(prefix="odysseus-tinymist-") as td:
                root = Path(td)
                inp = root / "main.typ"
                out = root / f"out.{suffix}"
                inp.write_text(source or "", encoding="utf-8")
                proc = await asyncio.create_subprocess_exec(
                    "tinymist", "compile", str(inp), str(out),
                    cwd=str(root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                except asyncio.TimeoutError:
                    proc.kill(); await proc.communicate()
                    return 124, b"", "", "Tinymist compile timed out"
                if (proc.returncode or 0) == 0 and out.exists():
                    return 0, out.read_bytes(), stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
                err = stderr.decode("utf-8", "replace") + stdout.decode("utf-8", "replace")
                if "compile" not in err.lower() and "error" in err.lower():
                    return proc.returncode or 1, b"", "", err
        return await self.fallback._run_compile(source, suffix, timeout=timeout)


def _parse_diagnostics(raw: str) -> List[TypstDiagnostic]:
    if not raw:
        return []
    diags: List[TypstDiagnostic] = []
    chunks = re.split(r"\n(?=error:|warning:)", raw, flags=re.I)
    for chunk in chunks:
        c = chunk.strip()
        if not c:
            continue
        sev = "error" if c.lower().startswith("error") else "warning" if c.lower().startswith("warning") else "error"
        first = c.splitlines()[0]
        diags.append(TypstDiagnostic(sev, first, c))
    return diags or [TypstDiagnostic("error", raw.splitlines()[0], raw)]


def _split_svg_pages(svg: str) -> List[TypstPreviewPage]:
    # Typst may emit one SVG containing all pages or one page. Keep page-level
    # contract even if backend output is not split yet.
    m = re.search(r'<svg[^>]*\bwidth="([0-9.]+)[^"]*"[^>]*\bheight="([0-9.]+)[^"]*"', svg)
    width = float(m.group(1)) if m else None
    height = float(m.group(2)) if m else None
    h = hashlib.sha256(svg.encode("utf-8", "replace")).hexdigest()
    return [TypstPreviewPage(page=1, width=width, height=height, hash=h, svg=svg)]


class TypstSessionManager:
    def __init__(self):
        self.sessions: Dict[str, TypstSession] = {}
        self.backends = {
            "tinymist": TinymistBackend(),
            "typst-cli": CliTypstBackend("typst"),
        }

    def create(self, *, owner: Optional[str], owner_type: str, owner_id: Optional[str], source: str, backend: str = "tinymist", auto_refresh: bool = True) -> TypstSession:
        sid = "typst_" + uuid.uuid4().hex
        if backend not in self.backends:
            backend = "tinymist"
        sess = TypstSession(sid, owner, owner_type, owner_id, source or "", backend_name=backend, auto_refresh=auto_refresh)
        self.sessions[sid] = sess
        self._publish(sess, {"type": "session-created", "sessionId": sid, "revision": sess.source_revision, "backend": backend})
        return sess

    def get(self, sid: str, owner: Optional[str]) -> TypstSession:
        sess = self.sessions.get(sid)
        if not sess:
            raise KeyError(sid)
        if owner and sess.owner and owner != sess.owner:
            raise PermissionError(sid)
        sess.last_accessed_at = time.time()
        return sess

    def delete(self, sid: str, owner: Optional[str]) -> bool:
        self.get(sid, owner)
        return self.sessions.pop(sid, None) is not None

    def update_source(self, sess: TypstSession, source: str, revision: Optional[int]) -> int:
        if revision is None:
            revision = sess.source_revision + 1
        if revision < sess.source_revision:
            return sess.source_revision
        sess.source = source or ""
        sess.source_revision = revision
        sess.last_accessed_at = time.time()
        self._publish(sess, {"type": "source-updated", "sessionId": sess.id, "revision": revision})
        return revision

    async def request_compile(self, sess: TypstSession, revision: Optional[int] = None) -> CompileResult:
        sess.pending_revision = revision if revision is not None else sess.source_revision
        if sess.compiling:
            return CompileResult(True, sess.pending_revision, sess.backend_name, pages=sess.pages, diagnostics=sess.diagnostics)
        result: Optional[CompileResult] = None
        while sess.pending_revision is not None:
            target = sess.pending_revision
            sess.pending_revision = None
            sess.compiling = True
            self._publish(sess, {"type": "compile-started", "sessionId": sess.id, "revision": target})
            backend = self.backends.get(sess.backend_name) or self.backends["tinymist"]
            if not getattr(backend, "available", False):
                result = CompileResult(False, target, backend.name, error="Neither tinymist nor typst CLI is installed", diagnostics=[TypstDiagnostic("error", "Neither tinymist nor typst CLI is installed")])
            else:
                result = await backend.compile(sess, sess.source, target, "svg")
            sess.compiling = False
            if target == sess.source_revision:
                sess.last_compiled_revision = target if result.ok else sess.last_compiled_revision
                sess.pages = result.pages if result.ok else sess.pages
                sess.diagnostics = result.diagnostics
                sess.last_error = result.error
                payload = self._result_payload(sess, result)
                payload["type"] = "compile-finished" if result.ok else "compile-error"
                self._publish(sess, payload)
            else:
                self._publish(sess, {"type": "compile-stale", "sessionId": sess.id, "revision": target, "currentRevision": sess.source_revision})
        return result or CompileResult(True, sess.source_revision, sess.backend_name, pages=sess.pages, diagnostics=sess.diagnostics)

    async def export(self, sess: TypstSession, fmt: str) -> bytes:
        if fmt == "typ":
            return sess.source.encode("utf-8")
        backend = self.backends.get(sess.backend_name) or self.backends["tinymist"]
        if not getattr(backend, "available", False):
            raise RuntimeError("Neither tinymist nor typst CLI is installed")
        return await backend.export(sess, sess.source, fmt)

    def page_svg(self, sess: TypstSession, page: int) -> str:
        for p in sess.pages:
            if p.page == page:
                return p.svg
        raise KeyError(page)

    def _result_payload(self, sess: TypstSession, result: CompileResult) -> Dict[str, Any]:
        return {
            "sessionId": sess.id,
            "revision": result.revision,
            "backend": result.backend,
            "ok": result.ok,
            "durationMs": result.duration_ms,
            "error": result.error,
            "pages": [{"page": p.page, "width": p.width, "height": p.height, "hash": p.hash, "svgUrl": f"/api/typst/sessions/{sess.id}/pages/{p.page}.svg"} for p in result.pages],
            "diagnostics": [d.__dict__ for d in result.diagnostics],
        }

    def snapshot(self, sess: TypstSession) -> Dict[str, Any]:
        return {
            "id": sess.id,
            "ownerType": sess.owner_type,
            "ownerId": sess.owner_id,
            "sourceRevision": sess.source_revision,
            "lastCompiledRevision": sess.last_compiled_revision,
            "assetRevision": sess.asset_revision,
            "backend": sess.backend_name,
            "autoRefresh": sess.auto_refresh,
            "compiling": sess.compiling,
            "pages": [{"page": p.page, "width": p.width, "height": p.height, "hash": p.hash, "svgUrl": f"/api/typst/sessions/{sess.id}/pages/{p.page}.svg"} for p in sess.pages],
            "diagnostics": [d.__dict__ for d in sess.diagnostics],
            "lastError": sess.last_error,
        }

    def _publish(self, sess: TypstSession, event: Dict[str, Any]) -> None:
        event = {**event, "ts": time.time()}
        sess.events.append(event)
        sess.events[:] = sess.events[-200:]
        for q in list(sess.subscribers):
            try:
                q.put_nowait(event)
            except Exception:
                pass

    async def subscribe(self, sess: TypstSession) -> AsyncIterator[str]:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        sess.subscribers.append(q)
        try:
            yield f"event: snapshot\ndata: {json.dumps(self.snapshot(sess))}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"event: {ev.get('type', 'message')}\ndata: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if q in sess.subscribers:
                sess.subscribers.remove(q)


typst_session_manager = TypstSessionManager()
