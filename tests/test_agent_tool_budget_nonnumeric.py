"""Regression: malformed agent tool-budget settings fall back safely."""

import pytest

from src.agent_runtime import resolve_agent_execution_limits


@pytest.mark.parametrize("raw, expected", [
    ("unlimited", 0), ("", 0), (None, 0), ("25", 25), (12, 12),
])
def test_tool_budget_coercion_falls_back_to_zero(monkeypatch, raw, expected):
    def get_setting(key, default):
        return raw if key == "agent_max_tool_calls" and raw is not None else default

    monkeypatch.setattr("src.agent_runtime.get_setting", get_setting)

    assert resolve_agent_execution_limits().max_tool_calls == expected
