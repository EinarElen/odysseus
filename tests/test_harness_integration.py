import types

import pytest
from fastapi import HTTPException

from core.models import Session
from routes.chat_routes import _clear_orphaned_session_endpoint
from routes.session_routes import _reject_raw_endpoint_url_for_non_admin, _validate_harness_id
from src.harness import get_harness_adapter, harness_config_from_session, is_harness_session, list_harness_capabilities
from src.harness.sdk import (
    HarnessControlBroker,
    HarnessControlRequest,
    HarnessControlResult,
    HarnessBridgeProcess,
    HarnessCapabilities,
    HarnessToolBroker,
    HarnessToolCall,
    HarnessToolDefinition,
    HarnessToolResult,
    OdysseusToolBroker,
    SdkHarnessAdapter,
)
from src.provider_options import sanitize_provider_options


def test_harness_provider_options_are_sanitized_and_preserved():
    clean = sanitize_provider_options(
        "harness://pi",
        "codex",
        {
            "harness": {
                "id": "pi",
                "mode": "bridged",
                "workspace": "/tmp/project",
                "model_provider": "openai-codex-responses",
                "model": "codex-mini",
                "thinking_level": "high",
                "verbosity": "debug",
                "accept_harness_tools": True,
                "unexpected": "drop-me",
            },
            "service_tier": "fast",
        },
    )

    assert clean == {
        "harness": {
            "id": "pi",
            "mode": "bridged",
            "workspace": "/tmp/project",
            "model_provider": "openai-codex-responses",
            "model": "codex-mini",
            "thinking_level": "high",
            "verbosity": "debug",
            "accept_harness_tools": True,
        }
    }


def test_unknown_harness_provider_options_are_dropped():
    assert sanitize_provider_options(
        "harness://other",
        "model",
        {"harness": {"id": "other", "mode": "bridged"}},
    ) == {}
    with pytest.raises(HTTPException):
        _validate_harness_id("other")


def test_session_harness_detection_uses_provider_options():
    session = Session(
        id="s1",
        name="Harness",
        endpoint_url="harness://pi",
        model="codex-mini",
        provider_options={"harness": {"id": "pi", "mode": "observe"}},
    )

    assert is_harness_session(session) is True
    assert harness_config_from_session(session) == {"id": "pi", "mode": "observe"}


def test_harness_session_is_not_cleared_as_orphaned_model_endpoint():
    session = Session(
        id="s1",
        name="Harness",
        endpoint_url="harness://pi",
        model="codex",
        provider_options={"harness": {"id": "pi", "mode": "bridged"}},
    )

    assert _clear_orphaned_session_endpoint(session) is False
    assert session.endpoint_url == "harness://pi"
    assert session.model == "codex"


def test_harness_pseudo_endpoint_does_not_require_registered_endpoint():
    request = types.SimpleNamespace(
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                auth_manager=types.SimpleNamespace(is_admin=lambda _user: False)
            )
        )
    )

    _reject_raw_endpoint_url_for_non_admin(request, "alice", None, "harness://pi")

    with pytest.raises(HTTPException):
        _reject_raw_endpoint_url_for_non_admin(request, "alice", None, "https://api.example.com/v1")


def test_pi_capabilities_publish_bidirectional_integration_contract():
    caps = {item["id"]: item for item in list_harness_capabilities()}

    assert caps["pi"]["session"]["resume"] is True
    assert caps["pi"]["tools"]["list_native"] is True
    assert caps["pi"]["tools"]["provide_external"] is True
    assert caps["pi"]["tools"]["intercept_native"] == "bridge"
    assert caps["pi"]["tools"]["disable_native"] is True
    assert caps["pi"]["control_flow"]["cooperative_turns"] is True
    assert caps["pi"]["control_flow"]["yield_control"] is True
    assert caps["pi"]["control_flow"]["resume_with_result"] is True
    assert "bridged" in caps["pi"]["modes"]


def test_pi_adapter_uses_generic_sdk_adapter():
    adapter = get_harness_adapter("pi")

    assert isinstance(adapter, SdkHarnessAdapter)
    assert adapter.bridge_command[-1].endswith("pi_sdk_bridge.mjs")
    assert callable(adapter.command)


