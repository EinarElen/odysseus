"""Responses API function-call identifier regressions."""

from src import chatgpt_subscription


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
