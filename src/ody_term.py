"""Terminal Client command spine for Odysseus.

This module intentionally starts with the public command contract rather than
backend behavior. Later tickets can attach API clients, profiles, auth, event
streams, and TUI state to the command handlers registered here.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator, TextIO, TypedDict, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from ody_term_tui import run_tui as run_interactive_tui

try:
    from src.constants import (
        COOKBOOK_STATE_FILE,
        ODY_TERM_CONFIG_FILE,
        ODY_TERM_DEFAULT_HOST,
        ODY_TERM_DEFAULT_PORT,
        ODY_TERM_RUNTIME_FILE,
        ODY_TERM_SECRETS_FILE,
        ODY_TERM_SERVER_LOG_FILE,
        SERVER_FAILED_LAUNCH_SHUTDOWN_TIMEOUT_S,
        SERVER_READINESS_POLL_INTERVAL_S,
        SERVER_READINESS_REQUEST_TIMEOUT_S,
        SERVER_READINESS_TIMEOUT_S,
        TERMINAL_API_TIMEOUT_S,
        TERMINAL_EVENT_STREAM_MEDIA_TYPE,
    )
except Exception:  # pragma: no cover - keeps standalone ody_term packaging usable.
    COOKBOOK_STATE_FILE = ""
    ODY_TERM_CONFIG_FILE = ""
    ODY_TERM_DEFAULT_HOST = "127.0.0.1"
    ODY_TERM_DEFAULT_PORT = "7860"
    ODY_TERM_RUNTIME_FILE = ""
    ODY_TERM_SECRETS_FILE = ""
    ODY_TERM_SERVER_LOG_FILE = ""
    TERMINAL_API_TIMEOUT_S = 30
    TERMINAL_EVENT_STREAM_MEDIA_TYPE = "application/x-ndjson"
    SERVER_READINESS_TIMEOUT_S = 30.0
    SERVER_READINESS_POLL_INTERVAL_S = 0.2
    SERVER_READINESS_REQUEST_TIMEOUT_S = 1.0
    SERVER_FAILED_LAUNCH_SHUTDOWN_TIMEOUT_S = 3.0


DOMAINS = ("auth", "config", "server", "session", "run", "harness", "service", "inspect", "tui")
OUTPUT_PROFILES = ("human", "grug", "clanker")
FORMATS = ("text", "json", "jsonl", "raw", "debug")
COLOR_MODES = ("auto", "always", "never")
TERMINAL_CAPABILITIES = (
    "session:read",
    "session:write",
    "run:read",
    "run:start",
    "run:stop",
    "event:read",
    "event:raw",
    "harness:read",
    "harness:control",
    "service:read",
    "service:restart",
    "service:kill",
    "auth:capabilities",
)
CONFIRMATION_CAPABILITIES = {"run:stop", "harness:control", "service:restart"}
ELEVATED_CAPABILITIES = {"service:kill"}
ADMIN_ONLY_CAPABILITIES = {"service:kill"}

ALIASES = (
    {
        "alias": "sessions",
        "canonical": ["session", "list"],
        "status": "reserved",
        "description": "Shortcut for listing durable Odysseus Sessions.",
    },
    {
        "alias": "runs",
        "canonical": ["run", "list"],
        "status": "reserved",
        "description": "Shortcut for listing active and recent Runs.",
    },
)

COMMANDS: dict[str, tuple[str, ...]] = {
    "auth": ("status", "login", "logout", "capabilities"),
    "config": ("show", "profile", "resolve-target"),
    "server": ("status", "start", "stop", "logs"),
    "session": ("list", "show", "history", "export"),
    "run": ("start", "list", "status", "attach", "stop"),
    "harness": ("list", "status", "attach", "stop"),
    "service": ("list", "status", "logs", "stop", "restart"),
    "inspect": ("domains", "aliases", "contracts", "globals", "events"),
    "tui": (),
}


class CommandError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = 2,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details


@dataclass
class GlobalOptions:
    target: str | None = None
    profile: str | None = None
    output: str | None = None
    format: str = "text"
    color: str = "auto"
    verbose: int = 0
    quiet: bool = False
    yes: bool = False
    yolo: bool = False
    start: bool = False
    ensure_server: bool = False


@dataclass
class CommandRequest:
    domain: str
    verb: str | None
    args: list[str] = field(default_factory=list)
    globals: GlobalOptions = field(default_factory=GlobalOptions)
    output_profile: str = "human"


@dataclass
class CommandResponse:
    ok: bool
    command: list[str]
    message: str
    data: dict[str, object] = field(default_factory=dict)
    raw: object | None = None
    event_stream: Iterable[dict[str, object]] | None = None


class EventFilters(TypedDict):
    source: str | None
    kind: str | None
    level: str | None
    session_id: str | None
    run_id: str | None
    harness_session_id: str | None
    span_id: str | None
    parent_id: str | None
    tag: str | None


def _usage() -> str:
    domains = ", ".join(DOMAINS)
    return (
        "usage: ody-term [global-options] <domain> <verb> [command-options]\n\n"
        f"domains: {domains}\n"
        "global options: --target, --profile, --output, --format, --color, -v/--verbose, "
        "-q/--quiet, --yes, --yolo, --start, --ensure-server\n"
        "output profiles: human, grug, clanker\n"
        "formats: text, json, jsonl, raw, debug\n"
    )


def _config_path() -> Path:
    override = os.getenv("ODY_TERM_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    if ODY_TERM_CONFIG_FILE:
        return Path(ODY_TERM_CONFIG_FILE).expanduser()
    return Path.home() / ".config" / "odysseus" / "ody-term.json"


def _runtime_state_path() -> Path:
    override = os.getenv("ODY_TERM_RUNTIME", "").strip()
    if override:
        return Path(override).expanduser()
    if ODY_TERM_RUNTIME_FILE:
        return Path(ODY_TERM_RUNTIME_FILE).expanduser()
    return Path.home() / ".local" / "state" / "odysseus" / "ody-term-runtime.json"


def _secrets_path() -> Path:
    override = os.getenv("ODY_TERM_SECRETS", "").strip()
    if override:
        return Path(override).expanduser()
    if ODY_TERM_SECRETS_FILE:
        return Path(ODY_TERM_SECRETS_FILE).expanduser()
    return _config_path().with_name("ody-term-secrets.json")


def _default_local_url() -> str:
    return f"http://{ODY_TERM_DEFAULT_HOST}:{ODY_TERM_DEFAULT_PORT}"


def _empty_config() -> dict[str, object]:
    return {"version": 1, "default_profile": None, "profiles": {}}


def _load_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommandError("corrupt_state_file", f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CommandError("corrupt_state_file", f"{path} must contain a JSON object")
    return data


def load_config() -> dict[str, object]:
    config = _empty_config()
    config.update(_load_json_object(_config_path()))
    profiles = config.get("profiles")
    if not isinstance(profiles, dict):
        raise CommandError("corrupt_config", "Terminal Client config profiles must be an object")
    return config


def save_config(config: dict[str, object]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _parse_command_options(args: list[str]) -> tuple[dict[str, str | bool], list[str]]:
    options: dict[str, str | bool] = {}
    positionals: list[str] = []
    index = 0
    value_flags = {
        "--url",
        "--repo",
        "--token-ref",
        "--profile-output",
        "--host",
        "--port",
        "--lines",
        "--token",
        "--scopes",
        "--source",
        "--kind",
        "--level",
        "--cursor",
        "--session-id",
        "--run-id",
        "--harness-session-id",
        "--harness-adapter",
        "--harness-mode",
        "--workspace",
        "--span-id",
        "--parent-id",
        "--tag",
        "--message",
        "--endpoint-url",
        "--model",
        "--preset-id",
        "--status",
        "--view",
        "--key",
        "--mouse",
        "--select-event",
        "--repl",
        "--export-format",
    }
    bool_flags = {"--default", "--dry-run", "--force"}
    while index < len(args):
        token = args[index]
        if token in value_flags:
            if index + 1 >= len(args):
                raise _needs_value(token)
            options[token[2:].replace("-", "_")] = args[index + 1]
            index += 2
        elif any(token.startswith(flag + "=") for flag in value_flags):
            name, value = token[2:].split("=", 1)
            options[name.replace("-", "_")] = value
            index += 1
        elif token in bool_flags:
            options[token[2:].replace("-", "_")] = True
            index += 1
        else:
            positionals.append(token)
            index += 1
    return options, positionals


def _profiles(config: dict[str, object]) -> dict[str, object]:
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        raise CommandError("corrupt_config", "Terminal Client config profiles must be an object")
    return cast(dict[str, object], profiles)


def _profile_payload(profile: object) -> dict[str, object]:
    if not isinstance(profile, dict):
        raise CommandError("corrupt_config", "Terminal Client profile must be an object")
    return cast(dict[str, object], dict(profile))


def _load_secrets() -> dict[str, object]:
    data = _load_json_object(_secrets_path())
    tokens = data.get("tokens")
    if tokens is None:
        data["tokens"] = {}
    elif not isinstance(tokens, dict):
        raise CommandError("corrupt_secret_store", "Terminal Client secret store tokens must be an object")
    return data


def _secret_backend_mode() -> str:
    return os.getenv("ODY_TERM_SECRET_BACKEND", "auto").strip().lower() or "auto"


def _keychain_available() -> bool:
    return sys.platform == "darwin" and shutil.which("security") is not None and _secret_backend_mode() != "file"


def _default_token_ref() -> str:
    if _secret_backend_mode() == "file":
        return "file:default"
    if _keychain_available():
        return "keychain:ody-term/default"
    return "file:default"


def _keychain_account(ref: str) -> str:
    return ref.split(":", 1)[1] if ":" in ref else ref


def _load_os_secret(ref: str) -> str | None:
    if not ref.startswith("keychain:") or not _keychain_available():
        return None
    result = subprocess.run(  # noqa: S603 - fixed macOS keychain command argv.
        ["security", "find-generic-password", "-s", "ody-term", "-a", _keychain_account(ref), "-w"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def _store_os_secret(ref: str, token: str) -> bool:
    if not ref.startswith("keychain:") or not _keychain_available():
        return False
    result = subprocess.run(  # noqa: S603 - fixed macOS keychain command argv.
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            "ody-term",
            "-a",
            _keychain_account(ref),
            "-w",
            token,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0


def _delete_os_secret(ref: str) -> bool:
    if not ref.startswith("keychain:") or not _keychain_available():
        return False
    result = subprocess.run(  # noqa: S603 - fixed macOS keychain command argv.
        ["security", "delete-generic-password", "-s", "ody-term", "-a", _keychain_account(ref)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0


def _save_secrets(secrets: dict[str, object]) -> None:
    path = _secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(secrets, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _parse_scopes(raw: object) -> list[str]:
    if isinstance(raw, str):
        parts = raw.replace(" ", ",").split(",")
    elif isinstance(raw, list):
        parts = [str(item) for item in raw]
    else:
        parts = []
    return sorted({part.strip() for part in parts if part.strip()})


def _auth_disabled() -> bool:
    return os.getenv("AUTH_ENABLED", "true").lower() == "false"


def _localhost_bypass_enabled() -> bool:
    return os.getenv("LOCALHOST_BYPASS", "false").lower() == "true"


def _selected_token_ref() -> str:
    config = load_config()
    profiles = _profiles(config)
    profile_name = config.get("default_profile")
    if isinstance(profile_name, str):
        profile = _profile_payload(profiles.get(profile_name, {}))
        token_ref = profile.get("token_ref")
        if isinstance(token_ref, str) and token_ref:
            return token_ref
    return _default_token_ref()


def _secret_token_entry(ref: str) -> dict[str, object]:
    secrets = _load_secrets()
    tokens = secrets.get("tokens", {})
    if not isinstance(tokens, dict):
        raise CommandError("corrupt_secret_store", "Terminal Client secret store tokens must be an object")
    entry = tokens.get(ref)
    metadata = cast(dict[str, object], dict(entry)) if isinstance(entry, dict) else {}
    os_secret = _load_os_secret(ref)
    if os_secret:
        return {**metadata, "token": os_secret, "storage_mode": "keychain"}
    return metadata


def _resolved_auth() -> dict[str, object]:
    env_token = os.getenv("ODY_TERM_TOKEN", "").strip()
    disabled = _auth_disabled()
    localhost_bypass = _localhost_bypass_enabled()

    token_ref = "env:ODY_TERM_TOKEN"
    token_present = bool(env_token)
    owner = None
    user = None
    scopes: list[str] = []
    is_admin = False
    storage = {
        "mode": "environment",
        "ref": token_ref,
        "visible_weaker_fallback": False,
    }

    if token_present:
        scopes = _parse_scopes(os.getenv("ODY_TERM_SCOPES", ""))

    if not token_present:
        token_ref = _selected_token_ref()
        entry = _secret_token_entry(token_ref)
        token_present = bool(entry.get("token"))
        scopes = _parse_scopes(entry.get("scopes")) if token_present else []
        storage_mode = "keychain" if entry.get("storage_mode") == "keychain" else "file-fallback"
        storage = {
            "mode": storage_mode,
            "ref": token_ref,
            "path": str(_secrets_path()) if storage_mode == "file-fallback" else None,
            "visible_weaker_fallback": bool(token_present and storage_mode == "file-fallback"),
        }

    if token_present:
        auth_mode = "token"
    elif disabled:
        auth_mode = "auth-disabled"
    elif localhost_bypass:
        auth_mode = "localhost-bypass"
    else:
        auth_mode = "none"

    return {
        "auth_mode": auth_mode,
        "user": user,
        "owner": owner,
        "is_admin": is_admin,
        "bypass_modes": {
            "auth_disabled": disabled,
            "localhost_bypass": localhost_bypass,
        },
        "token": {
            "present": token_present,
            "ref": token_ref if token_present else None,
            "scopes": scopes if token_present else [],
            "storage": storage if token_present else None,
        },
    }


def _capability_status(capability: str, auth: dict[str, object]) -> dict[str, object]:
    token = auth.get("token")
    token_payload = token if isinstance(token, dict) else {}
    scopes = set(_parse_scopes(token_payload.get("scopes")))
    auth_mode = auth.get("auth_mode")
    bypass = auth_mode in {"auth-disabled", "localhost-bypass"}
    is_admin = bool(auth.get("is_admin"))
    if auth_mode == "token" and not scopes:
        allowed = False
        reason = "server_capabilities_unavailable"
    else:
        allowed = bool(bypass or capability in scopes)
        reason = None if allowed else "missing_scope"
    if capability in ADMIN_ONLY_CAPABILITIES and not is_admin and not bypass:
        allowed = False
        reason = "admin_only"
    if capability in ADMIN_ONLY_CAPABILITIES and bypass:
        allowed = False
        reason = "admin_only"
    return {
        "resource": capability.split(":", 1)[0],
        "action": capability.split(":", 1)[1],
        "allowed": allowed,
        "requires_confirmation": capability in CONFIRMATION_CAPABILITIES or capability in ELEVATED_CAPABILITIES,
        "requires_yolo": capability in ELEVATED_CAPABILITIES,
        "admin_only": capability in ADMIN_ONLY_CAPABILITIES,
        "reason": reason,
    }


def _capabilities_payload() -> dict[str, object]:
    auth = _resolved_auth()
    token = cast(dict[str, object], auth["token"])
    token_scopes = _parse_scopes(token.get("scopes"))
    capabilities = {capability: _capability_status(capability, auth) for capability in TERMINAL_CAPABILITIES}
    return {
        "auth_facts": {
            "auth_mode": auth["auth_mode"],
            "user": auth["user"],
            "owner": auth["owner"],
            "is_admin": auth["is_admin"],
            "token_scopes": token_scopes,
        },
        "policy_facts": {
            "terminal_scopes": token_scopes,
            "bypass_modes": auth["bypass_modes"],
        },
        "capabilities": capabilities,
    }


def _require_capability(capability: str, request: CommandRequest) -> dict[str, object]:
    status = _capability_status(capability, _resolved_auth())
    if not status["allowed"]:
        raise CommandError(
            "capability_denied",
            f"{capability} is not allowed: {status['reason']}",
            exit_code=2,
        )
    if status["requires_yolo"]:
        if not request.globals.yolo:
            raise CommandError(
                "elevated_confirmation_required",
                f"{capability} requires --yolo",
                exit_code=2,
            )
        return {"capability": capability, "required": "elevated", "satisfied_by": "--yolo"}
    if status["requires_confirmation"]:
        if not request.globals.yes and not request.globals.yolo:
            raise CommandError(
                "confirmation_required",
                f"{capability} requires --yes",
                exit_code=2,
            )
        return {
            "capability": capability,
            "required": "ordinary",
            "satisfied_by": "--yolo" if request.globals.yolo else "--yes",
        }
    return {"capability": capability, "required": None, "satisfied_by": None}


def _resolve_target(request: CommandRequest) -> dict[str, object]:
    config = load_config()
    profiles = _profiles(config)
    profile_name = request.globals.profile
    considered: list[str] = []

    if request.globals.target:
        return {"ok": True, "url": request.globals.target, "source": "explicit-target", "considered": considered}
    considered.append("explicit-target")

    if profile_name:
        profile = _profile_payload(profiles.get(profile_name, {}))
        url = profile.get("url")
        if isinstance(url, str) and url:
            return {
                "ok": True,
                "url": url,
                "source": "explicit-profile",
                "profile": profile_name,
                "considered": considered,
            }
        return {
            "ok": False,
            "source": "explicit-profile",
            "profile": profile_name,
            "reason": "profile has no target URL",
            "considered": considered,
        }
    considered.append("explicit-profile")

    env_url = os.getenv("ODY_TERM_URL", "").strip() or os.getenv("ODYSSEUS_URL", "").strip()
    if env_url:
        return {"ok": True, "url": env_url, "source": "environment", "considered": considered}
    considered.append("environment")

    default_profile = config.get("default_profile")
    if isinstance(default_profile, str) and default_profile:
        profile = _profile_payload(profiles.get(default_profile, {}))
        url = profile.get("url")
        if isinstance(url, str) and url:
            return {
                "ok": True,
                "url": url,
                "source": "default-profile",
                "profile": default_profile,
                "considered": considered,
            }
    considered.append("default-profile")

    runtime_status = _server_status_payload()
    runtime = runtime_status.get("runtime_state")
    runtime_state = cast(dict[str, object], runtime) if isinstance(runtime, dict) else None
    runtime_url = runtime_state.get("url") if runtime_state is not None else None
    if runtime_status.get("status") == "running" and isinstance(runtime_url, str) and runtime_url:
        return {"ok": True, "url": runtime_url, "source": "runtime-state", "considered": considered}
    considered.append("runtime-state")

    if request.globals.start or request.globals.ensure_server:
        started = _start_server(host=ODY_TERM_DEFAULT_HOST, port=None, dry_run=False)
        server = cast(dict[str, object], started.data["server"])
        state = cast(dict[str, object], server["runtime_state"])
        url = state.get("url") if isinstance(state, dict) else None
        return {
            "ok": True,
            "url": url,
            "source": "local-bootstrap",
            "considered": considered,
            "server": started.data["server"],
        }

    if request.output_profile == "human" or request.domain == "tui":
        return {
            "ok": True,
            "url": _default_local_url(),
            "source": "localhost-fallback",
            "considered": considered,
            "reachable": "unchecked",
        }

    return {
        "ok": False,
        "source": "unresolved",
        "reason": "no target URL found for non-interactive command",
        "considered": considered,
    }


def _token_value() -> str | None:
    env_token = os.getenv("ODY_TERM_TOKEN", "").strip()
    if env_token:
        return env_token
    token_ref = _selected_token_ref()
    entry = _secret_token_entry(token_ref)
    token = entry.get("token")
    return str(token) if isinstance(token, str) and token else None


def _terminal_api_url_and_headers(
    request: CommandRequest,
    path: str,
    *,
    query: dict[str, object] | None,
    accept: str,
) -> tuple[str, dict[str, str]]:
    target = _resolve_target(request)
    if not target.get("ok") or not isinstance(target.get("url"), str):
        raise CommandError(
            "target_unresolved",
            str(target.get("reason") or "no target URL found for terminal-client API command"),
            details={"target": target},
        )
    base = str(target["url"]).rstrip("/")
    query_string = f"?{urlencode({k: v for k, v in (query or {}).items() if v is not None})}" if query else ""
    headers = {"Accept": accept}
    token = _token_value()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return f"{base}{path}{query_string}", headers


def _terminal_api_http_error(exc: HTTPError) -> CommandError:
    try:
        details = json.loads(exc.read().decode("utf-8") or "{}")
    except Exception:
        details = {"status": exc.code}
    return CommandError(
        "terminal_api_error",
        f"terminal-client API returned HTTP {exc.code}",
        exit_code=1,
        details=details,
    )


def _terminal_api_request(
    request: CommandRequest,
    method: str,
    path: str,
    *,
    query: dict[str, object] | None = None,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    url, headers = _terminal_api_url_and_headers(request, path, query=query, accept="application/json")
    payload = json.dumps(body or {}).encode("utf-8") if body is not None else None
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = UrlRequest(url, data=payload, headers=headers, method=method)
    try:
        with urlopen(req, timeout=TERMINAL_API_TIMEOUT_S) as response:  # noqa: S310 - user-selected target.
            data = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        raise _terminal_api_http_error(exc) from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise CommandError("terminal_api_unavailable", f"terminal-client API request failed: {exc}", exit_code=1) from exc
    if not isinstance(data, dict):
        raise CommandError("terminal_api_error", "terminal-client API response must be a JSON object", exit_code=1)
    return cast(dict[str, object], data)


def _terminal_api_event_stream(
    request: CommandRequest,
    path: str,
    *,
    query: dict[str, object] | None = None,
) -> Iterator[dict[str, object]]:
    url, headers = _terminal_api_url_and_headers(request, path, query=query, accept=TERMINAL_EVENT_STREAM_MEDIA_TYPE)
    req = UrlRequest(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=None) as response:  # noqa: S310 - intentional live tail on user-selected target.
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise CommandError("terminal_api_error", "terminal event stream item must be a JSON object", exit_code=1)
                yield cast(dict[str, object], event)
    except HTTPError as exc:
        raise _terminal_api_http_error(exc) from exc
    except CommandError:
        raise
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommandError("terminal_api_unavailable", f"terminal event stream failed: {exc}", exit_code=1) from exc


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _server_log_path() -> Path:
    if ODY_TERM_SERVER_LOG_FILE:
        return Path(ODY_TERM_SERVER_LOG_FILE).expanduser()
    return _runtime_state_path().with_name("ody-term-server.log")


def _launcher_command(*, host: str, port: str | None, dry_run: bool) -> list[str]:
    command = ["uv", "run", "ody", "launch", "select", "--method", "uv-dev", "--host", host]
    if port:
        command.extend(["--port", port])
    if dry_run:
        command.append("--dry-run")
    return command


def _process_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_group_matches(pid: object, pgid: object) -> bool:
    if not isinstance(pid, int) or not isinstance(pgid, int) or pid <= 0 or pgid <= 0:
        return False
    try:
        return os.getpgid(pid) == pgid
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def _server_state() -> dict[str, object]:
    state = _load_json_object(_runtime_state_path())
    if not state:
        return {}
    if state.get("kind") != "ody-term-local-server":
        raise CommandError("ambiguous_runtime_state", "runtime state is not owned by ody-term local server")
    return state


def _save_server_state(state: dict[str, object]) -> None:
    path = _runtime_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _server_status_payload() -> dict[str, object]:
    state = _server_state()
    if not state:
        return {"status": "absent", "runtime_state": None}
    alive = _process_alive(state.get("pid")) and _process_group_matches(state.get("pid"), state.get("pgid"))
    return {
        "status": "running" if alive else "stale",
        "runtime_state": state,
        "ownership": {
            "kind": state.get("kind"),
            "repo": state.get("repo"),
            "command": state.get("command"),
            "pid": state.get("pid"),
            "pgid": state.get("pgid"),
        },
    }


def _tail_file(path: Path, lines: int) -> list[str]:
    if lines <= 0:
        return []
    if not path.exists():
        return []
    text_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return text_lines[-lines:]


def _event_level_from_text(text: str) -> str:
    lowered = text.lower()
    if "error" in lowered or "exception" in lowered or "traceback" in lowered:
        return "error"
    if "warn" in lowered:
        return "warn"
    if "debug" in lowered:
        return "debug"
    if "trace" in lowered:
        return "trace"
    return "info"


def _bounded_summary(text: str, limit: int = 160) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "..."


def _server_log_events(*, lines: int, cursor: int | None) -> dict[str, object]:
    state = _server_state()
    log_path = Path(str(state.get("log_path") or _server_log_path()))
    raw_lines = _tail_file(log_path, max(lines, 0))
    if log_path.exists():
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            total_lines = sum(1 for _ in handle)
        first_seq = max(1, total_lines - len(raw_lines) + 1)
    else:
        first_seq = 1
    base_time = str(state.get("started_at") or _utc_now())
    events: list[dict[str, object]] = []
    for offset, line in enumerate(raw_lines):
        seq = first_seq + offset
        if cursor is not None and seq <= cursor:
            continue
        event: dict[str, object] = {
            "schema": "ody.event.v1",
            "id": f"evt_server_log_{seq}",
            "seq": seq,
            "time": base_time,
            "source": "server",
            "kind": "log",
            "level": _event_level_from_text(line),
            "summary": _bounded_summary(line),
            "payload": {"message": line},
            "raw": {
                "transport": "log",
                "type": "server.log",
                "body": {"line": line},
            },
        }
        events.append(event)
    next_cursor = str(events[-1]["seq"]) if events else (str(cursor) if cursor is not None else None)
    return {
        "events": events,
        "cursor": {"after": str(cursor) if cursor is not None else None, "next": next_cursor, "count": len(events)},
        "source": {"type": "local-server-log", "path": str(log_path)},
    }


def _filter_events(
    events: list[dict[str, object]],
    *,
    filters: EventFilters,
) -> list[dict[str, object]]:
    filtered = events
    for key in ("source", "kind", "level", "session_id", "run_id", "harness_session_id", "span_id", "parent_id"):
        value = filters[key]
        if value:
            filtered = [event for event in filtered if event.get(key) == value]
    if filters["tag"]:
        filtered = [
            event
            for event in filtered
            if isinstance(event.get("tags"), list) and filters["tag"] in cast(list[object], event["tags"])
        ]
    return filtered


RUN_ACTIVE_STATUSES = {"queued", "starting", "running", "waiting", "stopping"}
TUI_VIEWS = ("Live", "REPL", "Browse", "Inspect")
TUI_KEYBOARD_BINDINGS = {
    "f1": "Live",
    "f2": "REPL",
    "f3": "Browse",
    "f4": "Inspect",
    "1": "Live",
    "2": "REPL",
    "3": "Browse",
    "4": "Inspect",
    "tab": "next-view",
    "shift+tab": "previous-view",
    "enter": "activate-selection",
}
TUI_MOUSE_BINDINGS = {
    "tab_click": "switch-view",
    "event_click": "select-event",
    "tree_click": "select-node",
    "control_click": "queue-repl-command",
}
TUI_COMMANDS = (
    {
        "command": "status",
        "description": "Show selected Run or Lifecycle Target status",
        "capability": "run:read",
    },
    {
        "command": "tail",
        "description": "Follow Event Envelopes for the selected Run",
        "capability": "event:read",
    },
    {
        "command": "filter",
        "description": "Filter events by source, kind, level, identity, or tag",
        "capability": "event:read",
    },
    {
        "command": "stop",
        "description": "Request bounded Run stop through the Run lifecycle",
        "capability": "run:stop",
    },
    {
        "command": "harness",
        "description": "Attempt supported harness adapter controls",
        "capability": "harness:control",
    },
    {
        "command": "service",
        "description": "Attempt managed Lifecycle Target controls",
        "capability": "service:restart",
    },
)


def _run_api_path(run_id: str | None, session_id: str | None, suffix: str = "") -> str:
    if run_id:
        return f"/api/terminal/runs/{run_id}{suffix}"
    if session_id:
        return f"/api/terminal/runs/by-session/{session_id}{suffix}"
    raise CommandError("missing_run_target", "run command requires a run id or --session-id")


def _api_run_events(request: CommandRequest, runs: list[dict[str, object]]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for run in runs:
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run.get("events_available"):
            continue
        payload = _terminal_api_request(
            request,
            "GET",
            "/api/terminal/events",
            query={"run_id": run_id, "limit": 80, "include_raw": False},
        )
        raw_events = payload.get("events")
        if isinstance(raw_events, list):
            events.extend(cast(dict[str, object], event) for event in raw_events if isinstance(event, dict))
    return events


def _merged_tui_events(
    events: list[dict[str, object]],
    *,
    lifecycle_targets: list[dict[str, object]],
) -> list[dict[str, object]]:
    events = list(events)
    main_server = next((target for target in lifecycle_targets if target.get("id") == "main-server"), None)
    if main_server is not None:
        try:
            payload = _server_log_events(lines=20, cursor=None)
            events.extend(cast(list[dict[str, object]], payload["events"]))
        except CommandError:
            pass
    events.sort(key=lambda event: (str(event.get("time") or ""), str(event.get("source") or ""), int(event.get("seq") or 0)))
    return events


def _run_start(request: CommandRequest) -> CommandResponse:
    _require_capability("run:start", request)
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_run_args", f"unexpected run start args: {' '.join(positionals)}")
    kind = str(options.get("kind") or "chat")
    if kind not in {"chat", "agent", "harness"}:
        raise CommandError("unsupported_run_kind", f"run start supports chat, agent, and harness Runs, not {kind}")
    if kind == "harness" and not isinstance(options.get("harness_adapter"), str):
        raise CommandError("missing_harness_adapter", "harness Runs require --harness-adapter")
    payload = _terminal_api_request(
        request,
        "POST",
        "/api/terminal/runs",
        body={
            "kind": kind,
            "session_id": options.get("session_id") if isinstance(options.get("session_id"), str) else None,
            "message": str(options.get("message") or ""),
            "endpoint_url": options.get("endpoint_url") if isinstance(options.get("endpoint_url"), str) else None,
            "model": options.get("model") if isinstance(options.get("model"), str) else None,
            "preset_id": options.get("preset_id") if isinstance(options.get("preset_id"), str) else None,
            "harness_adapter_id": options.get("harness_adapter") if isinstance(options.get("harness_adapter"), str) else None,
            "harness_session_id": options.get("harness_session_id") if isinstance(options.get("harness_session_id"), str) else None,
            "harness_mode": str(options.get("harness_mode") or "observe"),
            "workspace": options.get("workspace") if isinstance(options.get("workspace"), str) else None,
        },
    )
    return CommandResponse(
        ok=True,
        command=["run", "start"],
        message=f"Started {kind} Run {cast(dict[str, object], payload.get('run', {})).get('run_id', '')}",
        data=payload,
    )


def _run_list(request: CommandRequest) -> CommandResponse:
    _require_capability("run:read", request)
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_run_args", f"unexpected run list args: {' '.join(positionals)}")
    kind_filter = str(options.get("kind")) if isinstance(options.get("kind"), str) else None
    status_filter = str(options.get("status")) if isinstance(options.get("status"), str) else None
    payload = _terminal_api_request(
        request,
        "GET",
        "/api/terminal/runs",
        query={"kind": kind_filter, "status": status_filter},
    )
    runs = payload.get("runs")
    return CommandResponse(
        ok=True,
        command=["run", "list"],
        message=f"{len(runs) if isinstance(runs, list) else 0} Run(s)",
        data={"runs": runs if isinstance(runs, list) else []},
    )


def _session_list(request: CommandRequest) -> CommandResponse:
    _require_capability("session:read", request)
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_session_args", f"unexpected session list args: {' '.join(positionals)}")
    payload = _terminal_api_request(request, "GET", "/api/terminal/sessions")
    sessions = payload.get("sessions")
    return CommandResponse(
        ok=True,
        command=["session", "list"],
        message=f"{len(sessions) if isinstance(sessions, list) else 0} Session(s)",
        data={"sessions": sessions if isinstance(sessions, list) else []},
    )


def _session_read(request: CommandRequest) -> CommandResponse:
    _require_capability("session:read", request)
    options, positionals = _parse_command_options(request.args)
    if len(positionals) != 1:
        raise CommandError("missing_session", f"session {request.verb} requires exactly one Session id")
    session_id = positionals[0]
    suffix = "/history" if request.verb == "history" else ""
    query = None
    if request.verb == "export":
        suffix = "/export"
        export_format = str(options.get("export_format") or "md")
        if export_format not in {"md", "txt", "json"}:
            raise CommandError("invalid_export_format", "--export-format must be md, txt, or json")
        query = {"format": export_format}
    payload = _terminal_api_request(request, "GET", f"/api/terminal/sessions/{session_id}{suffix}", query=query)
    return CommandResponse(
        ok=True,
        command=["session", str(request.verb)],
        message=f"Session {session_id} {request.verb}",
        data=payload,
        raw=payload.get("content") if request.verb == "export" else None,
    )
def _run_status(request: CommandRequest) -> CommandResponse:
    _require_capability("run:read", request)
    options, positionals = _parse_command_options(request.args)
    if len(positionals) > 1:
        raise CommandError("unexpected_run_args", f"unexpected run status args: {' '.join(positionals[1:])}")
    run_id = positionals[0] if positionals else (str(options["run_id"]) if isinstance(options.get("run_id"), str) else None)
    session_id = str(options["session_id"]) if isinstance(options.get("session_id"), str) else None
    payload = _terminal_api_request(request, "GET", _run_api_path(run_id, session_id))
    run = cast(dict[str, object], payload.get("run", {}))
    return CommandResponse(
        ok=True,
        command=["run", "status"],
        message=f"Run {run.get('run_id', run_id or session_id)} is {run.get('status', 'unknown')}",
        data={"run": run},
    )


def _run_attach(request: CommandRequest) -> CommandResponse:
    capability = "event:raw" if request.globals.format in {"raw", "debug"} else "event:read"
    _require_capability(capability, request)
    options, positionals = _parse_command_options(request.args)
    if len(positionals) > 1:
        raise CommandError("unexpected_run_args", f"unexpected run attach args: {' '.join(positionals[1:])}")
    run_id = positionals[0] if positionals else (str(options["run_id"]) if isinstance(options.get("run_id"), str) else None)
    session_id = str(options["session_id"]) if isinstance(options.get("session_id"), str) else None
    raw_cursor = options.get("cursor")
    try:
        cursor = int(raw_cursor) if isinstance(raw_cursor, str) and raw_cursor else None
    except ValueError as exc:
        raise CommandError("invalid_cursor", f"--cursor must be an integer: {raw_cursor}") from exc
    include_raw = request.globals.format in {"raw", "debug"}
    event_stream: Iterable[dict[str, object]] | None = None
    if request.globals.format == "jsonl":
        event_stream = _terminal_api_event_stream(
            request,
            "/api/terminal/events/stream",
            query={
                "run_id": run_id,
                "session_id": session_id,
                "cursor": cursor,
                "include_raw": include_raw,
            },
        )
        payload: dict[str, object] = {
            "events": [],
            "cursor": {
                "after": str(cursor) if cursor is not None else None,
                "next": None,
                "count": None,
            },
        }
    else:
        payload = _terminal_api_request(
            request,
            "GET",
            _run_api_path(run_id, session_id, "/events"),
            query={"cursor": cursor, "include_raw": include_raw},
        )
    events = payload.get("events")
    return CommandResponse(
        ok=True,
        command=["run", "attach"],
        message=f"{len(events) if isinstance(events, list) else 0} Run Event Envelope(s)",
        data=payload,
        raw=[event.get("raw") for event in events if isinstance(event, dict)] if isinstance(events, list) else [],
        event_stream=event_stream,
    )


def _run_stop(request: CommandRequest) -> CommandResponse:
    confirmation = _require_capability("run:stop", request)
    options, positionals = _parse_command_options(request.args)
    if len(positionals) > 1:
        raise CommandError("unexpected_run_args", f"unexpected run stop args: {' '.join(positionals[1:])}")
    run_id = positionals[0] if positionals else (str(options["run_id"]) if isinstance(options.get("run_id"), str) else None)
    session_id = str(options["session_id"]) if isinstance(options.get("session_id"), str) else None
    payload = _terminal_api_request(request, "POST", _run_api_path(run_id, session_id, "/stop"))
    run = cast(dict[str, object], payload.get("run", {}))
    stopped = bool(payload.get("stopped"))
    return CommandResponse(
        ok=stopped,
        command=["run", "stop"],
        message=f"Stopped Run {run.get('run_id', run_id)}" if stopped else f"Run {run.get('run_id', run_id)} was not stopped",
        data={"run": run, "stopped": stopped, "confirmation": confirmation},
    )


def _harness_capabilities() -> list[dict[str, object]]:
    try:
        from src.harness import list_harness_capabilities
    except Exception:
        from harness import list_harness_capabilities  # type: ignore[no-redef]

    return cast(list[dict[str, object]], list_harness_capabilities())


def _harness_capability(adapter_id: str) -> dict[str, object]:
    for capability in _harness_capabilities():
        if capability.get("id") == adapter_id:
            return capability
    raise CommandError("unknown_harness_adapter", f"unknown harness adapter: {adapter_id}", exit_code=1)


def _harness_list(request: CommandRequest) -> CommandResponse:
    _require_capability("harness:read", request)
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_harness_args", f"unexpected harness list args: {' '.join(positionals)}")
    capabilities = _harness_capabilities()
    return CommandResponse(
        ok=True,
        command=["harness", "list"],
        message=f"{len(capabilities)} Harness adapter(s)",
        data={"harnesses": capabilities},
    )


def _harness_status(request: CommandRequest) -> CommandResponse:
    _require_capability("harness:read", request)
    options, positionals = _parse_command_options(request.args)
    if len(positionals) > 1:
        raise CommandError("unexpected_harness_args", f"unexpected harness status args: {' '.join(positionals[1:])}")
    adapter_id = positionals[0] if positionals else str(options.get("harness_adapter") or "")
    if not adapter_id:
        raise CommandError("missing_harness_adapter", "harness status requires an adapter id")
    capability = _harness_capability(adapter_id)
    payload = _terminal_api_request(request, "GET", "/api/terminal/runs", query={"kind": "harness"})
    runs = payload.get("runs")
    linked_runs = (
        [cast(dict[str, object], run) for run in runs if isinstance(run, dict) and run.get("harness_adapter_id") == adapter_id]
        if isinstance(runs, list)
        else []
    )
    return CommandResponse(
        ok=True,
        command=["harness", "status"],
        message=f"Harness adapter {adapter_id}",
        data={"harness": capability, "runs": linked_runs},
    )


def _harness_stop(request: CommandRequest) -> CommandResponse:
    _require_capability("harness:control", request)
    options, positionals = _parse_command_options(request.args)
    if len(positionals) > 1:
        raise CommandError("unexpected_harness_args", f"unexpected harness stop args: {' '.join(positionals[1:])}")
    run_id = positionals[0] if positionals else (str(options["run_id"]) if isinstance(options.get("run_id"), str) else None)
    session_id = str(options["session_id"]) if isinstance(options.get("session_id"), str) else None
    payload = _terminal_api_request(request, "GET", _run_api_path(run_id, session_id))
    run = cast(dict[str, object], payload.get("run", {}))
    if run.get("kind") != "harness" or not run.get("harness_adapter_id"):
        raise CommandError(
            "not_harness_run",
            f"Run {run.get('run_id')} is not a harness-linked Run",
            exit_code=1,
            details={"run_id": run.get("run_id"), "kind": run.get("kind")},
        )
    adapter_id = str(run.get("harness_adapter_id") or options.get("harness_adapter") or "")
    if not adapter_id:
        raise CommandError("missing_harness_adapter", "harness stop requires a harness-linked Run or --harness-adapter")
    capability = _harness_capability(adapter_id)
    session_caps = capability.get("session") if isinstance(capability.get("session"), dict) else {}
    if not bool(cast(dict[str, object], session_caps).get("abort")):
        raise CommandError(
            "unsupported_harness_action",
            f"harness adapter {adapter_id} does not support abort",
            exit_code=1,
            details={"adapter": adapter_id, "action": "abort", "supported": False},
        )
    request.args = ["--run-id", str(run["run_id"]), *request.args]
    return _run_stop(request)


def _managed_runtime_snapshot_events(
    request: CommandRequest,
    *,
    source: str,
    cursor: int | None,
    limit: int,
    targets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if cursor is not None:
        raise CommandError(
            "snapshot_cursor_unsupported",
            f"{source} managed-runtime snapshots do not support continuation cursors",
            exit_code=1,
        )
    targets = _lifecycle_targets(request) if targets is None else targets
    records: list[tuple[str, dict[str, object]]] = []
    if source == "service":
        records = [("lifecycle.status", target) for target in targets]
    elif source == "process":
        for target in targets:
            raw = target.get("raw") if isinstance(target.get("raw"), dict) else {}
            runtime = cast(dict[str, object], raw).get("runtime_state") if isinstance(raw, dict) else None
            if isinstance(runtime, dict) and isinstance(runtime.get("pid"), int):
                records.append(("process.status", target))
    elif source == "system":
        server = next((target for target in targets if target.get("id") == "main-server"), None)
        if server is not None:
            records = [("system.status", server)]

    events: list[dict[str, object]] = []
    include_raw = request.globals.format in {"raw", "debug"}
    if limit <= 0:
        return {
            "events": [],
            "cursor": {"after": None, "next": None, "count": 0, "mode": "snapshot"},
            "source": "managed-runtime-snapshot",
        }
    for seq, (kind, target) in enumerate(records, start=1):
        target_id = str(target.get("id") or f"target-{seq}")
        payload = {
            "target_id": target_id,
            "target_kind": target.get("kind"),
            "status": target.get("status"),
            "label": target.get("label"),
            "ownership": target.get("ownership"),
            "capabilities": target.get("capabilities"),
            "last_activity": target.get("last_activity"),
        }
        event: dict[str, object] = {
            "schema": "ody.event.v1",
            "id": f"evt_{source}_{target_id.replace(':', '_')}",
            "seq": seq,
            "time": target.get("last_activity") or _utc_now(),
            "source": source,
            "kind": kind,
            "level": "warn" if target.get("status") in {"error", "stale", "unknown"} else "info",
            "summary": f"{target.get('label') or target_id} is {target.get('status') or 'unknown'}",
            "payload": payload,
        }
        if include_raw:
            event["raw"] = {"transport": "managed-runtime-snapshot", "target": target}
        events.append(event)
        if len(events) >= limit:
            break
    return {
        "events": events,
        "cursor": {"after": None, "next": None, "count": len(events), "mode": "snapshot"},
        "source": "managed-runtime-snapshot",
    }


def _inspect_events(request: CommandRequest) -> CommandResponse:
    capability = "event:raw" if request.globals.format in {"raw", "debug"} else "event:read"
    _require_capability(capability, request)
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_inspect_args", f"unexpected inspect events args: {' '.join(positionals)}")
    raw_lines = options.get("lines")
    try:
        lines = int(raw_lines) if isinstance(raw_lines, str) else 80
    except ValueError as exc:
        raise CommandError("invalid_lines", f"--lines must be an integer: {raw_lines}") from exc
    raw_cursor = options.get("cursor")
    try:
        cursor = int(raw_cursor) if isinstance(raw_cursor, str) and raw_cursor else None
    except ValueError as exc:
        raise CommandError("invalid_cursor", f"--cursor must be an integer: {raw_cursor}") from exc
    filters: EventFilters = {
        "source": str(options["source"]) if isinstance(options.get("source"), str) else None,
        "kind": str(options["kind"]) if isinstance(options.get("kind"), str) else None,
        "level": str(options["level"]) if isinstance(options.get("level"), str) else None,
        "session_id": str(options["session_id"]) if isinstance(options.get("session_id"), str) else None,
        "run_id": str(options["run_id"]) if isinstance(options.get("run_id"), str) else None,
        "harness_session_id": str(options["harness_session_id"])
        if isinstance(options.get("harness_session_id"), str)
        else None,
        "span_id": str(options["span_id"]) if isinstance(options.get("span_id"), str) else None,
        "parent_id": str(options["parent_id"]) if isinstance(options.get("parent_id"), str) else None,
        "tag": str(options["tag"]) if isinstance(options.get("tag"), str) else None,
    }
    run_id = filters["run_id"]
    session_id = filters["session_id"]
    use_terminal_api = bool(run_id or session_id) and filters["source"] != "server"
    event_stream: Iterable[dict[str, object]] | None = None
    if filters["source"] in {"service", "process", "system"} and not run_id and not session_id:
        payload = _managed_runtime_snapshot_events(
            request,
            source=str(filters["source"]),
            cursor=cursor,
            limit=max(lines, 0),
        )
    elif use_terminal_api:
        query: dict[str, object] = {
            "run_id": run_id,
            "session_id": session_id,
            "cursor": cursor,
            "source": filters["source"],
            "kind": filters["kind"],
            "level": filters["level"],
            "include_raw": request.globals.format in {"raw", "debug"},
        }
        if request.globals.format == "jsonl":
            query["batch_limit"] = max(lines, 1)
            source_stream = _terminal_api_event_stream(request, "/api/terminal/events/stream", query=query)
            event_stream = (
                event
                for event in source_stream
                if _filter_events([event], filters=filters)
            )
            payload = {
                "events": [],
                "cursor": {"after": str(cursor) if cursor is not None else None, "next": None, "count": None},
                "source": "terminal-api-stream",
            }
        else:
            query["limit"] = max(lines, 0)
            payload = _terminal_api_request(request, "GET", "/api/terminal/events", query=query)
            payload["source"] = "terminal-api"
    else:
        payload = _server_log_events(lines=max(lines, 0), cursor=cursor)
    events = cast(list[dict[str, object]], payload["events"])
    events = _filter_events(events, filters=filters)
    cursor_payload = cast(dict[str, object], payload["cursor"])
    cursor_payload["count"] = len(events)
    capability = _capability_status(capability, _resolved_auth())
    data = {
        "events": events,
        "cursor": cursor_payload,
        "filters": filters,
        "source": payload["source"],
        "capability": capability,
        "safety": {
            "capability": f"{capability['resource']}:{capability['action']}",
            "raw_mode": request.globals.format in {"raw", "debug"},
            "identity_fields_absent_when_unknown": True,
        },
    }
    return CommandResponse(
        ok=True,
        command=["inspect", "events"],
        message=f"{len(events)} Event Envelope(s)",
        data=data,
        raw=[event.get("raw") for event in events],
        event_stream=event_stream,
    )


def _static_lifecycle_targets() -> list[dict[str, object]]:
    return [
        {
            "id": "model-serving",
            "kind": "model-serving",
            "label": "Model serving",
            "status": "unknown",
            "ownership": {"managed_by": "odysseus", "source": "configured-endpoints"},
            "source": {"type": "configured-endpoints"},
            "last_activity": None,
            "capabilities": {"logs": False, "stop": False, "restart": False, "force": False},
            "raw": {"note": "HTTP/API-first visibility placeholder for configured model-serving endpoints"},
        },
        {
            "id": "mcp",
            "kind": "mcp",
            "label": "MCP servers",
            "status": "unknown",
            "ownership": {"managed_by": "odysseus", "source": "builtin-mcp"},
            "source": {"type": "builtin-mcp"},
            "last_activity": None,
            "capabilities": {"logs": False, "stop": False, "restart": False, "force": False},
            "raw": {"note": "MCP lifecycle is visible as a managed target group, not a raw host process group"},
        },
        {
            "id": "cookbook-serving",
            "kind": "cookbook",
            "label": "Cookbook serving",
            "status": "unknown",
            "ownership": {"managed_by": "odysseus", "source": "cookbook-lifecycle"},
            "source": {"type": "cookbook-lifecycle"},
            "last_activity": None,
            "capabilities": {"logs": False, "stop": False, "restart": False, "force": False},
            "raw": {"note": "Cookbook task lifecycle is visible as a managed target group"},
        },
        {
            "id": "service-health",
            "kind": "service-health",
            "label": "Service health",
            "status": "unknown",
            "ownership": {"managed_by": "odysseus", "source": "service-health"},
            "source": {"type": "service-health"},
            "last_activity": None,
            "capabilities": {"logs": False, "stop": False, "restart": False, "force": False},
            "raw": {"note": "Supporting service health checks are inspectable but not mutable lifecycle targets"},
        },
    ]


def _cookbook_task_lifecycle_targets() -> list[dict[str, object]]:
    if not COOKBOOK_STATE_FILE:
        return []
    path = Path(COOKBOOK_STATE_FILE).expanduser()
    if not path.exists():
        return []
    state = _load_json_object(path)
    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        return []
    targets: list[dict[str, object]] = []
    for task in tasks:
        if not isinstance(task, dict) or task.get("type") != "serve":
            continue
        session_id = str(task.get("sessionId") or task.get("id") or "").strip()
        if not session_id:
            continue
        status = str(task.get("status") or "unknown")
        output = task.get("output")
        targets.append(
            {
                "id": f"cookbook:{session_id}",
                "kind": "cookbook",
                "label": str(task.get("name") or task.get("modelId") or session_id),
                "status": status,
                "ownership": {
                    "managed_by": "odysseus",
                    "session_id": session_id,
                    "owner": task.get("_scheduledByOwner"),
                    "task": task.get("_scheduledByTask"),
                    "remote_host": task.get("remoteHost"),
                },
                "source": {"type": "cookbook-state", "path": str(path), "session_id": session_id},
                "last_activity": task.get("_lastStatusFlipAt") or task.get("ts"),
                "capabilities": {
                    "logs": isinstance(output, str) and bool(output),
                    "stop": False,
                    "restart": False,
                    "force": False,
                },
                "raw": {"task": dict(task)},
            }
        )
    return targets


def _server_lifecycle_target() -> dict[str, object]:
    server = _server_status_payload()
    raw_runtime_state = server.get("runtime_state")
    runtime_state = cast(dict[str, object], raw_runtime_state) if isinstance(raw_runtime_state, dict) else None
    raw = {"runtime_state": runtime_state, "server": server}
    ownership = server.get("ownership") if isinstance(server.get("ownership"), dict) else {}
    return {
        "id": "main-server",
        "kind": "server",
        "label": "Main Odysseus server",
        "status": server.get("status"),
        "ownership": ownership,
        "source": {"type": "local-server-runtime", "url": runtime_state.get("url") if runtime_state else None},
        "last_activity": runtime_state.get("started_at") if runtime_state else None,
        "capabilities": {
            "logs": True,
            "stop": server.get("status") == "running",
            "restart": server.get("status") == "running",
            "force": False,
        },
        "raw": raw,
    }


def _api_runs(request: CommandRequest) -> list[dict[str, object]]:
    payload = _terminal_api_request(request, "GET", "/api/terminal/runs")
    runs = payload.get("runs")
    return [cast(dict[str, object], run) for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []


def _run_lifecycle_targets(runs: list[dict[str, object]]) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for run in runs:
        run_id = str(run.get("run_id") or "")
        if not run_id:
            continue
        status = str(run.get("status") or "unknown")
        targets.append(
            {
                "id": f"run:{run_id}",
                "kind": "run",
                "label": f"{run.get('kind', 'run')} Run {run_id}",
                "status": status,
                "ownership": {
                    "managed_by": "odysseus",
                    "session_id": run.get("session_id"),
                    "run_id": run_id,
                    "harness_session_id": run.get("harness_session_id"),
                },
                "source": {
                    "type": "terminal-client-api",
                    "run_kind": run.get("kind"),
                    "event_source": run.get("kind"),
                    "harness_adapter_id": run.get("harness_adapter_id"),
                },
                "last_activity": run.get("last_activity"),
                "capabilities": {
                    "logs": bool(run.get("events_available")),
                    "stop": status in RUN_ACTIVE_STATUSES,
                    "restart": False,
                    "force": False,
                },
                "raw": {"run": dict(run)},
            }
        )
    return targets


def _harness_bridge_lifecycle_targets(runs: list[dict[str, object]]) -> list[dict[str, object]]:
    active_runs_by_adapter: dict[str, list[dict[str, object]]] = {}
    for run in runs:
        adapter_id = run.get("harness_adapter_id")
        if isinstance(adapter_id, str) and adapter_id:
            active_runs_by_adapter.setdefault(adapter_id, []).append(dict(run))
    targets: list[dict[str, object]] = []
    for harness in _harness_capabilities():
        adapter_id = str(harness.get("id") or "")
        if not adapter_id:
            continue
        linked_runs = active_runs_by_adapter.get(adapter_id, [])
        running = any(str(run.get("status")) in RUN_ACTIVE_STATUSES for run in linked_runs)
        targets.append(
            {
                "id": f"harness-bridge:{adapter_id}",
                "kind": "harness-bridge",
                "label": f"{harness.get('label') or adapter_id} harness bridge",
                "status": "running" if running else "available",
                "ownership": {"managed_by": "odysseus", "adapter_id": adapter_id},
                "source": {"type": "harness-registry", "adapter_id": adapter_id},
                "last_activity": linked_runs[0].get("last_activity") if linked_runs else None,
                "capabilities": {"logs": bool(linked_runs), "stop": False, "restart": False, "force": False},
                "raw": {"harness": harness, "runs": linked_runs},
            }
        )
    return targets


def _lifecycle_targets(request: CommandRequest) -> list[dict[str, object]]:
    try:
        runs = _api_runs(request)
    except CommandError:
        # Local server/cookbook/static targets remain inspectable when the
        # authenticated Run API target is unavailable. Do not resurrect
        # client-local Run fixtures here.
        runs = []
    targets = [_server_lifecycle_target()]
    targets.extend(_run_lifecycle_targets(runs))
    targets.extend(_harness_bridge_lifecycle_targets(runs))
    targets.extend(_cookbook_task_lifecycle_targets())
    targets.extend(_static_lifecycle_targets())
    return targets


def _lifecycle_target(request: CommandRequest, target_id: str) -> dict[str, object]:
    for target in _lifecycle_targets(request):
        if target.get("id") == target_id:
            return target
    raise CommandError("unknown_lifecycle_target", f"unknown managed lifecycle target: {target_id}", exit_code=1)


def _unsupported_lifecycle_action(target: dict[str, object], action: str) -> CommandError:
    return CommandError(
        "unsupported_lifecycle_action",
        f"{target.get('id')} does not support service {action}",
        exit_code=1,
        details={"target": target.get("id"), "action": action, "supported": False},
    )


def _service_logs(request: CommandRequest) -> CommandResponse:
    _require_capability("service:read", request)
    options, positionals = _parse_command_options(request.args)
    if len(positionals) != 1:
        raise CommandError("missing_lifecycle_target", "service logs requires exactly one managed target id")
    target = _lifecycle_target(request, positionals[0])
    raw_lines = options.get("lines")
    try:
        lines = int(raw_lines) if isinstance(raw_lines, str) else 80
    except ValueError as exc:
        raise CommandError("invalid_lines", f"--lines must be an integer: {raw_lines}") from exc
    if target["id"] == "main-server":
        payload = _server_log_events(lines=max(lines, 0), cursor=None)
        events = cast(list[dict[str, object]], payload["events"])
        return CommandResponse(
            ok=True,
            command=["service", "logs"],
            message=f"{len(events)} Lifecycle Target log Event Envelope(s)",
            data={"target": target, "events": events, "cursor": payload["cursor"], "source": payload["source"]},
            raw=[event.get("raw") for event in events],
        )
    if str(target.get("id", "")).startswith("run:"):
        run_id = str(target["id"]).split(":", 1)[1]
        payload = _terminal_api_request(
            request,
            "GET",
            f"/api/terminal/runs/{run_id}/events",
            query={"cursor": None, "include_raw": request.globals.format in {"raw", "debug"}},
        )
        raw_events = payload.get("events")
        events = [cast(dict[str, object], event) for event in raw_events if isinstance(event, dict)] if isinstance(raw_events, list) else []
        bounded_lines = max(lines, 0)
        events = events[-bounded_lines:] if bounded_lines else []
        return CommandResponse(
            ok=True,
            command=["service", "logs"],
            message=f"{len(events)} Run lifecycle Event Envelope(s)",
            data={"target": target, "events": events, "cursor": {"after": None, "next": str(len(events)) if events else None, "count": len(events)}},
            raw=[event.get("raw") for event in events],
        )
    if str(target.get("id", "")).startswith("cookbook:"):
        raw_task = target.get("raw") if isinstance(target.get("raw"), dict) else {}
        task = cast(dict[str, object], raw_task).get("task")
        task_payload = cast(dict[str, object], task) if isinstance(task, dict) else {}
        output = task_payload.get("output")
        lines_payload = str(output).splitlines()[-max(lines, 0) :] if isinstance(output, str) else []
        events = [
            {
                "schema": "ody.event.v1",
                "id": f"evt_{target['id']}_log_{index + 1}",
                "seq": index + 1,
                "time": _utc_now(),
                "source": "cookbook",
                "kind": "log",
                "level": _event_level_from_text(line),
                "summary": _bounded_summary(line),
                "payload": {"message": line},
                "raw": {"transport": "cookbook-state", "type": "cookbook.output", "body": {"line": line}},
            }
            for index, line in enumerate(lines_payload)
        ]
        return CommandResponse(
            ok=True,
            command=["service", "logs"],
            message=f"{len(events)} Cookbook lifecycle Event Envelope(s)",
            data={
                "target": target,
                "events": events,
                "cursor": {"after": None, "next": str(len(events)) if events else None, "count": len(events)},
            },
            raw=[event.get("raw") for event in events],
        )
    capabilities = target.get("capabilities") if isinstance(target.get("capabilities"), dict) else {}
    if not bool(cast(dict[str, object], capabilities).get("logs")):
        raise _unsupported_lifecycle_action(target, "logs")
    return CommandResponse(
        ok=True,
        command=["service", "logs"],
        message="No lifecycle logs available",
        data={"target": target, "events": [], "cursor": {"after": None, "next": None, "count": 0}},
        raw=[],
    )


def _stop_run_lifecycle_target(
    request: CommandRequest,
    target: dict[str, object],
    confirmation: dict[str, object],
) -> CommandResponse:
    run_id = str(target["id"]).split(":", 1)[1]
    _terminal_api_request(request, "POST", f"/api/terminal/runs/{run_id}/stop")
    stopped = _lifecycle_target(request, str(target["id"]))
    return CommandResponse(
        ok=True,
        command=["service", "stop"],
        message=f"Stopped lifecycle target {target['id']}",
        data={"target": stopped, "confirmation": confirmation},
    )


def _stop_main_server_lifecycle_target(
    request: CommandRequest,
    target: dict[str, object],
    confirmation: dict[str, object],
) -> CommandResponse:
    if target.get("status") != "running":
        raise _unsupported_lifecycle_action(target, "stop")
    state = _server_state()
    pid = state.get("pid")
    if state.get("repo") != str(_repo_root()) or not isinstance(state.get("command"), list):
        raise CommandError("ambiguous_runtime_state", "runtime state ownership evidence does not match this checkout")
    if not _process_alive(pid) or not _process_group_matches(pid, state.get("pgid")):
        raise CommandError("ambiguous_runtime_state", "runtime state ownership evidence does not match this checkout")
    assert isinstance(pid, int)
    os.killpg(pid, signal.SIGTERM)
    state["stopped_at"] = _utc_now()
    _save_server_state(state)
    stopped = _lifecycle_target(request, "main-server")
    return CommandResponse(
        ok=True,
        command=["service", "stop"],
        message="Main server stop signal sent",
        data={"target": stopped, "confirmation": confirmation},
    )


def _service_control(request: CommandRequest) -> CommandResponse:
    options, positionals = _parse_command_options(request.args)
    if len(positionals) != 1:
        raise CommandError("missing_lifecycle_target", f"service {request.verb} requires exactly one managed target id")
    target_id = positionals[0]
    if target_id.startswith("pid:"):
        raise CommandError(
            "arbitrary_process_unsupported",
            "service commands require managed lifecycle target ids, not raw host PIDs",
        )
    capability = "service:kill" if options.get("force") else "service:restart"
    confirmation = _require_capability(capability, request)
    target = _lifecycle_target(request, target_id)
    capabilities = target.get("capabilities") if isinstance(target.get("capabilities"), dict) else {}
    target_capabilities = cast(dict[str, object], capabilities)
    if options.get("force") and not bool(target_capabilities.get("force")):
        raise _unsupported_lifecycle_action(target, "force")
    verb = str(request.verb)
    if not bool(target_capabilities.get("stop" if verb == "stop" else "restart")):
        raise _unsupported_lifecycle_action(target, verb)
    if verb == "stop" and target_id == "main-server":
        return _stop_main_server_lifecycle_target(request, target, confirmation)
    if verb == "stop" and target_id.startswith("run:"):
        return _stop_run_lifecycle_target(request, target, confirmation)
    if verb == "restart" and target_id == "main-server":
        if options.get("dry_run"):
            return CommandResponse(
                ok=True,
                command=["service", "restart"],
                message="Main server restart plan",
                data={"target": target, "confirmation": confirmation, "delegates_to": "uv run ody launch select"},
            )
        if target.get("status") == "running":
            _stop_main_server_lifecycle_target(request, target, confirmation)
        started = _start_server(host=ODY_TERM_DEFAULT_HOST, port=None, dry_run=False)
        return CommandResponse(
            ok=True,
            command=["service", "restart"],
            message="Main server restart delegated",
            data={"target": _lifecycle_target(request, "main-server"), "confirmation": confirmation, "server": started.data["server"]},
        )
    raise _unsupported_lifecycle_action(target, verb)


def _wait_for_server_readiness(*, process: subprocess.Popen[bytes], url: str) -> str | None:
    """Wait for the owned local process to answer the unauthenticated liveness probe."""
    deadline = time.monotonic() + SERVER_READINESS_TIMEOUT_S
    health_url = f"{url.rstrip('/')}/api/health"
    last_error = "connection refused"
    while True:
        exit_code = process.poll()
        if exit_code is not None:
            return f"process exited with status {exit_code} before it became ready"
        try:
            request = UrlRequest(health_url, headers={"Accept": "application/json"}, method="GET")
            with urlopen(request, timeout=SERVER_READINESS_REQUEST_TIMEOUT_S) as response:  # noqa: S310 - URL is constructed from validated local start options.
                if 200 <= response.status < 300:
                    return None
                last_error = f"health endpoint returned HTTP {response.status}"
        except HTTPError as exc:
            last_error = f"health endpoint returned HTTP {exc.code}"
        except (OSError, URLError) as exc:
            last_error = str(exc.reason) if isinstance(exc, URLError) else str(exc)
        if time.monotonic() >= deadline:
            return f"did not become ready within {SERVER_READINESS_TIMEOUT_S:g}s ({last_error})"
        time.sleep(min(SERVER_READINESS_POLL_INTERVAL_S, max(0.0, deadline - time.monotonic())))


def _terminate_owned_server_process(process: subprocess.Popen[bytes], *, pgid: int) -> None:
    """Stop the captured owned process group and reap a live leader when possible."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if process.poll() is None:
        try:
            process.wait(timeout=SERVER_FAILED_LAUNCH_SHUTDOWN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pass

    # The leader can exit while children remain in its original process group.
    # Keep using the PGID captured at launch; deriving it from the dead leader
    # would skip cleanup of those surviving children.
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if process.poll() is None:
        try:
            process.wait(timeout=SERVER_FAILED_LAUNCH_SHUTDOWN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pass


def _start_server(*, host: str, port: str | None, dry_run: bool) -> CommandResponse:
    launcher = _launcher_command(host=host, port=port, dry_run=True)
    command = ["server", "start"]
    if dry_run:
        return CommandResponse(
            ok=True,
            command=command,
            message="Local server launch plan",
            data={"launcher": launcher, "delegates_to": "uv run ody launch select"},
        )

    log_path = _server_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab")
    try:
        process = subprocess.Popen(  # noqa: S603 - command is fixed launcher argv plus validated option strings.
            _launcher_command(host=host, port=port, dry_run=False),
            cwd=_repo_root(),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log_file.close()
    # start_new_session=True makes the spawned process the leader of its owned
    # process group. Its PID is therefore the stable group ID, even if the
    # launcher exits before the readiness probe or cleanup runs.
    pgid = process.pid
    url_port = port or ODY_TERM_DEFAULT_PORT
    url = f"http://{host}:{url_port}"
    readiness_error = _wait_for_server_readiness(process=process, url=url)
    if readiness_error:
        _terminate_owned_server_process(process, pgid=pgid)
        raise CommandError(
            "server_start_failed",
            f"Local server {readiness_error}; inspect {_server_log_path()} and retry.",
            exit_code=1,
        )
    state: dict[str, object] = {
        "kind": "ody-term-local-server",
        "pid": process.pid,
        "pgid": pgid,
        "repo": str(_repo_root()),
        "command": _launcher_command(host=host, port=port, dry_run=False),
        "url": url,
        "log_path": str(log_path),
        "started_at": _utc_now(),
    }
    _save_server_state(state)
    return CommandResponse(
        ok=True,
        command=command,
        message="Local server ready",
        data={"server": {"status": "running", "runtime_state": state}},
    )


def _safe_tui_value(label: str, callback: object) -> dict[str, object]:
    try:
        if not callable(callback):
            raise TypeError(f"{label} source is not callable")
        return {"ok": True, "value": callback()}
    except CommandError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details or {}}}


def _event_line(event: dict[str, object]) -> str:
    identity = event.get("run_id") or event.get("session_id") or event.get("id")
    return (
        f"{event.get('seq', '?')} {event.get('level', 'info')} "
        f"{event.get('source', 'system')}.{event.get('kind', 'event')} "
        f"{identity}: {event.get('summary', '')}"
    ).strip()


def _session_tree(runs: list[dict[str, object]]) -> list[dict[str, object]]:
    sessions: dict[str, dict[str, object]] = {}
    for run in runs:
        session_id = str(run.get("session_id") or "unknown-session")
        session = sessions.setdefault(
            session_id,
            {"id": session_id, "label": f"Session {session_id}", "type": "session", "children": []},
        )
        children = cast(list[dict[str, object]], session["children"])
        run_id = str(run.get("run_id") or "unknown-run")
        run_node: dict[str, object] = {
            "id": run_id,
            "label": f"{run.get('kind', 'run')} Run {run_id} ({run.get('status', 'unknown')})",
            "type": "run",
            "children": [
                {
                    "id": f"{run_id}:events",
                    "label": f"{run.get('event_count', 0)} Event Envelope(s)",
                    "type": "events",
                }
            ],
        }
        harness_session_id = run.get("harness_session_id")
        if isinstance(harness_session_id, str) and harness_session_id:
            cast(list[dict[str, object]], run_node["children"]).append(
                {"id": harness_session_id, "label": f"Harness Session {harness_session_id}", "type": "harness-session"}
            )
        children.append(run_node)
    return sorted(sessions.values(), key=lambda node: str(node["id"]))


def _tui_control_log(
    *,
    capabilities: dict[str, object],
    lifecycle_targets: list[dict[str, object]],
    attempts: list[dict[str, object]],
) -> list[dict[str, object]]:
    restartable = [
        target.get("id")
        for target in lifecycle_targets
        if isinstance(target.get("capabilities"), dict) and cast(dict[str, object], target["capabilities"]).get("restart")
    ]
    available = [
        {
            "command": descriptor["command"],
            "capability": descriptor["capability"],
            "status": "available" if _capability_allowed(capabilities, str(descriptor["capability"])) else "denied",
            "targets": restartable if descriptor["command"] == "service" else [],
            "attempted": False,
        }
        for descriptor in TUI_COMMANDS
    ]
    return [*attempts, *available]


def _capability_allowed(capabilities: dict[str, object], capability: str) -> bool:
    payload = capabilities.get(capability)
    return bool(payload.get("allowed")) if isinstance(payload, dict) else False


def _tui_active_view(options: dict[str, str | bool]) -> str:
    view = str(options.get("view") or "Live")
    if view not in TUI_VIEWS:
        raise CommandError("invalid_tui_view", f"unknown TUI view: {view}")
    return view


def _tui_selected_event(events: list[dict[str, object]], selector: str | None) -> dict[str, object] | None:
    if not events:
        return None
    if not selector:
        return next((event for event in reversed(events) if event.get("run_id")), events[-1])
    for event in events:
        if str(event.get("id")) == selector:
            return event
    raise CommandError("unknown_tui_event", f"unknown TUI event: {selector}", exit_code=1)


def _tui_repl_attempt(
    command: str,
    *,
    runs: list[dict[str, object]],
    events: list[dict[str, object]],
    lifecycle_targets: list[dict[str, object]],
    capabilities: dict[str, object],
    selected_event: dict[str, object] | None,
) -> dict[str, object]:
    descriptor = next((item for item in TUI_COMMANDS if item["command"] == command), None)
    if descriptor is None:
        raise CommandError("unknown_tui_repl_command", f"unknown TUI REPL command: {command}")
    capability = str(descriptor["capability"])
    allowed = _capability_allowed(capabilities, capability)
    result: dict[str, object] = {
        "command": command,
        "capability": capability,
        "attempted": True,
        "status": "available" if allowed else "denied",
        "result": None,
    }
    if not allowed:
        result["result"] = {"reason": "capability_denied"}
        return result
    if command == "status":
        result["result"] = {"runs": runs[:5], "selected_event": selected_event}
    elif command == "tail":
        result["result"] = {"events": events[-10:], "cursor": str(events[-1]["seq"]) if events else None}
    elif command == "filter":
        sources = sorted({str(event.get("source")) for event in events if event.get("source")})
        levels = sorted({str(event.get("level")) for event in events if event.get("level")})
        result["result"] = {"sources": sources, "levels": levels}
    elif command == "stop":
        result["status"] = "confirmation_required"
        result["result"] = {"requires": "--yes", "run_id": selected_event.get("run_id") if selected_event else None}
    elif command == "harness":
        harness_events = [event for event in events if event.get("harness_session_id") or event.get("source") == "harness"]
        result["result"] = {"supported": bool(harness_events), "events": harness_events[-5:]}
    elif command == "service":
        restartable = [
            target
            for target in lifecycle_targets
            if isinstance(target.get("capabilities"), dict) and cast(dict[str, object], target["capabilities"]).get("restart")
        ]
        result["status"] = "confirmation_required" if restartable else "unsupported"
        result["result"] = {"targets": restartable, "requires": "--yes" if restartable else None}
    return result


def _tui_interaction(options: dict[str, str | bool], *, active_view: str, selected_event: dict[str, object] | None) -> dict[str, object]:
    key = str(options.get("key")) if isinstance(options.get("key"), str) else None
    mouse = str(options.get("mouse")) if isinstance(options.get("mouse"), str) else None
    interaction: dict[str, object] = {"keyboard_event": None, "mouse_event": None, "active_view": active_view}
    if key:
        action = TUI_KEYBOARD_BINDINGS.get(key.lower())
        if action in TUI_VIEWS:
            interaction["keyboard_event"] = {"input": key, "action": "switch-view", "view": action}
            interaction["active_view"] = action
        elif action:
            interaction["keyboard_event"] = {"input": key, "action": action, "selected_event": selected_event}
        else:
            raise CommandError("unknown_tui_key", f"unknown TUI key binding: {key}")
    if mouse:
        action = TUI_MOUSE_BINDINGS.get(mouse)
        if not action:
            raise CommandError("unknown_tui_mouse", f"unknown TUI mouse binding: {mouse}")
        interaction["mouse_event"] = {"input": mouse, "action": action, "selected_event": selected_event}
    return interaction


def _build_tui_model(request: CommandRequest) -> dict[str, object]:
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_tui_args", f"unexpected tui args: {' '.join(positionals)}")
    runs = _api_runs(request)
    runs.sort(key=lambda run: str(run.get("updated_at") or ""), reverse=True)
    lifecycle_result = _safe_tui_value("lifecycle", lambda: _lifecycle_targets(request))
    lifecycle_targets = (
        cast(list[dict[str, object]], lifecycle_result["value"])
        if lifecycle_result.get("ok") and isinstance(lifecycle_result.get("value"), list)
        else []
    )
    run_events = _merged_tui_events(_api_run_events(request, runs), lifecycle_targets=lifecycle_targets)
    for snapshot_source in ("service", "process", "system"):
        snapshot = _managed_runtime_snapshot_events(
            request,
            source=snapshot_source,
            cursor=None,
            limit=80,
            targets=lifecycle_targets,
        )
        run_events.extend(cast(list[dict[str, object]], snapshot["events"]))
    run_events.sort(key=lambda event: (str(event.get("time") or ""), str(event.get("source") or ""), int(event.get("seq") or 0)))
    capability_payload = _capabilities_payload()
    capabilities = cast(dict[str, object], capability_payload["capabilities"])
    target = _safe_tui_value("target", lambda: _resolve_target(request))
    auth = _resolved_auth()
    active_view = _tui_active_view(options)
    selected_event = _tui_selected_event(
        run_events,
        str(options["select_event"]) if isinstance(options.get("select_event"), str) else None,
    )
    attempts = []
    if isinstance(options.get("repl"), str):
        attempts.append(
            _tui_repl_attempt(
                str(options["repl"]),
                runs=runs,
                events=run_events,
                lifecycle_targets=lifecycle_targets,
                capabilities=capabilities,
                selected_event=selected_event,
            )
        )
    interaction = _tui_interaction(options, active_view=active_view, selected_event=selected_event)
    active_view = str(interaction["active_view"])
    control_log = _tui_control_log(capabilities=capabilities, lifecycle_targets=lifecycle_targets, attempts=attempts)
    return {
        "schema": "ody.tui.v1",
        "active_view": active_view,
        "views": {
            "Live": {
                "timeline": run_events,
                "timeline_lines": [_event_line(event) for event in run_events],
                "selected_event": selected_event,
                "control_log": control_log,
                "filters": {"source": None, "kind": None, "level": None, "run_id": None, "tag": None},
            },
            "REPL": {
                "prompt": "ody-term>",
                "commands": [dict(command) for command in TUI_COMMANDS],
                "history": control_log,
                "capability_limited": True,
            },
            "Browse": {
                "tree": _session_tree(runs),
                "lifecycle_targets": lifecycle_targets,
                "selected_node": (str(runs[0].get("run_id")) if runs else "sessions"),
            },
            "Inspect": {
                "model": {
                    "sessions": len({str(run.get("session_id")) for run in runs}),
                    "runs": len(runs),
                    "events": len(run_events),
                    "lifecycle_targets": len(lifecycle_targets),
                },
                "auth": auth,
                "target": target,
                "capabilities": capability_payload,
                "event_envelope_sample": selected_event,
                "shared_state_sources": [
                    "terminal-client-api",
                    "event-envelopes",
                    "lifecycle-targets",
                    "terminal-capabilities",
                    "target-resolution",
                ],
            },
        },
        "interaction": {
            "keyboard": TUI_KEYBOARD_BINDINGS,
            "mouse": TUI_MOUSE_BINDINGS,
            "last": interaction,
        },
        "state": {
            "runs": runs,
            "events": run_events,
            "lifecycle_targets": lifecycle_targets,
        },
        "source_errors": {
            "lifecycle": lifecycle_result.get("error") if not lifecycle_result.get("ok") else None,
            "target": target.get("error") if not target.get("ok") else None,
        },
    }


def _tui_model_attempt(model: dict[str, object], command: str) -> dict[str, object]:
    views = cast(dict[str, object], model.get("views", {}))
    live = cast(dict[str, object], views.get("Live", {}))
    inspect = cast(dict[str, object], views.get("Inspect", {}))
    state = cast(dict[str, object], model.get("state", {}))
    capability_payload = cast(dict[str, object], inspect.get("capabilities", {}))
    return _tui_repl_attempt(
        command,
        runs=cast(list[dict[str, object]], state.get("runs", [])),
        events=cast(list[dict[str, object]], state.get("events", [])),
        lifecycle_targets=cast(list[dict[str, object]], state.get("lifecycle_targets", [])),
        capabilities=cast(dict[str, object], capability_payload.get("capabilities", {})),
        selected_event=cast(dict[str, object] | None, live.get("selected_event")),
    )


def _render_tui_screen(model: dict[str, object]) -> str:
    views = cast(dict[str, object], model["views"])
    live = cast(dict[str, object], views["Live"])
    repl = cast(dict[str, object], views["REPL"])
    browse = cast(dict[str, object], views["Browse"])
    inspect = cast(dict[str, object], views["Inspect"])
    timeline_lines = cast(list[str], live["timeline_lines"])
    commands = cast(list[dict[str, object]], repl["commands"])
    tree = cast(list[dict[str, object]], browse["tree"])
    inspect_model = cast(dict[str, object], inspect["model"])
    tabs = " | ".join(f"[{view}]" if view == model["active_view"] else view for view in TUI_VIEWS)
    lines = [
        "ody-term tui",
        tabs,
        "",
        "Live",
        *(timeline_lines[-12:] or ["No Event Envelopes yet"]),
        "",
        "REPL",
        "commands: " + ", ".join(str(command["command"]) for command in commands),
        "",
        "Browse",
        *(str(node["label"]) for node in tree[:6]),
        "",
        "Inspect",
        (
            f"sessions={inspect_model['sessions']} runs={inspect_model['runs']} "
            f"events={inspect_model['events']} lifecycle_targets={inspect_model['lifecycle_targets']}"
        ),
        "keyboard: F1-F4, 1-4, Tab; mouse: tabs, event rows, tree nodes, controls",
    ]
    return "\n".join(lines)


def _help_for_domain(domain: str) -> str:
    if domain == "tui":
        return "usage: ody-term [global-options] tui\n\nOpen the Terminal Client TUI.\n"
    verbs = ", ".join(COMMANDS[domain])
    return f"usage: ody-term [global-options] {domain} <verb> [command-options]\n\nverbs: {verbs}\n"


def _needs_value(flag: str) -> CommandError:
    return CommandError("missing_global_value", f"{flag} requires a value")


def _read_value(argv: list[str], index: int, flag: str) -> tuple[str, int]:
    if index + 1 >= len(argv):
        raise _needs_value(flag)
    return argv[index + 1], index + 2


def _parse_global_args(argv: list[str]) -> tuple[GlobalOptions, list[str], bool]:
    options = GlobalOptions()
    positionals: list[str] = []
    help_requested = False
    index = 0

    while index < len(argv):
        token = argv[index]
        if token in ("-h", "--help"):
            help_requested = True
            index += 1
        elif token == "--target":
            options.target, index = _read_value(argv, index, token)
        elif token.startswith("--target="):
            options.target = token.split("=", 1)[1]
            index += 1
        elif token == "--profile":
            options.profile, index = _read_value(argv, index, token)
        elif token.startswith("--profile="):
            options.profile = token.split("=", 1)[1]
            index += 1
        elif token in ("--output", "-o"):
            options.output, index = _read_value(argv, index, token)
        elif token.startswith("--output="):
            options.output = token.split("=", 1)[1]
            index += 1
        elif token == "--format":
            options.format, index = _read_value(argv, index, token)
        elif token.startswith("--format="):
            options.format = token.split("=", 1)[1]
            index += 1
        elif token == "--color":
            options.color, index = _read_value(argv, index, token)
        elif token.startswith("--color="):
            options.color = token.split("=", 1)[1]
            index += 1
        elif token in ("-q", "--quiet"):
            options.quiet = True
            index += 1
        elif token in ("-v", "--verbose"):
            options.verbose += 1
            index += 1
        elif token.startswith("-v") and set(token) == {"-", "v"}:
            options.verbose += len(token) - 1
            index += 1
        elif token == "--yes":
            options.yes = True
            index += 1
        elif token == "--yolo":
            options.yolo = True
            index += 1
        elif token == "--start":
            options.start = True
            index += 1
        elif token == "--ensure-server":
            options.ensure_server = True
            index += 1
        else:
            positionals.append(token)
            index += 1

    if options.output is not None and options.output not in OUTPUT_PROFILES:
        raise CommandError("invalid_output_profile", f"unknown output profile: {options.output}")
    if options.format not in FORMATS:
        raise CommandError("invalid_format", f"unknown format: {options.format}")
    if options.color not in COLOR_MODES:
        raise CommandError("invalid_color", f"unknown color mode: {options.color}")
    if options.yolo:
        options.yes = True
    return options, positionals, help_requested


def parse_request(argv: list[str], *, stdout_is_tty: bool) -> tuple[CommandRequest | None, str | None]:
    options, positionals, help_requested = _parse_global_args(argv)
    output_profile = options.output or ("human" if stdout_is_tty else "clanker")

    if not positionals:
        if help_requested:
            return None, _usage()
        raise CommandError("missing_domain", "expected a domain")

    domain = positionals[0]
    if domain not in DOMAINS:
        raise CommandError("unknown_domain", f"unknown domain: {domain}")

    if help_requested:
        return None, _help_for_domain(domain)

    if domain == "tui":
        return (
            CommandRequest(
                domain=domain,
                verb=None,
                args=positionals[1:],
                globals=options,
                output_profile=output_profile,
            ),
            None,
        )

    if len(positionals) < 2:
        raise CommandError("missing_verb", f"expected a verb for {domain}")

    verb = positionals[1]
    if verb not in COMMANDS[domain]:
        raise CommandError("unknown_verb", f"unknown {domain} verb: {verb}")
    return (
        CommandRequest(
            domain=domain,
            verb=verb,
            args=positionals[2:],
            globals=options,
            output_profile=output_profile,
        ),
        None,
    )


def _globals_payload(options: GlobalOptions) -> dict[str, object]:
    return asdict(options)


def _command_path(request: CommandRequest) -> list[str]:
    return [request.domain] if request.verb is None else [request.domain, request.verb]


def _auth_status(request: CommandRequest) -> CommandResponse:
    command = _command_path(request)
    return CommandResponse(
        ok=True,
        command=command,
        message=f"Auth mode: {_resolved_auth()['auth_mode']}",
        data={"auth": _resolved_auth()},
    )


def _auth_login(request: CommandRequest) -> CommandResponse:
    command = _command_path(request)
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_auth_args", f"unexpected auth login args: {' '.join(positionals)}")
    token = options.get("token")
    if not isinstance(token, str) or not token:
        raise CommandError("missing_token", "auth login requires --token")
    token_ref = str(options.get("token_ref") or _default_token_ref())
    scopes = _parse_scopes(options.get("scopes"))
    stored_in_os_secret = _store_os_secret(token_ref, token)
    if not stored_in_os_secret:
        token_ref = "file:default" if token_ref.startswith("keychain:") else token_ref
    secrets = _load_secrets()
    tokens = secrets.get("tokens", {})
    if not isinstance(tokens, dict):
        raise CommandError("corrupt_secret_store", "Terminal Client secret store tokens must be an object")
    tokens = cast(dict[str, object], tokens)
    tokens[token_ref] = (
        {
            "token": token,
            "scopes": scopes,
            "updated_at": _utc_now(),
            "storage_mode": "file-fallback",
        }
        if not stored_in_os_secret
        else {
            "scopes": scopes,
            "updated_at": _utc_now(),
            "storage_mode": "keychain-metadata",
        }
    )
    secrets["tokens"] = tokens
    _save_secrets(secrets)
    return CommandResponse(
        ok=True,
        command=command,
        message="Terminal Client token stored",
        data={"auth": _resolved_auth()},
    )


def _auth_logout(request: CommandRequest) -> CommandResponse:
    command = _command_path(request)
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_auth_args", f"unexpected auth logout args: {' '.join(positionals)}")
    token_ref = str(options.get("token_ref") or _selected_token_ref())
    secrets = _load_secrets()
    tokens = secrets.get("tokens", {})
    removed = _delete_os_secret(token_ref)
    if isinstance(tokens, dict):
        removed = tokens.pop(token_ref, None) is not None or removed
        secrets["tokens"] = tokens
        _save_secrets(secrets)
    auth = _resolved_auth()
    return CommandResponse(
        ok=True,
        command=command,
        message="Terminal Client token removed" if removed else "No stored Terminal Client token found",
        data={"auth": auth, "removed": removed},
    )


def _auth_capabilities(request: CommandRequest) -> CommandResponse:
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message="Terminal Client capabilities",
        data=_capabilities_payload(),
    )