def test_harness_sdk_tool_definition_from_openai_schema():
    definition = HarnessToolDefinition.from_openai_function_schema({
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    })

    assert definition.to_dict() == {
        "name": "read_file",
        "label": "read_file",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    }


def test_odysseus_tool_broker_exports_selected_tools():
    tools = {tool.name: tool for tool in OdysseusToolBroker(["read_file", "edit_file"]).list_tools()}

    assert sorted(tools) == ["edit_file", "read_file"]
    assert tools["read_file"].parameters["type"] == "object"


@pytest.mark.asyncio
async def test_harness_bridge_process_buffers_unrelated_events():
    process = HarnessBridgeProcess("s1", ["unused"])
    process.pending.append({"type": "event", "data": {"n": 1}})
    await process.queue.put({"type": "response", "id": "r1", "success": True})

    assert await process.next_message() == {"type": "event", "data": {"n": 1}}
    assert await process.next_message() == {"type": "response", "id": "r1", "success": True}


@pytest.mark.asyncio
async def test_harness_bridge_process_request_buffers_unrelated_queue_events(monkeypatch):
    process = HarnessBridgeProcess("s1", ["unused"])
    sent = []

    async def _send_json(payload):
        sent.append(payload)
        await process.queue.put({"type": "event", "data": {"before": True}})
        await process.queue.put({"type": "response", "id": payload["id"], "success": True, "data": {"ok": True}})

    monkeypatch.setattr(process, "send_json", _send_json)

    assert await process.request("start_session") == {"ok": True}
    assert sent[0]["type"] == "start_session"
    assert await process.next_message() == {"type": "event", "data": {"before": True}}


@pytest.mark.asyncio
async def test_harness_bridge_process_request_forwards_startup_events(monkeypatch):
    process = HarnessBridgeProcess("s1", ["unused"])
    sent = []
    forwarded = []

    async def _send_json(payload):
        sent.append(payload)
        await process.queue.put({
            "type": "harness_status",
            "data": {"phase": "sdk_loading", "label": "Loading SDK", "status": "running"},
        })
        await process.queue.put({"type": "response", "id": payload["id"], "success": True, "data": {"ok": True}})

    async def _event_cb(raw):
        forwarded.append(raw)

    monkeypatch.setattr(process, "send_json", _send_json)

    assert await process.request_with_events("start_session", event_cb=_event_cb) == {"ok": True}
    assert sent[0]["type"] == "start_session"
    assert forwarded == [{
        "type": "harness_status",
        "data": {"phase": "sdk_loading", "label": "Loading SDK", "status": "running"},
    }]
    assert process.pending == []


def test_sdk_harness_adapter_normalizes_generic_events():
    adapter = SdkHarnessAdapter(
        adapter_id="example",
        label="Example",
        command=["node", "bridge.mjs"],
        capabilities=HarnessCapabilities(id="example", label="Example"),
    )

    assert adapter.normalize_event({"type": "text_delta", "data": {"text": "x"}}).type == "text_delta"
    assert adapter.normalize_event({
        "type": "harness_status",
        "data": {"phase": "session_starting", "label": "Starting", "status": "running"},
    }).data["phase"] == "session_starting"
    assert adapter.normalize_event({"type": "event", "event_type": "done"}).type == "done"
    assert adapter.normalize_event({"type": "error", "error": "bad"}).data == {"message": "bad"}


class _FakeBroker(HarnessToolBroker):
    def list_tools(self):
        return [HarnessToolDefinition(name="fake_tool", description="Fake")]

    async def execute(self, call: HarnessToolCall, **kwargs):
        assert call.name == "fake_tool"
        assert call.arguments == {"value": 7}
        assert kwargs["session_id"] == "s1"
        assert kwargs["workspace"] == "/tmp/work"
        return HarnessToolResult("ok", {"value": 7})


class _FakeControlBroker(HarnessControlBroker):
    async def handle(self, request: HarnessControlRequest, **kwargs):
        assert request.kind == "approval"
        assert request.blocking is True
        assert kwargs["session_id"] == "s1"
        return HarnessControlResult(request.id, request.kind, "approved", {"by": "test"})


