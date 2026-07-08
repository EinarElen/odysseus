from src.harness.transcript import (
    finish_harness_run,
    record_harness_control,
    record_harness_status,
    record_harness_tool_end,
    record_harness_tool_start,
    record_harness_tool_update,
    start_harness_run,
    update_harness_ref,
)


def test_harness_run_records_nested_activity_without_agent_tool_events():
    run = start_harness_run(
        harness_id="pi",
        label="Pi harness",
        mode="bridged",
        workspace="/tmp/work",
    )

    update_harness_ref(run, session_id="pi-session")
    record_harness_status(run, {"phase": "session_ready", "label": "Pi ready", "status": "done"})
    record_harness_tool_start(run, {"id": "t1", "name": "read_file", "input": {"path": "app.py"}})
    record_harness_tool_update(run, {"id": "t1", "partial": {"tail": "reading"}})
    record_harness_tool_end(run, {"id": "t1", "name": "read_file", "result": "ok"})
    record_harness_control(run, {"id": "c1", "kind": "yield_control", "blocking": True})
    finish_harness_run(run, status="completed", duration_seconds=1.25)

    assert run["status"] == "completed"
    assert run["session_id"] == "pi-session"
    assert run["summary"] == "1 actions"
    assert [event["type"] for event in run["events"]] == ["status", "tool", "control"]
    assert run["events"][1]["status"] == "completed"
    assert run["events"][1]["input"] == '{"path": "app.py"}'
    assert run["events"][1]["output"] == "ok"
    assert "progress" not in run["events"][1]


def test_harness_run_summary_counts_failed_actions():
    run = start_harness_run(harness_id="pi", label="Pi harness", mode="bridged")

    record_harness_tool_start(run, {"id": "t1", "name": "edit_file"})
    record_harness_tool_end(run, {"id": "t1", "name": "edit_file", "is_error": True, "result": "bad"})
    finish_harness_run(run, status="completed", duration_seconds=2)

    assert run["summary"] == "1 actions, 1 failed"
    assert run["events"][0]["status"] == "failed"