def _service_list(request: CommandRequest) -> CommandResponse:
    _require_capability("service:read", request)
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_service_args", f"unexpected service list args: {' '.join(positionals)}")
    targets = _lifecycle_targets(request)
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message=f"{len(targets)} Lifecycle Target(s)",
        data={"targets": targets},
    )


def _service_status(request: CommandRequest) -> CommandResponse:
    _require_capability("service:read", request)
    options, positionals = _parse_command_options(request.args)
    if len(positionals) != 1:
        raise CommandError("missing_lifecycle_target", "service status requires exactly one managed target id")
    target = _lifecycle_target(request, positionals[0])
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message=f"Lifecycle target {target['id']} is {target['status']}",
        data={"target": target},
    )


def _server_status(request: CommandRequest) -> CommandResponse:
    status = _server_status_payload()
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message=f"Local server runtime state: {status['status']}",
        data={"server": status},
    )


def _server_start(request: CommandRequest) -> CommandResponse:
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_server_args", f"unexpected server start args: {' '.join(positionals)}")
    status = _server_status_payload()
    if status["status"] == "running":
        return CommandResponse(
            ok=True,
            command=_command_path(request),
            message="Local server already running",
            data={"server": status},
        )
    host = str(options.get("host") or ODY_TERM_DEFAULT_HOST)
    port = str(options["port"]) if isinstance(options.get("port"), str) else None
    dry_run = bool(options.get("dry_run"))
    response = _start_server(host=host, port=port, dry_run=dry_run)
    response.command = _command_path(request)
    return response


