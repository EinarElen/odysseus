"""Terminal Client command spine for Odysseus.

This module intentionally starts with the public command contract rather than
backend behavior. Later tickets can attach API clients, profiles, auth, event
streams, and TUI state to the command handlers registered here.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, TypedDict, cast

try:
    from src.constants import ODY_TERM_RUNS_FILE, ODY_TERM_SECRETS_FILE
except Exception:  # pragma: no cover - keeps standalone ody_term packaging usable.
    ODY_TERM_RUNS_FILE = ""
    ODY_TERM_SECRETS_FILE = ""


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
    "config": ("show", "profile", "resolve-target", "set", "unset"),
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
    base = os.getenv("XDG_CONFIG_HOME", "").strip()
    if base:
        return Path(base).expanduser() / "odysseus" / "ody-term.json"
    return Path.home() / ".config" / "odysseus" / "ody-term.json"


def _runtime_state_path() -> Path:
    override = os.getenv("ODY_TERM_RUNTIME", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.getenv("XDG_STATE_HOME", "").strip()
    if base:
        return Path(base).expanduser() / "odysseus" / "ody-term-runtime.json"
    return Path.home() / ".local" / "state" / "odysseus" / "ody-term-runtime.json"


def _run_state_path() -> Path:
    override = os.getenv("ODY_TERM_RUNS", "").strip()
    if override:
        return Path(override).expanduser()
    if ODY_TERM_RUNS_FILE:
        return Path(ODY_TERM_RUNS_FILE).expanduser()
    return _runtime_state_path().with_name("ody-term-runs.json")


def _secrets_path() -> Path:
    override = os.getenv("ODY_TERM_SECRETS", "").strip()
    if override:
        return Path(override).expanduser()
    if ODY_TERM_SECRETS_FILE:
        return Path(ODY_TERM_SECRETS_FILE).expanduser()
    return _config_path().with_name("ody-term-secrets.json")


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
        "--source",
        "--kind",
        "--level",
        "--cursor",
        "--session-id",
        "--run-id",
        "--harness-session-id",
        "--span-id",
        "--parent-id",
        "--tag",
        "--message",
        "--status",
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
    return "file:default"


def _secret_token_entry(ref: str) -> dict[str, object]:
    secrets = _load_secrets()
    tokens = secrets.get("tokens", {})
    if not isinstance(tokens, dict):
        raise CommandError("corrupt_secret_store", "Terminal Client secret store tokens must be an object")
    entry = tokens.get(ref)
    if not isinstance(entry, dict):
        return {}
    return cast(dict[str, object], entry)


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

    if not token_present:
        token_ref = _selected_token_ref()
        entry = _secret_token_entry(token_ref)
        token_present = bool(entry.get("token"))
        storage = {
            "mode": "file-fallback",
            "ref": token_ref,
            "path": str(_secrets_path()),
            "visible_weaker_fallback": token_present,
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
        if not request.globals.yes:
            raise CommandError(
                "confirmation_required",
                f"{capability} requires --yes",
                exit_code=2,
            )
        return {"capability": capability, "required": "ordinary", "satisfied_by": "--yes"}
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
        started = _start_server(host="127.0.0.1", port=None, dry_run=False)
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
            "url": "http://127.0.0.1:7860",
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _server_log_path() -> Path:
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


def _empty_run_state() -> dict[str, object]:
    return {"version": 1, "runs": {}, "events": {}}


def _load_run_state() -> dict[str, object]:
    state = _empty_run_state()
    state.update(_load_json_object(_run_state_path()))
    if not isinstance(state.get("runs"), dict):
        raise CommandError("corrupt_run_state", "Terminal Client run state runs must be an object")
    if not isinstance(state.get("events"), dict):
        raise CommandError("corrupt_run_state", "Terminal Client run state events must be an object")
    return state


def _save_run_state(state: dict[str, object]) -> None:
    path = _run_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _new_identity(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _runs_payload(state: dict[str, object]) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], state["runs"])


def _events_payload(state: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    return cast(dict[str, list[dict[str, object]]], state["events"])


def _run_events(state: dict[str, object], run_id: str) -> list[dict[str, object]]:
    events = _events_payload(state).get(run_id, [])
    return [event for event in events if isinstance(event, dict)]


def _last_activity(state: dict[str, object], run_id: str) -> dict[str, object] | None:
    events = _run_events(state, run_id)
    if not events:
        return None
    event = events[-1]
    return {
        "time": event.get("time"),
        "kind": event.get("kind"),
        "level": event.get("level"),
        "summary": event.get("summary"),
    }


def _run_summary(state: dict[str, object], run: dict[str, object]) -> dict[str, object]:
    run_id = str(run["run_id"])
    events = _run_events(state, run_id)
    summary = dict(run)
    summary["events_available"] = bool(events)
    summary["replay_available"] = bool(events)
    summary["cursor_available"] = bool(events)
    summary["event_count"] = len(events)
    summary["last_activity"] = _last_activity(state, run_id)
    heartbeat = _last_activity(state, run_id)
    summary["heartbeat"] = heartbeat
    return summary


def _append_run_event(
    state: dict[str, object],
    run: dict[str, object],
    *,
    kind: str,
    level: str,
    summary: str,
    payload: dict[str, object],
) -> dict[str, object]:
    run_id = str(run["run_id"])
    session_id = str(run["session_id"])
    events_by_run = _events_payload(state)
    events = events_by_run.setdefault(run_id, [])
    seq = len(events) + 1
    event: dict[str, object] = {
        "schema": "ody.event.v1",
        "id": f"evt_{run_id}_{seq}",
        "seq": seq,
        "time": _utc_now(),
        "session_id": session_id,
        "run_id": run_id,
        "source": "chat",
        "kind": kind,
        "level": level,
        "summary": summary,
        "payload": payload,
        "raw": {
            "transport": "compat",
            "type": kind,
            "body": payload,
        },
    }
    events.append(event)
    return event


def _resolve_run_reference(state: dict[str, object], *, run_id: str | None, session_id: str | None) -> dict[str, object]:
    runs = _runs_payload(state)
    if run_id:
        run = runs.get(run_id)
        if not isinstance(run, dict):
            raise CommandError("unknown_run", f"Run {run_id} was not found", exit_code=1)
        return run
    if not session_id:
        raise CommandError("missing_run_target", "run command requires a run id or --session-id")
    matches = [
        run
        for run in runs.values()
        if run.get("session_id") == session_id and str(run.get("status")) in RUN_ACTIVE_STATUSES
    ]
    if len(matches) != 1:
        if len(matches) > 1:
            choices = ", ".join(str(run.get("run_id")) for run in matches)
            raise CommandError(
                "ambiguous_run",
                f"Session {session_id} has multiple active Runs: {choices}",
                details={
                    "session_id": session_id,
                    "choices": [
                        {
                            "run_id": run.get("run_id"),
                            "kind": run.get("kind"),
                            "status": run.get("status"),
                            "started_at": run.get("started_at"),
                            "updated_at": run.get("updated_at"),
                        }
                        for run in matches
                    ],
                },
            )
        raise CommandError("unknown_run", f"Session {session_id} has no active Run", exit_code=1)
    return matches[0]


def _run_start(request: CommandRequest) -> CommandResponse:
    _require_capability("run:start", request)
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_run_args", f"unexpected run start args: {' '.join(positionals)}")
    kind = str(options.get("kind") or "chat")
    if kind != "chat":
        raise CommandError("unsupported_run_kind", f"run start currently supports chat Runs, not {kind}")
    state = _load_run_state()
    runs = _runs_payload(state)
    run_id = _new_identity("run")
    session_id = str(options.get("session_id") or _new_identity("ses"))
    now = _utc_now()
    run: dict[str, object] = {
        "run_id": run_id,
        "session_id": session_id,
        "kind": kind,
        "status": "running",
        "started_at": now,
        "updated_at": now,
        "finished_at": None,
    }
    runs[run_id] = run
    message = str(options.get("message") or "")
    _append_run_event(
        state,
        run,
        kind="run.status",
        level="info",
        summary="chat Run started",
        payload={"status": "running", "message": message},
    )
    _save_run_state(state)
    return CommandResponse(
        ok=True,
        command=["run", "start"],
        message=f"Started chat Run {run_id}",
        data={"run": _run_summary(state, run), "cursor": {"after": None, "next": "1", "count": 1}},
    )


def _run_list(request: CommandRequest) -> CommandResponse:
    _require_capability("run:read", request)
    options, positionals = _parse_command_options(request.args)
    if positionals:
        raise CommandError("unexpected_run_args", f"unexpected run list args: {' '.join(positionals)}")
    state = _load_run_state()
    runs = [_run_summary(state, run) for run in _runs_payload(state).values()]
    runs.sort(key=lambda run: str(run.get("updated_at") or ""), reverse=True)
    status_filter = str(options.get("status")) if isinstance(options.get("status"), str) else None
    if status_filter:
        runs = [run for run in runs if run.get("status") == status_filter]
    return CommandResponse(ok=True, command=["run", "list"], message=f"{len(runs)} Run(s)", data={"runs": runs})


def _run_status(request: CommandRequest) -> CommandResponse:
    _require_capability("run:read", request)
    options, positionals = _parse_command_options(request.args)
    if len(positionals) > 1:
        raise CommandError("unexpected_run_args", f"unexpected run status args: {' '.join(positionals[1:])}")
    run_id = positionals[0] if positionals else (str(options["run_id"]) if isinstance(options.get("run_id"), str) else None)
    session_id = str(options["session_id"]) if isinstance(options.get("session_id"), str) else None
    state = _load_run_state()
    run = _resolve_run_reference(state, run_id=run_id, session_id=session_id)
    return CommandResponse(
        ok=True,
        command=["run", "status"],
        message=f"Run {run['run_id']} is {run['status']}",
        data={"run": _run_summary(state, run)},
    )


def _run_attach(request: CommandRequest) -> CommandResponse:
    _require_capability("event:read", request)
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
    state = _load_run_state()
    run = _resolve_run_reference(state, run_id=run_id, session_id=session_id)
    events = []
    for event in _run_events(state, str(run["run_id"])):
        seq = event.get("seq")
        if not isinstance(seq, int):
            continue
        if cursor is None or seq > cursor:
            events.append(event)
    next_cursor = str(events[-1]["seq"]) if events else (str(cursor) if cursor is not None else None)
    return CommandResponse(
        ok=True,
        command=["run", "attach"],
        message=f"{len(events)} Run Event Envelope(s)",
        data={
            "run": _run_summary(state, run),
            "events": events,
            "cursor": {"after": str(cursor) if cursor is not None else None, "next": next_cursor, "count": len(events)},
        },
        raw=[event.get("raw") for event in events],
    )


def _run_stop(request: CommandRequest) -> CommandResponse:
    confirmation = _require_capability("run:stop", request)
    options, positionals = _parse_command_options(request.args)
    if len(positionals) > 1:
        raise CommandError("unexpected_run_args", f"unexpected run stop args: {' '.join(positionals[1:])}")
    run_id = positionals[0] if positionals else (str(options["run_id"]) if isinstance(options.get("run_id"), str) else None)
    session_id = str(options["session_id"]) if isinstance(options.get("session_id"), str) else None
    state = _load_run_state()
    run = _resolve_run_reference(state, run_id=run_id, session_id=session_id)
    now = _utc_now()
    run["status"] = "stopped"
    run["updated_at"] = now
    run["finished_at"] = now
    _append_run_event(
        state,
        run,
        kind="run.status",
        level="warn",
        summary="chat Run stopped",
        payload={"status": "stopped"},
    )
    _save_run_state(state)
    return CommandResponse(
        ok=True,
        command=["run", "stop"],
        message=f"Stopped Run {run['run_id']}",
        data={"run": _run_summary(state, run), "confirmation": confirmation},
    )


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
    )


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
    url_port = port or "7860"
    state: dict[str, object] = {
        "kind": "ody-term-local-server",
        "pid": process.pid,
        "pgid": os.getpgid(process.pid),
        "repo": str(_repo_root()),
        "command": _launcher_command(host=host, port=port, dry_run=False),
        "url": f"http://{host}:{url_port}",
        "log_path": str(log_path),
        "started_at": _utc_now(),
    }
    _save_server_state(state)
    return CommandResponse(
        ok=True,
        command=command,
        message="Local server start delegated",
        data={"server": {"status": "starting", "runtime_state": state}},
    )


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
        if len(positionals) > 1:
            raise CommandError("unexpected_tui_args", "tui does not accept a verb in the command spine")
        return CommandRequest(domain=domain, verb=None, globals=options, output_profile=output_profile), None

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


def execute(request: CommandRequest) -> CommandResponse:
    command = [request.domain] if request.verb is None else [request.domain, request.verb]
    if request.domain == "auth" and request.verb == "status":
        return CommandResponse(
            ok=True,
            command=command,
            message=f"Auth mode: {_resolved_auth()['auth_mode']}",
            data={"auth": _resolved_auth()},
        )
    if request.domain == "auth" and request.verb == "login":
        options, positionals = _parse_command_options(request.args)
        if positionals:
            raise CommandError("unexpected_auth_args", f"unexpected auth login args: {' '.join(positionals)}")
        token = options.get("token")
        if not isinstance(token, str) or not token:
            raise CommandError("missing_token", "auth login requires --token")
        token_ref = str(options.get("token_ref") or "file:default")
        secrets = _load_secrets()
        tokens = secrets.get("tokens", {})
        if not isinstance(tokens, dict):
            raise CommandError("corrupt_secret_store", "Terminal Client secret store tokens must be an object")
        tokens = cast(dict[str, object], tokens)
        tokens[token_ref] = {
            "token": token,
            "updated_at": _utc_now(),
        }
        secrets["tokens"] = tokens
        _save_secrets(secrets)
        auth = _resolved_auth()
        if auth["token"] and cast(dict[str, object], auth["token"]).get("ref") != token_ref:
            token_payload = cast(dict[str, object], auth["token"])
            token_payload["ref"] = token_ref
        return CommandResponse(
            ok=True,
            command=command,
            message="Terminal Client token stored",
            data={"auth": _resolved_auth()},
        )
    if request.domain == "auth" and request.verb == "logout":
        options, positionals = _parse_command_options(request.args)
        if positionals:
            raise CommandError("unexpected_auth_args", f"unexpected auth logout args: {' '.join(positionals)}")
        token_ref = str(options.get("token_ref") or _selected_token_ref())
        secrets = _load_secrets()
        tokens = secrets.get("tokens", {})
        removed = False
        if isinstance(tokens, dict):
            removed = tokens.pop(token_ref, None) is not None
            secrets["tokens"] = tokens
            _save_secrets(secrets)
        auth = _resolved_auth()
        return CommandResponse(
            ok=True,
            command=command,
            message="Terminal Client token removed" if removed else "No stored Terminal Client token found",
            data={"auth": auth, "removed": removed},
        )
    if request.domain == "auth" and request.verb == "capabilities":
        return CommandResponse(
            ok=True,
            command=command,
            message="Terminal Client capabilities",
            data=_capabilities_payload(),
        )
    if request.domain == "run" and request.verb == "start":
        return _run_start(request)
    if request.domain == "run" and request.verb == "list":
        return _run_list(request)
    if request.domain == "run" and request.verb == "status":
        return _run_status(request)
    if request.domain == "run" and request.verb == "attach":
        return _run_attach(request)
    if request.domain == "run" and request.verb == "stop":
        return _run_stop(request)
    if request.domain == "service" and request.verb in {"stop", "restart"}:
        options, positionals = _parse_command_options(request.args)
        if not positionals:
            raise CommandError("missing_lifecycle_target", f"service {request.verb} requires a managed target id")
        if any(target.startswith("pid:") for target in positionals):
            raise CommandError(
                "arbitrary_process_unsupported",
                "service commands require managed lifecycle target ids, not raw host PIDs",
            )
        capability = "service:kill" if options.get("force") else "service:restart"
        confirmation = _require_capability(capability, request)
        return CommandResponse(
            ok=False,
            command=command,
            message=f"service {request.verb} is capability-gated but lifecycle execution is implemented by a later ticket",
            data={"implemented": False, "args": positionals, "confirmation": confirmation},
        )
    if request.domain == "server" and request.verb == "status":
        status = _server_status_payload()
        return CommandResponse(
            ok=True,
            command=command,
            message=f"Local server runtime state: {status['status']}",
            data={"server": status},
        )
    if request.domain == "server" and request.verb == "start":
        options, positionals = _parse_command_options(request.args)
        if positionals:
            raise CommandError("unexpected_server_args", f"unexpected server start args: {' '.join(positionals)}")
        status = _server_status_payload()
        if status["status"] == "running":
            return CommandResponse(
                ok=True,
                command=command,
                message="Local server already running",
                data={"server": status},
            )
        host = str(options.get("host") or "127.0.0.1")
        port = str(options["port"]) if isinstance(options.get("port"), str) else None
        dry_run = bool(options.get("dry_run"))
        response = _start_server(host=host, port=port, dry_run=dry_run)
        response.command = command
        return response
    if request.domain == "server" and request.verb == "stop":
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
    if request.domain == "server" and request.verb == "logs":
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
            command=command,
            message="Local server logs",
            data={"log_path": str(log_path), "lines": _tail_file(log_path, max(lines, 0))},
        )
    if request.domain == "config" and request.verb == "show":
        config = load_config()
        return CommandResponse(
            ok=True,
            command=command,
            message="Terminal Client config",
            data={"config": config, "path": str(_config_path())},
        )
    if request.domain == "config" and request.verb == "resolve-target":
        resolved = _resolve_target(request)
        return CommandResponse(
            ok=bool(resolved.get("ok")),
            command=command,
            message="Target resolved" if resolved.get("ok") else "Target resolution failed",
            data={"target": resolved},
        )
    if request.domain == "config" and request.verb == "profile":
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
    if request.domain == "inspect" and request.verb == "domains":
        return CommandResponse(
            ok=True,
            command=command,
            message="Terminal Client domains",
            data={"domains": list(DOMAINS), "commands": {key: list(value) for key, value in COMMANDS.items()}},
        )
    if request.domain == "inspect" and request.verb == "aliases":
        return CommandResponse(
            ok=True,
            command=command,
            message="Terminal Client aliases",
            data={"aliases": list(ALIASES)},
        )
    if request.domain == "inspect" and request.verb == "contracts":
        return CommandResponse(
            ok=True,
            command=command,
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
            },
        )
    if request.domain == "inspect" and request.verb == "globals":
        return CommandResponse(
            ok=True,
            command=command,
            message="Terminal Client global options",
            data={"globals": _globals_payload(request.globals), "output_profile": request.output_profile},
        )
    if request.domain == "inspect" and request.verb == "events":
        return _inspect_events(request)
    if request.domain == "tui":
        return CommandResponse(
            ok=True,
            command=command,
            message="TUI command spine is available; live panes are implemented by a later ticket.",
            data={"views": ["Live", "REPL", "Browse", "Inspect"]},
        )
    return CommandResponse(
        ok=False,
        command=command,
        message=f"{' '.join(command)} is registered but not implemented yet",
        data={"implemented": False, "args": request.args},
    )


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
        if events is not None:
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
