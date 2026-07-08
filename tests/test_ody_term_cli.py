from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import ody_term  # noqa: E402


class TtyStringIO(io.StringIO):
    def __init__(self, *, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def run_cli(argv: list[str], *, is_tty: bool = False) -> tuple[int, str, str]:
    stdout = TtyStringIO(is_tty=is_tty)
    stderr = TtyStringIO(is_tty=False)
    exit_code = ody_term.main(argv, stdout=stdout, stderr=stderr)
    return exit_code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture
def isolated_term_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODY_TERM_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("ODY_TERM_RUNTIME", str(tmp_path / "runtime.json"))
    monkeypatch.setenv("ODY_TERM_RUNS", str(tmp_path / "runs.json"))
    monkeypatch.setenv("ODY_TERM_SECRETS", str(tmp_path / "secrets.json"))
    monkeypatch.setattr(ody_term, "COOKBOOK_STATE_FILE", str(tmp_path / "cookbook_state.json"))
    monkeypatch.delenv("ODY_TERM_URL", raising=False)
    monkeypatch.delenv("ODYSSEUS_URL", raising=False)
    monkeypatch.delenv("ODY_TERM_TOKEN", raising=False)
    monkeypatch.delenv("ODY_TERM_SCOPES", raising=False)
    monkeypatch.delenv("ODY_TERM_OWNER", raising=False)
    monkeypatch.delenv("ODY_TERM_ADMIN", raising=False)
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)


def test_help_exposes_terminal_client_domains() -> None:
    exit_code, stdout, stderr = run_cli(["--help"], is_tty=True)

    assert exit_code == 0
    assert stderr == ""
    for domain in ("auth", "config", "server", "session", "run", "harness", "service", "inspect", "tui"):
        assert domain in stdout


def test_domain_help_lists_canonical_verbs() -> None:
    exit_code, stdout, stderr = run_cli(["run", "--help"], is_tty=True)

    assert exit_code == 0
    assert stderr == ""
    assert "ody-term [global-options] run <verb>" in stdout
    for verb in ("start", "list", "status", "attach", "stop"):
        assert verb in stdout


def test_non_tty_defaults_to_clanker_json_contract() -> None:
    exit_code, stdout, stderr = run_cli(["inspect", "contracts"])

    assert exit_code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["profile"] == "clanker"
    assert payload["data"]["default_profile"] == "clanker"
    assert payload["data"]["event_envelope_required_fields"] == [
        "schema",
        "id",
        "seq",
        "time",
        "source",
        "kind",
        "level",
        "payload",
    ]
    assert payload["data"]["event_envelope_field_aliases"] == {"sequence": "seq"}


def test_tty_defaults_to_human_output() -> None:
    exit_code, stdout, stderr = run_cli(["inspect", "contracts"], is_tty=True)

    assert exit_code == 0
    assert stderr == ""
    assert stdout == "Terminal Client output contracts\n"


def test_global_options_parse_before_and_after_domain_path() -> None:
    exit_code, stdout, stderr = run_cli(
        [
            "--target",
            "http://127.0.0.1:7860",
            "inspect",
            "globals",
            "--profile",
            "local",
            "--output",
            "grug",
            "--format",
            "json",
            "--color=never",
            "-vv",
            "--quiet",
            "--yes",
            "--yolo",
            "--start",
            "--ensure-server",
        ],
        is_tty=True,
    )

    assert exit_code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    globals_payload = payload["data"]["globals"]
    assert payload["profile"] == "grug"
    assert globals_payload["target"] == "http://127.0.0.1:7860"
    assert globals_payload["profile"] == "local"
    assert globals_payload["format"] == "json"
    assert globals_payload["color"] == "never"
    assert globals_payload["verbose"] == 2
    assert globals_payload["quiet"] is True
    assert globals_payload["yes"] is True
    assert globals_payload["yolo"] is True
    assert globals_payload["start"] is True
    assert globals_payload["ensure_server"] is True


def test_jsonl_format_emits_one_object_per_line() -> None:
    exit_code, stdout, stderr = run_cli(["--format=jsonl", "inspect", "domains"])

    assert exit_code == 0
    assert stderr == ""
    lines = stdout.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["data"]["domains"][0] == "auth"


def test_registered_but_unimplemented_commands_have_structured_baseline() -> None:
    exit_code, stdout, stderr = run_cli(["session", "list"])

    assert exit_code == 1
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is False
    assert payload["command"] == ["session", "list"]
    assert payload["data"]["implemented"] is False


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["not-a-domain", "list"], "unknown_domain"),
        (["run"], "missing_verb"),
        (["run", "dance"], "unknown_verb"),
        (["--format=xml", "inspect", "domains"], "invalid_format"),
    ],
)
def test_errors_are_structured_for_automation(argv: list[str], code: str) -> None:
    exit_code, stdout, stderr = run_cli(argv)

    assert exit_code == 2
    assert stdout == ""
    payload = json.loads(stderr)
    assert payload["ok"] is False
    assert payload["error"]["code"] == code


