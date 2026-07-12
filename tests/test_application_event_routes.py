import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.application_event_routes import setup_application_event_routes
from src import event_bus


def make_client(monkeypatch):
    event_bus.reset_application_events_for_tests()
    monkeypatch.setattr("routes.application_event_routes.effective_user", lambda request: "alice")
    monkeypatch.setattr("routes.application_event_routes.require_user", lambda request: "alice")
    app = FastAPI()
    app.include_router(setup_application_event_routes())
    return TestClient(app)


def test_owner_event_query_is_cursor_replayable_and_isolated(monkeypatch):
    client = make_client(monkeypatch)
    event_bus.publish_application_event(
        "session.updated", owner="alice", domain="sessions",
        resource_id="ses-1", summary="Session renamed", payload={"name": "New"},
    )
    event_bus.publish_application_event("email.received", owner="bob", domain="email")
    event_bus.publish_application_event("task.completed", owner="alice", domain="tasks")

    first = client.get("/api/events", params={"limit": 1}).json()
    assert [item["kind"] for item in first["events"]] == ["session.updated"]
    assert first["events"][0]["resource_id"] == "ses-1"

    second = client.get("/api/events", params={"cursor": first["cursor"]["after"]}).json()
    assert [item["kind"] for item in second["events"]] == ["task.completed"]
    assert all(item["owner"] == "alice" for item in first["events"] + second["events"])


def test_owner_event_stream_emits_ndjson_after_cursor(monkeypatch):
    client = make_client(monkeypatch)
    event_bus.publish_application_event("research.started", owner="alice", domain="research")
    event_bus.publish_application_event("research.completed", owner="alice", domain="research")

    with client.stream("GET", "/api/events/stream", params={"cursor": 1, "once": True}) as response:
        lines = [json.loads(line) for line in response.iter_lines() if line]

    assert response.status_code == 200
    assert [item["kind"] for item in lines] == ["research.completed"]
