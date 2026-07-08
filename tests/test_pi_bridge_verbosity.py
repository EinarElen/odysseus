import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "src" / "harness" / "bridges" / "pi_sdk_bridge.mjs"
pytestmark = pytest.mark.slow


def _run_start(verbosity=None):
    config = {
        "odysseus_session_id": f"verbosity-{verbosity or 'normal'}",
        "workspace": str(ROOT),
        "mode": "observe",
        "persist": False,
        "in_memory": True,
        "model": "codex",
        "thinking_level": "medium",
    }
    if verbosity:
        config["verbosity"] = verbosity
    payload = {"id": "t1", "type": "start_session", "config": config, "tools": []}
    proc = subprocess.run(
        ["node", str(BRIDGE)],
        input=json.dumps(payload) + "\n",
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=8,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_pi_bridge_normal_verbosity_suppresses_startup_checklist():
    phases = [
        item.get("data", {}).get("phase")
        for item in _run_start()
        if item.get("type") == "harness_status"
    ]

    assert "session_ready" in phases
    assert "sdk_loading" not in phases
    assert "auth_loading" not in phases
    assert "session_manager_loading" not in phases


def test_pi_bridge_debug_verbosity_includes_startup_checklist():
    phases = [
        item.get("data", {}).get("phase")
        for item in _run_start("debug")
        if item.get("type") == "harness_status"
    ]

    assert "sdk_loading" in phases
    assert "auth_loading" in phases
    assert "session_manager_loading" in phases
    assert "session_ready" in phases
