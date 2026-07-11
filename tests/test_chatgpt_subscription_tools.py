import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, ModelEndpoint
from src import chatgpt_subscription, llm_core
import src.agent_loop as agent_loop


class _FakeResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _FakeStream:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResp(self._lines)

    async def __aexit__(self, *_args):
        return False


class _FakeClient:
    def __init__(self, lines):
        self.lines = lines
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _FakeStream(self.lines)


def _events(chunks):
    out = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                out.append(json.loads(line[6:]))
    return out


def test_responses_input_preserves_function_call_round_trip():
    messages = [
        {"role": "user", "content": "check"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command":"pwd"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "/tmp/work"},
    ]

    items = chatgpt_subscription.build_responses_input(messages)

    assert items[1] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "bash",
        "arguments": '{"command":"pwd"}',
    }
    assert items[2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "/tmp/work",
    }


def test_chatgpt_subscription_payload_replays_plain_chat_history():
    messages = [
        {"role": "system", "content": "You answer from the visible conversation."},
        {"role": "user", "content": "The magic word is banana."},
        {"role": "assistant", "content": "Got it. I will remember banana."},
        {"role": "user", "content": "What is the magic word?"},
    ]

    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.5",
        messages,
        0.7,
        1000,
        stream=True,
    )

    assert payload["instructions"] == "You answer from the visible conversation."
    assert payload["input"] == [
        {"role": "user", "content": "The magic word is banana."},
        {"role": "assistant", "content": "Got it. I will remember banana."},
        {"role": "user", "content": "What is the magic word?"},
    ]


def test_responses_tools_convert_chat_completions_schema_to_strict_function():
    tools = [{
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "search",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "time_filter": {"type": "string", "enum": ["day", "week"]},
                },
                "required": ["query"],
            },
        },
    }]

    converted = chatgpt_subscription.build_responses_tools(tools)

    assert converted[0]["type"] == "function"
    assert converted[0]["name"] == "web_search"
    assert converted[0]["strict"] is True
    assert converted[0]["parameters"]["additionalProperties"] is False
    assert set(converted[0]["parameters"]["required"]) == {"query", "time_filter"}
    assert converted[0]["parameters"]["properties"]["time_filter"]["anyOf"][1] == {"type": "null"}


def test_chatgpt_subscription_stream_emits_tool_calls(monkeypatch):
    lines = [
        "data: " + json.dumps({
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "bash",
                "arguments": "",
            },
        }),
        "data: " + json.dumps({
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": '{"command":"',
        }),
        "data: " + json.dumps({
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "delta": 'pwd"}',
        }),
        "data: " + json.dumps({"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 2}}}),
    ]
    client = _FakeClient(lines)
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *_args, **_kwargs: None)

    async def run():
        return [
            chunk async for chunk in llm_core.stream_llm(
                "https://chatgpt.com/backend-api/codex/responses",
                "gpt-5.1-codex",
                [{"role": "user", "content": "check"}],
                headers={"Authorization": "Bearer token"},
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "description": "run shell",
                        "parameters": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    },
                }],
            )
        ]

    events = _events(asyncio.run(run()))

    assert client.calls[0][2]["json"]["tools"][0]["name"] == "bash"
    calls = next(event["calls"] for event in events if event.get("type") == "tool_calls")
    assert calls == [{"id": "call_1", "name": "bash", "arguments": '{"command":"pwd"}'}]


def test_chatgpt_subscription_agent_sends_tools_despite_stale_false_endpoint(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False)
    db = TestSessionLocal()
    try:
        db.add(ModelEndpoint(
            id="chatgpt-old",
            name="ChatGPT Subscription",
            base_url="https://chatgpt.com/backend-api/codex",
            owner="alice",
            is_enabled=True,
            endpoint_kind="api",
            supports_tools=False,
            cached_models=json.dumps(["gpt-5.5"]),
        ))
        db.commit()
    finally:
        db.close()

    import core.database as database

    monkeypatch.setattr(database, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: 0 if key == "agent_input_token_budget" else default, raising=False)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set(), raising=False)

    captured = {}

    async def _fake_stream(_candidates, messages, **kwargs):
        captured["tools"] = kwargs.get("tools")
        yield "data: " + json.dumps({"delta": "ok"}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", _fake_stream, raising=False)

    async def _run():
        return [
            chunk async for chunk in agent_loop.stream_agent_loop(
                "https://chatgpt.com/backend-api/codex/responses",
                "gpt-5.5",
                [{"role": "user", "content": "Please research diff viewer tools."}],
                max_rounds=1,
                owner="alice",
                relevant_tools={"trigger_research"},
            )
        ]

    asyncio.run(_run())

    tool_names = {
        tool["function"]["name"]
        for tool in (captured.get("tools") or [])
        if isinstance(tool, dict) and "function" in tool
    }
    assert "trigger_research" in tool_names
