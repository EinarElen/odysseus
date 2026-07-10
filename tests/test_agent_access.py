from __future__ import annotations

from types import SimpleNamespace

from src.agent_access import resolve_agent_access


class FakeAuthManager:
    def __init__(self, privileges: dict[str, bool]) -> None:
        self.privileges = privileges

    def get_privileges(self, user: str) -> dict[str, bool]:
        assert user == "alice"
        return self.privileges


def _request(privileges: dict[str, bool]) -> SimpleNamespace:
    state = SimpleNamespace(auth_manager=FakeAuthManager(privileges))
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_agent_access_resolves_owner_privileges_and_global_tool_policy(monkeypatch):
    monkeypatch.setattr(
        "src.agent_access.get_setting",
        lambda key, default: ["web_search", "send_email"] if key == "disabled_tools" else default,
    )

    access = resolve_agent_access(
        _request(
            {
                "can_use_agent": False,
                "can_use_bash": False,
                "can_use_browser": False,
                "can_use_documents": False,
                "can_generate_images": False,
                "can_manage_memory": False,
                "can_use_research": False,
            }
        ),
        "alice",
    )

    assert access.agent_allowed is False
    assert access.research_allowed is False
    assert access.disabled_tools == frozenset(
        {
            "bash",
            "python",
            "read_file",
            "write_file",
            "builtin_browser",
            "create_document",
            "edit_document",
            "update_document",
            "suggest_document",
            "generate_image",
            "manage_memory",
            "manage_skills",
            "web_search",
            "send_email",
        }
    )


def test_agent_access_can_preserve_explicit_web_intent_from_global_policy(monkeypatch):
    monkeypatch.setattr(
        "src.agent_access.get_setting",
        lambda key, default: ["web_search", "web_fetch", "send_email"],
    )

    access = resolve_agent_access(
        _request({}),
        "alice",
        allow_globally_disabled_web=True,
    )

    assert access.agent_allowed is True
    assert access.research_allowed is True
    assert access.disabled_tools == frozenset({"send_email"})
