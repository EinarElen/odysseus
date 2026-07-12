"""Coherent uv-based task and launch runner for Odysseus.

This module is intentionally additive: it backs project console scripts, uvx
invocation, and the existing scripts/odysseus-run wrapper without replacing the
older scripts/odysseus-* commands.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence


def find_repo_root() -> Path:
    """Find the user's current Odysseus checkout.

    uvx installs console scripts into a temporary environment, so __file__ may
    point outside the checkout. Prefer the current working directory when it
    looks like the repo, then fall back to the source-tree module location.
    """

    module_root = Path(__file__).resolve().parents[1]
    candidates = [Path.cwd(), *Path.cwd().parents, module_root, *module_root.parents]
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "app.py").exists() and (resolved / "requirements.txt").exists():
            return resolved
    return module_root


REPO_ROOT = find_repo_root()
REQUIREMENTS = REPO_ROOT / "requirements.txt"
OPTIONAL_REQUIREMENTS = REPO_ROOT / "requirements-optional.txt"
DEFAULT_VENV = REPO_ROOT / "venv"
RUFF_VERSION = "0.12.7"
TY_VERSION = "0.0.41"
PRIMARY_INVOCATION = "uv run ody"
DISPATCHER_INVOCATION = "scripts/odysseus run"


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    category: str
    summary: str
    details: str
    examples: tuple[str, ...] = ()
    common: bool = False


@dataclass(frozen=True)
class CommandPlan:
    label: str
    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LaunchCandidate:
    name: str
    summary: str
    valid: bool
    reason: str
    plan: CommandPlan
    rank: int


def _env_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 1 <= value <= 65535 else None


def _port_has_listener(port: int) -> bool:
    """True when any local process is already listening on this TCP port.

    A bind probe alone is not reliable on macOS: AirPlay/ControlCenter can hold
    *:7000 while uvicorn still binds 127.0.0.1:7000, leaving clients split
    between Odysseus and AirTunes. lsof sees the wildcard listener up front.
    """
    lsof = shutil.which("lsof")
    if not lsof:
        return False
    try:
        proc = subprocess.run(
            [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return False
    return any(line.strip() for line in proc.stdout.splitlines()[1:])


def _can_bind_port(host: str, port: int) -> bool:
    bind_host = "::" if host == "::" else host
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, port))
        return True
    except OSError:
        return False


def _port_available(host: str, port: int) -> bool:
    return not _port_has_listener(port) and _can_bind_port(host, port)


def _candidate_ports(preferred: int) -> list[int]:
    ports: list[int] = [preferred]
    ports.extend(range(preferred + 1, preferred + 50))

    seen: set[int] = set()
    result: list[int] = []
    for port in ports:
        if 1 <= port <= 65535 and port not in seen:
            seen.add(port)
            result.append(port)
    return result


def resolve_launch_port(args: argparse.Namespace) -> int:
    requested = getattr(args, "port", None)
    host = getattr(args, "host", "") or "127.0.0.1"
    if requested is not None:
        if platform.system() == "Darwin" and int(requested) == 7000:
            preferred = 7860
            if getattr(args, "dry_run", False):
                return preferred
            for port in _candidate_ports(preferred):
                if _port_available(host, port):
                    print(
                        "Port 7000 is reserved by macOS AirPlay on many systems; "
                        f"launching on {host}:{port} instead.",
                        file=sys.stderr,
                    )
                    return port
            raise SystemExit(f"No available launch port found near {preferred}.")
        return int(requested)

    env_port = _env_int("APP_PORT")
    if platform.system() == "Darwin" and env_port == 7000:
        env_port = None
    preferred = env_port or (7860 if platform.system() == "Darwin" else 7000)
    if getattr(args, "dry_run", False):
        return preferred

    for port in _candidate_ports(preferred):
        if _port_available(host, port):
            if port != preferred:
                print(
                    f"Port {preferred} is already claimed; launching on {host}:{port} instead.",
                    file=sys.stderr,
                )
            return port
    raise SystemExit(f"No available launch port found near {preferred}. Pass --port explicitly.")


LAUNCH_METHODS = (
    "auto",
    "uv-dev",
    "uv",
    "launcher",
    "macos",
    "windows",
    "docker",
    "docker-dev",
    "docker-gpu-nvidia",
    "docker-gpu-amd",
)


CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        name="doctor",
        category="inspect",
        summary="Check local tools, repo basis, Python, uv, Docker, Node, and venv state.",
        details=(
            "Runs cheap local probes only. This is the first command to use when a "
            "launch or check behaves differently across machines."
        ),
        examples=("scripts/odysseus run doctor", "scripts/odysseus run doctor --json"),
        common=True,
    ),
    CatalogEntry(
        name="setup",
        category="environment",
        summary="Create a uv-managed venv and install Python requirements.",
        details=(
            "Creates the target virtual environment with uv, installs requirements.txt "
            "with uv pip, optionally installs requirements-optional.txt, and can run "
            "the existing setup.py without replacing it."
        ),
        examples=(
            "scripts/odysseus run setup",
            "scripts/odysseus run setup --optional",
            "scripts/odysseus run setup --python 3.13 --dry-run",
        ),
        common=True,
    ),
    CatalogEntry(
        name="install",
        category="environment",
        summary="Install requirements into an existing uv-created venv.",
        details=(
            "Uses uv pip install against the configured venv Python. This is useful "
            "after editing requirements without recreating the environment."
        ),
        examples=("scripts/odysseus run install", "scripts/odysseus run install --optional"),
    ),
    CatalogEntry(
        name="lock",
        category="environment",
        summary="Compile requirements into a uv pip lock-style requirements file.",
        details=(
            "Runs uv pip compile and writes requirements.lock by default. Existing "
            "requirements files are left untouched."
        ),
        examples=("scripts/odysseus run lock", "scripts/odysseus run lock --optional"),
    ),
    CatalogEntry(
        name="sync",
        category="environment",
        summary="Sync an existing lock file into the local venv with uv pip sync.",
        details=(
            "Uses requirements.lock by default. This command only works once a lock "
            "file has been generated or supplied."
        ),
        examples=("scripts/odysseus run sync", "scripts/odysseus run sync --lock requirements.lock"),
    ),
    CatalogEntry(
        name="launch select",
        category="meta",
        summary="Inspect valid launch methods and run one selected method.",
        details=(
            "Shows the platform-dependent launch choices available in this checkout "
            "and can run a chosen method with --method. This is the meta task for "
            "cases where native, uv, Docker, and GPU-overlay launches are all valid "
            "but have different tradeoffs."
        ),
        examples=(
            "scripts/odysseus run launch select",
            "scripts/odysseus run launch select --method uv-dev",
            "scripts/odysseus run launch select --method auto --dry-run",
        ),
        common=True,
    ),
    CatalogEntry(
        name="meta launch",
        category="meta",
        summary="Alias for the launch-method selector.",
        details=(
            "Progressively discloses launch/run choices without committing to one "
            "implementation. Use --method to select a concrete path."
        ),
        examples=(
            "scripts/odysseus run meta launch",
            "scripts/odysseus run meta launch --method docker-dev --dry-run",
        ),
    ),
    CatalogEntry(
        name="launch app",
        category="launch",
        summary="Run the FastAPI app through uv and requirements.txt.",
        details=(
            "Starts uvicorn through uv run --with-requirements requirements.txt. "
            "Use this for a normal local app launch without developer-mode flags."
        ),
        examples=(
            "scripts/odysseus run launch app",
            "scripts/odysseus run launch app --host 0.0.0.0 --port 7000",
        ),
        common=True,
    ),
    CatalogEntry(
        name="launch dev",
        category="launch",
        summary="Run the app with Odysseus developer-mode environment and interactive reload prompts.",
        details=(
            "Starts uvicorn through uv, enables ODYSSEUS_DEV_MODE, and leaves "
            "server reload under user control through the browser toast and "
            "Developer settings panel. This is for hacking on the user's current "
            "bespoke checkout, not only upstream contribution workflows. Use "
            "--auto-reload only when you explicitly want uvicorn's file watcher."
        ),
        examples=(
            "scripts/odysseus run launch dev",
            "scripts/odysseus run launch dev --auto-reload",
            "scripts/odysseus run launch dev --python 3.13 --dry-run",
        ),
        common=True,
    ),
    CatalogEntry(
        name="launch launcher",
        category="launch",
        summary="Run launcher.py through uv.",
        details=(
            "Uses the standalone Python launcher entry point while still executing "
            "it through uv run and requirements.txt."
        ),
        examples=("scripts/odysseus run launch launcher",),
    ),
    CatalogEntry(
        name="launch macos",
        category="launch",
        summary="Run the existing macOS native setup/launch script.",
        details=(
            "Wraps start-macos.sh. This script manages Homebrew-side native "
            "dependencies, ChromaDB startup, Apfel bootstrap on Apple Silicon, and "
            "macOS-specific defaults such as port 7860."
        ),
        examples=("scripts/odysseus run launch macos",),
    ),
    CatalogEntry(
        name="launch windows",
        category="launch",
        summary="Run the existing Windows native setup/launch script.",
        details=(
            "Wraps launch-windows.ps1. This is selectable for Windows users without "
            "removing the existing PowerShell launcher."
        ),
        examples=("scripts/odysseus run launch windows --dry-run",),
    ),
    CatalogEntry(
        name="launch docker",
        category="launch",
        summary="Build and start the production Docker Compose stack.",
        details=(
            "Wraps the existing docker-compose.yml flow. Docker remains a first-class "
            "launch path, but the command is discoverable from the same runner."
        ),
        examples=("scripts/odysseus run launch docker",),
    ),
    CatalogEntry(
        name="launch docker-dev",
        category="launch",
        summary="Build and start Docker Compose with docker-compose.dev.yml overrides.",
        details=(
            "Wraps docker compose -f docker-compose.yml -f docker-compose.dev.yml "
            "up -d --build."
        ),
        examples=("scripts/odysseus run launch docker-dev",),
    ),
    CatalogEntry(
        name="launch docker-gpu-nvidia",
        category="launch",
        summary="Build and start Docker Compose with the NVIDIA GPU overlay.",
        details=(
            "Wraps docker compose with docker/gpu.nvidia.yml. The GPU diagnostic "
            "scripts remain the source for validating host passthrough."
        ),
        examples=("scripts/odysseus run launch docker-gpu-nvidia --dry-run",),
    ),
    CatalogEntry(
        name="launch docker-gpu-amd",
        category="launch",
        summary="Build and start Docker Compose with the AMD GPU overlay.",
        details=(
            "Wraps docker compose with docker/gpu.amd.yml. The AMD diagnostic "
            "script remains the source for validating host passthrough."
        ),
        examples=("scripts/odysseus run launch docker-gpu-amd --dry-run",),
    ),
    CatalogEntry(
        name="check compile",
        category="check",
        summary="Compile Python files through uv run.",
        details="Runs python -m compileall over the main application, scripts, and tests.",
        examples=("scripts/odysseus run check compile",),
        common=True,
    ),
    CatalogEntry(
        name="check pytest",
        category="check",
        summary="Run pytest through uv and requirements.txt.",
        details=(
            "Runs python -m pytest through uv run --with-requirements requirements.txt. "
            "Pass pytest arguments after --."
        ),
        examples=(
            "scripts/odysseus run check pytest",
            "scripts/odysseus run check pytest -- -q tests/test_auth.py",
        ),
        common=True,
    ),
    CatalogEntry(
        name="check focus",
        category="check",
        summary="Run the existing focused test harness through uv.",
        details="Wraps tests/run_focus.py while keeping that script unchanged.",
        examples=("scripts/odysseus run check focus",),
    ),
    CatalogEntry(
        name="check ruff",
        category="check",
        summary="Run the pinned Ruff version with uvx.",
        details=(
            "Uses uvx --from ruff==0.12.7 to mirror the pinned CI command without "
            "requiring Ruff to be installed in the local venv."
        ),
        examples=("scripts/odysseus run check ruff",),
        common=True,
    ),
    CatalogEntry(
        name="check ty",
        category="check",
        summary="Run the pinned ty version with uvx.",
        details=(
            "Uses uvx --from ty==0.0.41 to mirror the pinned CI command without "
            "requiring ty to be installed in the local venv."
        ),
        examples=("scripts/odysseus run check ty",),
    ),
    CatalogEntry(
        name="check js",
        category="check",
        summary="Run node --check over first-party JavaScript files.",
        details=(
            "JavaScript is not uv-managed, but this keeps the existing syntax-check "
            "operation discoverable beside the uv-based checks."
        ),
        examples=("scripts/odysseus run check js",),
    ),
    CatalogEntry(
        name="check audit",
        category="check",
        summary="Run the existing uv audit requirements helper.",
        details=(
            "Wraps .github/scripts/uv_audit_requirements.py. That helper performs "
            "the uv audit behavior used by CI."
        ),
        examples=("scripts/odysseus run check audit",),
    ),
    CatalogEntry(
        name="check all",
        category="check",
        summary="Run the normal local check bundle.",
        details=(
            "Runs compile, JavaScript syntax checks, Ruff, ty, and pytest in order. "
            "Use --skip-tests for a faster syntax and static-check pass."
        ),
        examples=(
            "scripts/odysseus run check all",
            "scripts/odysseus run check all --skip-tests",
        ),
        common=True,
    ),
    CatalogEntry(
        name="docker config",
        category="docker",
        summary="Render Docker Compose config.",
        details="Wraps docker compose config.",
        examples=("scripts/odysseus run docker config",),
    ),
    CatalogEntry(
        name="docker logs",
        category="docker",
        summary="Tail Odysseus Docker logs.",
        details="Wraps docker compose logs for the odysseus service.",
        examples=("scripts/odysseus run docker logs --follow",),
    ),
    CatalogEntry(
        name="docker ps",
        category="docker",
        summary="Show Docker Compose service status.",
        details="Wraps docker compose ps.",
        examples=("scripts/odysseus run docker ps",),
    ),
    CatalogEntry(
        name="docker down",
        category="docker",
        summary="Stop the Docker Compose stack.",
        details="Wraps docker compose down.",
        examples=("scripts/odysseus run docker down",),
    ),
)

CATALOG_BY_NAME = {entry.name: entry for entry in CATALOG}


TOPICS = {
    "overview": """\
        Odysseus run is an additive, uv-centered command surface for common local
        development and launch operations.

        Common flow:
          uv run ody doctor
          uv run ody setup
          uv run ody launch select
          uv run ody launch dev
          uv run ody check all

        Discovery:
          uv run ody list
          uv run ody list --all
          uv run ody meta launch
          uv run ody describe "launch dev"
          uv run ody help launch

        Compatibility:
          scripts/odysseus run ... still works through the existing dispatcher.
    """,
    "launch": """\
        Launch commands keep existing entry points available while giving one
        predictable local workflow.

        launch app starts uvicorn normally through uv run.
        launch dev enables ODYSSEUS_DEV_MODE and interactive reload prompts.
        launch launcher runs launcher.py through uv.
        launch macos and launch windows wrap existing native launchers.
        launch docker, launch docker-dev, and GPU variants wrap Compose files.

        The selector is the meta task for launch/run ambiguity:
          uv run ody launch select
          uv run ody launch select --method uv-dev
          uv run ody launch select --method auto --dry-run

        Useful examples:
          uv run ody launch app --port 7000
          uv run ody launch dev
          uv run ody launch dev --auto-reload
          uv run ody launch dev --python 3.13 --dry-run
    """,
    "check": """\
        Check commands mirror the repo's existing CI and local commands while
        making them easier to find.

        uv run is used for Python execution that needs repo requirements.
        uvx is used for pinned standalone tools like Ruff and ty.

        Useful examples:
          uv run ody check compile
          uv run ody check ruff
          uv run ody check pytest -- -q tests/test_auth.py
          uv run ody check all --skip-tests
    """,
    "environment": """\
        Environment commands are uv-native wrappers around the existing
        requirements files. They do not remove the old setup.py or shell scripts.

        setup creates a venv and installs requirements.
        install updates an existing venv.
        lock writes requirements.lock with uv pip compile.
        sync installs from a lock file with uv pip sync.
    """,
    "docker": """\
        Docker commands wrap the current Compose files for discoverability.

        Useful examples:
          uv run ody launch docker
          uv run ody launch docker-dev
          uv run ody docker logs --follow
          uv run ody docker down
    """,
    "meta": """\
        Meta tasks describe and select among multiple valid implementation
        paths. They are for operations where the repo already has several
        platform-specific or workflow-specific ways to do the same thing.

        The first meta task is launch selection:
          uv run ody meta launch
          uv run ody meta launch --method auto --dry-run
          uv run ody meta launch --method docker-gpu-nvidia

        No existing launcher is removed. The selector just exposes the choices
        and dispatches to the selected method.
    """,
    "uv": """\
        This runner treats uv as the Python environment/task substrate:

          uv venv       persistent local virtualenv creation
          uv pip        install, compile, and sync requirements
          uv run        launch app/test commands with requirements.txt
          uvx --from    pinned one-shot tools such as Ruff and ty

        Set ODYSSEUS_UV_PYTHON or pass --python on launch/check commands when
        you want uv to use a specific interpreter version.
    """,
}


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def clean_remainder(args: Sequence[str] | None) -> list[str]:
    if not args:
        return []
    values = list(args)
    if values and values[0] == "--":
        return values[1:]
    return values


def preferred_invocation(text: str) -> str:
    return text.replace(DISPATCHER_INVOCATION, PRIMARY_INVOCATION)


def venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def shell_display(argv: Sequence[str], env: dict[str, str] | None = None) -> str:
    command = shlex.join(str(part) for part in argv)
    if not env:
        return command
    prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in sorted(env.items()))
    return f"{prefix} {command}"


def print_plan(plan: CommandPlan) -> None:
    print(f"[{plan.label}]")
    print(f"$ {shell_display(plan.argv, plan.env)}")


def run_plan(plan: CommandPlan, *, dry_run: bool) -> int:
    print_plan(plan)
    if dry_run:
        return 0
    env = os.environ.copy()
    env.update(plan.env)
    return subprocess.call([str(part) for part in plan.argv], cwd=REPO_ROOT, env=env)


def run_sequence(plans: Iterable[CommandPlan], *, dry_run: bool) -> int:
    for plan in plans:
        status = run_plan(plan, dry_run=dry_run)
        if status != 0:
            return status
    return 0


def uv_python_value(args: argparse.Namespace) -> str:
    return getattr(args, "python", "") or os.environ.get("ODYSSEUS_UV_PYTHON", "")


def uv_run_prefix(
    args: argparse.Namespace,
    *,
    requirements: bool = True,
    env_file: str | None = None,
) -> list[str]:
    argv = ["uv", "run"]
    if requirements:
        argv.extend(["--with-requirements", relative(REQUIREMENTS)])
    python = uv_python_value(args)
    if python:
        argv.extend(["--python", python])
    if env_file:
        argv.extend(["--env-file", env_file])
    return argv


def env_file_for_launch(args: argparse.Namespace) -> str | None:
    if getattr(args, "no_env_file", False):
        return None
    explicit = getattr(args, "env_file", None)
    if explicit:
        return explicit
    default_env = REPO_ROOT / ".env"
    if default_env.exists():
        return relative(default_env)
    return None


def print_wrapped(text: str) -> None:
    print(textwrap.dedent(text).strip())


def print_overview() -> None:
    print_wrapped(TOPICS["overview"])
    print()
    print("Common tasks:")
    for entry in (item for item in CATALOG if item.common):
        print(f"  {entry.name:<16} {entry.summary}")


def print_topic(topic: str) -> int:
    body = TOPICS.get(topic)
    if body is None:
        print(f"Unknown help topic: {topic}", file=sys.stderr)
        print("Known topics: " + ", ".join(sorted(TOPICS)), file=sys.stderr)
        return 2
    print_wrapped(body)
    return 0


def list_entries(args: argparse.Namespace) -> int:
    entries = CATALOG if args.all else tuple(entry for entry in CATALOG if entry.common)
    if args.category:
        entries = tuple(entry for entry in entries if entry.category == args.category)
    if not entries:
        print("No tasks matched.", file=sys.stderr)
        return 1

    current_category = None
    for entry in entries:
        if entry.category != current_category:
            current_category = entry.category
            print(f"\n{current_category}:")
        print(f"  {entry.name:<18} {entry.summary}")
    print()
    if not args.all:
        print(f"Use '{PRIMARY_INVOCATION} list --all' to see less common operations.")
    print(f"Use '{PRIMARY_INVOCATION} describe <name>' for command details.")
    return 0


def describe_entry(args: argparse.Namespace) -> int:
    name = " ".join(args.name).strip().lower()
    entry = CATALOG_BY_NAME.get(name)
    if entry is None:
        print(f"Unknown task: {name}", file=sys.stderr)
        print(f"Use '{PRIMARY_INVOCATION} list --all' to discover task names.", file=sys.stderr)
        return 2

    print(f"{entry.name}")
    print(f"Category: {entry.category}")
    print()
    print(entry.summary)
    print()
    print(textwrap.fill(entry.details, width=88))
    if entry.examples:
        print()
        print("Examples:")
        for example in entry.examples:
            print(f"  {preferred_invocation(example)}")
    return 0


def command_version(argv: Sequence[str]) -> str | None:
    try:
        completed = subprocess.run(
            [str(part) for part in argv],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = completed.stdout.strip().splitlines()
    return output[0] if output else None


def git_basis() -> dict[str, object]:
    git_dir = REPO_ROOT / ".git"
    data: dict[str, object] = {"present": git_dir.exists()}
    if not git_dir.exists():
        return data
    branch = command_version(["git", "branch", "--show-current"])
    commit = command_version(["git", "rev-parse", "--short", "HEAD"])
    dirty = command_version(["git", "status", "--porcelain"])
    data.update(
        {
            "branch": branch or None,
            "commit": commit or None,
            "dirty": bool(dirty),
        }
    )
    return data


def doctor(args: argparse.Namespace) -> int:
    checks = {
        "repo_root": str(REPO_ROOT),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "git_basis": git_basis(),
        "tools": {
            "uv": {
                "path": shutil.which("uv"),
                "version": command_version(["uv", "--version"]),
            },
            "git": {
                "path": shutil.which("git"),
                "version": command_version(["git", "--version"]),
            },
            "docker": {
                "path": shutil.which("docker"),
                "version": command_version(["docker", "--version"]),
            },
            "node": {
                "path": shutil.which("node"),
                "version": command_version(["node", "--version"]),
            },
        },
        "files": {
            "requirements.txt": REQUIREMENTS.exists(),
            "requirements-optional.txt": OPTIONAL_REQUIREMENTS.exists(),
            "docker-compose.yml": (REPO_ROOT / "docker-compose.yml").exists(),
            "docker-compose.dev.yml": (REPO_ROOT / "docker-compose.dev.yml").exists(),
            "pyproject.toml": (REPO_ROOT / "pyproject.toml").exists(),
        },
        "venv": {
            "path": relative(DEFAULT_VENV),
            "exists": DEFAULT_VENV.exists(),
            "python": str(venv_python(DEFAULT_VENV)),
            "python_exists": venv_python(DEFAULT_VENV).exists(),
        },
    }
    if args.json:
        print(json.dumps(checks, indent=2, sort_keys=True))
        return 0

    print("Odysseus local runner doctor")
    print(f"repo:   {checks['repo_root']}")
    print(f"python: {checks['python']}")
    basis = checks["git_basis"]
    if isinstance(basis, dict):
        if basis.get("present"):
            branch = basis.get("branch") or "(detached)"
            commit = basis.get("commit") or "(unknown)"
            dirty = "dirty" if basis.get("dirty") else "clean"
            print(f"git:    {branch} @ {commit} ({dirty})")
        else:
            print("git:    not present")
    print()
    print("tools:")
    tools = checks["tools"]
    if isinstance(tools, dict):
        for name, info in tools.items():
            if not isinstance(info, dict):
                continue
            version = info.get("version") or "missing"
            path = info.get("path") or "-"
            print(f"  {name:<7} {version:<24} {path}")
    print()
    print("files:")
    files = checks["files"]
    if isinstance(files, dict):
        for name, present in files.items():
            marker = "yes" if present else "no"
            print(f"  {name:<28} {marker}")
    print()
    venv = checks["venv"]
    if isinstance(venv, dict):
        print(f"venv:   {venv['path']} ({'ready' if venv['python_exists'] else 'not ready'})")
    return 0


def setup_environment(args: argparse.Namespace) -> int:
    venv = Path(args.venv)
    if not venv.is_absolute():
        venv = REPO_ROOT / venv
    py = venv_python(venv)
    plans = [
        CommandPlan("Create virtualenv", ("uv", "venv", relative(venv), "--python", args.python)),
        CommandPlan(
            "Install requirements",
            (
                "uv",
                "pip",
                "install",
                "--python",
                relative(py),
                "-r",
                relative(REQUIREMENTS),
            ),
        ),
    ]
    if args.optional:
        plans.append(
            CommandPlan(
                "Install optional requirements",
                (
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    relative(py),
                    "-r",
                    relative(OPTIONAL_REQUIREMENTS),
                ),
            )
        )
    if not args.skip_app_setup:
        plans.append(CommandPlan("Run existing setup.py", (relative(py), "setup.py")))
    return run_sequence(plans, dry_run=args.dry_run)


def install_environment(args: argparse.Namespace) -> int:
    venv = Path(args.venv)
    if not venv.is_absolute():
        venv = REPO_ROOT / venv
    py = venv_python(venv)
    plans = [
        CommandPlan(
            "Install requirements",
            (
                "uv",
                "pip",
                "install",
                "--python",
                relative(py),
                "-r",
                relative(REQUIREMENTS),
            ),
        )
    ]
    if args.optional:
        plans.append(
            CommandPlan(
                "Install optional requirements",
                (
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    relative(py),
                    "-r",
                    relative(OPTIONAL_REQUIREMENTS),
                ),
            )
        )
    return run_sequence(plans, dry_run=args.dry_run)


def lock_environment(args: argparse.Namespace) -> int:
    sources = [relative(REQUIREMENTS)]
    if args.optional:
        sources.append(relative(OPTIONAL_REQUIREMENTS))
    argv = ["uv", "pip", "compile", *sources, "-o", args.output]
    if args.upgrade:
        argv.append("--upgrade")
    return run_plan(CommandPlan("Compile lock file", tuple(argv)), dry_run=args.dry_run)


def sync_environment(args: argparse.Namespace) -> int:
    venv = Path(args.venv)
    if not venv.is_absolute():
        venv = REPO_ROOT / venv
    argv = (
        "uv",
        "pip",
        "sync",
        "--python",
        relative(venv_python(venv)),
        args.lock,
    )
    return run_plan(CommandPlan("Sync lock file", argv), dry_run=args.dry_run)


def with_defaults(args: argparse.Namespace, **overrides: object) -> argparse.Namespace:
    values = vars(args).copy()
    values.update(overrides)
    return argparse.Namespace(**values)


def plan_launch_app(args: argparse.Namespace, *, dev: bool) -> CommandPlan:
    port = resolve_launch_port(args)
    argv = uv_run_prefix(args, requirements=True, env_file=env_file_for_launch(args))
    argv.extend(
        [
            "python",
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            args.host,
            "--port",
            str(port),
        ]
    )
    env: dict[str, str] = {"APP_BIND": str(args.host), "APP_PORT": str(port)}
    if dev:
        env["ODYSSEUS_DEV_MODE"] = "1"
        env["ODYSSEUS_DEV_LAUNCH"] = "1"
        env["ODYSSEUS_RELOAD_MODE"] = "interactive"
        if getattr(args, "auto_reload", False) and not getattr(args, "no_reload", False):
            env["ODYSSEUS_RELOAD_MODE"] = "auto"
            env["ODYSSEUS_RELOAD"] = "1"
            env["ODYSSEUS_RELOAD_ACTIVE"] = "1"
            argv.extend(["--reload", "--reload-dir", str(REPO_ROOT)])
    elif args.reload:
        env["ODYSSEUS_RELOAD"] = "1"
        env["ODYSSEUS_RELOAD_ACTIVE"] = "1"
        argv.extend(["--reload", "--reload-dir", str(REPO_ROOT)])
    return CommandPlan("Launch Odysseus dev server" if dev else "Launch Odysseus server", tuple(argv), env)


def launch_app(args: argparse.Namespace, *, dev: bool) -> int:
    return run_plan(plan_launch_app(args, dev=dev), dry_run=args.dry_run)


def plan_launch_docker(*, dev: bool) -> CommandPlan:
    if dev:
        argv = (
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.dev.yml",
            "up",
            "-d",
            "--build",
        )
        label = "Launch Docker development stack"
    else:
        argv = ("docker", "compose", "up", "-d", "--build")
        label = "Launch Docker stack"
    return CommandPlan(label, argv)


def launch_docker(args: argparse.Namespace, *, dev: bool) -> int:
    return run_plan(plan_launch_docker(dev=dev), dry_run=args.dry_run)


def plan_launch_docker_gpu(vendor: str) -> CommandPlan:
    overlay = f"docker/gpu.{vendor}.yml"
    label = f"Launch Docker {vendor.upper()} GPU stack"
    return CommandPlan(
        label,
        (
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            overlay,
            "up",
            "-d",
            "--build",
        ),
    )


def launch_docker_gpu(args: argparse.Namespace, *, vendor: str) -> int:
    return run_plan(plan_launch_docker_gpu(vendor), dry_run=args.dry_run)


def plan_launch_macos(args: argparse.Namespace) -> CommandPlan:
    env: dict[str, str] = {}
    if getattr(args, "host", ""):
        env["ODYSSEUS_HOST"] = args.host
    if getattr(args, "port", None) is not None:
        env["ODYSSEUS_PORT"] = str(args.port)
    if getattr(args, "reload", False):
        env["ODYSSEUS_DEV_MODE"] = "1"
        env["ODYSSEUS_RELOAD"] = "1"
        env["ODYSSEUS_RELOAD_ACTIVE"] = "1"
    return CommandPlan("Launch native macOS script", ("./start-macos.sh",), env)


def launch_macos(args: argparse.Namespace) -> int:
    if platform.system() != "Darwin" and not args.dry_run:
        print("start-macos.sh is only a valid native launcher on macOS.", file=sys.stderr)
        return 2
    return run_plan(plan_launch_macos(args), dry_run=args.dry_run)


def plan_launch_windows(args: argparse.Namespace) -> CommandPlan:
    argv = ["powershell", "-ExecutionPolicy", "Bypass", "-File", ".\\launch-windows.ps1"]
    if getattr(args, "port", None) is not None:
        argv.extend(["-Port", str(args.port)])
    if getattr(args, "host", ""):
        argv.extend(["-BindHost", args.host])
    if getattr(args, "reload", False):
        argv.append("-Reload")
    return CommandPlan("Launch native Windows script", tuple(argv))


def launch_windows(args: argparse.Namespace) -> int:
    if platform.system() != "Windows" and not args.dry_run:
        print("launch-windows.ps1 is only a valid native launcher on Windows.", file=sys.stderr)
        return 2
    return run_plan(plan_launch_windows(args), dry_run=args.dry_run)


def plan_launch_launcher(args: argparse.Namespace) -> CommandPlan:
    argv = uv_run_prefix(args, requirements=True, env_file=env_file_for_launch(args))
    argv.extend(["python", "launcher.py"])
    env: dict[str, str] = {}
    if getattr(args, "host", ""):
        env["APP_BIND"] = args.host
    if getattr(args, "port", None) is not None:
        env["APP_PORT"] = str(args.port)
    if getattr(args, "reload", False):
        env["ODYSSEUS_DEV_MODE"] = "1"
        env["ODYSSEUS_RELOAD"] = "1"
        env["ODYSSEUS_RELOAD_ACTIVE"] = "1"
    return CommandPlan("Launch launcher.py", tuple(argv), env)


def launch_launcher(args: argparse.Namespace) -> int:
    return run_plan(plan_launch_launcher(args), dry_run=args.dry_run)


def plan_launch_nvim(args: argparse.Namespace) -> CommandPlan:
    # Match ody_term.py: reuse the shared constants when importable, else the
    # same documented defaults (the CLI runtime doesn't have src on the path).
    try:
        from src.constants import ODY_TERM_DEFAULT_HOST, ODY_TERM_DEFAULT_PORT
    except ImportError:
        ODY_TERM_DEFAULT_HOST, ODY_TERM_DEFAULT_PORT = "127.0.0.1", "7860"

    host = getattr(args, "host", "") or ODY_TERM_DEFAULT_HOST
    # Resolve the URL to connect to (not a port to bind), reusing the same
    # default host/port the ody-term client uses.
    port = getattr(args, "port", None) or int(ODY_TERM_DEFAULT_PORT)
    url = getattr(args, "url", "") or f"http://{host}:{port}"
    plugin_dir = REPO_ROOT / "clients" / "nvim" / "odysseus.nvim"
    argv: list[str] = ["nvim"]
    if getattr(args, "clean", False):
        argv.append("--clean")
    argv.extend([
        "--cmd", f"set runtimepath^={plugin_dir}",
        "-c", "runtime plugin/odysseus.lua",
        "-c", f"lua require('odysseus').setup({{ base_url = '{url}' }})",
        "-c", "Odysseus",
    ])
    return CommandPlan(f"Launch Neovim client ({url})", tuple(argv))


def launch_nvim(args: argparse.Namespace) -> int:
    if shutil.which("nvim") is None and not args.dry_run:
        print("nvim is not on PATH. Install Neovim 0.10+ to launch the client.", file=sys.stderr)
        return 2
    return run_plan(plan_launch_nvim(args), dry_run=args.dry_run)


def launch_candidates(args: argparse.Namespace) -> list[LaunchCandidate]:
    uv_ready = shutil.which("uv") is not None and REQUIREMENTS.exists()
    docker_ready = shutil.which("docker") is not None and (REPO_ROOT / "docker-compose.yml").exists()
    git_ready = (REPO_ROOT / ".git").exists()

    uv_args = with_defaults(
        args,
        host=getattr(args, "host", "") or "127.0.0.1",
        port=getattr(args, "port", None),
        reload=getattr(args, "reload", False),
        no_reload=getattr(args, "no_reload", False),
        auto_reload=getattr(args, "auto_reload", False) or getattr(args, "reload", False),
        env_file=getattr(args, "env_file", ""),
        no_env_file=getattr(args, "no_env_file", False),
    )
    launcher_args = with_defaults(
        args,
        host=getattr(args, "host", ""),
        port=getattr(args, "port", None),
        reload=getattr(args, "reload", False),
        env_file=getattr(args, "env_file", ""),
        no_env_file=getattr(args, "no_env_file", False),
    )
    script_args = with_defaults(
        args,
        host=getattr(args, "host", ""),
        port=getattr(args, "port", None),
        reload=getattr(args, "reload", False),
    )

    return [
        LaunchCandidate(
            "uv-dev",
            "uv run + developer-mode environment + interactive reload prompts",
            uv_ready and git_ready,
            "uv and git checkout present" if uv_ready and git_ready else "requires uv, requirements.txt, and .git",
            plan_launch_app(uv_args, dev=True),
            10,
        ),
        LaunchCandidate(
            "uv",
            "uv run + requirements.txt + uvicorn",
            uv_ready,
            "uv and requirements.txt present" if uv_ready else "requires uv and requirements.txt",
            plan_launch_app(uv_args, dev=False),
            20,
        ),
        LaunchCandidate(
            "launcher",
            "launcher.py through uv run",
            uv_ready and (REPO_ROOT / "launcher.py").exists(),
            "uv, requirements.txt, and launcher.py present"
            if uv_ready and (REPO_ROOT / "launcher.py").exists()
            else "requires uv, requirements.txt, and launcher.py",
            plan_launch_launcher(launcher_args),
            30,
        ),
        LaunchCandidate(
            "macos",
            "start-macos.sh native setup and launch",
            platform.system() == "Darwin" and (REPO_ROOT / "start-macos.sh").exists(),
            "macOS and start-macos.sh present"
            if platform.system() == "Darwin" and (REPO_ROOT / "start-macos.sh").exists()
            else "requires macOS and start-macos.sh",
            plan_launch_macos(script_args),
            40,
        ),
        LaunchCandidate(
            "windows",
            "launch-windows.ps1 native setup and launch",
            platform.system() == "Windows" and (REPO_ROOT / "launch-windows.ps1").exists(),
            "Windows and launch-windows.ps1 present"
            if platform.system() == "Windows" and (REPO_ROOT / "launch-windows.ps1").exists()
            else "requires Windows and launch-windows.ps1",
            plan_launch_windows(script_args),
            50,
        ),
        LaunchCandidate(
            "docker",
            "Docker Compose production stack",
            docker_ready,
            "docker and docker-compose.yml present" if docker_ready else "requires docker and docker-compose.yml",
            plan_launch_docker(dev=False),
            60,
        ),
        LaunchCandidate(
            "docker-dev",
            "Docker Compose stack with docker-compose.dev.yml",
            docker_ready and (REPO_ROOT / "docker-compose.dev.yml").exists(),
            "docker and dev Compose override present"
            if docker_ready and (REPO_ROOT / "docker-compose.dev.yml").exists()
            else "requires docker, docker-compose.yml, and docker-compose.dev.yml",
            plan_launch_docker(dev=True),
            70,
        ),
        LaunchCandidate(
            "docker-gpu-nvidia",
            "Docker Compose stack with NVIDIA GPU overlay",
            docker_ready and (REPO_ROOT / "docker/gpu.nvidia.yml").exists(),
            "docker and NVIDIA overlay present; validate host passthrough separately"
            if docker_ready and (REPO_ROOT / "docker/gpu.nvidia.yml").exists()
            else "requires docker, docker-compose.yml, and docker/gpu.nvidia.yml",
            plan_launch_docker_gpu("nvidia"),
            80,
        ),
        LaunchCandidate(
            "docker-gpu-amd",
            "Docker Compose stack with AMD GPU overlay",
            docker_ready and (REPO_ROOT / "docker/gpu.amd.yml").exists(),
            "docker and AMD overlay present; validate host passthrough separately"
            if docker_ready and (REPO_ROOT / "docker/gpu.amd.yml").exists()
            else "requires docker, docker-compose.yml, and docker/gpu.amd.yml",
            plan_launch_docker_gpu("amd"),
            90,
        ),
    ]


def print_launch_candidates(candidates: Sequence[LaunchCandidate]) -> None:
    print("Launch methods:")
    for candidate in sorted(candidates, key=lambda item: item.rank):
        marker = "ok" if candidate.valid else "no"
        print(f"  {marker:<2} {candidate.name:<18} {candidate.summary}")
        print(f"     {candidate.reason}")
    print()
    print(f"Select with: {PRIMARY_INVOCATION} launch select --method <name>")
    print(f"Dry-run with: {PRIMARY_INVOCATION} launch select --method <name> --dry-run")


def launch_select(args: argparse.Namespace) -> int:
    candidates = launch_candidates(args)
    if args.list or not args.method:
        print_launch_candidates(candidates)
        return 0

    if args.method == "auto":
        valid = [candidate for candidate in candidates if candidate.valid]
        if not valid:
            print_launch_candidates(candidates)
            print("No valid launch method found.", file=sys.stderr)
            return 2
        selected = sorted(valid, key=lambda item: item.rank)[0]
    else:
        selected = next((candidate for candidate in candidates if candidate.name == args.method), None)
        if selected is None:
            print(f"Unknown launch method: {args.method}", file=sys.stderr)
            return 2
        if not selected.valid and not args.dry_run:
            print_launch_candidates(candidates)
            print(f"Selected launch method is not valid here: {selected.reason}", file=sys.stderr)
            return 2

    print(f"Selected launch method: {selected.name}")
    if not selected.valid:
        print(f"Note: method is currently marked invalid: {selected.reason}")
    return run_plan(selected.plan, dry_run=args.dry_run)


def check_compile(args: argparse.Namespace) -> int:
    argv = uv_run_prefix(args, requirements=False)
    argv.extend(
        [
            "python",
            "-m",
            "compileall",
            "-q",
            "app.py",
            "core",
            "routes",
            "src",
            "services",
            "scripts",
            "tests",
        ]
    )
    return run_plan(CommandPlan("Compile Python", tuple(argv)), dry_run=args.dry_run)


def check_pytest(args: argparse.Namespace) -> int:
    pytest_args = clean_remainder(getattr(args, "pytest_args", [])) or ["-q"]
    argv = uv_run_prefix(args, requirements=True)
    argv.extend(["python", "-m", "pytest", *pytest_args])
    return run_plan(CommandPlan("Run pytest", tuple(argv)), dry_run=args.dry_run)


def check_focus(args: argparse.Namespace) -> int:
    focus_args = clean_remainder(getattr(args, "focus_args", []))
    argv = uv_run_prefix(args, requirements=True)
    argv.extend(["python", "tests/run_focus.py", *focus_args])
    return run_plan(CommandPlan("Run focused tests", tuple(argv)), dry_run=args.dry_run)


def check_ruff(args: argparse.Namespace) -> int:
    extra = clean_remainder(getattr(args, "ruff_args", []))
    argv = [
        "uvx",
        "--from",
        f"ruff=={RUFF_VERSION}",
        "ruff",
        "check",
        "app.py",
        "core",
        "routes",
        "src",
        "services",
        "scripts",
        "tests",
        *extra,
    ]
    return run_plan(CommandPlan("Run Ruff", tuple(argv)), dry_run=args.dry_run)


def check_ty(args: argparse.Namespace) -> int:
    extra = clean_remainder(getattr(args, "ty_args", []))
    argv = ["uvx", "--from", f"ty=={TY_VERSION}", "ty", "check", *extra]
    return run_plan(CommandPlan("Run ty", tuple(argv)), dry_run=args.dry_run)


def javascript_files() -> list[Path]:
    roots = [REPO_ROOT / "static", REPO_ROOT / "tests"]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        files.extend(path for path in root.rglob("*.js") if path.is_file())
    return sorted(files)


def check_js(args: argparse.Namespace) -> int:
    files = javascript_files()
    if not files:
        print("No JavaScript files found.")
        return 0
    if args.dry_run:
        display = " ".join(relative(path) for path in files[:8])
        suffix = "" if len(files) <= 8 else f" ... ({len(files)} files)"
        print("[Check JavaScript syntax]")
        print(f"$ node --check {display}{suffix}")
        return 0
    status = 0
    for path in files:
        plan = CommandPlan("Check JavaScript syntax", ("node", "--check", relative(path)))
        status = run_plan(plan, dry_run=False)
        if status != 0:
            return status
    return status


def check_audit(args: argparse.Namespace) -> int:
    audit_args = clean_remainder(getattr(args, "audit_args", []))
    argv = uv_run_prefix(args, requirements=False)
    argv.extend(["python", ".github/scripts/uv_audit_requirements.py", *audit_args])
    return run_plan(CommandPlan("Run uv audit helper", tuple(argv)), dry_run=args.dry_run)


def check_all(args: argparse.Namespace) -> int:
    for runner in (check_compile, check_js, check_ruff, check_ty):
        status = runner(args)
        if status != 0:
            return status
    if args.skip_tests:
        return 0
    args.pytest_args = ["-q"]
    return check_pytest(args)


def docker_command(args: argparse.Namespace) -> int:
    if args.action == "config":
        argv = ("docker", "compose", "config")
        label = "Render Docker Compose config"
    elif args.action == "up":
        argv = ("docker", "compose", "up", "-d", "--build")
        label = "Start Docker Compose stack"
    elif args.action == "dev-up":
        argv = (
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.dev.yml",
            "up",
            "-d",
            "--build",
        )
        label = "Start Docker Compose development stack"
    elif args.action == "logs":
        argv_list = ["docker", "compose", "logs", "--tail", str(args.tail)]
        if args.follow:
            argv_list.append("--follow")
        argv_list.append(args.service)
        argv = tuple(argv_list)
        label = "Tail Docker Compose logs"
    elif args.action == "ps":
        argv = ("docker", "compose", "ps")
        label = "Show Docker Compose status"
    elif args.action == "down":
        argv = ("docker", "compose", "down")
        label = "Stop Docker Compose stack"
    else:
        print(f"Unknown docker action: {args.action}", file=sys.stderr)
        return 2
    return run_plan(CommandPlan(label, argv), dry_run=args.dry_run)


def add_env_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--venv",
        default=relative(DEFAULT_VENV),
        help="Virtualenv path, relative to the repo root by default.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them.",
    )


def add_uv_python_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--python",
        default="",
        help="Python interpreter/version for uv. Defaults to ODYSSEUS_UV_PYTHON or uv's default.",
    )


def add_launch_options(parser: argparse.ArgumentParser, *, dev: bool) -> None:
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", default=None, type=int, help="Port to bind. Defaults to 7860 on macOS, 7000 elsewhere.")
    add_uv_python_option(parser)
    parser.add_argument(
        "--env-file",
        default="",
        help="Pass an env file to uv run. Defaults to .env when present.",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Do not pass .env to uv run even if it exists.",
    )
    if dev:
        parser.add_argument(
            "--auto-reload",
            action="store_true",
            help="Opt into uvicorn file watching. Default dev launches are interactive/manual.",
        )
        parser.add_argument(
            "--no-reload",
            action="store_true",
            help="Keep server reload manual. This is the default for developer-mode launches.",
        )
    else:
        parser.add_argument(
            "--reload",
            action="store_true",
            help="Enable uvicorn reload without the full developer-mode launch environment.",
        )
    parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")


def add_select_launch_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--method",
        choices=LAUNCH_METHODS,
        help="Launch method to execute. Omit to list available methods.",
    )
    parser.add_argument("--list", action="store_true", help="List methods instead of executing one.")
    parser.add_argument(
        "--host",
        default="",
        help="Host override for methods that accept a bind host.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port override for methods that accept a bind port.",
    )
    add_uv_python_option(parser)
    parser.add_argument(
        "--env-file",
        default="",
        help="Pass an env file to uv-backed methods. Defaults to .env when present.",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Do not pass .env to uv-backed methods even if it exists.",
    )
    parser.add_argument(
        "--auto-reload",
        action="store_true",
        help="Opt into uvicorn file watching for uv-dev. Default dev selection is interactive/manual.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Request auto reload where the selected method supports it.",
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable default reload for uv-dev selection.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")


def add_native_launch_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="", help="Host override for the native launcher.")
    parser.add_argument("--port", type=int, default=None, help="Port override for the native launcher.")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Request developer reload where the native launcher supports it.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")


def add_launcher_py_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="", help="APP_BIND override.")
    parser.add_argument("--port", type=int, default=None, help="APP_PORT override.")
    add_uv_python_option(parser)
    parser.add_argument(
        "--env-file",
        default="",
        help="Pass an env file to uv run. Defaults to .env when present.",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Do not pass .env to uv run even if it exists.",
    )
    parser.add_argument("--reload", action="store_true", help="Enable developer reload environment.")
    parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")


def add_check_common(parser: argparse.ArgumentParser) -> None:
    add_uv_python_option(parser)
    parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")


def set_topic_func(topic: str) -> Callable[[argparse.Namespace], int]:
    return lambda _args: print_topic(topic)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PRIMARY_INVOCATION,
        description="uv-based task and launch runner for Odysseus.",
    )
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="List discoverable tasks.")
    list_parser.add_argument("--all", action="store_true", help="Show all tasks, not only common tasks.")
    list_parser.add_argument(
        "--category",
        choices=sorted({entry.category for entry in CATALOG}),
        help="Only show tasks from one category.",
    )
    list_parser.set_defaults(func=list_entries)

    describe_parser = subparsers.add_parser("describe", help="Show details for one task.")
    describe_parser.add_argument("name", nargs="+", help="Task name, for example: launch dev")
    describe_parser.set_defaults(func=describe_entry)

    help_parser = subparsers.add_parser("help", help="Show progressive help topics.")
    help_parser.add_argument("topic", nargs="?", default="overview", choices=sorted(TOPICS))
    help_parser.set_defaults(func=lambda args: print_topic(args.topic))

    doctor_parser = subparsers.add_parser("doctor", help="Inspect local runner prerequisites.")
    doctor_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    doctor_parser.set_defaults(func=doctor)

    setup_parser = subparsers.add_parser("setup", help="Create venv and install requirements with uv.")
    add_env_options(setup_parser)
    setup_parser.add_argument("--python", default="3.13", help="Python version for uv venv.")
    setup_parser.add_argument(
        "--optional",
        action="store_true",
        help="Also install requirements-optional.txt.",
    )
    setup_parser.add_argument(
        "--skip-app-setup",
        action="store_true",
        help="Do not run the existing setup.py after installing requirements.",
    )
    setup_parser.set_defaults(func=setup_environment)

    install_parser = subparsers.add_parser("install", help="Install requirements into an existing venv.")
    add_env_options(install_parser)
    install_parser.add_argument(
        "--optional",
        action="store_true",
        help="Also install requirements-optional.txt.",
    )
    install_parser.set_defaults(func=install_environment)

    lock_parser = subparsers.add_parser("lock", help="Compile requirements with uv pip compile.")
    lock_parser.add_argument("--output", default="requirements.lock", help="Output lock file path.")
    lock_parser.add_argument("--optional", action="store_true", help="Include requirements-optional.txt.")
    lock_parser.add_argument("--upgrade", action="store_true", help="Upgrade pinned dependencies.")
    lock_parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")
    lock_parser.set_defaults(func=lock_environment)

    sync_parser = subparsers.add_parser("sync", help="Sync a lock file into the venv.")
    add_env_options(sync_parser)
    sync_parser.add_argument("--lock", default="requirements.lock", help="Lock file to sync.")
    sync_parser.set_defaults(func=sync_environment)

    launch_parser = subparsers.add_parser("launch", help="Launch app or Docker targets.")
    launch_parser.set_defaults(func=set_topic_func("launch"))
    launch_subparsers = launch_parser.add_subparsers(dest="launch_target")

    select_parser = launch_subparsers.add_parser("select", help="List or run selectable launch methods.")
    add_select_launch_options(select_parser)
    select_parser.set_defaults(func=launch_select)

    app_parser = launch_subparsers.add_parser("app", help="Run the app normally through uv.")
    add_launch_options(app_parser, dev=False)
    app_parser.set_defaults(func=lambda args: launch_app(args, dev=False))

    dev_parser = launch_subparsers.add_parser("dev", help="Run the app in developer mode through uv.")
    add_launch_options(dev_parser, dev=True)
    dev_parser.set_defaults(func=lambda args: launch_app(args, dev=True))

    launcher_parser = launch_subparsers.add_parser("launcher", help="Run launcher.py through uv.")
    add_launcher_py_options(launcher_parser)
    launcher_parser.set_defaults(func=launch_launcher)

    macos_parser = launch_subparsers.add_parser("macos", help="Run the native macOS launcher.")
    add_native_launch_options(macos_parser)
    macos_parser.set_defaults(func=launch_macos)

    windows_parser = launch_subparsers.add_parser("windows", help="Run the native Windows launcher.")
    add_native_launch_options(windows_parser)
    windows_parser.set_defaults(func=launch_windows)

    nvim_parser = launch_subparsers.add_parser("nvim", help="Launch the Neovim client (odysseus.nvim).")
    nvim_parser.add_argument("--url", default="", help="Server base URL. Overrides --host/--port.")
    nvim_parser.add_argument("--host", default="127.0.0.1", help="Server host.")
    nvim_parser.add_argument("--port", type=int, default=None, help="Server port. Defaults to 7860 on macOS, 7000 elsewhere.")
    nvim_parser.add_argument("--clean", action="store_true", help="Launch nvim with --clean (ignore your Neovim config).")
    nvim_parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")
    nvim_parser.set_defaults(func=launch_nvim)

    docker_launch_parser = launch_subparsers.add_parser("docker", help="Run Docker Compose production stack.")
    docker_launch_parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")
    docker_launch_parser.set_defaults(func=lambda args: launch_docker(args, dev=False))

    docker_dev_launch_parser = launch_subparsers.add_parser(
        "docker-dev", help="Run Docker Compose development stack."
    )
    docker_dev_launch_parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")
    docker_dev_launch_parser.set_defaults(func=lambda args: launch_docker(args, dev=True))

    docker_gpu_nvidia_parser = launch_subparsers.add_parser(
        "docker-gpu-nvidia", help="Run Docker Compose with the NVIDIA GPU overlay."
    )
    docker_gpu_nvidia_parser.add_argument(
        "--dry-run", action="store_true", help="Print command without executing it."
    )
    docker_gpu_nvidia_parser.set_defaults(func=lambda args: launch_docker_gpu(args, vendor="nvidia"))

    docker_gpu_amd_parser = launch_subparsers.add_parser(
        "docker-gpu-amd", help="Run Docker Compose with the AMD GPU overlay."
    )
    docker_gpu_amd_parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")
    docker_gpu_amd_parser.set_defaults(func=lambda args: launch_docker_gpu(args, vendor="amd"))

    check_parser = subparsers.add_parser("check", help="Run local checks.")
    check_parser.set_defaults(func=set_topic_func("check"))
    check_subparsers = check_parser.add_subparsers(dest="check_target")

    compile_parser = check_subparsers.add_parser("compile", help="Compile Python files.")
    add_check_common(compile_parser)
    compile_parser.set_defaults(func=check_compile)

    pytest_parser = check_subparsers.add_parser("pytest", help="Run pytest.")
    add_check_common(pytest_parser)
    pytest_parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="pytest args after --.")
    pytest_parser.set_defaults(func=check_pytest)

    focus_parser = check_subparsers.add_parser("focus", help="Run focused tests.")
    add_check_common(focus_parser)
    focus_parser.add_argument("focus_args", nargs=argparse.REMAINDER, help="focus harness args after --.")
    focus_parser.set_defaults(func=check_focus)

    ruff_parser = check_subparsers.add_parser("ruff", help="Run Ruff through uvx.")
    ruff_parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")
    ruff_parser.add_argument("ruff_args", nargs=argparse.REMAINDER, help="Ruff args after --.")
    ruff_parser.set_defaults(func=check_ruff)

    ty_parser = check_subparsers.add_parser("ty", help="Run ty through uvx.")
    ty_parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")
    ty_parser.add_argument("ty_args", nargs=argparse.REMAINDER, help="ty args after --.")
    ty_parser.set_defaults(func=check_ty)

    js_parser = check_subparsers.add_parser("js", help="Run node --check on JavaScript.")
    js_parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")
    js_parser.set_defaults(func=check_js)

    audit_parser = check_subparsers.add_parser("audit", help="Run uv audit helper.")
    add_check_common(audit_parser)
    audit_parser.add_argument("audit_args", nargs=argparse.REMAINDER, help="Audit helper args after --.")
    audit_parser.set_defaults(func=check_audit)

    all_parser = check_subparsers.add_parser("all", help="Run the normal local check bundle.")
    add_check_common(all_parser)
    all_parser.add_argument("--skip-tests", action="store_true", help="Skip pytest.")
    all_parser.set_defaults(func=check_all)

    docker_parser = subparsers.add_parser("docker", help="Docker Compose helpers.")
    docker_parser.set_defaults(func=set_topic_func("docker"))
    docker_subparsers = docker_parser.add_subparsers(dest="action")

    for action, help_text in (
        ("config", "Render Docker Compose config."),
        ("up", "Start the Docker Compose stack."),
        ("dev-up", "Start the Docker Compose development stack."),
        ("ps", "Show Docker Compose status."),
        ("down", "Stop the Docker Compose stack."),
    ):
        action_parser = docker_subparsers.add_parser(action, help=help_text)
        action_parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")
        action_parser.set_defaults(func=docker_command)

    logs_parser = docker_subparsers.add_parser("logs", help="Tail Docker Compose logs.")
    logs_parser.add_argument("--tail", default=200, type=int, help="Number of log lines to show.")
    logs_parser.add_argument("--follow", action="store_true", help="Follow logs.")
    logs_parser.add_argument("--service", default="odysseus", help="Compose service name.")
    logs_parser.add_argument("--dry-run", action="store_true", help="Print command without executing it.")
    logs_parser.set_defaults(func=docker_command)

    meta_parser = subparsers.add_parser("meta", help="Meta tasks for selecting among multiple valid paths.")
    meta_parser.set_defaults(func=set_topic_func("meta"))
    meta_subparsers = meta_parser.add_subparsers(dest="meta_target")

    meta_launch_parser = meta_subparsers.add_parser("launch", help="List or run selectable launch methods.")
    add_select_launch_options(meta_launch_parser)
    meta_launch_parser.set_defaults(func=launch_select)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args_list = list(argv if argv is not None else sys.argv[1:])
    if args_list[:1] == ["run"]:
        args_list = args_list[1:]
    if not args_list:
        print_overview()
        return 0
    args = parser.parse_args(args_list)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    return int(func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
