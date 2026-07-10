"""Shared runtime policy for Odysseus agent execution adapters."""

from __future__ import annotations

from dataclasses import dataclass

from src.agent_tools import MAX_AGENT_ROUNDS
from src.constants import AGENT_MAX_ROUNDS_LIMIT
from src.settings import get_setting


@dataclass(frozen=True)
class AgentExecutionLimits:
    max_tool_calls: int
    max_rounds: int


def resolve_agent_execution_limits() -> AgentExecutionLimits:
    """Resolve validated per-message limits from application settings."""
    try:
        max_tool_calls = int(get_setting("agent_max_tool_calls", 0))
    except (TypeError, ValueError):
        max_tool_calls = 0
    try:
        max_rounds = int(get_setting("agent_max_rounds", MAX_AGENT_ROUNDS) or MAX_AGENT_ROUNDS)
    except (TypeError, ValueError):
        max_rounds = MAX_AGENT_ROUNDS
    max_rounds = max(1, min(max_rounds, AGENT_MAX_ROUNDS_LIMIT))
    return AgentExecutionLimits(max_tool_calls=max_tool_calls, max_rounds=max_rounds)
