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
    monkeypatch.setenv("ODY_TERM_SECRETS", str(tmp_path / "secrets.json"))
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
    exit_code, stdout, stderr = run_cli(["run", "list"])

    assert exit_code == 1
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is False
    assert payload["command"] == ["run", "list"]
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

    exit_code, stdout, stderr = run_cli(["run", "stop", "run-1", "--yes", "--format=json"])

    assert exit_code == 1
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