class _FakeProcess:
    def __init__(self):
        self.proc = types.SimpleNamespace(returncode=None)
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)

    async def request(self, command_type, payload=None):
        self.sent.append({"type": command_type, **(payload or {})})
        return {"ok": True, "command": payload.get("command") if payload else ""}

    async def request_with_events(self, command_type, payload=None, event_cb=None):
        return await self.request(command_type, payload)


@pytest.mark.asyncio
async def test_sdk_harness_adapter_command_uses_bridge_request():
    adapter = SdkHarnessAdapter(
        adapter_id="example",
        label="Example",
        command=["node", "bridge.mjs"],
        capabilities=HarnessCapabilities(id="example", label="Example"),
    )
    process = _FakeProcess()
    adapter._processes["s1"] = process
    ref = types.SimpleNamespace(
        harness_session_id="pi-session",
        odysseus_session_id="s1",
        workspace="/tmp/work",
        config={},
    )

    assert await adapter.command(ref, "get_state", {}) == {"ok": True, "command": "get_state"}
    assert process.sent == [{"type": "command", "command": "get_state", "payload": {}}]


@pytest.mark.asyncio
async def test_sdk_harness_adapter_reuses_started_session():
    adapter = SdkHarnessAdapter(
        adapter_id="example",
        label="Example",
        command=["node", "bridge.mjs"],
        capabilities=HarnessCapabilities(id="example", label="Example"),
    )
    process = _FakeProcess()
    adapter._processes["s1"] = process

    first = await adapter.start({"odysseus_session_id": "s1", "workspace": "/tmp/work", "mode": "observe"})
    second = await adapter.start({"odysseus_session_id": "s1", "workspace": "/tmp/work", "mode": "observe"})

    assert first.harness_session_id == "s1"
    assert second.harness_session_id == "s1"
    assert [item["type"] for item in process.sent] == ["start_session"]


@pytest.mark.asyncio
async def test_sdk_harness_adapter_brokers_external_tool_call():
    adapter = SdkHarnessAdapter(
        adapter_id="example",
        label="Example",
        command=["node", "bridge.mjs"],
        capabilities=HarnessCapabilities(id="example", label="Example"),
        tool_broker_factory=lambda _config: _FakeBroker(),
    )
    process = _FakeProcess()
    adapter._processes["s1"] = process
    adapter._brokers["s1"] = _FakeBroker()
    ref = types.SimpleNamespace(
        harness_session_id="s1",
        odysseus_session_id="s1",
        workspace="/tmp/work",
        config={},
    )

    events = [
        event async for event in adapter._handle_tool_call(
            ref,
            {"id": "req1", "type": "tool_call", "toolCallId": "tc1", "name": "fake_tool", "arguments": {"value": 7}},
        )
    ]

    assert [event.type for event in events] == ["tool_start", "tool_end"]
    assert process.sent == [{
        "id": "req1",
        "type": "tool_result",
        "toolCallId": "tc1",
        "result": {"content": "ok", "details": {"value": 7}, "isError": False},
    }]


@pytest.mark.asyncio
async def test_sdk_harness_adapter_handles_cooperative_control_request():
    adapter = SdkHarnessAdapter(
        adapter_id="example",
        label="Example",
        command=["node", "bridge.mjs"],
        capabilities=HarnessCapabilities(id="example", label="Example"),
        control_broker_factory=lambda _config: _FakeControlBroker(),
    )
    process = _FakeProcess()
    adapter._processes["s1"] = process
    adapter._control_brokers["s1"] = _FakeControlBroker()
    ref = types.SimpleNamespace(
        harness_session_id="pi-session",
        odysseus_session_id="s1",
        workspace="/tmp/work",
        config={},
    )

    events = [
        event async for event in adapter._handle_control_request(
            ref,
            {"id": "req2", "type": "approval_request", "blocking": True, "data": {"message": "Allow edit?"}},
        )
    ]

    assert [event.type for event in events] == ["control_request", "control_result"]
    assert events[0].data["kind"] == "approval"
    assert events[1].data["status"] == "approved"
    assert process.sent == [{
        "id": "req2",
        "type": "control_result",
        "result": {"id": "req2", "kind": "approval", "status": "approved", "data": {"by": "test"}},
    }]
