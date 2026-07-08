from __future__ import annotations

import os
import shlex
from typing import Dict

from .base import HarnessAdapter, HarnessCapabilities
from .sdk import SdkHarnessAdapter


def _pi_sdk_command():
    override = os.getenv("ODYSSEUS_PI_SDK_COMMAND", "").strip()
    if override:
        return shlex.split(override)
    bridge = os.path.join(os.path.dirname(__file__), "bridges", "pi_sdk_bridge.mjs")
    return ["node", bridge]


_ADAPTERS: Dict[str, HarnessAdapter] = {
    "pi": SdkHarnessAdapter(
        adapter_id="pi",
        label="Pi",
        command=_pi_sdk_command(),
        capabilities=HarnessCapabilities(
            id="pi",
            label="Pi",
            defaults={
                "model": "codex",
                "mode": "bridged",
                "thinking_level": "medium",
                "provide_odysseus_tools": True,
                "accept_harness_tools": True,
            },
            session={
                "resume": True,
                "branching": True,
                "abort": True,
                "steer": True,
                "follow_up": True,
            },
            tools={
                "list_native": True,
                "provide_external": True,
                "intercept_native": "bridge",
                "disable_native": True,
            },
            files={
                "workspace": True,
                "diff_events": True,
                "read_events": True,
                "write_events": True,
            },
            models={
                "set_model": True,
                "thinking_level": True,
            },
            control_flow={
                "cooperative_turns": True,
                "yield_control": True,
                "resume_with_result": True,
                "approval_request": True,
                "user_input_request": True,
                "policy_check": True,
                "ui_request": True,
                "state_update": True,
            },
            modes=["observe", "bridged"],
        ),
    ),
}


def get_harness_adapter(adapter_id: str) -> HarnessAdapter:
    adapter = _ADAPTERS.get((adapter_id or "").strip().lower())
    if adapter is None:
        raise KeyError(f"Unknown harness adapter: {adapter_id}")
    return adapter


def list_harness_capabilities():
    return [adapter.capabilities.to_dict() for adapter in _ADAPTERS.values()]


def list_harness_ids():
    return sorted(_ADAPTERS)
