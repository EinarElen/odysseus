import asyncio

from src.harness.sdk import HarnessToolCall, OdysseusToolBroker


def test_pi_harness_bash_arguments_stay_typed(monkeypatch, tmp_path):
    import src.tool_execution as tool_execution

    monkeypatch.setattr(tool_execution, "is_public_blocked_tool", lambda _tool: False)
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda _owner: True)

    async def run():
        return await OdysseusToolBroker(tool_names=["bash"]).execute(
            HarnessToolCall(name="bash", arguments={"command": "printf ok"}),
            workspace=str(tmp_path),
            owner="tester",
        )

    result = asyncio.run(run())

    assert result.is_error is False
    assert result.content == "ok"
    assert "{command" not in result.content
