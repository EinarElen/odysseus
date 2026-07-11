"""HTTP contract tests for Typst preview sessions."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.typst_routes as typst_routes
from services.typst_service import TypstSessionManager


def test_source_update_reports_revision_conflict(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = TypstSessionManager()
    monkeypatch.setattr(typst_routes, "typst_session_manager", manager)
    app = FastAPI()
    app.include_router(typst_routes.setup_typst_routes())
    client = TestClient(app)

    created = client.post(
        "/api/typst/sessions",
        json={"source": "initial", "autoRefresh": False},
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    assert client.patch(
        f"/api/typst/sessions/{session_id}/source",
        json={"source": "newer", "revision": 1},
    ).status_code == 200

    stale = client.patch(
        f"/api/typst/sessions/{session_id}/source",
        json={"source": "stale", "revision": 0},
    )

    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "revision_conflict",
        "currentRevision": 1,
    }
    assert manager.sessions[session_id].source == "newer"

    old_compile = client.post(
        f"/api/typst/sessions/{session_id}/compile",
        json={"revision": 0},
    )
    assert old_compile.status_code == 409
    assert old_compile.json()["detail"]["currentRevision"] == 1

    deleted = client.delete(f"/api/typst/sessions/{session_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
