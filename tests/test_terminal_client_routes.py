from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core.models import ChatMessage, Session
from routes.terminal_client_routes import setup_terminal_client_routes
from src import agent_runs, terminal_client_runs
from src.constants import TERMINAL_CLIENT_RUNS_FILE

MODEL_ENDPOINT_URL = "http://model.local/v1/chat/completions"
TEST_MODEL = "test-model"
CREATED_MODEL = "created-model"


@pytest.fixture(autouse=True)
def isolate_terminal_client_run_store(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal_client_runs, "_TERMINAL_RUN_STORE", tmp_path / Path(TERMINAL_CLIENT_RUNS_FILE).name)
    terminal_client_runs.reset_for_tests(clear_persisted=True)
    yield
    terminal_client_runs.reset_for_tests(clear_persisted=True)


def reset_terminal_run_process_memory() -> None:
    terminal_client_runs._RUNS.clear()
    terminal_client_runs._SESSION_ACTIVE.clear()
    terminal_client_runs._LIVE_RUN_BY_SESSION.clear()
    terminal_client_runs._LOADED = False
    agent_runs.reset_for_tests()


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


def install_terminal_route_fakes(monkeypatch, *, patch_owner=True):
    monkeypatch.setattr("routes.terminal_client_routes.resolve_session_auth", lambda *args, **kwargs: None)
    if patch_owner:
        monkeypatch.setattr("routes.terminal_client_routes._verify_session_owner", lambda *args, **kwargs: None)

    def fake_save_assistant_response(sess, session_manager, session_id, full_response, last_metrics, **kwargs):
        sess.add_message(ChatMessage("assistant", full_response, metadata=last_metrics or {}))
        session_manager.save_sessions()
        return "msg_assistant"

    monkeypatch.setattr("routes.terminal_client_routes.save_assistant_response", fake_save_assistant_response)


@pytest.mark.asyncio
async def test_terminal_run_event_source_matches_agent_run_kind():
    async def agent_stream():
        yield 'data: {"type": "agent_prep", "data": {"prompt_build": 0.01}}\n\n'
        yield "data: [DONE]\n\n"

    created = terminal_client_runs.create_run(
        kind="agent",
        session_id="ses-agent",
        message="work",
        stream=agent_stream(),
    )

    attached = await terminal_client_runs.attach_run(run_id=created["run"]["run_id"])

    assert attached["run"]["kind"] == "agent"
    assert {event["source"] for event in attached["events"]} == {"agent"}
    assert attached["events"][0]["kind"] == "heartbeat"
    assert attached["events"][0]["payload"]["type"] == "agent_prep"


