"""Resolve owner-scoped access to Odysseus agent capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.settings import get_setting

_PRIVILEGE_DISABLED_TOOLS: dict[str, frozenset[str]] = {
    "can_use_bash": frozenset({"bash", "python", "read_file", "write_file"}),
    "can_use_browser": frozenset({"builtin_browser"}),
    "can_use_documents": frozenset(
        {"create_document", "edit_document", "update_document", "suggest_document"}
    ),
    "can_generate_images": frozenset({"generate_image"}),
    "can_manage_memory": frozenset({"manage_memory", "manage_skills"}),
}


@dataclass(frozen=True)
class AgentAccess:
    """The effective agent capabilities for one authenticated owner."""

    agent_allowed: bool
    research_allowed: bool
    disabled_tools: frozenset[str]


def resolve_agent_access(
    request: Any,
    user: str | None,
    *,
    allow_globally_disabled_web: bool = False,
) -> AgentAccess:
    """Return the effective agent access shared by HTTP adapters."""
    auth_manager = getattr(getattr(request.app, "state", None), "auth_manager", None)
    privileges = auth_manager.get_privileges(user) if user and auth_manager else {}

    disabled_tools: set[str] = set()
    for privilege, tools in _PRIVILEGE_DISABLED_TOOLS.items():
        if privileges and not privileges.get(privilege, True):
            disabled_tools.update(tools)

    global_disabled = get_setting("disabled_tools", [])
    if isinstance(global_disabled, list):
        if allow_globally_disabled_web:
            disabled_tools.update(
                tool for tool in global_disabled if tool not in {"web_search", "web_fetch"}
            )
        else:
            disabled_tools.update(global_disabled)

    return AgentAccess(
        agent_allowed=not privileges or privileges.get("can_use_agent", True),
        research_allowed=not privileges or privileges.get("can_use_research", True),
        disabled_tools=frozenset(disabled_tools),
    )