def _server_stop(request: CommandRequest) -> CommandResponse:
    command = _command_path(request)
    state = _server_state()
    if not state:
        return CommandResponse(
            ok=True,
            command=command,
            message="No ody-term local server runtime state found",
            data={"server": {"status": "absent"}},
        )
    if state.get("repo") != str(_repo_root()) or not isinstance(state.get("command"), list):
        raise CommandError("ambiguous_runtime_state", "runtime state ownership evidence does not match this checkout")
    pid = state.get("pid")
    if not _process_alive(pid):
        return CommandResponse(
            ok=False,
            command=command,
            message="Local server runtime state is stale",
            data={"server": {"status": "stale", "runtime_state": state}},
        )
    if not _process_group_matches(pid, state.get("pgid")):
        raise CommandError("ambiguous_runtime_state", "runtime state ownership evidence does not match this checkout")
    assert isinstance(pid, int)
    os.killpg(pid, signal.SIGTERM)
    state["stopped_at"] = _utc_now()
    _save_server_state(state)
    return CommandResponse(
        ok=True,
        command=command,
        message="Local server stop signal sent",
        data={"server": {"status": "stopping", "runtime_state": state}},
    )


def _server_logs(request: CommandRequest) -> CommandResponse:
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_server_args", f"unexpected server logs args: {' '.join(positionals)}")
    raw_lines = options.get("lines")
    try:
        lines = int(raw_lines) if isinstance(raw_lines, str) else 80
    except ValueError as exc:
        raise CommandError("invalid_lines", f"--lines must be an integer: {raw_lines}") from exc
    state = _server_state()
    log_path = Path(str(state.get("log_path") or _server_log_path()))
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message="Local server logs",
        data={"log_path": str(log_path), "lines": _tail_file(log_path, max(lines, 0))},
    )