def test_terminal_client_agent_run_uses_shared_context_policy_and_real_run_api(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()
    install_terminal_route_fakes(monkeypatch)
    monkeypatch.setattr("routes.terminal_client_routes.effective_user", lambda request: "alice")

    context_calls = []
    stream_calls = []

    async def fake_build_chat_context(sess, request, chat_handler, chat_processor, message, session_id, **kwargs):
        context_calls.append(kwargs)
        sess.add_message(ChatMessage("user", message))
        return SimpleNamespace(
            messages=sess.get_context_messages(),
            context_length=8192,
            preset=SimpleNamespace(temperature=0.2, max_tokens=321, character_name=None),
            uploaded_files=[],
            rag_sources=[],
            used_memories=[],
        )

    async def fake_stream_agent_loop(endpoint_url, model, messages, **kwargs):
        stream_calls.append({"endpoint_url": endpoint_url, "model": model, "messages": messages, "kwargs": kwargs})
        yield 'data: {"type": "tool_start", "tool": "read_file"}\n\n'
        yield 'data: {"delta": "agent result"}\n\n'
        yield 'data: {"type": "metrics", "data": {"tool_events": [{"tool": "read_file"}]}}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.build_chat_context", fake_build_chat_context)
    monkeypatch.setattr("routes.terminal_client_routes.stream_agent_loop", fake_stream_agent_loop)
    monkeypatch.setattr(
        "routes.terminal_client_routes.resolve_agent_access",
        lambda request, user: SimpleNamespace(
            agent_allowed=True,
            research_allowed=True,
            disabled_tools=frozenset({"send_email"}),
        ),
    )

    app = FastAPI()
    app.include_router(
        setup_terminal_client_routes(
            session_manager=manager,
            chat_handler=FakeChatHandler(),
            chat_processor=object(),
        )
    )
    client = TestClient(app)

    started = client.post(
        "/api/terminal/runs",
        json={"kind": "agent", "session_id": "ses-real", "message": "inspect it"},
    )
    assert started.status_code == 200
    run = started.json()["run"]
    assert run["kind"] == "agent"

    attached = client.get(f"/api/terminal/runs/{run['run_id']}/events").json()
    assert {event["source"] for event in attached["events"]} == {"agent"}
    assert context_calls[0]["agent_mode"] is True
    assert stream_calls[0]["kwargs"]["owner"] == "alice"
    assert stream_calls[0]["kwargs"]["disabled_tools"] == {"send_email"}
    assert [message.role for message in manager.sessions["ses-real"].history] == ["user", "assistant"]
    assert manager.sessions["ses-real"].history[-1].content == "agent result"


def test_terminal_client_agent_run_rejects_owner_without_agent_privilege(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()
    install_terminal_route_fakes(monkeypatch)
    monkeypatch.setattr("routes.terminal_client_routes.effective_user", lambda request: "alice")
    monkeypatch.setattr(
        "routes.terminal_client_routes.resolve_agent_access",
        lambda request, user: SimpleNamespace(
            agent_allowed=False,
            research_allowed=True,
            disabled_tools=frozenset(),
        ),
    )

    app = FastAPI()
    app.include_router(
        setup_terminal_client_routes(
            session_manager=manager,
            chat_handler=FakeChatHandler(),
            chat_processor=object(),
        )
    )

    response = TestClient(app).post(
        "/api/terminal/runs",
        json={"kind": "agent", "session_id": "ses-real", "message": "inspect it"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Agent execution is not permitted for this user"
    assert manager.sessions["ses-real"].history == []


@pytest.mark.parametrize(
    ("status_code", "detail"),
    [
        (403, "Your account is not allowed to use model 'test-model'."),
        (429, "Daily message limit reached (1). Try again in 24 hours."),
    ],
)
def test_terminal_client_agent_run_enforces_shared_model_and_quota_gate(
    monkeypatch,
    status_code,
    detail,
):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()
    install_terminal_route_fakes(monkeypatch)
    monkeypatch.setattr("routes.terminal_client_routes.effective_user", lambda request: "alice")
    monkeypatch.setattr(
        "routes.terminal_client_routes.resolve_agent_access",
        lambda request, user: SimpleNamespace(
            agent_allowed=True,
            research_allowed=True,
            disabled_tools=frozenset(),
        ),
    )

    def reject_agent_run(request, sess):
        raise HTTPException(status_code, detail)

    monkeypatch.setattr("routes.terminal_client_routes._enforce_chat_privileges", reject_agent_run)

    app = FastAPI()
    app.include_router(
        setup_terminal_client_routes(
            session_manager=manager,
            chat_handler=FakeChatHandler(),
            chat_processor=object(),
        )
    )

    response = TestClient(app).post(
        "/api/terminal/runs",
        json={"kind": "agent", "session_id": "ses-real", "message": "inspect it"},
    )

    assert response.status_code == status_code
    assert response.json()["detail"] == detail
    assert manager.sessions["ses-real"].history == []


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
    assert "raw" not in payload["events"][0]
    raw_events = client.get(f"/api/terminal/runs/{run['run_id']}/events", params={"include_raw": True}).json()
    assert raw_events["events"][0]["raw"]["transport"] == "sse"
    assert stream_calls
    assert stream_calls[0]["candidates"][0][1] == TEST_MODEL
    assert stream_calls[0]["messages"][-1] == {"role": "user", "content": "hello"}
    assert [message.role for message in manager.sessions["ses-real"].history] == ["user", "assistant"]
    assert manager.sessions["ses-real"].history[-1].content == "real chat"

    stopped = client.post(f"/api/terminal/runs/{run['run_id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["run"]["status"] in {"done", "stopped"}


def test_terminal_client_event_query_filters_real_run_events(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "real event"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    run = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
    ).json()["run"]

    response = client.get(
        "/api/terminal/events",
        params={"run_id": run["run_id"], "source": "chat", "level": "info", "cursor": 1, "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["run_id"] == run["run_id"]
    assert payload["cursor"] == {"after": "1", "next": "2", "count": 1}
    assert [event["seq"] for event in payload["events"]] == [2]
    assert {event["source"] for event in payload["events"]} == {"chat"}
    assert {event["level"] for event in payload["events"]} == {"info"}

    reconnected = client.get(
        "/api/terminal/events",
        params={"run_id": run["run_id"], "cursor": payload["cursor"]["next"], "limit": 1},
    ).json()
    assert reconnected["cursor"] == {"after": "2", "next": "3", "count": 1}
    assert [event["seq"] for event in reconnected["events"]] == [3]

    by_session = client.get(
        "/api/terminal/events",
        params={"session_id": "ses-real", "kind": "run.status", "cursor": 1},
    )
    assert by_session.status_code == 200
    filtered = by_session.json()
    assert filtered["cursor"] == {"after": "1", "next": "4", "count": 1}
    assert [(event["seq"], event["kind"]) for event in filtered["events"]] == [(4, "run.status")]


def test_terminal_client_event_stream_replays_jsonl_after_cursor(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "streamed"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)
    run_id = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
    ).json()["run"]["run_id"]

    with client.stream(
        "GET",
        "/api/terminal/events/stream",
        params={"run_id": run_id, "cursor": 1, "source": "chat"},
    ) as response:
        lines = [json.loads(line) for line in response.iter_lines() if line]

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert [event["seq"] for event in lines] == [2, 3, 4]
    assert all(event["run_id"] == run_id for event in lines)
    assert all("raw" not in event for event in lines)


def test_terminal_client_event_stream_yields_before_active_run_finishes():
    async def scenario():
        release = asyncio.Event()

        async def slow_stream():
            yield 'data: {"delta": "first"}\n\n'
            await release.wait()
            yield 'data: {"delta": "second"}\n\n'
            yield "data: [DONE]\n\n"

        created = terminal_client_runs.create_chat_run(session_id="ses-live", message="hello", stream=slow_stream())
        stream = terminal_client_runs.stream_events(run_id=created["run"]["run_id"], cursor=0)
        first = await asyncio.wait_for(anext(stream), timeout=0.2)
        pending_second = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.02)
        assert not pending_second.done()
        release.set()
        second = await asyncio.wait_for(pending_second, timeout=0.2)
        await stream.aclose()
        return first, second

    first, second = asyncio.run(scenario())

    assert first is not None and first["payload"] == {"delta": "first"}
    assert second is not None and second["payload"] == {"delta": "second"}


def test_terminal_client_http_stream_delivers_before_run_finishes(monkeypatch):
    import uvicorn

    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()
    release = threading.Event()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "first"}\n\n'
        while not release.is_set():
            await asyncio.sleep(0.01)
        yield 'data: {"delta": "second"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)
    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    server_thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 2
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)

    try:
        started_request = UrlRequest(
            f"http://127.0.0.1:{port}/api/terminal/runs",
            data=json.dumps({"kind": "chat", "session_id": "ses-real", "message": "hello"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(started_request, timeout=2) as started_response:
            run_id = json.loads(started_response.read())["run"]["run_id"]

        with urlopen(f"http://127.0.0.1:{port}/api/terminal/events/stream?run_id={run_id}", timeout=2) as response:
            first = json.loads(response.readline())
            assert first["payload"] == {"delta": "first"}
            assert release.is_set() is False
            release.set()
            second = json.loads(response.readline())
            assert second["payload"] == {"delta": "second"}
    finally:
        release.set()
        server.should_exit = True
        server_thread.join(timeout=2)
        listener.close()


def test_terminal_client_event_query_requires_run_or_session():
    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=FakeSessionManager(), chat_handler=FakeChatHandler()))
    client = TestClient(app)

    response = client.get("/api/terminal/events")

    assert response.status_code == 400
    assert response.json()["detail"] == "Terminal event query requires run_id or session_id"


def test_terminal_client_event_query_does_not_wait_for_active_run():
    async def scenario():
        draining = asyncio.Event()
        release = asyncio.Event()

        async def slow_stream():
            yield 'data: {"delta": "first"}\n\n'
            draining.set()
            await release.wait()
            yield "data: [DONE]\n\n"

        created = terminal_client_runs.create_chat_run(session_id="ses-slow", message="hello", stream=slow_stream())
        await asyncio.wait_for(draining.wait(), timeout=0.2)
        payload = await asyncio.wait_for(
            terminal_client_runs.query_events(run_id=created["run"]["run_id"], limit=1),
            timeout=0.2,
        )
        release.set()
        await asyncio.sleep(0)
        return payload

    payload = asyncio.run(scenario())

    assert payload["run"]["status"] == "running"
    assert payload["cursor"] == {"after": None, "next": "1", "count": 1}
    assert payload["events"][0]["payload"] == {"delta": "first"}


def test_terminal_client_events_remain_bound_to_distinct_runs_on_one_session():
    async def scenario():
        async def stream(text):
            yield f'data: {{"delta": "{text}"}}\n\n'
            yield "data: [DONE]\n\n"

        first = terminal_client_runs.create_chat_run(session_id="ses-shared", message="first", stream=stream("first"))
        await asyncio.sleep(0.01)
        second = terminal_client_runs.create_chat_run(session_id="ses-shared", message="second", stream=stream("second"))
        await asyncio.sleep(0.01)
        first_events = await terminal_client_runs.query_events(run_id=first["run"]["run_id"])
        second_events = await terminal_client_runs.query_events(run_id=second["run"]["run_id"])
        return first_events, second_events

    first_events, second_events = asyncio.run(scenario())

    assert first_events["events"][0]["run_id"] != second_events["events"][0]["run_id"]
    assert first_events["events"][0]["payload"] == {"delta": "first"}
    assert second_events["events"][0]["payload"] == {"delta": "second"}

    first_attach = asyncio.run(terminal_client_runs.attach_run(run_id=first_events["run"]["run_id"]))
    assert first_attach["events"][0]["payload"] == {"delta": "first"}


def test_terminal_client_events_persist_without_a_reader():
    async def complete_unobserved_run():
        async def stream():
            yield 'data: {"delta": "durable"}\n\n'
            yield "data: [DONE]\n\n"

        created = terminal_client_runs.create_chat_run(session_id="ses-durable", message="hello", stream=stream())
        await asyncio.sleep(0.01)
        return created["run"]["run_id"]

    run_id = asyncio.run(complete_unobserved_run())
    reset_terminal_run_process_memory()
    payload = asyncio.run(terminal_client_runs.query_events(run_id=run_id))

    assert payload["events"][0]["payload"] == {"delta": "durable"}
    assert payload["cursor"]["next"] == "2"


def test_terminal_client_event_query_enforces_owner_and_raw_scope(monkeypatch):
    manager = FakeSessionManager()
    manager.sessions["ses-real"].owner = "alice"
    token = {"owner": "alice", "scopes": ["run:start", "event:read"]}

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "private"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)

    def verify_owner(request, session_id, session_manager):
        session = session_manager.sessions[session_id]
        if session.owner != request.state.api_token_owner:
            raise HTTPException(404, f"Session {session_id} not found")

    monkeypatch.setattr("routes.terminal_client_routes._verify_session_owner", verify_owner)

    app = FastAPI()

    @app.middleware("http")
    async def fake_token(request, call_next):
        request.state.api_token = True
        request.state.api_token_owner = token["owner"]
        request.state.api_token_scopes = token["scopes"]
        request.state.current_user = "api"
        return await call_next(request)

    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)
    run_id = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
    ).json()["run"]["run_id"]

    bounded = client.get("/api/terminal/events", params={"run_id": run_id})
    assert bounded.status_code == 200
    assert bounded.json()["events"]
    assert all("raw" not in event for event in bounded.json()["events"])
    streamed = client.get("/api/terminal/events/stream", params={"run_id": run_id})
    streamed_events = [json.loads(line) for line in streamed.text.splitlines()]
    assert streamed.status_code == 200
    assert streamed_events
    assert all("raw" not in event for event in streamed_events)

    raw_denied = client.get("/api/terminal/events", params={"run_id": run_id, "include_raw": True})
    assert raw_denied.status_code == 403
    assert "event:raw" in raw_denied.json()["detail"]
    stream_raw_denied = client.get("/api/terminal/events/stream", params={"run_id": run_id, "include_raw": True})
    assert stream_raw_denied.status_code == 403
    assert "event:raw" in stream_raw_denied.json()["detail"]

    token["scopes"] = ["event:raw"]
    raw_allowed = client.get("/api/terminal/events", params={"run_id": run_id, "include_raw": True})
    assert raw_allowed.status_code == 200
    assert raw_allowed.json()["events"][0]["raw"]["transport"] == "sse"
    stream_raw_allowed = client.get("/api/terminal/events/stream", params={"run_id": run_id, "include_raw": True})
    assert json.loads(stream_raw_allowed.text.splitlines()[0])["raw"]["transport"] == "sse"

    token["scopes"] = ["event:read"]
    assert client.get("/api/terminal/runs").status_code == 403
    assert client.get(f"/api/terminal/runs/{run_id}").status_code == 403
    assert client.post(f"/api/terminal/runs/{run_id}/stop").status_code == 403

    token["scopes"] = ["run:read"]
    assert client.get("/api/terminal/runs").status_code == 200
    assert client.get(f"/api/terminal/runs/{run_id}").status_code == 200

    token["scopes"] = ["event:raw"]
    token["owner"] = "bob"
    wrong_owner = client.get("/api/terminal/events", params={"run_id": run_id})
    assert wrong_owner.status_code == 404
    wrong_owner_stream = client.get("/api/terminal/events/stream", params={"run_id": run_id})
    assert wrong_owner_stream.status_code == 404


def test_terminal_client_chat_run_can_create_real_session(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "new session"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(
        "src.endpoint_resolver.resolve_endpoint",
        lambda *args, **kwargs: pytest.fail("explicit endpoint/model must not resolve the configured default"),
    )
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


def test_terminal_client_chat_run_resolves_owner_default_model(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()
    resolved_for = []

    def fake_resolve_endpoint(prefix, owner=None):
        resolved_for.append((prefix, owner))
        return MODEL_ENDPOINT_URL, CREATED_MODEL, {"Authorization": "Bearer resolved"}

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "default model"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr("routes.terminal_client_routes.effective_user", lambda request: "alice")
    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    started = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "message": "hello"},
    )

    assert started.status_code == 200
    session = manager.sessions[started.json()["run"]["session_id"]]
    assert resolved_for == [("default", "alice")]
    assert session.endpoint_url == MODEL_ENDPOINT_URL
    assert session.model == CREATED_MODEL
    assert session.owner == "alice"
    assert session.headers == {"Authorization": "Bearer resolved"}


def test_terminal_client_chat_run_reports_missing_default_model(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()
    initial_session_ids = set(manager.sessions)
    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", lambda prefix, owner=None: (None, None, None))
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    started = client.post("/api/terminal/runs", json={"kind": "chat", "message": "hello"})

    assert started.status_code == 400
    assert started.json()["detail"] == "No default chat model is configured; pass endpoint_url and model"
    assert set(manager.sessions) == initial_session_ids


def test_terminal_client_chat_run_without_runtime_does_not_fake_success(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")

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


def test_terminal_client_chat_run_stop_waits_for_terminal_status(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()
    statuses = iter(["running", "running", "stopped", "stopped"])

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "session"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)
    monkeypatch.setattr("src.terminal_client_runs.agent_runs.get_status", lambda session_id: next(statuses, "stopped"))
    monkeypatch.setattr("src.terminal_client_runs.agent_runs.stop", lambda session_id: True)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    run_id = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
    ).json()["run"]["run_id"]

    stopped = client.post(f"/api/terminal/runs/{run_id}/stop")

    assert stopped.status_code == 200
    assert stopped.json()["stopped"] is True
    assert stopped.json()["run"]["status"] == "stopped"


def test_terminal_client_chat_run_by_session_selects_only_current_execution(monkeypatch):
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

    run_ids = []
    for _ in range(2):
        started = client.post(
            "/api/terminal/runs",
            json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
        )
        assert started.status_code == 200
        run_ids.append(started.json()["run"]["run_id"])

    status = client.get("/api/terminal/runs/by-session/ses-real")

    assert status.status_code == 200
    assert status.json()["run"]["run_id"] == run_ids[-1]
    previous = client.get(f"/api/terminal/runs/{run_ids[0]}").json()["run"]
    assert previous["status"] in {"done", "interrupted"}


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

    reset_terminal_run_process_memory()

    restored_status = client.get("/api/terminal/runs/by-session/ses-real")

    assert restored_status.status_code == 200
    assert restored_status.json()["run"]["run_id"] == second


def test_terminal_client_chat_run_registry_survives_process_memory_reset(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    manager = FakeSessionManager()

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        yield 'data: {"delta": "persisted"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("routes.terminal_client_routes.stream_llm_with_fallback", fake_stream_llm_with_fallback)
    install_terminal_route_fakes(monkeypatch)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=manager, chat_handler=FakeChatHandler()))
    client = TestClient(app)

    run = client.post(
        "/api/terminal/runs",
        json={"kind": "chat", "session_id": "ses-real", "message": "hello"},
    ).json()["run"]
    assert client.get(f"/api/terminal/runs/{run['run_id']}/events").status_code == 200
    assert terminal_client_runs._TERMINAL_RUN_STORE.exists()

    reset_terminal_run_process_memory()

    status = client.get(f"/api/terminal/runs/{run['run_id']}")
    listed = client.get("/api/terminal/runs", params={"kind": "chat"})
    by_session = client.get("/api/terminal/runs/by-session/ses-real")
    events = client.get(f"/api/terminal/runs/{run['run_id']}/events", params={"cursor": 1})

    assert status.status_code == 200
    assert status.json()["run"]["run_id"] == run["run_id"]
    assert status.json()["run"]["session_id"] == "ses-real"
    assert status.json()["run"]["events_available"] is True
    assert status.json()["run"]["last_activity"]
    assert listed.status_code == 200
    assert listed.json()["runs"][0]["run_id"] == run["run_id"]
    assert by_session.status_code == 200
    assert by_session.json()["run"]["run_id"] == run["run_id"]
    assert events.status_code == 200
    assert [event["schema"] for event in events.json()["events"]] == ["ody.event.v1"] * 3
    assert events.json()["cursor"]["after"] == "1"


def test_terminal_client_reloaded_running_run_without_execution_is_interrupted(monkeypatch):
    terminal_client_runs.reset_for_tests()
    agent_runs.reset_for_tests()
    monkeypatch.setenv("AUTH_ENABLED", "false")
    run = terminal_client_runs.TerminalRun(
        run_id="run_stale_active",
        session_id="ses-real",
        status="running",
    )
    terminal_client_runs._RUNS[run.run_id] = run
    terminal_client_runs._SESSION_ACTIVE.setdefault(run.session_id, []).append(run.run_id)
    terminal_client_runs._save_persisted_runs()

    reset_terminal_run_process_memory()
    monkeypatch.setattr("src.terminal_client_runs.agent_runs.get_persisted_status", lambda session_id: None)
    monkeypatch.setattr("routes.terminal_client_routes._verify_session_owner", lambda *args, **kwargs: None)

    app = FastAPI()
    app.include_router(setup_terminal_client_routes(session_manager=FakeSessionManager(), chat_handler=FakeChatHandler()))
    client = TestClient(app)

    status = client.get("/api/terminal/runs/run_stale_active")

    assert status.status_code == 200
    assert status.json()["run"]["status"] == "interrupted"
    assert status.json()["run"]["finished_at"]
