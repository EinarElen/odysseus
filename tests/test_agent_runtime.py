from __future__ import annotations

from src.agent_runtime import resolve_agent_execution_limits


def test_agent_execution_limits_validate_settings_and_clamp_rounds(monkeypatch):
    values = {
        "agent_max_tool_calls": "not-an-integer",
        "agent_max_rounds": "999",
    }
    monkeypatch.setattr("src.agent_runtime.get_setting", lambda key, default: values.get(key, default))

    limits = resolve_agent_execution_limits()

    assert limits.max_tool_calls == 0
    assert limits.max_rounds == 200
