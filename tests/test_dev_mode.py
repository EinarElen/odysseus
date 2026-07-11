import os
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src import dev_mode
from routes import dev_routes


def _git(root, *args):
    try:
        return subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except FileNotFoundError:
        pytest.skip("git is required for developer-mode tests")


def _init_repo(tmp_path):
    _git(tmp_path, "init")
    return tmp_path


def test_repo_status_requires_launch_flag(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.delenv("ODYSSEUS_DEV_MODE", raising=False)
    monkeypatch.setattr(dev_mode, "get_app_root", lambda: str(tmp_path))

    status = dev_mode.repo_status()

    assert status["enabled"] is False
    assert status["launch_requested"] is False
    assert "ODYSSEUS_DEV_MODE" in status["reason"]


def test_repo_status_enables_for_matching_git_root(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.setenv("ODYSSEUS_DEV_MODE", "1")
    monkeypatch.setattr(dev_mode, "get_app_root", lambda: str(tmp_path))

    status = dev_mode.repo_status()

    assert status["enabled"] is True
    assert status["root"] == os.path.realpath(tmp_path)
    assert status["git_root"] == os.path.realpath(tmp_path)
    assert status["reload_mode"] == "interactive"


def test_interactive_reload_mode_disables_uvicorn_reload(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.setenv("ODYSSEUS_DEV_MODE", "1")
    monkeypatch.setenv("ODYSSEUS_RELOAD_MODE", "interactive")
    monkeypatch.setenv("ODYSSEUS_RELOAD", "1")
    monkeypatch.delenv("ODYSSEUS_RELOAD_ACTIVE", raising=False)
    monkeypatch.setattr(dev_mode, "get_app_root", lambda: str(tmp_path))

    assert dev_mode.dev_reload_requested() is False
    assert dev_mode.dev_reload_mode() == "interactive"
    assert dev_mode.uvicorn_reload_config() == {}


def test_auto_reload_mode_enables_uvicorn_reload(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.setenv("ODYSSEUS_DEV_MODE", "1")
    monkeypatch.setenv("ODYSSEUS_RELOAD_MODE", "auto")
    monkeypatch.delenv("ODYSSEUS_RELOAD_ACTIVE", raising=False)
    monkeypatch.setattr(dev_mode, "get_app_root", lambda: str(tmp_path))

    config = dev_mode.uvicorn_reload_config()

    assert config["reload"] is True
    assert os.environ["ODYSSEUS_RELOAD_ACTIVE"] == "1"


def test_dev_mode_tracks_active_clients():
    with dev_mode._CLIENT_LOCK:
        dev_mode._CLIENTS.clear()

    dev_mode.mark_client_seen("pytest-client")
    dev_mode.mark_client_seen("../bad")

    assert dev_mode.active_client_count() == 1


def test_request_server_reload_schedules_interactive_exec(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.setenv("ODYSSEUS_DEV_MODE", "1")
    monkeypatch.setenv("APP_BIND", "127.0.0.1")
    monkeypatch.setenv("APP_PORT", "7999")
    monkeypatch.delenv("ODYSSEUS_RELOAD_ACTIVE", raising=False)
    monkeypatch.setattr(dev_mode, "get_app_root", lambda: str(tmp_path))

    started = {}

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            started["target"] = target
            started["name"] = name
            started["daemon"] = daemon

        def start(self):
            started["called"] = True

    monkeypatch.setattr(dev_mode.threading, "Thread", FakeThread)

    result = dev_mode.request_server_reload(str(tmp_path), delay_s=0)

    assert result["ok"] is True
    assert result["mode"] == "interactive"
    assert "uvicorn app:app" in result["command"]
    assert callable(started["target"])
    assert started["name"] == "odysseus-dev-reload"
    assert started["daemon"] is True
    assert started["called"] is True
    # FakeThread intentionally never runs the restart target, so undo the
    # production drain state that the target would end by exiting the process.
    from src import agent_runs
    agent_runs.cancel_drain()


def test_server_reload_drains_agent_runs_before_exit(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.setenv("ODYSSEUS_DEV_MODE", "1")
    monkeypatch.delenv("ODYSSEUS_RELOAD_ACTIVE", raising=False)
    monkeypatch.setattr(dev_mode, "get_app_root", lambda: str(tmp_path))

    calls = []
    counts = iter((1, 1, 0))
    from src import agent_runs
    monkeypatch.setattr(agent_runs, "begin_drain", lambda: calls.append("drain") or 1)
    monkeypatch.setattr(agent_runs, "active_run_count", lambda: next(counts))
    monkeypatch.setattr(dev_mode, "_spawn_restart_helper", lambda *a, **k: calls.append("helper"))
    monkeypatch.setattr(dev_mode, "_request_graceful_exit", lambda: calls.append("exit"))
    monkeypatch.setattr(dev_mode.time, "sleep", lambda _seconds: calls.append("wait"))

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(dev_mode.threading, "Thread", ImmediateThread)

    result = dev_mode.request_server_reload(str(tmp_path), delay_s=0)

    assert result["draining"] is True
    assert result["draining_runs"] == 1
    assert calls[0:2] == ["drain", "helper"]
    assert calls[-1] == "exit"
    assert calls.count("wait") == 3


def test_request_server_reload_rejects_external_reload_supervisor(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.setenv("ODYSSEUS_DEV_MODE", "1")
    monkeypatch.setenv("ODYSSEUS_RELOAD_ACTIVE", "1")
    monkeypatch.setattr(dev_mode, "get_app_root", lambda: str(tmp_path))

    result = dev_mode.request_server_reload(str(tmp_path), delay_s=0)

    assert result["ok"] is False
    assert "external reload supervisor" in result["error"]


def test_dev_server_reload_requires_action_header(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.setattr(dev_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(
        dev_routes.dev_mode,
        "repo_status",
        lambda root=None: {"enabled": True, "root": str(tmp_path)},
    )
    router = dev_routes.setup_dev_routes()
    endpoint = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/dev/server/reload")
    request = SimpleNamespace(
        headers={"host": "localhost:7000"},
        url=SimpleNamespace(scheme="http"),
    )

    with pytest.raises(HTTPException) as exc:
        import asyncio
        asyncio.run(endpoint(request))

    assert exc.value.status_code == 403


def test_dev_server_reload_accepts_same_origin_action(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    called = {}
    monkeypatch.setattr(dev_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(
        dev_routes.dev_mode,
        "repo_status",
        lambda root=None: {"enabled": True, "root": str(tmp_path)},
    )
    def fake_reload(root):
        called["root"] = root
        return {"ok": True}

    monkeypatch.setattr(dev_routes.dev_mode, "request_server_reload", fake_reload)
    router = dev_routes.setup_dev_routes()
    endpoint = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/dev/server/reload")
    request = SimpleNamespace(
        headers={
            "host": "localhost:7000",
            "origin": "http://localhost:7000",
            "X-Odysseus-Dev-Action": "server-reload",
        },
        url=SimpleNamespace(scheme="http"),
    )

    import asyncio
    result = asyncio.run(endpoint(request))

    assert result == {"ok": True}
    assert called["root"] == str(tmp_path)


def test_revision_token_changes_when_watched_file_changes(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    target = static_dir / "style.css"
    target.write_text("body { color: red; }\n", encoding="utf-8")

    before = dev_mode.revision(str(tmp_path))
    target.write_text("body { color: rebeccapurple; }\n", encoding="utf-8")
    after = dev_mode.revision(str(tmp_path))

    assert before["css_token"] != after["css_token"]
    assert before["token"] != after["token"]


def test_revision_token_changes_when_watched_mjs_file_changes(tmp_path):
    bridge_dir = tmp_path / "src" / "harness" / "bridges"
    bridge_dir.mkdir(parents=True)
    target = bridge_dir / "pi_sdk_bridge.mjs"
    target.write_text("export const value = 1;\n", encoding="utf-8")

    before = dev_mode.revision(str(tmp_path))
    target.write_text("export const value = 2;\n", encoding="utf-8")
    after = dev_mode.revision(str(tmp_path))

    assert before["server_token"] != after["server_token"]
    assert before["token"] != after["token"]


def test_test_suggestions_include_changed_python_compile(tmp_path):
    _init_repo(tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "example.py").write_text("x = 1\n", encoding="utf-8")

    suggestions = dev_mode.test_suggestions(str(tmp_path))["suggestions"]

    assert any(item["id"] == "python_compile_changed" for item in suggestions)


def test_developer_context_note_is_only_for_active_checkout_workspace(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    monkeypatch.setenv("ODYSSEUS_DEV_MODE", "1")
    monkeypatch.setattr(dev_mode, "get_app_root", lambda: str(tmp_path))

    note = dev_mode.developer_context_note(str(tmp_path))
    child_note = dev_mode.developer_context_note(str(tmp_path / "src"))

    assert "bespoke local version" in note
    assert child_note == ""