def _config_show(request: CommandRequest) -> CommandResponse:
    config = load_config()
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message="Terminal Client config",
        data={"config": config, "path": str(_config_path())},
    )


def _config_resolve_target(request: CommandRequest) -> CommandResponse:
    resolved = _resolve_target(request)
    return CommandResponse(
        ok=bool(resolved.get("ok")),
        command=_command_path(request),
        message="Target resolved" if resolved.get("ok") else "Target resolution failed",
        data={"target": resolved},
    )


def _config_profile(request: CommandRequest) -> CommandResponse:
    command = [request.domain] if request.verb is None else [request.domain, request.verb]
    options, positionals = _parse_command_options(request.args)
    action = positionals[0] if positionals else "list"
    config = load_config()
    profiles = _profiles(config)
    if action == "list":
        return CommandResponse(
            ok=True,
            command=command,
            message="Terminal Client profiles",
            data={
                "default_profile": config.get("default_profile"),
                "profiles": sorted(profiles),
            },
        )
    if action == "show":
        if len(positionals) < 2:
            raise CommandError("missing_profile_name", "config profile show requires a profile name")
        name = positionals[1]
        if name not in profiles:
            raise CommandError("unknown_profile", f"unknown profile: {name}", exit_code=1)
        return CommandResponse(
            ok=True,
            command=command,
            message=f"Terminal Client profile {name}",
            data={"profile": name, "settings": _profile_payload(profiles[name])},
        )
    if action == "set":
        if len(positionals) < 2:
            raise CommandError("missing_profile_name", "config profile set requires a profile name")
        name = positionals[1]
        existing = _profile_payload(profiles.get(name, {}))
        for source_key, target_key in (
            ("url", "url"),
            ("repo", "repo"),
            ("token_ref", "token_ref"),
            ("profile_output", "output"),
        ):
            value = options.get(source_key)
            if isinstance(value, str) and value:
                existing[target_key] = value
        if existing.get("output") is not None and existing["output"] not in OUTPUT_PROFILES:
            raise CommandError("invalid_profile_output", f"unknown profile output: {existing['output']}")
        profiles[name] = existing
        if options.get("default"):
            config["default_profile"] = name
        save_config(config)
        return CommandResponse(
            ok=True,
            command=command,
            message=f"Terminal Client profile {name} saved",
            data={"profile": name, "settings": existing, "default_profile": config.get("default_profile")},
        )
    if action == "unset":
        if len(positionals) < 2:
            raise CommandError("missing_profile_name", "config profile unset requires a profile name")
        name = positionals[1]
        removed = profiles.pop(name, None) is not None
        if config.get("default_profile") == name:
            config["default_profile"] = None
        save_config(config)
        return CommandResponse(
            ok=True,
            command=command,
            message=f"Terminal Client profile {name} removed" if removed else f"Terminal Client profile {name} absent",
            data={"profile": name, "removed": removed, "default_profile": config.get("default_profile")},
        )
    raise CommandError("unknown_profile_action", f"unknown config profile action: {action}")


