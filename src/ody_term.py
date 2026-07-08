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
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast


DOMAINS = ("auth", "config", "server", "session", "run", "harness", "service", "inspect", "tui")
OUTPUT_PROFILES = ("human", "grug", "clanker")
FORMATS = ("text", "json", "jsonl", "raw", "debug")
COLOR_MODES = ("auto", "always", "never")

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
    "inspect": ("domains", "aliases", "contracts", "globals"),
    "tui": (),
}


class CommandError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


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
    value_flags = {"--url", "--repo", "--token-ref", "--profile-output", "--host", "--port", "--lines"}
    bool_flags = {"--default", "--dry-run"}
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
                    "sequence",
                    "time",
                    "source",
                    "kind",
                    "level",
                    "payload",
                ],
            },
        )
    if request.domain == "inspect" and request.verb == "globals":
        return CommandResponse(
            ok=True,
            command=command,
            message="Terminal Client global options",
            data={"globals": _globals_payload(request.globals), "output_profile": request.output_profile},
        )
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


def render(response: CommandResponse, request: CommandRequest, stdout: TextIO) -> None:
    payload = _response_payload(response, request)
    output_format = request.globals.format

    if output_format == "json":
        _write_json(payload, stdout)
    elif output_format == "jsonl":
        _write_json(payload, stdout)
    elif output_format == "raw":
        _write_json(response.raw if response.raw is not None else response.data, stdout)
    elif output_format == "debug":
        _write_json({"request": {"domain": request.domain, "verb": request.verb, "args": request.args}, **payload}, stdout)
    elif request.output_profile == "clanker":
        _write_json(payload, stdout)
    elif request.output_profile == "grug":
        status = "ok" if response.ok else "no"
        stdout.write(f"{status} {' '.join(response.command)}: {response.message}\n")
    else:
        stdout.write(response.message + "\n")


def render_error(error: CommandError, *, options: GlobalOptions | None, stdout_is_tty: bool, stderr: TextIO) -> None:
    output_profile = (options.output if options and options.output else None) or ("human" if stdout_is_tty else "clanker")
    output_format = options.format if options else "text"
    payload = {"ok": False, "error": {"code": error.code, "message": error.message}}

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
