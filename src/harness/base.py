from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol


CONTROL_REQUEST_TYPES = {
    "approval_request",
    "user_input_request",
    "policy_check",
    "ui_request",
    "state_update",
    "control_yield",
}


@dataclass(frozen=True)
class HarnessCapabilities:
    """Capabilities an open-source harness adapter exposes to Odysseus."""

    id: str
    label: str
    defaults: Dict[str, Any] = field(default_factory=dict)
    session: Dict[str, bool] = field(default_factory=dict)
    tools: Dict[str, Any] = field(default_factory=dict)
    files: Dict[str, bool] = field(default_factory=dict)
    models: Dict[str, bool] = field(default_factory=dict)
    control_flow: Dict[str, bool] = field(default_factory=dict)
    modes: List[str] = field(default_factory=lambda: ["observe"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "defaults": dict(self.defaults),
            "session": dict(self.session),
            "tools": dict(self.tools),
            "files": dict(self.files),
            "models": dict(self.models),
            "control_flow": dict(self.control_flow),
            "modes": list(self.modes),
        }


@dataclass(frozen=True)
class HarnessSessionRef:
    adapter_id: str
    odysseus_session_id: str
    harness_session_id: Optional[str] = None
    workspace: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessEvent:
    """Normalized event emitted by a harness adapter."""

    type: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessControlRequest:
    """A harness yield point that asks Odysseus to make or surface a decision."""

    id: str
    kind: str
    data: Dict[str, Any] = field(default_factory=dict)
    blocking: bool = False

    def to_event(self) -> HarnessEvent:
        return HarnessEvent(
            "control_request",
            {
                "id": self.id,
                "kind": self.kind,
                "blocking": self.blocking,
                **dict(self.data or {}),
            },
        )


@dataclass(frozen=True)
class HarnessControlResult:
    """Result sent back to a harness after Odysseus handles a yield point."""

    id: str
    kind: str
    status: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "data": dict(self.data or {}),
        }


class HarnessAdapter(Protocol):
    id: str
    label: str
    capabilities: HarnessCapabilities

    async def start(self, config: Dict[str, Any]) -> HarnessSessionRef:
        ...

    async def send(
        self,
        ref: HarnessSessionRef,
        message: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        reconciliation: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[HarnessEvent]:
        ...

    async def command(
        self,
        ref: HarnessSessionRef,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ...

    async def close(self, ref: HarnessSessionRef) -> None:
        ...


def harness_config_from_session(session: Any) -> Dict[str, Any]:
    options = getattr(session, "provider_options", None) or {}
    if not isinstance(options, dict):
        return {}
    config = options.get("harness") or {}
    return config if isinstance(config, dict) else {}


def is_harness_session(session: Any) -> bool:
    config = harness_config_from_session(session)
    return bool(config.get("id"))