def _inspect_domains(request: CommandRequest) -> CommandResponse:
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message="Terminal Client domains",
        data={"domains": list(DOMAINS), "commands": {key: list(value) for key, value in COMMANDS.items()}},
    )


def _inspect_aliases(request: CommandRequest) -> CommandResponse:
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message="Terminal Client aliases",
        data={"aliases": list(ALIASES)},
    )


def _inspect_contracts(request: CommandRequest) -> CommandResponse:
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message="Terminal Client output contracts",
        data={
            "profiles": list(OUTPUT_PROFILES),
            "formats": list(FORMATS),
            "default_profile": request.output_profile,
            "event_envelope_required_fields": [
                "schema",
                "id",
                "seq",
                "time",
                "source",
                "kind",
                "level",
                "payload",
            ],
            "event_envelope_field_aliases": {"sequence": "seq"},
            "clanker": {
                "bounded_json": "Command responses are JSON objects with ok, command, message, profile, format, and data.",
                "event_jsonl": "Event-stream commands emit one ody.event.v1 Event Envelope per line.",
                "raw_debug": "raw/debug are diagnostic capture modes; ody.event.v1 remains the replay contract.",
            },
            "exit_codes": {
                "0": "command succeeded",
                "1": "known runtime or target failure",
                "2": "usage, auth, capability, confirmation, or policy failure",
            },
            "cursor": {
                "flag": "--cursor",
                "field": "data.cursor.next",
                "reconnect": "pass the last cursor.next value to continue after that event sequence",
            },
        },
    )