def test_config_profile_set_show_and_list_use_client_local_config(isolated_term_state: None) -> None:
    exit_code, stdout, stderr = run_cli(
        [
            "config",
            "profile",
            "set",
            "local",
            "--url=http://127.0.0.1:7860",
            "--repo",
            "/repo",
            "--token-ref",
            "keyring:ody/local",
            "--profile-output",
            "grug",
            "--default",
            "--format=json",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    saved = json.loads(stdout)
    assert saved["data"]["settings"] == {
        "output": "grug",
        "repo": "/repo",
        "token_ref": "keyring:ody/local",
        "url": "http://127.0.0.1:7860",
    }
    assert saved["data"]["default_profile"] == "local"

    exit_code, stdout, stderr = run_cli(["config", "profile", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    listed = json.loads(stdout)
    assert listed["data"]["profiles"] == ["local"]
    assert listed["data"]["default_profile"] == "local"


def test_target_resolution_uses_explicit_target_first(isolated_term_state: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODY_TERM_URL", "http://env.example")

    exit_code, stdout, stderr = run_cli(
        ["config", "resolve-target", "--target", "http://explicit.example", "--format=json"]
    )

    assert exit_code == 0
    assert stderr == ""
    target = json.loads(stdout)["data"]["target"]
    assert target["ok"] is True
    assert target["url"] == "http://explicit.example"
    assert target["source"] == "explicit-target"


def test_target_resolution_uses_default_profile_before_runtime_state(isolated_term_state: None, tmp_path: Path) -> None:
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text('{"url":"http://runtime.example"}\n', encoding="utf-8")

    run_cli(
        [
            "config",
            "profile",
            "set",
            "local",
            "--url",
            "http://profile.example",
            "--default",
            "--format=json",
        ]
    )
    exit_code, stdout, stderr = run_cli(["config", "resolve-target", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    target = json.loads(stdout)["data"]["target"]
    assert target["ok"] is True
    assert target["url"] == "http://profile.example"
    assert target["source"] == "default-profile"
    assert target["profile"] == "local"


def test_noninteractive_target_resolution_fails_without_fallback(isolated_term_state: None) -> None:
    exit_code, stdout, stderr = run_cli(["config", "resolve-target", "--format=json"])

    assert exit_code == 1
    assert stderr == ""
    target = json.loads(stdout)["data"]["target"]
    assert target["ok"] is False
    assert target["source"] == "unresolved"
    assert "non-interactive" in target["reason"]


@pytest.mark.parametrize("flag", ["--start", "--ensure-server"])
def test_target_resolution_bootstraps_for_start_flags(
    isolated_term_state: None, flag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(ody_term.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(ody_term.os, "getpgid", lambda pid: pid)

    exit_code, stdout, stderr = run_cli(["config", "resolve-target", flag, "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    target = json.loads(stdout)["data"]["target"]
    assert target["ok"] is True
    assert target["source"] == "local-bootstrap"
    assert target["url"] == "http://127.0.0.1:7860"
    assert target["server"]["status"] == "starting"


def test_human_target_resolution_uses_localhost_fallback(isolated_term_state: None) -> None:
    exit_code, stdout, stderr = run_cli(["config", "resolve-target", "--format=json"], is_tty=True)

    assert exit_code == 0
    assert stderr == ""
    target = json.loads(stdout)["data"]["target"]
    assert target["ok"] is True
    assert target["url"] == "http://127.0.0.1:7860"
    assert target["source"] == "localhost-fallback"


def test_target_resolution_uses_only_owned_live_runtime_state(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(
        json.dumps(
            {
                "kind": "ody-term-local-server",
                "pid": 4242,
                "pgid": 4242,
                "repo": str(Path(__file__).resolve().parents[1]),
                "command": ["uv", "run", "ody"],
                "url": "http://runtime.example",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ody_term, "_process_alive", lambda pid: True)
    monkeypatch.setattr(ody_term, "_process_group_matches", lambda pid, pgid: True)

    exit_code, stdout, stderr = run_cli(["config", "resolve-target", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    target = json.loads(stdout)["data"]["target"]
    assert target["url"] == "http://runtime.example"
    assert target["source"] == "runtime-state"


def test_target_resolution_refuses_unowned_runtime_url(isolated_term_state: None, tmp_path: Path) -> None:
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text('{"kind":"other-tool","url":"http://unsafe.example"}\n', encoding="utf-8")

    exit_code, stdout, stderr = run_cli(["config", "resolve-target", "--format=json"])

    assert exit_code == 2
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "ambiguous_runtime_state"


def test_server_status_reports_absent_before_api_auth(isolated_term_state: None) -> None:
    exit_code, stdout, stderr = run_cli(["server", "status", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    server = json.loads(stdout)["data"]["server"]
    assert server["status"] == "absent"
    assert server["runtime_state"] is None


def test_server_start_dry_run_delegates_to_existing_launcher(isolated_term_state: None) -> None:
    exit_code, stdout, stderr = run_cli(["server", "start", "--host", "127.0.0.1", "--port", "7861", "--dry-run"])

    assert exit_code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["data"]["delegates_to"] == "uv run ody launch select"
    assert payload["data"]["launcher"] == [
        "uv",
        "run",
        "ody",
        "launch",
        "select",
        "--method",
        "uv-dev",
        "--host",
        "127.0.0.1",
        "--port",
        "7861",
        "--dry-run",
    ]


def test_server_logs_tails_owned_runtime_log(isolated_term_state: None, tmp_path: Path) -> None:
    log_path = tmp_path / "server.log"
    runtime_path = tmp_path / "runtime.json"
    log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    runtime_path.write_text(
        json.dumps(
            {
                "kind": "ody-term-local-server",
                "pid": 99999999,
                "repo": str(Path(__file__).resolve().parents[1]),
                "command": ["uv", "run", "ody"],
                "url": "http://127.0.0.1:7860",
                "log_path": str(log_path),
            }
        ),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(["server", "logs", "--lines", "2", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["data"]["lines"] == ["two", "three"]


def test_server_stop_refuses_stale_runtime_state(isolated_term_state: None, tmp_path: Path) -> None:
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(
        json.dumps(
            {
                "kind": "ody-term-local-server",
                "pid": 99999999,
                "repo": str(Path(__file__).resolve().parents[1]),
                "command": ["uv", "run", "ody"],
                "url": "http://127.0.0.1:7860",
            }
        ),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(["server", "stop", "--format=json"])

    assert exit_code == 1
    assert stderr == ""
    server = json.loads(stdout)["data"]["server"]
    assert server["status"] == "stale"


def test_server_status_refuses_unowned_runtime_state(isolated_term_state: None, tmp_path: Path) -> None:
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text('{"kind":"other-tool","pid":123}\n', encoding="utf-8")

    exit_code, stdout, stderr = run_cli(["server", "status"])

    assert exit_code == 2
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "ambiguous_runtime_state"


def test_auth_status_reports_disabled_and_localhost_bypass_modes(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("LOCALHOST_BYPASS", "true")

    exit_code, stdout, stderr = run_cli(["auth", "status", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    auth = json.loads(stdout)["data"]["auth"]
    assert auth["auth_mode"] == "auth-disabled"
    assert auth["bypass_modes"] == {
        "auth_disabled": True,
        "localhost_bypass": True,
    }
    assert auth["token"]["present"] is False


def test_auth_login_stores_token_in_visible_file_fallback_without_printing_secret(
    isolated_term_state: None,
) -> None:
    exit_code, stdout, stderr = run_cli(
        [
            "auth",
            "login",
            "--token",
            "ody_test_secret",
            "--format=json",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    login = json.loads(stdout)["data"]["auth"]
    assert login["token"]["present"] is True
    assert login["token"]["storage"]["mode"] == "file-fallback"
    assert login["token"]["storage"]["visible_weaker_fallback"] is True
    assert "ody_test_secret" not in stdout

    exit_code, stdout, stderr = run_cli(["auth", "status", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    auth = json.loads(stdout)["data"]["auth"]
    assert auth["auth_mode"] == "token"
    assert auth["owner"] is None
    assert auth["token"]["ref"] == "file:default"
    assert auth["token"]["scopes"] == []
    assert "ody_test_secret" not in stdout


def test_auth_capabilities_reports_token_facts_without_trusting_local_policy(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODY_TERM_TOKEN", "ody_env_secret")

    exit_code, stdout, stderr = run_cli(["auth", "capabilities", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert "ody_env_secret" not in stdout
    assert data["auth_facts"]["auth_mode"] == "token"
    assert data["auth_facts"]["owner"] is None
    assert data["auth_facts"]["token_scopes"] == []
    assert data["policy_facts"]["terminal_scopes"] == []
    capabilities = data["capabilities"]
    assert capabilities["session:read"]["allowed"] is False
    assert capabilities["session:read"]["reason"] == "server_capabilities_unavailable"
    assert capabilities["run:start"]["allowed"] is False
    assert capabilities["run:start"]["reason"] == "server_capabilities_unavailable"
    assert capabilities["service:restart"]["requires_confirmation"] is True
    assert capabilities["service:kill"]["requires_yolo"] is True
    assert capabilities["service:kill"]["admin_only"] is True


def test_auth_logout_removes_stored_token(isolated_term_state: None) -> None:
    run_cli(["auth", "login", "--token", "ody_test_secret", "--format=json"])

    exit_code, stdout, stderr = run_cli(["auth", "logout", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["auth"]["token"]["present"] is False


def test_confirmation_gates_do_not_bypass_missing_capability(isolated_term_state: None) -> None:
    run_cli(["auth", "login", "--token", "ody_test_secret", "--format=json"])

    exit_code, stdout, stderr = run_cli(["run", "stop", "run-1", "--yes", "--format=json"])

    assert exit_code == 2
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "capability_denied"
    assert "run:stop" in error["message"]


def test_run_start_creates_distinct_chat_run_for_new_session(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(["run", "start", "--kind", "chat", "--message", "hello", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    run = data["run"]
    assert run["kind"] == "chat"
    assert run["status"] == "running"
    assert run["run_id"].startswith("run_")
    assert run["session_id"].startswith("ses_")
    assert run["run_id"] != run["session_id"]
    assert run["events_available"] is True
    assert data["cursor"] == {"after": None, "next": "1", "count": 1}


def test_run_start_can_target_existing_session_and_status_lists_recent_runs(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    run_cli(["run", "start", "--kind", "chat", "--session-id", "ses_existing", "--message", "first", "--format=json"])
    exit_code, stdout, stderr = run_cli(["run", "list", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    runs = json.loads(stdout)["data"]["runs"]
    assert len(runs) == 1
    assert runs[0]["session_id"] == "ses_existing"
    assert runs[0]["kind"] == "chat"
    assert runs[0]["last_activity"]["kind"] == "run.status"
    assert runs[0]["replay_available"] is True
    assert runs[0]["cursor_available"] is True

    run_id = runs[0]["run_id"]
    exit_code, stdout, stderr = run_cli(["run", "status", run_id, "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    status = json.loads(stdout)["data"]["run"]
    assert status["run_id"] == run_id
    assert status["session_id"] == "ses_existing"
    assert status["heartbeat"]["summary"] == "chat Run started"


def test_run_attach_emits_chat_run_event_envelopes_as_jsonl(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(["run", "start", "--kind", "chat", "--session-id", "ses_chat", "--message", "hi"])
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(["run", "attach", run_id, "--format=jsonl"])

    assert exit_code == 0
    assert stderr == ""
    events = [json.loads(line) for line in stdout.splitlines()]
    assert len(events) == 1
    assert events[0]["schema"] == "ody.event.v1"
    assert events[0]["source"] == "chat"
    assert events[0]["kind"] == "run.status"
    assert events[0]["session_id"] == "ses_chat"
    assert events[0]["run_id"] == run_id
    assert events[0]["payload"]["message"] == "hi"


def test_agent_runs_use_same_run_lifecycle_and_heartbeat_events(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(["run", "start", "--kind", "agent", "--message", "work", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    run = data["run"]
    assert run["kind"] == "agent"
    assert run["status"] == "running"
    assert data["cursor"] == {"after": None, "next": "2", "count": 2}

    run_id = run["run_id"]
    exit_code, stdout, stderr = run_cli(["run", "attach", run_id, "--format=jsonl"])

    assert exit_code == 0
    assert stderr == ""
    events = [json.loads(line) for line in stdout.splitlines()]
    assert [event["source"] for event in events] == ["agent", "agent"]
    assert [event["kind"] for event in events] == ["run.status", "heartbeat"]
    assert events[0]["session_id"] == run["session_id"]
    assert events[0]["run_id"] == run_id
    assert events[1]["payload"] == {"activity": "started", "status": "running"}

    exit_code, stdout, stderr = run_cli(["run", "stop", run_id, "--yes", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["run"]["status"] == "stopped"


def test_harness_linked_runs_include_odysseus_and_harness_identities(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(
        [
            "run",
            "start",
            "--kind",
            "harness",
            "--session-id",
            "ses_ody",
            "--harness-adapter",
            "pi",
            "--harness-session-id",
            "pi-session-1",
            "--message",
            "observe",
            "--format=json",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    run = json.loads(stdout)["data"]["run"]
    assert run["kind"] == "harness"
    assert run["session_id"] == "ses_ody"
    assert run["harness_adapter_id"] == "pi"
    assert run["harness_session_id"] == "pi-session-1"

    exit_code, stdout, stderr = run_cli(["run", "attach", run["run_id"], "--format=jsonl"])

    assert exit_code == 0
    assert stderr == ""
    events = [json.loads(line) for line in stdout.splitlines()]
    assert [event["source"] for event in events] == ["harness", "harness"]
    assert all(event["session_id"] == "ses_ody" for event in events)
    assert all(event["run_id"] == run["run_id"] for event in events)
    assert all(event["harness_session_id"] == "pi-session-1" for event in events)
    assert all(event["harness_adapter_id"] == "pi" for event in events)
    assert events[0]["payload"]["harness_adapter_id"] == "pi"
    assert events[0]["payload"]["harness_session_id"] == "pi-session-1"


def test_harness_commands_report_adapter_capabilities(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(["harness", "list", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    harnesses = json.loads(stdout)["data"]["harnesses"]
    assert harnesses[0]["id"] == "pi"
    assert harnesses[0]["session"]["abort"] is True


def test_harness_stop_refuses_non_harness_runs_even_with_adapter_flag(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(["run", "start", "--kind", "agent", "--session-id", "ses_agent", "--format=json"])
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(
        ["harness", "stop", run_id, "--harness-adapter", "pi", "--yes", "--format=json"]
    )

    assert exit_code == 1
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "not_harness_run"
    assert error["details"] == {"kind": "agent", "run_id": run_id}


def test_run_attach_by_session_fails_when_multiple_active_runs_match(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    run_cli(["run", "start", "--session-id", "ses_ambiguous", "--message", "one", "--format=json"])
    run_cli(["run", "start", "--session-id", "ses_ambiguous", "--message", "two", "--format=json"])

    exit_code, stdout, stderr = run_cli(["run", "attach", "--session-id", "ses_ambiguous", "--format=json"])

    assert exit_code == 2
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "ambiguous_run"
    assert "ses_ambiguous" in error["message"]
    assert error["details"]["session_id"] == "ses_ambiguous"
    assert len(error["details"]["choices"]) == 2
    assert {choice["status"] for choice in error["details"]["choices"]} == {"running"}


def test_run_stop_targets_run_lifecycle_and_updates_status(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(["run", "start", "--session-id", "ses_stop", "--message", "stop me", "--format=json"])
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(["run", "stop", run_id, "--yes", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    stopped = json.loads(stdout)["data"]["run"]
    assert stopped["status"] == "stopped"
    assert stopped["run_id"] == run_id
    assert stopped["session_id"] == "ses_stop"

    exit_code, stdout, stderr = run_cli(["run", "status", run_id, "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["run"]["status"] == "stopped"


def test_ordinary_confirmation_requires_yes_when_capability_allows(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(["run", "stop", "run-1", "--format=json"])

    assert exit_code == 2
    assert stdout == ""
    assert json.loads(stderr)["error"]["code"] == "confirmation_required"


def test_ordinary_confirmation_accepts_yes_when_capability_allows(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(["run", "start", "--message", "confirm stop", "--format=json"])
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(["run", "stop", run_id, "--yes", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["confirmation"]["satisfied_by"] == "--yes"


def test_elevated_confirmation_never_bypasses_admin_policy(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(["service", "stop", "main-server", "--force", "--yolo", "--format=json"])

    assert exit_code == 2
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "capability_denied"
    assert "admin_only" in error["message"]


def test_service_control_does_not_treat_pid_as_an_elevated_target(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(["service", "stop", "pid:123", "--yes", "--format=json"])

    assert exit_code == 2
    assert stdout == ""
    assert json.loads(stderr)["error"]["code"] == "arbitrary_process_unsupported"


def test_service_list_includes_managed_lifecycle_targets_with_capability_flags(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    log_path = tmp_path / "server.log"
    log_path.write_text("booted\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text(
        json.dumps(
            {
                "kind": "ody-term-local-server",
                "pid": 4242,
                "pgid": 4242,
                "repo": str(Path(__file__).resolve().parents[1]),
                "command": ["uv", "run", "ody"],
                "url": "http://127.0.0.1:7860",
                "log_path": str(log_path),
                "started_at": "2026-07-08T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ody_term, "_process_alive", lambda pid: True)
    monkeypatch.setattr(ody_term, "_process_group_matches", lambda pid, pgid: True)
    (tmp_path / "cookbook_state.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "cookbook-session-1",
                        "sessionId": "cookbook-session-1",
                        "name": "scheduled llama",
                        "type": "serve",
                        "status": "running",
                        "ts": 1770000000000,
                        "output": "cookbook booted\nWARNING warming up\n",
                        "_scheduledByOwner": "alice",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    run_cli(["run", "start", "--kind", "harness", "--harness-adapter", "pi", "--format=json"])

    exit_code, stdout, stderr = run_cli(["service", "list", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    targets = json.loads(stdout)["data"]["targets"]
    by_id = {target["id"]: target for target in targets}
    assert by_id["main-server"]["kind"] == "server"
    assert by_id["main-server"]["status"] == "running"
    assert by_id["main-server"]["capabilities"] == {
        "logs": True,
        "stop": True,
        "restart": True,
        "force": False,
    }
    assert by_id["harness-bridge:pi"]["kind"] == "harness-bridge"
    assert by_id["model-serving"]["status"] == "unknown"
    assert by_id["mcp"]["kind"] == "mcp"
    assert by_id["cookbook-serving"]["kind"] == "cookbook"
    assert by_id["cookbook:cookbook-session-1"]["status"] == "running"
    assert by_id["cookbook:cookbook-session-1"]["last_activity"] == 1770000000000
    assert by_id["cookbook:cookbook-session-1"]["capabilities"]["logs"] is True
    assert any(target["kind"] == "run" and target["source"]["run_kind"] == "harness" for target in targets)


def test_service_status_preserves_raw_managed_target_details(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    (tmp_path / "runtime.json").write_text(
        json.dumps(
            {
                "kind": "ody-term-local-server",
                "pid": 4242,
                "pgid": 4242,
                "repo": str(Path(__file__).resolve().parents[1]),
                "command": ["uv", "run", "ody"],
                "url": "http://127.0.0.1:7860",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ody_term, "_process_alive", lambda pid: True)
    monkeypatch.setattr(ody_term, "_process_group_matches", lambda pid, pgid: True)

    exit_code, stdout, stderr = run_cli(["service", "status", "main-server", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    target = json.loads(stdout)["data"]["target"]
    assert target["id"] == "main-server"
    assert target["status"] == "running"
    assert target["source"]["type"] == "local-server-runtime"
    assert target["raw"]["runtime_state"]["pid"] == 4242


def test_service_logs_are_event_envelopes_for_managed_targets(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    log_path = tmp_path / "server.log"
    log_path.write_text("one\nERROR failed\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text(
        json.dumps(
            {
                "kind": "ody-term-local-server",
                "pid": 99999999,
                "repo": str(Path(__file__).resolve().parents[1]),
                "command": ["uv", "run", "ody"],
                "url": "http://127.0.0.1:7860",
                "log_path": str(log_path),
            }
        ),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(["service", "logs", "main-server", "--format=jsonl"])

    assert exit_code == 0
    assert stderr == ""
    events = [json.loads(line) for line in stdout.splitlines()]
    assert [event["source"] for event in events] == ["server", "server"]
    assert events[1]["kind"] == "log"
    assert events[1]["level"] == "error"
    assert events[1]["payload"] == {"message": "ERROR failed"}


def test_service_logs_exposes_cookbook_task_output_as_event_envelopes(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    (tmp_path / "cookbook_state.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "cookbook-session-1",
                        "sessionId": "cookbook-session-1",
                        "name": "scheduled llama",
                        "type": "serve",
                        "status": "running",
                        "output": "started\nERROR failed warmup\n",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(["service", "logs", "cookbook:cookbook-session-1", "--format=jsonl"])

    assert exit_code == 0
    assert stderr == ""
    events = [json.loads(line) for line in stdout.splitlines()]
    assert [event["source"] for event in events] == ["cookbook", "cookbook"]
    assert events[1]["kind"] == "log"
    assert events[1]["level"] == "error"
    assert events[1]["payload"] == {"message": "ERROR failed warmup"}


def test_service_stop_targets_run_lifecycle_without_host_process_mutation(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(["run", "start", "--kind", "agent", "--format=json"])
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(["service", "stop", f"run:{run_id}", "--yes", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert data["target"]["id"] == f"run:{run_id}"
    assert data["target"]["status"] == "stopped"
    assert data["confirmation"]["satisfied_by"] == "--yes"


def test_service_force_requires_target_force_capability_after_admin_gate(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(
        ody_term,
        "_capability_status",
        lambda capability, auth: {
            "resource": capability.split(":", 1)[0],
            "action": capability.split(":", 1)[1],
            "allowed": True,
            "requires_confirmation": True,
            "requires_yolo": capability == "service:kill",
            "admin_only": capability == "service:kill",
            "reason": None,
        },
    )

    exit_code, stdout, stderr = run_cli(["service", "stop", "main-server", "--force", "--yolo", "--format=json"])

    assert exit_code == 1
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "unsupported_lifecycle_action"
    assert error["details"] == {"target": "main-server", "action": "force", "supported": False}


def test_service_restart_is_bounded_to_known_restartable_targets(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(["service", "restart", "mcp", "--yes", "--format=json"])

    assert exit_code == 1
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "unsupported_lifecycle_action"
    assert error["details"] == {"target": "mcp", "action": "restart", "supported": False}


def test_service_restart_does_not_bootstrap_absent_main_server(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(["service", "restart", "main-server", "--yes", "--format=json"])

    assert exit_code == 1
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "unsupported_lifecycle_action"
    assert error["details"] == {"target": "main-server", "action": "restart", "supported": False}


def test_inspect_events_json_normalizes_server_logs_as_event_envelopes(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    log_path = tmp_path / "server.log"
    log_path.write_text("booted\nWARNING: slow provider\nERROR failed request\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text(
        json.dumps(
            {
                "kind": "ody-term-local-server",
                "pid": 99999999,
                "repo": str(Path(__file__).resolve().parents[1]),
                "command": ["uv", "run", "ody"],
                "url": "http://127.0.0.1:7860",
                "log_path": str(log_path),
                "started_at": "2026-07-08T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(["inspect", "events", "--lines", "2", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert data["cursor"] == {"after": None, "next": "3", "count": 2}
    events = data["events"]
    assert [event["seq"] for event in events] == [2, 3]
    assert [event["level"] for event in events] == ["warn", "error"]
    assert events[0]["schema"] == "ody.event.v1"
    assert events[0]["source"] == "server"
    assert events[0]["kind"] == "log"
    assert "session_id" not in events[0]
    assert "run_id" not in events[0]
    assert "harness_session_id" not in events[0]
    assert events[0]["payload"] == {"message": "WARNING: slow provider"}
    assert events[0]["raw"]["transport"] == "log"
    assert events[0]["raw"]["body"]["line"] == "WARNING: slow provider"


def test_inspect_events_jsonl_emits_one_envelope_per_line(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    log_path = tmp_path / "server.log"
    log_path.write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text(
        json.dumps({"kind": "ody-term-local-server", "log_path": str(log_path)}),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(["inspect", "events", "--format=jsonl"])

    assert exit_code == 0
    assert stderr == ""
    lines = stdout.splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["payload"]["message"] for line in lines] == ["one", "two"]


def test_inspect_events_raw_requires_raw_event_capability(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODY_TERM_TOKEN", "ody_env_secret")
    log_path = tmp_path / "server.log"
    log_path.write_text("one\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text(
        json.dumps({"kind": "ody-term-local-server", "log_path": str(log_path)}),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(["inspect", "events", "--format=raw"])

    assert exit_code == 2
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "capability_denied"
    assert "event:raw" in error["message"]


def test_inspect_events_filters_by_identity_and_correlation_fields(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    log_path = tmp_path / "server.log"
    log_path.write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text(
        json.dumps({"kind": "ody-term-local-server", "log_path": str(log_path)}),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(
        ["inspect", "events", "--run-id", "run_missing", "--span-id", "span_missing", "--format=json"]
    )

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert data["events"] == []
    assert data["filters"]["run_id"] == "run_missing"
    assert data["filters"]["span_id"] == "span_missing"


def test_inspect_events_debug_includes_renderer_and_safety_context(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    log_path = tmp_path / "server.log"
    log_path.write_text("one\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text(
        json.dumps({"kind": "ody-term-local-server", "log_path": str(log_path)}),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(["inspect", "events", "--format=debug"])

    assert exit_code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["renderer"]["contract"] == "event-envelope"
    assert payload["renderer"]["raw_included"] is True
    assert payload["data"]["safety"]["capability"] == "event:raw"
    assert payload["data"]["capability"]["allowed"] is True


def test_tui_model_live_view_uses_shared_events_and_service_logs(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    log_path = tmp_path / "server.log"
    log_path.write_text("server ready\nWARNING warmup\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text(
        json.dumps(
            {
                "kind": "ody-term-local-server",
                "pid": 4242,
                "pgid": 4242,
                "repo": str(Path(__file__).resolve().parents[1]),
                "command": ["uv", "run", "ody"],
                "url": "http://127.0.0.1:7860",
                "log_path": str(log_path),
                "started_at": "2026-07-08T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ody_term, "_process_alive", lambda pid: True)
    monkeypatch.setattr(ody_term, "_process_group_matches", lambda pid, pgid: True)
    _, stdout, _ = run_cli(
        [
            "run",
            "start",
            "--kind",
            "harness",
            "--session-id",
            "ses_tui",
            "--harness-adapter",
            "pi",
            "--harness-session-id",
            "pi-tui-1",
            "--message",
            "watch",
            "--format=json",
        ]
    )
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(["tui", "--format=json"], is_tty=True)

    assert exit_code == 0
    assert stderr == ""
    model = json.loads(stdout)["data"]["tui"]
    assert model["schema"] == "ody.tui.v1"
    assert model["active_view"] == "Live"
    assert set(model["views"]) == {"Live", "REPL", "Browse", "Inspect"}

    live = model["views"]["Live"]
    assert live["selected_event"]["schema"] == "ody.event.v1"
    assert any("harness.heartbeat" in line for line in live["timeline_lines"])
    assert any("server.log" in line for line in live["timeline_lines"])
    assert {event["source"] for event in live["timeline"]} == {"harness", "server"}
    assert any(event["run_id"] == run_id for event in live["timeline"] if event["source"] == "harness")


def test_tui_repl_and_interaction_paths_are_harnessed(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(
        [
            "run",
            "start",
            "--kind",
            "harness",
            "--session-id",
            "ses_tui",
            "--harness-adapter",
            "pi",
            "--harness-session-id",
            "pi-tui-1",
            "--message",
            "watch",
            "--format=json",
        ]
    )
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(
        ["tui", "--view", "REPL", "--key", "f3", "--mouse", "event_click", "--repl", "stop", "--format=json"],
        is_tty=True,
    )

    assert exit_code == 0
    assert stderr == ""
    model = json.loads(stdout)["data"]["tui"]
    assert model["active_view"] == "Browse"
    assert model["interaction"]["keyboard"]["f1"] == "Live"
    assert model["interaction"]["keyboard"]["f4"] == "Inspect"
    assert model["interaction"]["mouse"]["event_click"] == "select-event"
    assert model["interaction"]["last"]["keyboard_event"] == {"input": "f3", "action": "switch-view", "view": "Browse"}
    assert model["interaction"]["last"]["mouse_event"]["input"] == "event_click"
    assert model["interaction"]["last"]["mouse_event"]["selected_event"]["run_id"] == run_id

    repl = model["views"]["REPL"]
    assert repl["prompt"] == "ody-term>"
    assert [command["command"] for command in repl["commands"]] == [
        "status",
        "tail",
        "filter",
        "stop",
        "harness",
        "service",
    ]
    assert repl["capability_limited"] is True
    assert repl["history"][0]["command"] == "stop"
    assert repl["history"][0]["attempted"] is True
    assert repl["history"][0]["status"] == "confirmation_required"
    assert repl["history"][0]["result"]["run_id"] == run_id


def test_tui_browse_and_inspect_views_expose_shared_model_state(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    log_path = tmp_path / "server.log"
    log_path.write_text("server ready\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text(
        json.dumps(
            {
                "kind": "ody-term-local-server",
                "pid": 4242,
                "pgid": 4242,
                "repo": str(Path(__file__).resolve().parents[1]),
                "command": ["uv", "run", "ody"],
                "url": "http://127.0.0.1:7860",
                "log_path": str(log_path),
                "started_at": "2026-07-08T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ody_term, "_process_alive", lambda pid: True)
    monkeypatch.setattr(ody_term, "_process_group_matches", lambda pid, pgid: True)
    _, stdout, _ = run_cli(
        [
            "run",
            "start",
            "--kind",
            "harness",
            "--session-id",
            "ses_tui",
            "--harness-adapter",
            "pi",
            "--harness-session-id",
            "pi-tui-1",
            "--message",
            "watch",
            "--format=json",
        ]
    )
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(["tui", "--format=json"], is_tty=True)

    assert exit_code == 0
    assert stderr == ""
    model = json.loads(stdout)["data"]["tui"]
    browse = model["views"]["Browse"]
    assert browse["tree"][0]["id"] == "ses_tui"
    assert browse["tree"][0]["children"][0]["id"] == run_id
    assert browse["tree"][0]["children"][0]["children"][1]["id"] == "pi-tui-1"
    assert any(target["id"] == "main-server" for target in browse["lifecycle_targets"])
    assert any(target["id"] == f"run:{run_id}" for target in browse["lifecycle_targets"])
    assert any(target["id"] == "harness-bridge:pi" for target in browse["lifecycle_targets"])

    inspect = model["views"]["Inspect"]
    assert inspect["model"]["sessions"] == 1
    assert inspect["model"]["runs"] == 1
    assert inspect["model"]["events"] == 3
    assert inspect["target"]["value"]["source"] == "runtime-state"
    assert inspect["capabilities"]["auth_facts"]["auth_mode"] == "auth-disabled"
    assert inspect["event_envelope_sample"]["schema"] == "ody.event.v1"
    assert inspect["shared_state_sources"] == [
        "run-state",
        "event-envelopes",
        "lifecycle-targets",
        "terminal-capabilities",
        "target-resolution",
    ]


def test_tui_human_screen_has_focused_live_repl_browse_and_inspect_views(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    run_cli(["run", "start", "--kind", "agent", "--session-id", "ses_screen", "--message", "render", "--format=json"])

    exit_code, stdout, stderr = run_cli(["tui"], is_tty=True)

    assert exit_code == 0
    assert stderr == ""
    assert "ody-term tui" in stdout
    assert "[Live] | REPL | Browse | Inspect" in stdout
    assert "agent.heartbeat" in stdout
    assert "commands: status, tail, filter, stop, harness, service" in stdout
    assert "Session ses_screen" in stdout
    assert "sessions=1 runs=1 events=2" in stdout
    assert "keyboard: F1-F4, 1-4, Tab; mouse: tabs, event rows, tree nodes, controls" in stdout
