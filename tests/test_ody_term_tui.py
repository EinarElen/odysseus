from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ody_term_tui import TerminalClientApp  # noqa: E402
from textual.widgets import DataTable, Tree  # noqa: E402


def tui_model() -> dict[str, object]:
    event = {
        "schema": "ody.event.v1",
        "id": "evt_1",
        "seq": 1,
        "time": "2026-07-11T12:00:00Z",
        "session_id": "ses_live",
        "run_id": "run_live",
        "source": "harness",
        "kind": "message.delta",
        "level": "info",
        "summary": "Harness replied",
        "payload": {"delta": "ready"},
    }
    second_event = {
        **event,
        "id": "evt_2",
        "seq": 2,
        "kind": "run.status",
        "summary": "Harness finished",
        "payload": {"status": "done"},
    }
    return {
        "schema": "ody.tui.v1",
        "active_view": "Live",
        "views": {
            "Live": {
                "timeline": [event, second_event],
                "timeline_lines": [
                    "1 info harness.message.delta Harness replied",
                    "2 info harness.run.status Harness finished",
                ],
                "selected_event": event,
                "control_log": ["run run_live is running"],
            },
            "REPL": {"history": [], "commands": [{"command": "status"}]},
            "Browse": {
                "tree": [
                    {
                        "id": "ses_live",
                        "kind": "session",
                        "children": [{"id": "run_live", "kind": "run", "children": []}],
                    }
                ],
                "lifecycle_targets": [{"id": "run:run_live", "status": "running"}],
            },
            "Inspect": {"model": {"runs": 1}, "target": {"ok": True}},
        },
        "state": {"runs": [{"run_id": "run_live"}], "events": [event, second_event]},
    }


def attempt(command: str) -> dict[str, object]:
    results: dict[str, dict[str, object]] = {
        "status": {"command": "status", "status": "available", "result": {"runs": [{"run_id": "run_live"}]}},
        "filter": {"command": "filter", "status": "available", "result": {}},
        "stop": {"command": "stop", "status": "confirmation_required", "result": {"run_id": "run_live"}},
        "harness": {"command": "harness", "status": "available", "result": {}},
        "service": {"command": "service", "status": "unsupported", "result": {}},
    }
    return results[command]


@pytest.mark.asyncio
async def test_full_screen_tui_keyboard_navigation_uses_shared_model() -> None:
    app = TerminalClientApp(tui_model(), attempt)

    async with app.run_test(size=(120, 40)) as pilot:
        assert app.active_view == "Live"
        assert "Harness replied" in app.query_one("#event-detail").render().plain

        await pilot.press("f3")
        assert app.active_view == "Browse"
        tree = app.query_one("#browse-tree", Tree)
        assert "ses_live" in str(tree.root.children[0].label)
        await pilot.press("down", "enter")
        assert "ses_live" in app.query_one("#browse-detail").render().plain

        await pilot.press("f4")
        assert app.active_view == "Inspect"
        assert '"runs": 1' in app.query_one("#inspect-detail").render().plain


@pytest.mark.asyncio
async def test_full_screen_tui_mouse_selects_views_and_event_rows() -> None:
    app = TerminalClientApp(tui_model(), attempt)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#view-repl")
        assert app.active_view == "REPL"

        await pilot.click("#view-live")
        await pilot.click("#event-table", offset=(3, 4))
        assert "Harness finished" in app.query_one("#event-detail").render().plain


@pytest.mark.asyncio
async def test_full_screen_tui_repl_accepts_control_attempts() -> None:
    app = TerminalClientApp(tui_model(), attempt)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("f2")
        await pilot.click("#repl-input")
        await pilot.press("s", "t", "a", "t", "u", "s", "enter")

        assert "status" in app.query_one("#repl-history").render().plain
        assert "run_live" in app.query_one("#repl-history").render().plain

        await pilot.press("f1", "s")
        assert "stop: confirmation_required" in app.query_one("#control-log").render().plain


@pytest.mark.asyncio
async def test_full_screen_tui_repl_filters_event_table() -> None:
    app = TerminalClientApp(tui_model(), attempt)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("f2")
        await pilot.press(*"filter kind=run.status", "enter")

        assert app.query_one("#event-table", DataTable).row_count == 1
        assert '"event_count": 1' in app.query_one("#repl-history").render().plain


@pytest.mark.asyncio
async def test_full_screen_tui_mouse_selects_browse_nodes_and_controls() -> None:
    app = TerminalClientApp(tui_model(), attempt)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#control-stop")
        assert "stop" in app.query_one("#control-log").render().plain

        await pilot.click("#view-browse")
        await pilot.click("#browse-tree", offset=(4, 4))
        assert "run_live" in app.query_one("#browse-detail").render().plain