def _inspect_globals(request: CommandRequest) -> CommandResponse:
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message="Terminal Client global options",
        data={"globals": _globals_payload(request.globals), "output_profile": request.output_profile},
    )


def _tui_command(request: CommandRequest) -> CommandResponse:
    model = _build_tui_model(request)
    return CommandResponse(
        ok=True,
        command=_command_path(request),
        message=_render_tui_screen(model),
        data={"tui": model},
    )


CommandHandler = Callable[[CommandRequest], CommandResponse]


HANDLERS: dict[tuple[str, str | None], CommandHandler] = {
    ("auth", "status"): _auth_status,
    ("auth", "login"): _auth_login,
    ("auth", "logout"): _auth_logout,
    ("auth", "capabilities"): _auth_capabilities,
    ("session", "list"): _session_list,
    ("session", "show"): _session_read,
    ("session", "history"): _session_read,
    ("session", "export"): _session_read,
    ("run", "start"): _run_start,
    ("run", "list"): _run_list,
    ("run", "status"): _run_status,
    ("run", "attach"): _run_attach,
    ("run", "stop"): _run_stop,
    ("harness", "list"): _harness_list,
    ("harness", "status"): _harness_status,
    ("harness", "stop"): _harness_stop,
    ("harness", "attach"): _run_attach,
    ("service", "list"): _service_list,
    ("service", "status"): _service_status,
    ("service", "logs"): _service_logs,
    ("service", "stop"): _service_control,
    ("service", "restart"): _service_control,
    ("server", "status"): _server_status,
    ("server", "start"): _server_start,
    ("server", "stop"): _server_stop,
    ("server", "logs"): _server_logs,
    ("config", "show"): _config_show,
    ("config", "resolve-target"): _config_resolve_target,
    ("config", "profile"): _config_profile,
    ("inspect", "domains"): _inspect_domains,
    ("inspect", "aliases"): _inspect_aliases,
    ("inspect", "contracts"): _inspect_contracts,
    ("inspect", "globals"): _inspect_globals,
    ("inspect", "events"): _inspect_events,
    ("tui", None): _tui_command,
}


