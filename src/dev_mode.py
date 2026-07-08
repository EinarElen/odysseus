"""Launch-gated developer mode helpers.

Developer mode is intentionally local-checkout oriented: it only enables when
the running application root is also the Git worktree root and the process was
started with ODYSSEUS_DEV_MODE=1.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.atomic_io import atomic_write_json
from src.constants import DEV_MODE_DIR
from src.runtime_paths import get_app_root

TRUE_VALUES = {"1", "true", "yes", "on", "y"}
OUTPUT_LIMIT = 16000
CLIENT_TTL_SECONDS = 15.0

FRONTEND_WATCH = (
    "static/index.html",
    "static/app.js",
    "static/style.css",
    "static/sw.js",
    "static/js",
)
FRONTEND_CODE_WATCH = (
    "static/index.html",
    "static/app.js",
    "static/sw.js",
    "static/js",
)
CSS_WATCH = ("static/style.css",)
SERVER_WATCH = (
    "app.py",
    "launcher.py",
    "src",
    "routes",
    "core",
    "services",
)
WATCH_EXTS = {
    ".py",
    ".js",
    ".mjs",
    ".css",
    ".html",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".txt",
    ".md",
    ".sh",
}
IGNORE_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "cache",
    "data",
    "dist",
    "logs",
    "node_modules",
    "pi",
    "t3code",
    "venv",
}

_CLIENTS: Dict[str, float] = {}
_CLIENT_LOCK = threading.Lock()


def _env_true(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in TRUE_VALUES


def dev_launch_requested() -> bool:
    return _env_true("ODYSSEUS_DEV_MODE")


def _configured_reload_mode() -> str:
    return (os.getenv("ODYSSEUS_RELOAD_MODE") or os.getenv("ODYSSEUS_DEV_RELOAD_MODE") or "").strip().lower()


def dev_reload_requested() -> bool:
    mode = _configured_reload_mode()
    if mode in {"interactive", "manual", "prompt"}:
        return False
    if mode == "auto":
        return dev_launch_requested() and not is_frozen()
    return (
        dev_launch_requested()
        and not is_frozen()
        and (_env_true("ODYSSEUS_RELOAD") or _env_true("ODYSSEUS_DEV_RELOAD"))
    )


def dev_reload_active() -> bool:
    return _env_true("ODYSSEUS_RELOAD_ACTIVE")


def dev_reload_mode() -> str:
    if dev_reload_active() or dev_reload_requested():
        return "auto"
    if dev_launch_requested():
        return "interactive"
    return "off"


def mark_client_seen(client_id: Optional[str]) -> None:
    value = (client_id or "").strip()[:96]
    if not value or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        return
    now = time.time()
    with _CLIENT_LOCK:
        _CLIENTS[value] = now
        _prune_clients(now)


def _prune_clients(now: Optional[float] = None) -> None:
    cutoff = (now if now is not None else time.time()) - CLIENT_TTL_SECONDS
    for key, seen_at in list(_CLIENTS.items()):
        if seen_at < cutoff:
            _CLIENTS.pop(key, None)


def active_client_count() -> int:
    with _CLIENT_LOCK:
        _prune_clients()
        return len(_CLIENTS)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def source_root() -> str:
    return os.path.realpath(get_app_root())


def uvicorn_reload_config() -> Dict[str, Any]:
    """Return uvicorn kwargs for source reload, or an empty dict."""

    if not dev_reload_requested():
        return {}
    os.environ["ODYSSEUS_RELOAD_ACTIVE"] = "1"
    return {
        "reload": True,
        "reload_dirs": [source_root()],
        "reload_excludes": [
            ".git/*",
            ".pytest_cache/*",
            "__pycache__/*",
            "cache/*",
            "data/*",
            "logs/*",
            "node_modules/*",
            "pi/*",
            "t3code/*",
            "venv/*",
        ],
    }


def _trim(text: str, limit: int = OUTPUT_LIMIT) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def run_command(
    argv: Sequence[str],
    cwd: str,
    *,
    timeout: float = 30,
    output_limit: int = OUTPUT_LIMIT,
) -> Dict[str, Any]:
    started = time.perf_counter()
    command = [str(part) for part in argv]
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": _trim(proc.stdout or "", output_limit),
            "stderr": _trim(proc.stderr or "", output_limit),
            "duration_s": round(time.perf_counter() - started, 3),
            "command": shlex.join(command),
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "exit_code": 127,
            "stdout": "",
            "stderr": str(exc),
            "duration_s": round(time.perf_counter() - started, 3),
            "command": shlex.join(command),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": 124,
            "stdout": _trim(exc.stdout or "", output_limit) if isinstance(exc.stdout, str) else "",
            "stderr": _trim(exc.stderr or "", output_limit) if isinstance(exc.stderr, str) else f"Timed out after {timeout:g}s",
            "duration_s": round(time.perf_counter() - started, 3),
            "command": shlex.join(command),
        }


def _git(args: Sequence[str], root: str, *, timeout: float = 8) -> Dict[str, Any]:
    return run_command(["git", *args], root, timeout=timeout)


def _git_top_level(root: str) -> Optional[str]:
    result = _git(["rev-parse", "--show-toplevel"], root)
    if not result["ok"]:
        return None
    return os.path.realpath((result["stdout"] or "").strip())


def _github_repo_from_remote(url: str) -> Optional[str]:
    value = (url or "").strip()
    if not value:
        return None
    match = re.search(r"github\.com[:/]+([^/\s:]+)/([^/\s]+?)(?:\.git)?/?$", value)
    if not match:
        return None
    owner = match.group(1)
    repo = re.sub(r"\.git$", "", match.group(2).rstrip("/"))
    if owner and repo:
        return f"{owner}/{repo}"
    return None


def _repo_remotes(root: str) -> List[Dict[str, str]]:
    result = _git(["remote", "-v"], root)
    if not result["ok"]:
        return []
    seen = set()
    remotes: List[Dict[str, str]] = []
    for line in result["stdout"].splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] != "(fetch)":
            continue
        key = (parts[0], parts[1])
        if key in seen:
            continue
        seen.add(key)
        remotes.append({"name": parts[0], "url": parts[1], "repo": _github_repo_from_remote(parts[1]) or ""})
    return remotes


def _changed_files(root: str) -> List[Dict[str, str]]:
    result = _git(["status", "--porcelain=v1", "--untracked-files=all"], root)
    if not result["ok"]:
        return []
    changed: List[Dict[str, str]] = []
    for line in result["stdout"].splitlines():
        if not line:
            continue
        status = line[:2]
        path = line[3:] if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        changed.append({"status": status.strip() or status, "path": path})
    return changed


def _branch(root: str) -> str:
    result = _git(["branch", "--show-current"], root)
    if result["ok"] and result["stdout"].strip():
        return result["stdout"].strip()
    result = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    return result["stdout"].strip() if result["ok"] else ""


def _commit(root: str) -> str:
    result = _git(["rev-parse", "--short=12", "HEAD"], root)
    return result["stdout"].strip() if result["ok"] else ""


def repo_status(root: Optional[str] = None) -> Dict[str, Any]:
    app_root = os.path.realpath(root or source_root())
    launch_requested = dev_launch_requested()
    frozen = is_frozen()
    git_root = None if frozen else _git_top_level(app_root)
    source_checkout = bool(git_root and os.path.realpath(git_root) == app_root)
    enabled = bool(launch_requested and not frozen and source_checkout)

    if not launch_requested:
        reason = "Set ODYSSEUS_DEV_MODE=1 before launch to enable developer mode."
    elif frozen:
        reason = "Developer mode is disabled in frozen or bundled builds."
    elif not git_root:
        reason = "Developer mode requires the running app directory to be a Git worktree."
    elif not source_checkout:
        reason = f"Developer mode requires app root and Git root to match (app={app_root}, git={git_root})."
    else:
        reason = "Developer mode enabled for the running local checkout."

    remotes = _repo_remotes(app_root) if git_root else []
    repo = next((r["repo"] for r in remotes if r.get("repo")), "")
    dirty_files = _changed_files(app_root) if git_root else []

    return {
        "enabled": enabled,
        "launch_requested": launch_requested,
        "reason": reason,
        "root": app_root,
        "git_root": git_root or "",
        "source_checkout": source_checkout,
        "frozen": frozen,
        "branch": _branch(app_root) if git_root else "",
        "commit": _commit(app_root) if git_root else "",
        "dirty_count": len(dirty_files),
        "dirty_files": dirty_files[:200],
        "reload_requested": dev_reload_requested(),
        "reload_active": dev_reload_active(),
        "reload_mode": dev_reload_mode(),
        "manual_reload_supported": not dev_reload_active(),
        "client_count": active_client_count(),
        "repo": repo,
        "remotes": remotes,
    }


def developer_context_note(workspace: Optional[str]) -> str:
    if not workspace:
        return ""
    status = repo_status()
    if not status["enabled"]:
        return ""
    try:
        workspace_root = os.path.realpath(workspace)
    except Exception:
        return ""
    if workspace_root != status["root"]:
        return ""
    return (
        "ODYSSEUS DEVELOPER MODE CONTEXT\n"
        f"The active workspace is the running Odysseus source checkout: {status['root']}.\n"
        "Optimize for the user's bespoke local version first. Preserve local customizations, inspect before editing, "
        "keep changes scoped, and do not assume upstream contribution unless the user asks for it.\n"
        "There may be unrelated uncommitted changes in this checkout; work with them and never discard them."
    )


def _iter_watch_files(root: str, rels: Iterable[str]) -> Iterable[Tuple[str, str]]:
    for rel in rels:
        full = os.path.join(root, rel)
        if os.path.isfile(full):
            if os.path.splitext(full)[1] in WATCH_EXTS:
                yield rel.replace(os.sep, "/"), full
            continue
        if not os.path.isdir(full):
            continue
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            for filename in filenames:
                ext = os.path.splitext(filename)[1]
                if ext not in WATCH_EXTS:
                    continue
                path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(path, root).replace(os.sep, "/")
                yield rel_path, path


def _watch_digest(root: str, rels: Iterable[str]) -> Dict[str, Any]:
    digest = hashlib.sha256()
    count = 0
    newest = 0
    for rel_path, path in sorted(_iter_watch_files(root, rels)):
        try:
            st = os.stat(path)
        except OSError:
            continue
        digest.update(rel_path.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        digest.update(str(st.st_mtime_ns).encode())
        digest.update(b"\0")
        digest.update(str(st.st_size).encode())
        digest.update(b"\n")
        count += 1
        newest = max(newest, st.st_mtime_ns)
    return {"token": digest.hexdigest()[:24], "count": count, "newest_mtime_ns": newest}


def revision(root: Optional[str] = None) -> Dict[str, Any]:
    app_root = os.path.realpath(root or source_root())
    frontend = _watch_digest(app_root, FRONTEND_WATCH)
    frontend_code = _watch_digest(app_root, FRONTEND_CODE_WATCH)
    css = _watch_digest(app_root, CSS_WATCH)
    server = _watch_digest(app_root, SERVER_WATCH)
    overall = hashlib.sha256((frontend["token"] + css["token"] + server["token"]).encode()).hexdigest()[:24]
    return {
        "root": app_root,
        "token": overall,
        "frontend_token": frontend["token"],
        "frontend_code_token": frontend_code["token"],
        "css_token": css["token"],
        "server_token": server["token"],
        "frontend_count": frontend["count"],
        "server_count": server["count"],
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "reload_active": dev_reload_active(),
        "reload_mode": dev_reload_mode(),
        "manual_reload_supported": not dev_reload_active(),
        "client_count": active_client_count(),
    }


def _server_reload_argv() -> List[str]:
    host = os.getenv("APP_BIND", "127.0.0.1")
    port = os.getenv("APP_PORT", "7000")
    return [sys.executable, "-m", "uvicorn", "app:app", "--host", host, "--port", str(port)]


def _restart_helper_code() -> str:
    return (
        "import json, os, subprocess, sys, time\n"
        "pid = int(sys.argv[1])\n"
        "cwd = sys.argv[2]\n"
        "delay = float(sys.argv[3])\n"
        "argv = json.loads(os.environ.pop('ODYSSEUS_RESTART_ARGV_JSON'))\n"
        "deadline = time.time() + 45\n"
        "time.sleep(delay)\n"
        "while time.time() < deadline:\n"
        "    try:\n"
        "        os.kill(pid, 0)\n"
        "    except OSError:\n"
        "        break\n"
        "    time.sleep(0.1)\n"
        "else:\n"
        "    sys.exit(2)\n"
        "os.chdir(cwd)\n"
        "child_env = os.environ.copy()\n"
        "subprocess.Popen(argv, cwd=cwd, env=child_env)\n"
    )


def _spawn_restart_helper(app_root: str, argv: List[str], env: Dict[str, str], delay_s: float) -> subprocess.Popen:
    helper_env = env.copy()
    helper_env["ODYSSEUS_RESTART_ARGV_JSON"] = json.dumps(argv)
    return subprocess.Popen(
        [sys.executable, "-c", _restart_helper_code(), str(os.getpid()), app_root, str(delay_s)],
        cwd=app_root,
        env=helper_env,
    )


def _request_graceful_exit() -> None:
    sig = getattr(signal, "SIGTERM", signal.SIGINT)
    if os.name == "nt":
        signal.raise_signal(sig)
    else:
        os.kill(os.getpid(), sig)


def request_server_reload(root: Optional[str] = None, *, delay_s: float = 0.35) -> Dict[str, Any]:
    app_root = os.path.realpath(root or source_root())
    status = repo_status(app_root)
    if not status.get("enabled"):
        return {"ok": False, "error": status.get("reason") or "Developer mode is disabled"}
    if dev_reload_active():
        return {
            "ok": False,
            "error": "Manual server restart is disabled while an external reload supervisor is active.",
            "mode": dev_reload_mode(),
        }

    argv = _server_reload_argv()
    env = os.environ.copy()
    env["ODYSSEUS_DEV_MODE"] = "1"
    env["ODYSSEUS_RELOAD_MODE"] = "interactive"
    env.pop("ODYSSEUS_RELOAD", None)
    env.pop("ODYSSEUS_DEV_RELOAD", None)
    env.pop("ODYSSEUS_RELOAD_ACTIVE", None)

    def _restart() -> None:
        try:
            _spawn_restart_helper(app_root, argv, env, delay_s)
        except Exception:
            return
        time.sleep(max(0.05, min(delay_s, 2.0)))
        _request_graceful_exit()

    threading.Thread(target=_restart, name="odysseus-dev-reload", daemon=True).start()
    return {
        "ok": True,
        "scheduled": True,
        "delay_s": delay_s,
        "command": shlex.join(argv),
        "mode": "interactive",
    }


def _changed_paths(root: str) -> List[str]:
    return [item["path"] for item in _changed_files(root)]


def _existing_changed(root: str, paths: Iterable[str], suffixes: Tuple[str, ...]) -> List[str]:
    selected = []
    for path in paths:
        if not path.endswith(suffixes):
            continue
        full = os.path.join(root, path)
        if os.path.isfile(full):
            selected.append(path)
    return selected


def _suggestion(kind: str, label: str, argv: Sequence[str], files: Sequence[str]) -> Dict[str, Any]:
    return {
        "id": kind,
        "label": label,
        "command": list(argv),
        "command_text": shlex.join([str(a) for a in argv]),
        "files": list(files),
    }


def test_suggestions(root: Optional[str] = None) -> Dict[str, Any]:
    app_root = os.path.realpath(root or source_root())
    changed = _changed_paths(app_root)
    py_files = _existing_changed(app_root, changed, (".py",))
    js_files = _existing_changed(app_root, changed, (".js",))
    test_files = [p for p in py_files if p.startswith("tests/")]

    suggestions: List[Dict[str, Any]] = []
    if py_files:
        limited = py_files[:60]
        suggestions.append(_suggestion(
            "python_compile_changed",
            "Compile changed Python",
            [sys.executable, "-m", "py_compile", *limited],
            limited,
        ))
    if js_files:
        limited = js_files[:60]
        suggestions.append(_suggestion(
            "node_check_changed",
            "Syntax-check changed JavaScript",
            ["node", "--check", *limited],
            limited,
        ))
    if test_files:
        limited = test_files[:30]
        suggestions.append(_suggestion(
            "pytest_changed",
            "Run changed tests",
            [sys.executable, "-m", "pytest", *limited, "-q"],
            limited,
        ))
    focus_runner = os.path.join(app_root, "tests", "run_focus.py")
    if os.path.isfile(focus_runner):
        suggestions.append(_suggestion(
            "pytest_fast",
            "Run focused fast suite",
            [sys.executable, "tests/run_focus.py", "--fast"],
            ["tests/run_focus.py"],
        ))
    suggestions.append(_suggestion(
        "git_status",
        "Show Git status",
        ["git", "status", "--short"],
        [],
    ))
    return {"root": app_root, "changed_files": changed[:200], "suggestions": suggestions}


def _suggestion_by_id(root: str, kind: str) -> Optional[Dict[str, Any]]:
    for item in test_suggestions(root).get("suggestions", []):
        if item.get("id") == kind:
            return item
    return None


def run_dev_check(kind: str, root: Optional[str] = None) -> Dict[str, Any]:
    app_root = os.path.realpath(root or source_root())
    allowed = {"python_compile_changed", "node_check_changed", "pytest_changed", "pytest_fast", "git_status"}
    if kind not in allowed:
        return {"ok": False, "exit_code": 2, "stderr": f"Unknown dev check: {kind}", "stdout": "", "command": ""}
    suggestion = _suggestion_by_id(app_root, kind)
    if not suggestion:
        return {"ok": False, "exit_code": 2, "stderr": f"No runnable suggestion for {kind}", "stdout": "", "command": ""}
    command = suggestion["command"]
    if command and shutil.which(str(command[0])) is None and os.path.sep not in str(command[0]):
        return {
            "ok": False,
            "exit_code": 127,
            "stdout": "",
            "stderr": f"Command not found: {command[0]}",
            "command": shlex.join(command),
        }
    result = run_command(command, app_root, timeout=180, output_limit=24000)
    result["id"] = kind
    result["label"] = suggestion.get("label", kind)
    return result


def git_snapshot(root: Optional[str] = None) -> Dict[str, Any]:
    app_root = os.path.realpath(root or source_root())
    return {
        "status": repo_status(app_root),
        "short_status": _git(["status", "--short"], app_root)["stdout"],
        "diff_stat": _git(["diff", "--stat"], app_root)["stdout"],
        "diff_name_only": _git(["diff", "--name-only"], app_root)["stdout"].splitlines(),
        "recent_log": _git(["log", "--oneline", "--decorate", "-10"], app_root)["stdout"],
    }


def app_introspection(app: Any) -> Dict[str, Any]:
    routes = []
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", "")
        if not path:
            continue
        methods = sorted(getattr(route, "methods", []) or [])
        routes.append({"path": path, "methods": methods, "name": getattr(route, "name", "")})
    tool_names: List[str] = []
    try:
        from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS

        tool_names = sorted(BUILTIN_TOOL_DESCRIPTIONS.keys())
    except Exception:
        tool_names = []
    settings_keys: List[str] = []
    try:
        from src.settings import load_settings

        settings = load_settings()
        settings_keys = sorted(settings.keys()) if isinstance(settings, dict) else []
    except Exception:
        settings_keys = []
    return {
        "routes": sorted(routes, key=lambda r: (r["path"], ",".join(r["methods"]))),
        "route_count": len(routes),
        "built_in_tools": tool_names,
        "built_in_tool_count": len(tool_names),
        "settings_keys": settings_keys,
    }


def _github_cache_file() -> str:
    return os.path.join(DEV_MODE_DIR, "github_cache.json")


def read_github_cache() -> Dict[str, Any]:
    path = _github_cache_file()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {"prs": {"items": []}, "issues": {"items": []}}
    except Exception as exc:
        return {"prs": {"items": []}, "issues": {"items": []}, "error": str(exc)}


def refresh_github_cache(kinds: Sequence[str], *, limit: int = 20, root: Optional[str] = None) -> Dict[str, Any]:
    app_root = os.path.realpath(root or source_root())
    status = repo_status(app_root)
    repo = status.get("repo") or ""
    cache = read_github_cache()
    errors: List[str] = []
    requested = set(kinds or ("prs", "issues"))
    now = datetime.now(timezone.utc).isoformat()

    if not repo:
        errors.append("No GitHub remote found for this checkout.")
    elif shutil.which("gh") is None:
        errors.append("GitHub CLI not found. Install gh and authenticate it to refresh PRs/issues.")
    else:
        if "prs" in requested:
            result = run_command(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    repo,
                    "--state",
                    "open",
                    "--limit",
                    str(limit),
                    "--json",
                    "number,title,author,updatedAt,url,mergeStateStatus,reviewDecision,isDraft",
                ],
                app_root,
                timeout=30,
            )
            if result["ok"]:
                try:
                    items = json.loads(result["stdout"] or "[]")
                except Exception as exc:
                    items = []
                    errors.append(f"Could not parse pull request JSON: {exc}")
                cache["prs"] = {"fetched_at": now, "repo": repo, "items": items}
            else:
                errors.append(result["stderr"] or "Could not refresh pull requests.")
        if "issues" in requested:
            result = run_command(
                [
                    "gh",
                    "issue",
                    "list",
                    "--repo",
                    repo,
                    "--state",
                    "open",
                    "--limit",
                    str(limit),
                    "--json",
                    "number,title,author,updatedAt,url,labels",
                ],
                app_root,
                timeout=30,
            )
            if result["ok"]:
                try:
                    items = json.loads(result["stdout"] or "[]")
                except Exception as exc:
                    items = []
                    errors.append(f"Could not parse issue JSON: {exc}")
                cache["issues"] = {"fetched_at": now, "repo": repo, "items": items}
            else:
                errors.append(result["stderr"] or "Could not refresh issues.")

    cache["errors"] = errors
    cache["updated_at"] = now
    os.makedirs(DEV_MODE_DIR, exist_ok=True)
    atomic_write_json(_github_cache_file(), cache, indent=2)
    return cache
