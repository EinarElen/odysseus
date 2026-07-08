from __future__ import annotations

import subprocess
import sys
import tomllib
import argparse
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "odysseus-run"
DISPATCHER = REPO_ROOT / "scripts" / "odysseus"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def run_runner(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_overview_progressively_discloses_common_flow() -> None:
    result = run_runner()

    assert result.returncode == 0
    assert "Common flow:" in result.stdout
    assert "uv run ody launch select" in result.stdout
    assert "uv run ody list --all" in result.stdout


def test_list_all_includes_meta_launch_and_checks() -> None:
    result = run_runner("list", "--all")

    assert result.returncode == 0
    assert "meta:" in result.stdout
    assert "launch select" in result.stdout
    assert "check ruff" in result.stdout


def test_describe_launch_select_explains_selector() -> None:
    result = run_runner("describe", "launch", "select")

    assert result.returncode == 0
    assert "Inspect valid launch methods" in result.stdout
    assert "--method uv-dev" in result.stdout
    assert "uv run ody launch select" in result.stdout


def test_launch_select_lists_methods_without_running() -> None:
    result = run_runner("launch", "select")

    assert result.returncode == 0
    assert "Launch methods:" in result.stdout
    assert "uv-dev" in result.stdout
    assert "docker-gpu-nvidia" in result.stdout
    assert "Select with: uv run ody launch select" in result.stdout


def test_launch_select_uv_dev_dry_run_uses_reload_environment() -> None:
    result = run_runner("launch", "select", "--method", "uv-dev", "--dry-run")

    assert result.returncode == 0
    assert "Selected launch method: uv-dev" in result.stdout
    assert "ODYSSEUS_DEV_MODE=1" in result.stdout
    assert "ODYSSEUS_RELOAD_ACTIVE=1" in result.stdout
    assert "uv run --with-requirements requirements.txt" in result.stdout
    assert "python -m uvicorn app:app" in result.stdout
    assert "--reload" in result.stdout


def test_launch_app_dry_run_uses_macos_safe_default_port(monkeypatch) -> None:
    import odysseus_run_cli

    monkeypatch.delenv("APP_PORT", raising=False)
    monkeypatch.setattr(odysseus_run_cli.platform, "system", lambda: "Darwin")
    args = argparse.Namespace(port=None, host="127.0.0.1", dry_run=True)

    assert odysseus_run_cli.resolve_launch_port(args) == 7860


def test_launch_app_avoids_explicit_7000_on_macos(monkeypatch) -> None:
    import odysseus_run_cli

    monkeypatch.setattr(odysseus_run_cli.platform, "system", lambda: "Darwin")
    args = argparse.Namespace(port=7000, host="127.0.0.1", dry_run=True)

    assert odysseus_run_cli.resolve_launch_port(args) == 7860


def test_launch_app_ignores_env_port_7000_on_macos(monkeypatch) -> None:
    import odysseus_run_cli

    monkeypatch.setenv("APP_PORT", "7000")
    monkeypatch.setattr(odysseus_run_cli.platform, "system", lambda: "Darwin")
    args = argparse.Namespace(port=None, host="127.0.0.1", dry_run=True)

    assert odysseus_run_cli.resolve_launch_port(args) == 7860


def test_launch_select_can_choose_gpu_compose_method() -> None:
    result = run_runner("launch", "select", "--method", "docker-gpu-nvidia", "--dry-run")

    assert result.returncode == 0
    assert "Selected launch method: docker-gpu-nvidia" in result.stdout
    assert "docker compose -f docker-compose.yml -f docker/gpu.nvidia.yml up -d --build" in result.stdout


def test_existing_dispatcher_discovers_runner() -> None:
    result = subprocess.run(
        [sys.executable, str(DISPATCHER), "run", "list"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "launch select" in result.stdout
    assert "check all" in result.stdout


def test_module_accepts_dispatcher_style_run_prefix() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "odysseus_run_cli", "run", "list"],
        cwd=REPO_ROOT,
        env={"PYTHONPATH": str(REPO_ROOT / "src")},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "launch select" in result.stdout
    assert "check all" in result.stdout


def test_pyproject_exposes_uv_console_scripts() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text())

    scripts = pyproject["project"]["scripts"]
    assert scripts["ody"] == "odysseus_run_cli:main"
    assert scripts["odysseus"] == "odysseus_run_cli:main"
    assert scripts["odysseus-run"] == "odysseus_run_cli:main"
    assert pyproject["build-system"]["build-backend"] == "hatchling.build"
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["only-include"] == [
        "src/odysseus_run_cli.py"
    ]