def execute(request: CommandRequest) -> CommandResponse:
    handler = HANDLERS.get((request.domain, request.verb))
    if handler is None:
        command = _command_path(request)
        return CommandResponse(
            ok=False,
            command=command,
            message=f"{' '.join(command)} is registered but not implemented yet",
            data={"implemented": False, "args": request.args},
        )
    return handler(request)


def _response_payload(response: CommandResponse, request: CommandRequest) -> dict[str, object]:
    return {
        "ok": response.ok,
        "command": response.command,
        "message": response.message,
        "profile": request.output_profile,
        "format": request.globals.format,
        "data": response.data,
    }


def _write_json(payload: object, stdout: TextIO) -> None:
    stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def _event_payload(response: CommandResponse) -> list[dict[str, object]] | None:
    events = response.data.get("events")
    if not isinstance(events, list):
        return None
    return [cast(dict[str, object], event) for event in events if isinstance(event, dict)]


def render(response: CommandResponse, request: CommandRequest, stdout: TextIO) -> None:
    payload = _response_payload(response, request)
    output_format = request.globals.format
    events = _event_payload(response)

    if output_format == "json":
        _write_json(payload, stdout)
    elif output_format == "jsonl":
        if response.event_stream is not None:
            for event in response.event_stream:
                _write_json(event, stdout)
                stdout.flush()
        elif events is not None:
            for event in events:
                _write_json(event, stdout)
        else:
            _write_json(payload, stdout)
    elif output_format == "raw":
        _write_json(response.raw if response.raw is not None else response.data, stdout)
    elif output_format == "debug":
        _write_json(
            {
                "request": {"domain": request.domain, "verb": request.verb, "args": request.args},
                "renderer": {
                    "contract": "event-envelope" if events is not None else "command-response",
                    "raw_included": response.raw is not None,
                    "format": output_format,
                    "profile": request.output_profile,
                },
                **payload,
            },
            stdout,
        )
    elif request.output_profile == "clanker":
        _write_json(payload, stdout)
    elif request.output_profile == "grug":
        if events is not None:
            for event in events:
                stdout.write(f"{event.get('seq')} {event.get('level')} {event.get('source')}.{event.get('kind')}: {event.get('summary', '')}\n")
        else:
            status = "ok" if response.ok else "no"
            stdout.write(f"{status} {' '.join(response.command)}: {response.message}\n")
    else:
        if events is not None:
            for event in events:
                stdout.write(f"[{event.get('level')}] {event.get('source')}.{event.get('kind')} #{event.get('seq')}: {event.get('summary', '')}\n")
        elif response.command == ["tui"]:
            stdout.write(response.message + "\n")
        else:
            stdout.write(response.message + "\n")


