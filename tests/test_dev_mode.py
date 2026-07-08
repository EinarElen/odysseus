import os
import subprocess

import pytest

from src import dev_mode


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
