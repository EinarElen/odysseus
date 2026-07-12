from src import chatgpt_subscription
from src.agent_tools import TOOL_TAGS  # noqa: F401  (import first to avoid circular)
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_manage_settings_value_schema_is_valid_for_chatgpt_subscription_strict_tools():
    tool = next(t for t in FUNCTION_TOOL_SCHEMAS if t.get("function", {}).get("name") == "manage_settings")

    converted = chatgpt_subscription.build_responses_tools([tool])
    value_schema = converted[0]["parameters"]["properties"]["value"]

    assert "anyOf" in value_schema
    for variant in value_schema["anyOf"]:
        assert isinstance(variant, dict)
        assert "type" in variant
        if variant["type"] == "array":
            assert variant["items"].get("type") is not None
    for node in _walk(converted[0]["parameters"]):
        if node is value_schema:
            continue
        if "anyOf" in node:
            assert all(not isinstance(v, dict) or "type" in v for v in node["anyOf"])


def test_chat_completion_call_id_is_not_reused_as_responses_item_id():
    converted = chatgpt_subscription.build_responses_input(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_WX4bgFG5SXStdGgAnG4nZrJ8",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command":"pwd"}'},
                    }
                ],
            }
        ]
    )

    assert converted == [
        {
            "type": "function_call",
            "call_id": "call_WX4bgFG5SXStdGgAnG4nZrJ8",
            "name": "bash",
            "arguments": '{"command":"pwd"}',
        }
    ]