def render_error(error: CommandError, *, options: GlobalOptions | None, stdout_is_tty: bool, stderr: TextIO) -> None:
    output_profile = (options.output if options and options.output else None) or ("human" if stdout_is_tty else "clanker")
    output_format = options.format if options else "text"
    payload: dict[str, object] = {"ok": False, "error": {"code": error.code, "message": error.message}}
    if error.details:
        error_payload = cast(dict[str, object], payload["error"])
        error_payload["details"] = error.details

    if output_format in {"json", "jsonl"} or output_profile == "clanker":
        stderr.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    else:
        stderr.write(f"ody-term: {error.message}\n")


def main(argv: list[str] | None = None, *, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    stdout_is_tty = bool(stdout.isatty())

    try:
        request, help_text = parse_request(argv, stdout_is_tty=stdout_is_tty)
        if help_text is not None:
            stdout.write(help_text)
            return 0
        assert request is not None
        response = execute(request)
        if (
            request.domain == "tui"
            and request.globals.format == "text"
            and request.output_profile == "human"
            and stdout_is_tty
        ):
            model = cast(dict[str, object], response.data["tui"])
            run_interactive_tui(model, lambda command: _tui_model_attempt(model, command))
            return 0 if response.ok else 1
        render(response, request, stdout)
        return 0 if response.ok else 1
    except CommandError as exc:
        try:
            options, _, _ = _parse_global_args(argv)
        except CommandError:
            options = None
        render_error(exc, options=options, stdout_is_tty=stdout_is_tty, stderr=stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
