from types import SimpleNamespace
import asyncio

from src import execution_service


def test_ensure_running_reuses_live_worker(monkeypatch):
    state = {"port": 7123, "secret": "secret", "pid": 42}
    monkeypatch.setattr(execution_service, "current_state", lambda require_healthy=True: dict(state))

    result = execution_service.ensure_running()

    assert result == {**state, "reused": True}


def test_worker_health_requires_secret(monkeypatch):
    monkeypatch.setenv(execution_service.WORKER_ENV, "1")
    monkeypatch.setenv(execution_service.SECRET_ENV, "correct")
    router = execution_service.router()
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/execution/health")

    request = SimpleNamespace(headers={execution_service.HEADER: "wrong"})
    import pytest
    with pytest.raises(Exception) as exc:
        import asyncio
        asyncio.run(endpoint(request))
    assert getattr(exc.value, "status_code", None) == 404


def test_proxy_replaces_spoofed_internal_identity(monkeypatch):
    captured = {}

    class Upstream:
        status_code = 200
        headers = {"content-type": "application/json", "x-odysseus-run-id": "compare:123"}
        async def aread(self): return b"{}"
        async def aclose(self): pass

    class Client:
        def __init__(self, timeout=None): pass
        def build_request(self, method, url, headers, content):
            captured.update(headers)
            return object()
        async def send(self, built, stream=False): return Upstream()
        async def aclose(self): pass

    monkeypatch.setattr(execution_service, "current_state", lambda require_healthy=True: {"port": 7001, "secret": "real"})
    monkeypatch.setattr(execution_service.httpx, "AsyncClient", Client)
    request = SimpleNamespace(
        method="POST",
        headers={execution_service.OWNER_HEADER: "attacker"},
        state=SimpleNamespace(current_user="alice", api_token=False),
        url=SimpleNamespace(query=""),
        body=lambda: None,
    )
    async def body(): return b"{}"
    request.body = body

    response = asyncio.run(execution_service.proxy(request, "/api/chat_stream", streaming=False))

    assert captured[execution_service.HEADER] == "real"
    assert captured[execution_service.OWNER_HEADER] == "alice"
    assert response.headers["x-odysseus-run-id"] == "compare:123"


def test_disconnect_proxy_streams_cancels_only_registered_relays():
    async def scenario():
        relay = asyncio.create_task(asyncio.sleep(60))
        unrelated = asyncio.create_task(asyncio.sleep(0))
        execution_service._PROXY_STREAM_TASKS.add(relay)
        assert execution_service.disconnect_proxy_streams() == 1
        await asyncio.gather(relay, return_exceptions=True)
        await unrelated
        assert relay.cancelled()
        execution_service._PROXY_STREAM_TASKS.discard(relay)

    asyncio.run(scenario())
