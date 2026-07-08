from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.terminal_client_routes import setup_terminal_client_routes
from src import agent_runs, terminal_client_runs
from core.models import Session
from core.models import ChatMessage


MODEL_ENDPOINT_URL = "http://model.local/v1/chat/completions"
TEST_MODEL = "test-model"
CREATED_MODEL = "created-model"


class FakeSessionManager:
    def __init__(self) -> None:
        self.sessions = {
            "ses-real": Session(
                id="ses-real",
                name="",
                endpoint_url=MODEL_ENDPOINT_URL,
                model=TEST_MODEL,
            ),
            "ses-unconfigured": Session(id="ses-unconfigured", name="", endpoint_url="", model=""),
        }
        self.saved = 0

    def get_session(self, session_id):
        return self.sessions[session_id]

    def create_session(self, session_id, name, endpoint_url, model, owner=None):
        session = Session(id=session_id, name=name, endpoint_url=endpoint_url, model=model, owner=owner)
        self.sessions[session_id] = session
        return session

    def save_sessions(self):
        self.saved += 1

    def _persist_message(self, session_id, message):
        if message.metadata is None:
            message.metadata = {}
        message.metadata["_db_id"] = f"msg_{len(self.sessions[session_id].history)}"


class FakeChatHandler:
    def update_session_name_if_needed(self, session, message):
        if not session.name:
            session.name = f"Chat: {message}"


def install_terminal_route_fakes(monkeypatch):
    monkeypatch.setattr("routes.terminal_client_routes.resolve_session_auth", lambda *args, **kwargs: None)
    monkeypatch.setattr("routes.terminal_client_routes._verify_session_owner", lambda *args, **kwargs: None)

    def fake_save_assistant_response(sess, session_manager, session_id, full_response, last_metrics, **kwargs):
        sess.add_message(ChatMessage("assistant", full_response, metadata=last_metrics or {}))
        session_manager.save_sessions()
        return "msg_assistant"

    monkeypatch.setattr("routes.terminal_client_routes.save_assistant_response", fake_save_assistant_response)


def test_terminal_client_chat_run_api_exposes_distinct_run_events_and_stop(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()
    stream_calls = []

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        stream_calls.append({"candidates": candidates, "messages": messages, "kwargs": kwargs})
        yield 'data: {"delta": "real "}\n\n'
        yield 'data: {"delta": "chat"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
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
    assert payload["cursor"]["next"] == "5"
    assert [event["schema"] for event in payload["events"]] == ["ody.event.v1"] * 5
    assert payload["events"][0]["run_id"] == run["run_id"]
    assert payload["events"][0]["session_id"] == "ses-real"
    assert payload["events"][0]["raw"]["transport"] == "sse"
    assert stream_calls
    assert stream_calls[0]["candidates"][0][1] == TEST_MODEL
    assert stream_calls[0]["messages"][-1] == {"role": "user", "content": "hello"}
    assert [message.role for message in manager.sessions["ses-real"].history] == ["user", "assistant"]
    assert manager.sessions["ses-real"].history[-1].content == "real chat"

    stopped = client.post(f"/api/terminal/runs/{run['run_id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["run"]["status"] in {"done", "stopped"}


def test_terminal_client_chat_run_can_create_real_session(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "new session"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    started = client.post(
        "/api/terminal/runs",
        json={
            "kind": "chat",
            "message": "hello",
            "endpoint_url": MODEL_ENDPOINT_URL,
            "model": CREATED_MODEL,
        },
    )

    assert started.status_code == 200
    run = started.json()["run"]
    assert run["session_id"].startswith("ses_")
    assert manager.sessions[run["session_id"]].model == CREATED_MODEL
    events = client.get(f"/api/terminal/runs/{run['run_id']}/events").json()["events"]
    assert events[-1]["kind"] == "run.status"


def test_terminal_client_chat_run_without_runtime_does_not_fake_success():
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()

    app = FastAPI()
    app.include_router(setup_terminal_client_routes())
    client = TestClient(app)

    started = client.post("/api/terminal/runs", json={"kind": "chat", "session_id": "ses-real", "message": "hello"})

    assert started.status_code == 503


def test_terminal_client_chat_run_requires_configured_existing_session(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=FakeSessionManager(), chat_handler=FakeChatHandler()))
    client = TestClient(app)

    started = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-unconfigured", "message": "hello"},
    )

    assert started.status_code == 400
    assert started.json()["detail"] == "No model selected for this chat"


def test_terminal_client_stop_does_not_fake_stopped_when_detached_task_missing(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "waiting"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    started = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
    )
    run_id = started.json()["run"]["run_id"]
    monkeypatch.setattr("src.terminal_client_runs.agent_runs.stop", lambda session_id: False)
    monkeypatch.setattr("src.terminal_client_runs.agent_runs.get_status", lambda session_id: "running")

    stopped = client.post(f"/api/terminal/runs/{run_id}/stop")

    assert stopped.status_code == 200
    payload = stopped.json()
    assert payload["stopped"] is False
    assert payload["run"]["status"] == "running"


def test_terminal_client_chat_run_by_session_status_and_events(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "session"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    started = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
    )
    assert started.status_code == 200

    status = client.get("/api/terminal/runs/by-session/ses-real")
    assert status.status_code == 200
    assert status.json()["run"]["session_id"] == "ses-real"

    events = client.get("/api/terminal/runs/by-session/ses-real/events")
    assert events.status_code == 200
    assert [event["session_id"] for event in events.json()["events"]] == ["ses-real"] * 4


def test_terminal_client_chat_run_by_session_stop(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "session"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)
    monkeypatch.setattr("src.terminal_client_runs.agent_runs.get_status", lambda session_id: "running")
    monkeypatch.setattr("src.terminal_client_runs.agent_runs.stop", lambda session_id: True)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    started = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
    )
    assert started.status_code == 200

    stopped = client.post("/api/terminal/runs/by-session/ses-real/stop")

    assert stopped.status_code == 200
    assert stopped.json()["run"]["session_id"] == "ses-real"


def test_terminal_client_chat_run_by_session_reports_ambiguity(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "session"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)
    monkeypatch.setattr("src.terminal_client_runs.agent_runs.get_status", lambda session_id: "running")

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    for _ in range(2):
        started = client.post(
            "/api/terminal/runs",
            json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
        )
        assert started.status_code == 200

    status = client.get("/api/terminal/runs/by-session/ses-real")

    assert status.status_code == 409
    detail = status.json()["detail"]
    assert detail["code"] == "ambiguous_run"
    assert len(detail["choices"]) == 2


def test_terminal_client_chat_run_by_session_uses_latest_completed_run(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "session"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    first = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "first"},
    ).json()["run"]["run_id"]
    assert client.get(f"/api/terminal/runs/{first}/events").status_code == 200
    second = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "second"},
    ).json()["run"]["run_id"]
    assert client.get(f"/api/terminal/runs/{second}/events").status_code == 200

    status = client.get("/api/terminal/runs/by-session/ses-real")

    assert status.status_code == 200
    assert status.json()["run"]["run_id"] == second
