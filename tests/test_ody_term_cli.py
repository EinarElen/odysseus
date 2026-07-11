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

MODEL_ENDPOINT_URL = "http://model.local/v1/chat/completions"
TEST_MODEL = "test-model"
TEST_PRESET = "default"


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
    monkeypatch.setenv("ODY_TERM_SECRET_BACKEND", "file")
    monkeypatch.setattr(ody_term, "COOKBOOK_STATE_FILE", str(tmp_path / "cookbook_state.json"))
    monkeypatch.delenv("ODY_TERM_URL", raising=False)
    monkeypatch.delenv("ODYSSEUS_URL", raising=False)
    monkeypatch.delenv("ODY_TERM_TOKEN", raising=False)
    monkeypatch.delenv("ODY_TERM_SCOPES", raising=False)
    monkeypatch.delenv("ODY_TERM_OWNER", raising=False)
    monkeypatch.delenv("ODY_TERM_ADMIN", raising=False)
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)


@pytest.fixture
def terminal_api_fake(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, object] | None]] = []
    state: dict[str, object] = {"runs": {}, "events": {}}

    def _summary(run: dict[str, object]) -> dict[str, object]:
        events = state["events"].get(run["run_id"], [])  # type: ignore[index, union-attr]
        return {
            **run,
            "events_available": bool(events),
            "replay_available": bool(events),
            "cursor_available": bool(events),
            "event_count": len(events),
            "last_activity": {
                "time": events[-1]["time"],
                "kind": events[-1]["kind"],
                "level": events[-1]["level"],
                "summary": events[-1]["summary"],
            }
            if events
            else None,
            "heartbeat": {
                "time": events[-1]["time"],
                "kind": events[-1]["kind"],
                "level": events[-1]["level"],
                "summary": events[-1]["summary"],
            }
            if events
            else None,
        }

    def fake_api(
        request: ody_term.CommandRequest,
        method: str,
        path: str,
        *,
        query: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        calls.append((method, path, query, body))
        runs = state["runs"]  # type: ignore[assignment]
        events_by_run = state["events"]  # type: ignore[assignment]
        if method == "POST" and path == "/api/terminal/runs":
            assert body is not None
            run_id = f"run_api_{len(runs) + 1}"
            session_id = str(body.get("session_id") or f"ses_api_{len(runs) + 1}")
            kind = str(body.get("kind") or "chat")
            run = {
                "run_id": run_id,
                "session_id": session_id,
                "kind": kind,
                "status": "running",
                "started_at": "2026-07-09T00:00:00+00:00",
                "updated_at": "2026-07-09T00:00:00+00:00",
                "finished_at": None,
            }
            if kind == "harness":
                run["harness_adapter_id"] = str(body.get("harness_adapter_id") or "")
                run["harness_session_id"] = str(body.get("harness_session_id") or "pi-session-live")
            runs[run_id] = run
            events_by_run[run_id] = [
                {
                    "schema": "ody.event.v1",
                    "id": f"evt_{run_id}_1",
                    "seq": 1,
                    "time": "2026-07-09T00:00:00+00:00",
                    "session_id": session_id,
                    "run_id": run_id,
                    "source": kind,
                    "kind": "run.status",
                    "level": "info",
                    "summary": f"{kind} Run started",
                    "payload": {
                        "status": "running",
                        "message": str(body.get("message") or ""),
                        "kind": kind,
                        "harness_adapter_id": run.get("harness_adapter_id"),
                        "harness_session_id": run.get("harness_session_id"),
                    },
                    "raw": {
                        "transport": "sse",
                        "type": "status",
                        "body": {"status": "running", "message": str(body.get("message") or "")},
                    },
                }
            ]
            if kind in {"agent", "harness"}:
                events_by_run[run_id].append(
                    {
                        "schema": "ody.event.v1",
                        "id": f"evt_{run_id}_2",
                        "seq": 2,
                        "time": "2026-07-09T00:00:01+00:00",
                        "session_id": session_id,
                        "run_id": run_id,
                        "source": kind,
                        "kind": "heartbeat",
                        "level": "info",
                        "summary": f"{kind} Run heartbeat",
                        "payload": {"status": "running", "activity": "started"},
                        "raw": {
                            "transport": "sse",
                            "type": "heartbeat",
                            "body": {"status": "running", "activity": "started"},
                        },
                    }
                )
            if kind == "harness":
                for event in events_by_run[run_id]:
                    event["harness_adapter_id"] = run["harness_adapter_id"]
                    event["harness_session_id"] = run["harness_session_id"]
            count = len(events_by_run[run_id])
            return {"run": _summary(run), "cursor": {"after": None, "next": str(count), "count": count}}
        if method == "GET" and path == "/api/terminal/runs":
            kind = query.get("kind") if query else None
            status = query.get("status") if query else None
            values = [_summary(run) for run in runs.values() if (not kind or run.get("kind") == kind)]
            if status:
                values = [run for run in values if run.get("status") == status]
            return {"runs": values}
        if method == "GET" and path == "/api/terminal/events":
            assert query is not None
            run_id = str(query.get("run_id") or "")
            session_id = str(query.get("session_id") or "")
            if not run_id:
                matches = [run for run in runs.values() if run.get("session_id") == session_id]
                if len(matches) != 1:
                    raise AssertionError(f"unexpected event query resolve for {session_id}")
                run_id = str(matches[0]["run_id"])
            cursor = int(query["cursor"]) if query.get("cursor") is not None else None
            filters = {field: query.get(field) for field in ("source", "kind", "level")}
            scanned = [event for event in events_by_run[run_id] if cursor is None or event["seq"] > cursor]
            limit = int(query["limit"]) if query.get("limit") is not None else len(scanned)
            scanned = scanned[:limit]
            events = [
                dict(event)
                for event in scanned
                if all(expected is None or event.get(field) == expected for field, expected in filters.items())
            ]
            if not query.get("include_raw"):
                for event in events:
                    event.pop("raw", None)
            next_cursor = str(scanned[-1]["seq"]) if scanned else (str(cursor) if cursor is not None else None)
            return {
                "run": _summary(runs[run_id]),
                "events": events,
                "cursor": {
                    "after": str(cursor) if cursor is not None else None,
                    "next": next_cursor,
                    "count": len(events),
                },
                "filters": filters,
            }
        if method == "GET" and path.startswith("/api/terminal/runs/by-session/") and path.endswith("/events"):
            session_id = path.split("/")[5]
            matches = [run for run in runs.values() if run.get("session_id") == session_id and run.get("status") == "running"]
            if len(matches) != 1:
                raise ody_term.CommandError(
                    "ambiguous_run",
                    f"Session {session_id} matches multiple active Runs",
                    exit_code=2,
                    details={"session_id": session_id, "choices": [_summary(run) for run in matches]},
                )
            run = matches[0]
            cursor = int(query["cursor"]) if query and query.get("cursor") is not None else None
            events = [event for event in events_by_run[run["run_id"]] if cursor is None or event["seq"] > cursor]
            next_cursor = str(events[-1]["seq"]) if events else (str(cursor) if cursor is not None else None)
            return {
                "run": _summary(run),
                "events": events,
                "cursor": {"after": str(cursor) if cursor is not None else None, "next": next_cursor, "count": len(events)},
            }
        if method == "GET" and path.startswith("/api/terminal/runs/by-session/"):
            session_id = path.rsplit("/", 1)[1]
            matches = [run for run in runs.values() if run.get("session_id") == session_id and run.get("status") == "running"]
            if len(matches) != 1:
                raise AssertionError(f"unexpected by-session resolve for {session_id}")
            return {"run": _summary(matches[0])}
        if method == "POST" and path.startswith("/api/terminal/runs/by-session/") and path.endswith("/stop"):
            session_id = path.split("/")[5]
            matches = [run for run in runs.values() if run.get("session_id") == session_id and run.get("status") == "running"]
            if len(matches) != 1:
                raise AssertionError(f"unexpected by-session stop for {session_id}")
            run = matches[0]
            run["status"] = "stopped"
            run["updated_at"] = "2026-07-09T00:00:01+00:00"
            run["finished_at"] = "2026-07-09T00:00:01+00:00"
            return {"run": _summary(run), "stopped": True}
        if method == "GET" and path.startswith("/api/terminal/runs/") and path.endswith("/events"):
            run_id = path.split("/")[4]
            cursor = int(query["cursor"]) if query and query.get("cursor") is not None else None
            events = [event for event in events_by_run[run_id] if cursor is None or event["seq"] > cursor]
            next_cursor = str(events[-1]["seq"]) if events else (str(cursor) if cursor is not None else None)
            return {
                "run": _summary(runs[run_id]),
                "events": events,
                "cursor": {"after": str(cursor) if cursor is not None else None, "next": next_cursor, "count": len(events)},
            }
        if method == "GET" and path.startswith("/api/terminal/runs/"):
            run_id = path.rsplit("/", 1)[1]
            return {"run": _summary(runs[run_id])}
        if method == "POST" and path.endswith("/stop"):
            run_id = path.split("/")[4]
            run = runs[run_id]
            run["status"] = "stopped"
            run["updated_at"] = "2026-07-09T00:00:01+00:00"
            run["finished_at"] = "2026-07-09T00:00:01+00:00"
            return {"run": _summary(run), "stopped": True}
        raise AssertionError(f"unexpected API request {method} {path}")

    def fake_event_stream(request, path, *, query=None):
        assert path == "/api/terminal/events/stream"
        assert query is not None
        calls.append(("GET", path, query, None))
        runs = state["runs"]
        events_by_run = state["events"]
        run_id = str(query.get("run_id") or "")
        if not run_id:
            session_id = str(query.get("session_id") or "")
            matches = [run for run in runs.values() if run.get("session_id") == session_id]  # type: ignore[union-attr]
            if len(matches) != 1:
                raise AssertionError(f"unexpected event stream resolve for {session_id}")
            run_id = str(matches[0]["run_id"])
        cursor = int(query["cursor"]) if query.get("cursor") is not None else None
        filters = {field: query.get(field) for field in ("source", "kind", "level")}
        for stored in events_by_run[run_id]:  # type: ignore[index]
            if cursor is not None and stored["seq"] <= cursor:
                continue
            if not all(expected is None or stored.get(field) == expected for field, expected in filters.items()):
                continue
            event = dict(stored)
            if not query.get("include_raw"):
                event.pop("raw", None)
            yield event

    monkeypatch.setattr(ody_term, "_terminal_api_request", fake_api)
    monkeypatch.setattr(ody_term, "_terminal_api_event_stream", fake_event_stream)
    return {"calls": calls, "state": state}


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
    assert payload["data"]["clanker"]["event_jsonl"] == "Event-stream commands emit one ody.event.v1 Event Envelope per line."
    assert payload["data"]["cursor"]["field"] == "data.cursor.next"
    assert payload["data"]["exit_codes"]["2"] == "usage, auth, capability, confirmation, or policy failure"


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


def test_auth_login_persists_declared_scope_metadata(isolated_term_state: None) -> None:
    exit_code, stdout, stderr = run_cli(
        [
            "auth",
            "login",
            "--token",
            "ody_test_secret",
            "--scopes",
            "run:start, run:read,event:read,event:raw",
            "--format=json",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    auth = json.loads(stdout)["data"]["auth"]
    assert auth["token"]["scopes"] == ["event:raw", "event:read", "run:read", "run:start"]
    assert "ody_test_secret" not in stdout

    secrets = ody_term._load_secrets()
    entry = secrets["tokens"]["file:default"]
    assert entry["scopes"] == ["event:raw", "event:read", "run:read", "run:start"]


def test_auth_login_uses_keychain_when_available(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setenv("ODY_TERM_SECRET_BACKEND", "auto")
    monkeypatch.setattr(ody_term, "_keychain_available", lambda: True)
    monkeypatch.setattr(ody_term, "_store_os_secret", lambda ref, token: stored.setdefault(ref, token) == token)
    monkeypatch.setattr(ody_term, "_load_os_secret", lambda ref: stored.get(ref))

    exit_code, stdout, stderr = run_cli(["auth", "login", "--token", "ody_keychain_secret", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    auth = json.loads(stdout)["data"]["auth"]
    assert auth["token"]["ref"] == "keychain:ody-term/default"
    assert auth["token"]["storage"]["mode"] == "keychain"
    assert auth["token"]["storage"]["visible_weaker_fallback"] is False
    assert "ody_keychain_secret" not in stdout


def test_auth_login_keeps_scope_metadata_for_keychain_token(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setenv("ODY_TERM_SECRET_BACKEND", "auto")
    monkeypatch.setattr(ody_term, "_keychain_available", lambda: True)
    monkeypatch.setattr(ody_term, "_store_os_secret", lambda ref, token: stored.setdefault(ref, token) == token)
    monkeypatch.setattr(ody_term, "_load_os_secret", lambda ref: stored.get(ref))

    exit_code, stdout, stderr = run_cli(
        [
            "auth",
            "login",
            "--token",
            "ody_keychain_secret",
            "--scopes=run:start,event:read",
            "--format=json",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    auth = json.loads(stdout)["data"]["auth"]
    assert auth["token"]["storage"]["mode"] == "keychain"
    assert auth["token"]["scopes"] == ["event:read", "run:start"]
    assert "ody_keychain_secret" not in stdout

    secrets = ody_term._load_secrets()
    entry = secrets["tokens"]["keychain:ody-term/default"]
    assert "token" not in entry
    assert entry["scopes"] == ["event:read", "run:start"]


def test_environment_token_accepts_declared_scope_metadata(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODY_TERM_TOKEN", "ody_env_secret")
    monkeypatch.setenv("ODY_TERM_SCOPES", "run:start event:read,event:raw")

    exit_code, stdout, stderr = run_cli(["auth", "capabilities", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert data["auth_facts"]["token_scopes"] == ["event:raw", "event:read", "run:start"]
    assert data["capabilities"]["run:start"]["allowed"] is True
    assert data["capabilities"]["event:read"]["allowed"] is True
    assert "ody_env_secret" not in stdout


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


def test_auth_capabilities_uses_stored_token_scope_metadata(isolated_term_state: None) -> None:
    run_cli(["auth", "login", "--token", "ody_test_secret", "--format=json"])
    secrets = ody_term._load_secrets()
    tokens = secrets["tokens"]
    assert isinstance(tokens, dict)
    entry = tokens["file:default"]
    assert isinstance(entry, dict)
    entry["scopes"] = ["event:read", "run:read", "run:start"]
    ody_term._save_secrets(secrets)

    exit_code, stdout, stderr = run_cli(["auth", "capabilities", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert data["auth_facts"]["token_scopes"] == ["event:read", "run:read", "run:start"]
    assert data["capabilities"]["run:start"]["allowed"] is True
    assert data["capabilities"]["event:read"]["allowed"] is True
    assert data["capabilities"]["run:stop"]["allowed"] is False


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


def test_yolo_satisfies_ordinary_confirmation(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(["run", "start", "--kind", "agent", "--format=json"])
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(["run", "stop", run_id, "--yolo", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert data["confirmation"]["satisfied_by"] == "--yolo"
    assert data["run"]["status"] == "stopped"


def test_run_start_creates_distinct_chat_run_for_new_session(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
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
    assert terminal_api_fake["calls"][0][0:2] == ("POST", "/api/terminal/runs")


def test_session_commands_read_durable_session_api_without_conflating_run_events(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    session = {"session_id": "ses_durable", "name": "Durable chat", "message_count": 2}
    history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

    def fake_api(request, method, path, *, query=None, body=None):
        calls.append((method, path, query))
        if path == "/api/terminal/sessions":
            return {"sessions": [session]}
        if path == "/api/terminal/sessions/ses_durable/history":
            return {"session": session, "history": history, "runs": [{"run_id": "run_recent"}]}
        if path == "/api/terminal/sessions/ses_durable/export":
            return {"session": session, "format": "md", "media_type": "text/markdown", "content": "## USER\n\nhello"}
        return {"session": session, "runs": [{"run_id": "run_recent"}]}

    monkeypatch.setattr(ody_term, "_terminal_api_request", fake_api)

    list_code, list_stdout, _ = run_cli(["session", "list", "--format=json"])
    show_code, show_stdout, _ = run_cli(["session", "show", "ses_durable", "--format=json"])
    history_code, history_stdout, _ = run_cli(["session", "history", "ses_durable", "--format=json"])
    export_code, export_stdout, _ = run_cli(
        ["session", "export", "ses_durable", "--export-format", "md", "--format=json"]
    )

    assert [list_code, show_code, history_code, export_code] == [0, 0, 0, 0]
    assert json.loads(list_stdout)["data"]["sessions"] == [session]
    assert json.loads(show_stdout)["data"]["runs"] == [{"run_id": "run_recent"}]
    assert json.loads(history_stdout)["data"]["history"] == history
    assert json.loads(export_stdout)["data"]["content"] == "## USER\n\nhello"
    assert calls == [
        ("GET", "/api/terminal/sessions", None),
        ("GET", "/api/terminal/sessions/ses_durable", None),
        ("GET", "/api/terminal/sessions/ses_durable/history", None),
        ("GET", "/api/terminal/sessions/ses_durable/export", {"format": "md"}),
    ]


def test_run_start_default_chat_uses_terminal_client_api(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(["run", "start", "--message", "default chat", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    run = json.loads(stdout)["data"]["run"]
    assert run["kind"] == "chat"
    assert terminal_api_fake["calls"][0][0:2] == ("POST", "/api/terminal/runs")
    assert terminal_api_fake["calls"][0][3]["message"] == "default chat"


def test_run_start_forwards_model_without_endpoint_for_server_resolution(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(
        ["run", "start", "--message", "new chat", "--model", "gpt-5.6-terra", "--format=json"]
    )

    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["run"]["session_id"].startswith("ses_")
    body = terminal_api_fake["calls"][0][3]
    assert body["endpoint_url"] is None
    assert body["model"] == "gpt-5.6-terra"


def test_run_start_forwards_new_session_runtime_fields(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(
        [
            "run",
            "start",
            "--message",
            "new chat",
            "--endpoint-url",
            MODEL_ENDPOINT_URL,
            "--model",
            TEST_MODEL,
            "--preset-id",
            TEST_PRESET,
            "--format=json",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["run"]["session_id"].startswith("ses_")
    body = terminal_api_fake["calls"][0][3]
    assert body["endpoint_url"] == MODEL_ENDPOINT_URL
    assert body["model"] == TEST_MODEL
    assert body["preset_id"] == TEST_PRESET


def test_run_start_can_target_existing_session_and_status_lists_recent_runs(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    run_cli(["run", "start", "--kind", "chat", "--session-id", "ses_existing", "--message", "first", "--format=json"])
    exit_code, stdout, stderr = run_cli(["run", "list", "--kind", "chat", "--format=json"])

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
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
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


def test_chat_run_status_attach_stop_by_session_use_terminal_api(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    run_cli(["run", "start", "--kind", "chat", "--session-id", "ses_api_session", "--message", "hi", "--format=json"])

    exit_code, stdout, stderr = run_cli(["run", "status", "--kind", "chat", "--session-id", "ses_api_session", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["run"]["session_id"] == "ses_api_session"

    exit_code, stdout, stderr = run_cli(["run", "attach", "--kind", "chat", "--session-id", "ses_api_session", "--format=jsonl"])

    assert exit_code == 0
    assert stderr == ""
    assert [json.loads(line)["session_id"] for line in stdout.splitlines()] == ["ses_api_session"]

    exit_code, stdout, stderr = run_cli(
        ["run", "stop", "--kind", "chat", "--session-id", "ses_api_session", "--yes", "--format=json"]
    )

    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["run"]["status"] == "stopped"
    assert [call[1] for call in terminal_api_fake["calls"][-3:]] == [
        "/api/terminal/runs/by-session/ses_api_session",
        "/api/terminal/events/stream",
        "/api/terminal/runs/by-session/ses_api_session/stop",
    ]


def test_run_attach_jsonl_follows_live_event_stream(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    events = [
        {
            "schema": "ody.event.v1",
            "id": "evt_run_follow_1",
            "seq": 1,
            "run_id": "run_follow",
            "session_id": "ses_follow",
            "source": "agent",
            "kind": "message.delta",
            "level": "info",
            "payload": {"delta": "hello"},
        },
        {
            "schema": "ody.event.v1",
            "id": "evt_run_follow_2",
            "seq": 2,
            "run_id": "run_follow",
            "session_id": "ses_follow",
            "source": "agent",
            "kind": "run.status",
            "level": "info",
            "payload": {"done": True},
        },
    ]
    calls = []

    def fake_event_stream(request, path, *, query=None):
        calls.append((path, query))
        yield from events

    monkeypatch.setattr(ody_term, "_terminal_api_event_stream", fake_event_stream)

    exit_code, stdout, stderr = run_cli(
        ["run", "attach", "run_follow", "--cursor", "0", "--format=jsonl"]
    )

    assert exit_code == 0
    assert stderr == ""
    assert [json.loads(line) for line in stdout.splitlines()] == events
    assert calls == [
        (
            "/api/terminal/events/stream",
            {
                "run_id": "run_follow",
                "session_id": None,
                "cursor": 0,
                "include_raw": False,
            },
        )
    ]


def test_chat_run_stop_reports_api_not_stopped(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    def fake_terminal_api_request(request, method, path, *, query=None, body=None):
        assert method == "POST"
        assert path == "/api/terminal/runs/run_api_busy/stop"
        return {
            "stopped": False,
            "run": {
                "run_id": "run_api_busy",
                "session_id": "ses_api_busy",
                "kind": "chat",
                "status": "running",
            },
        }

    monkeypatch.setattr(ody_term, "_terminal_api_request", fake_terminal_api_request)

    exit_code, stdout, stderr = run_cli(["run", "stop", "run_api_busy", "--yes", "--format=json"])

    assert exit_code == 1
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["ok"] is False
    assert payload["message"] == "Run run_api_busy was not stopped"
    assert payload["data"]["stopped"] is False
    assert payload["data"]["run"]["status"] == "running"


@pytest.mark.parametrize(
    ("argv", "method", "path"),
    [
        (["run", "status", "run_local_only", "--format=json"], "GET", "/api/terminal/runs/run_local_only"),
        (["run", "attach", "run_local_only", "--format=json"], "GET", "/api/terminal/runs/run_local_only/events"),
        (["run", "stop", "run_local_only", "--yes", "--format=json"], "POST", "/api/terminal/runs/run_local_only/stop"),
    ],
)
def test_legacy_local_run_state_does_not_fake_success_when_api_is_unavailable(
    isolated_term_state: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    method: str,
    path: str,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    legacy_path = tmp_path / "runs.json"
    legacy_path.write_text(
        json.dumps({"runs": {"run_local_only": {"run_id": "run_local_only", "status": "running"}}, "events": {}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ODY_TERM_RUNS", str(legacy_path))
    calls: list[tuple[str, str]] = []

    def unavailable_api(request, method, path, **kwargs):
        calls.append((method, path))
        raise ody_term.CommandError("terminal_api_unavailable", "terminal-client API unavailable", exit_code=1)

    monkeypatch.setattr(ody_term, "_terminal_api_request", unavailable_api)

    exit_code, stdout, stderr = run_cli(argv)

    assert exit_code == 1
    assert stdout == ""
    assert json.loads(stderr)["error"]["code"] == "terminal_api_unavailable"
    assert calls == [(method, path)]
    assert json.loads(legacy_path.read_text())["runs"]["run_local_only"]["status"] == "running"


def test_run_attach_cursor_continues_after_last_seen_event(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(["run", "start", "--kind", "agent", "--session-id", "ses_cursor", "--format=json"])
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(["run", "attach", run_id, "--cursor", "1", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert data["cursor"] == {"after": "1", "next": "2", "count": 1}
    assert [event["seq"] for event in data["events"]] == [2]
    assert data["events"][0]["kind"] == "heartbeat"


def test_run_attach_raw_requires_raw_event_capability(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODY_TERM_TOKEN", "ody_env_secret")

    exit_code, stdout, stderr = run_cli(["run", "attach", "run_missing", "--format=raw"])

    assert exit_code == 2
    assert stdout == ""
    error = json.loads(stderr)["error"]
    assert error["code"] == "capability_denied"
    assert "event:raw" in error["message"]


def test_run_attach_raw_capture_outputs_source_native_diagnostics(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(["run", "start", "--kind", "chat", "--message", "raw me", "--format=json"])
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(["run", "attach", run_id, "--format=raw"])

    assert exit_code == 0
    assert stderr == ""
    raw_events = json.loads(stdout)
    assert raw_events == [
        {
            "body": {"message": "raw me", "status": "running"},
            "transport": "sse",
            "type": "status",
        }
    ]


def test_agent_runs_use_same_run_lifecycle_and_heartbeat_events(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
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
    exit_code, stdout, stderr = run_cli(["run", "list", "--kind", "agent", "--format=json"])
    assert exit_code == 0
    assert stderr == ""
    assert [item["run_id"] for item in json.loads(stdout)["data"]["runs"]] == [run_id]

    exit_code, stdout, stderr = run_cli(["run", "status", run_id, "--format=json"])
    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["run"]["kind"] == "agent"

    exit_code, stdout, stderr = run_cli(["run", "attach", run_id, "--format=jsonl"])

    assert exit_code == 0
    assert stderr == ""
    events = [json.loads(line) for line in stdout.splitlines()]
    assert [event["source"] for event in events] == ["agent", "agent"]
    assert [event["kind"] for event in events] == ["run.status", "heartbeat"]
    assert events[0]["session_id"] == run["session_id"]
    assert events[0]["run_id"] == run_id
    assert events[1]["payload"] == {"activity": "started", "status": "running"}
    assert terminal_api_fake["calls"][0][0:2] == ("POST", "/api/terminal/runs")

    exit_code, stdout, stderr = run_cli(["run", "stop", run_id, "--yes", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["run"]["status"] == "stopped"


def test_harness_linked_runs_include_odysseus_and_harness_identities(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
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
            "--harness-mode",
            "observe",
            "--workspace",
            "/tmp/workspace",
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
    assert terminal_api_fake["calls"][0][0:2] == ("POST", "/api/terminal/runs")
    assert terminal_api_fake["calls"][0][3]["workspace"] == "/tmp/workspace"
    assert terminal_api_fake["calls"][0][3]["harness_mode"] == "observe"


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


def test_harness_status_lists_only_api_backed_adapter_runs(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, pi_stdout, _ = run_cli(
        ["run", "start", "--kind", "harness", "--harness-adapter", "pi", "--format=json"]
    )
    pi_run_id = json.loads(pi_stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(["harness", "status", "pi", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    assert [run["run_id"] for run in json.loads(stdout)["data"]["runs"]] == [pi_run_id]
    assert terminal_api_fake["calls"][-1][0:3] == ("GET", "/api/terminal/runs", {"kind": "harness"})


def test_harness_stop_refuses_non_harness_runs_even_with_adapter_flag(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
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
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    run_cli(["run", "start", "--kind", "agent", "--session-id", "ses_ambiguous", "--message", "one", "--format=json"])
    run_cli(["run", "start", "--kind", "agent", "--session-id", "ses_ambiguous", "--message", "two", "--format=json"])

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
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(["run", "start", "--kind", "agent", "--session-id", "ses_stop", "--message", "stop me", "--format=json"])
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
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(["run", "start", "--kind", "agent", "--message", "confirm stop", "--format=json"])
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
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
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


def test_service_logs_zero_lines_returns_no_run_events(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(
        ["run", "start", "--kind", "harness", "--harness-adapter", "pi", "--format=json"]
    )
    run_id = json.loads(stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(
        ["service", "logs", f"run:{run_id}", "--lines", "0", "--format=json"]
    )

    assert exit_code == 0
    assert stderr == ""
    assert json.loads(stdout)["data"]["events"] == []


def test_service_stop_targets_run_lifecycle_without_host_process_mutation(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, stdout, _ = run_cli(
        ["run", "start", "--kind", "harness", "--harness-adapter", "pi", "--format=json"]
    )
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


def test_inspect_events_queries_real_terminal_run_events(
    isolated_term_state: None,
    terminal_api_fake,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    started_code, started_stdout, _ = run_cli(
        ["--target", "http://ody.test", "run", "start", "--kind", "chat", "--message", "hello", "--format=json"]
    )
    assert started_code == 0
    run_id = json.loads(started_stdout)["data"]["run"]["run_id"]
    terminal_api_fake["state"]["events"][run_id].append(
        {
            "schema": "ody.event.v1",
            "id": f"evt_{run_id}_2",
            "seq": 2,
            "time": "2026-07-09T00:00:01+00:00",
            "session_id": "ses_api_1",
            "run_id": run_id,
            "source": "chat",
            "kind": "message.delta",
            "level": "info",
            "summary": "next",
            "payload": {"text": "next"},
            "raw": {"transport": "sse", "type": "message", "body": "data: next\n\n"},
        }
    )

    exit_code, stdout, stderr = run_cli(
        [
            "--target",
            "http://ody.test",
            "inspect",
            "events",
            "--run-id",
            run_id,
            "--source",
            "chat",
            "--kind",
            "run.status",
            "--level",
            "info",
            "--cursor",
            "0",
            "--lines",
            "1",
            "--format=json",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert data["source"] == "terminal-api"


    assert data["cursor"] == {"after": "0", "next": "1", "count": 1}
    assert [event["run_id"] for event in data["events"]] == [run_id]
    assert terminal_api_fake["calls"][-1] == (
        "GET",
        "/api/terminal/events",
        {
            "run_id": run_id,
            "session_id": None,
            "cursor": 0,
            "source": "chat",
            "kind": "run.status",
            "level": "info",
            "limit": 1,
            "include_raw": False,
        },
        None,
    )

    reconnect_code, reconnect_stdout, reconnect_stderr = run_cli(
        [
            "--target",
            "http://ody.test",
            "inspect",
            "events",
            "--run-id",
            run_id,
            "--cursor",
            data["cursor"]["next"],
            "--lines",
            "1",
            "--format=jsonl",
        ]
    )
    assert reconnect_code == 0
    assert reconnect_stderr == ""
    assert [(event["seq"], event["payload"]) for event in map(json.loads, reconnect_stdout.splitlines())] == [
        (2, {"text": "next"})
    ]


@pytest.mark.parametrize(
    ("source", "kind"),
    [("service", "lifecycle.status"), ("process", "process.status"), ("system", "system.status")],
)
def test_inspect_events_exposes_managed_runtime_snapshot_sources(
    isolated_term_state: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_api_fake,
    source: str,
    kind: str,
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
                "started_at": "2026-07-11T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ody_term, "_process_alive", lambda pid: True)
    monkeypatch.setattr(ody_term, "_process_group_matches", lambda pid, pgid: True)

    exit_code, stdout, stderr = run_cli(["inspect", "events", "--source", source, "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert data["source"] == "managed-runtime-snapshot"
    assert data["events"]
    assert all(event["schema"] == "ody.event.v1" for event in data["events"])
    assert all(event["source"] == source for event in data["events"])
    assert all(event["kind"] == kind for event in data["events"])
    assert data["cursor"]["count"] == len(data["events"])
    assert data["cursor"]["mode"] == "snapshot"
    assert data["cursor"]["next"] is None


def test_managed_runtime_snapshots_reject_stream_cursor_semantics(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")

    exit_code, stdout, stderr = run_cli(
        ["inspect", "events", "--source", "service", "--cursor", "1", "--format=json"]
    )

    assert exit_code == 1
    assert stdout == ""
    assert json.loads(stderr)["error"]["code"] == "snapshot_cursor_unsupported"


def test_inspect_events_by_session_streams_real_events_as_jsonl(
    isolated_term_state: None,
    terminal_api_fake,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, started_stdout, _ = run_cli(
        [
            "--target",
            "http://ody.test",
            "run",
            "start",
            "--kind",
            "chat",
            "--session-id",
            "ses_api_real",
            "--message",
            "hello",
            "--format=json",
        ]
    )
    run_id = json.loads(started_stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(
        [
            "--target",
            "http://ody.test",
            "inspect",
            "events",
            "--session-id",
            "ses_api_real",
            "--cursor",
            "0",
            "--format=jsonl",
        ]
    )

    assert exit_code == 0
    assert stderr == ""
    events = [json.loads(line) for line in stdout.splitlines()]
    assert [(event["run_id"], event["seq"]) for event in events] == [(run_id, 1)]


def test_inspect_events_jsonl_writes_live_api_events_incrementally(
    isolated_term_state: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    stdout = TtyStringIO(is_tty=False)
    stderr = TtyStringIO(is_tty=False)
    first = {
        "schema": "ody.event.v1",
        "id": "evt_run_live_1",
        "seq": 1,
        "time": "2026-07-10T00:00:00+00:00",
        "session_id": "ses_live",
        "run_id": "run_live",
        "source": "chat",
        "kind": "message.delta",
        "level": "info",
        "payload": {"delta": "one"},
    }
    second = {**first, "id": "evt_run_live_2", "seq": 2, "payload": {"delta": "two"}}
    calls = []

    def fake_event_stream(request, path, *, query=None):
        calls.append((path, query))
        yield first
        assert [json.loads(line) for line in stdout.getvalue().splitlines()] == [first]
        yield second

    monkeypatch.setattr(ody_term, "_terminal_api_event_stream", fake_event_stream, raising=False)

    exit_code = ody_term.main(
        [
            "--target",
            "http://ody.test",
            "inspect",
            "events",
            "--run-id",
            "run_live",
            "--cursor",
            "0",
            "--lines",
            "4",
            "--format=jsonl",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert [json.loads(line) for line in stdout.getvalue().splitlines()] == [first, second]
    assert calls == [
        (
            "/api/terminal/events/stream",
            {
                "run_id": "run_live",
                "session_id": None,
                "cursor": 0,
                "source": None,
                "kind": None,
                "level": None,
                "batch_limit": 4,
                "include_raw": False,
            },
        )
    ]


def test_inspect_real_events_debug_preserves_source_native_details(
    isolated_term_state: None,
    terminal_api_fake,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _, started_stdout, _ = run_cli(
        ["--target", "http://ody.test", "run", "start", "--message", "hello", "--format=json"]
    )
    run_id = json.loads(started_stdout)["data"]["run"]["run_id"]

    exit_code, stdout, stderr = run_cli(
        ["--target", "http://ody.test", "inspect", "events", "--run-id", run_id, "--format=debug"]
    )

    assert exit_code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["data"]["source"] == "terminal-api"
    assert payload["data"]["events"][0]["raw"] == {
        "transport": "sse",
        "type": "status",
        "body": {"status": "running", "message": "hello"},
    }
    assert payload["renderer"]["raw_included"] is True


def test_inspect_events_cursor_continues_after_last_seen_log_event(
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    log_path = tmp_path / "server.log"
    log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "runtime.json").write_text(
        json.dumps({"kind": "ody-term-local-server", "log_path": str(log_path)}),
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(["inspect", "events", "--cursor", "1", "--format=json"])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)["data"]
    assert data["cursor"] == {"after": "1", "next": "3", "count": 2}
    assert [event["seq"] for event in data["events"]] == [2, 3]
    assert [event["payload"]["message"] for event in data["events"]] == ["two", "three"]


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


def test_inspect_server_events_filters_by_identity_and_correlation_fields(
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
        [
            "inspect",
            "events",
            "--source",
            "server",
            "--run-id",
            "run_missing",
            "--span-id",
            "span_missing",
            "--format=json",
        ]
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
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
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
    assert {event["source"] for event in live["timeline"]} == {"harness", "server", "service", "process", "system"}
    assert any(event["run_id"] == run_id for event in live["timeline"] if event["source"] == "harness")


def test_tui_repl_and_interaction_paths_are_harnessed(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
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
    isolated_term_state: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
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
    assert inspect["model"]["events"] == len(model["views"]["Live"]["timeline"])
    assert inspect["target"]["value"]["source"] == "runtime-state"
    assert inspect["capabilities"]["auth_facts"]["auth_mode"] == "auth-disabled"
    assert inspect["event_envelope_sample"]["schema"] == "ody.event.v1"
    assert inspect["shared_state_sources"] == [
        "terminal-client-api",
        "event-envelopes",
        "lifecycle-targets",
        "terminal-capabilities",
        "target-resolution",
    ]


def test_tui_text_fallback_has_focused_live_repl_browse_and_inspect_views(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    run_cli(
        [
            "run",
            "start",
            "--kind",
            "harness",
            "--harness-adapter",
            "pi",
            "--session-id",
            "ses_screen",
            "--message",
            "render",
            "--format=json",
        ]
    )

    exit_code, stdout, stderr = run_cli(["--output", "human", "tui"], is_tty=False)

    assert exit_code == 0
    assert stderr == ""
    assert "ody-term tui" in stdout
    assert "[Live] | REPL | Browse | Inspect" in stdout
    assert "harness.heartbeat" in stdout
    assert "commands: status, tail, filter, stop, harness, service" in stdout
    assert "Session ses_screen" in stdout
    assert "sessions=1 runs=1 events=" in stdout
    assert "keyboard: F1-F4, 1-4, Tab; mouse: tabs, event rows, tree nodes, controls" in stdout


def test_tui_human_tty_opens_full_screen_renderer(
    isolated_term_state: None, monkeypatch: pytest.MonkeyPatch, terminal_api_fake
) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    opened: list[tuple[dict[str, object], object]] = []
    monkeypatch.setattr(ody_term, "run_interactive_tui", lambda model, attempt: opened.append((model, attempt)))

    exit_code, stdout, stderr = run_cli(["tui"], is_tty=True)

    assert exit_code == 0
    assert stdout == ""
    assert stderr == ""
    model, attempt = opened[0]
    assert model["schema"] == "ody.tui.v1"
    assert model["views"].keys() == {"Live", "REPL", "Browse", "Inspect"}
    assert callable(attempt)
