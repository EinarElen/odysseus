from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.terminal_client_routes import setup_terminal_client_routes
from src import agent_runs, terminal_client_runs


def test_terminal_client_chat_run_api_exposes_distinct_run_events_and_stop(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()

    app = FastAPI()
    app.include_router(setup_terminal_client_routes())
    client = TestClient(app)

    started = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
    )
    assert started.status_code == 200
    run = started.json()["run"]
    assert run["run_id"].startswith("run_")
    assert run["session_id"] == "ses-real"
    assert run["run_id"] != run["session_id"]

    listed = client.get("/api/terminal/runs", params={"kind": "chat"})
    assert listed.status_code == 200
    assert listed.json()["runs"][0]["run_id"] == run["run_id"]

    events = client.get(f"/api/terminal/runs/{run['run_id']}/events")
    assert events.status_code == 200
    payload = events.json()
    assert payload["cursor"]["next"] == "2"
    assert [event["schema"] for event in payload["events"]] == ["ody.event.v1", "ody.event.v1"]
    assert payload["events"][0]["run_id"] == run["run_id"]
    assert payload["events"][0]["session_id"] == "ses-real"
    assert payload["events"][0]["raw"]["transport"] == "sse"

    stopped = client.post(f"/api/terminal/runs/{run['run_id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["run"]["status"] in {"done", "stopped"}
