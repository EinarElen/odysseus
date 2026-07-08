from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from core.middleware import SecurityHeadersMiddleware


def _client():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/plain")
    async def plain():
        return {"ok": True}

    @app.get("/api/document/{doc_id}/render-pdf")
    async def render_pdf(doc_id: str):
        return Response(b"%PDF-1.4\n", media_type="application/pdf")

    return TestClient(app)


def test_default_routes_remain_unframeable():
    response = _client().get("/plain")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_dev_mode_allows_embedded_preview(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DEV_MODE", "1")

    response = _client().get("/plain")

    assert "X-Frame-Options" not in response.headers
    assert "frame-ancestors" not in response.headers["Content-Security-Policy"]
    assert "script-src" in response.headers["Content-Security-Policy"]


def test_document_pdf_preview_can_be_framed_by_same_origin():
    response = _client().get("/api/document/doc-123/render-pdf")

    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'none'; frame-ancestors 'self'"
    )
