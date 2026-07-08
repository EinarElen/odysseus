#!/usr/bin/env python3
"""PROTOTYPE: Textual live-control TUI simulator for wayfinder ticket 007."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
import json

from rich.syntax import Syntax
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Input, Label, Log, RichLog, Static, TabbedContent, TabPane, Tree


@dataclass(frozen=True)
class Event:
    seq: int
    source: str
    kind: str
    level: str
    summary: str
    payload: dict[str, object]
    raw: dict[str, object] = field(default_factory=dict)

    def envelope(self) -> dict[str, object]:
        return {
            "schema": "ody.event.v1",
            "id": f"evt_{self.seq:04d}",
            "seq": self.seq,
            "time": "2026-07-08T19:42:00.000Z",
            "session_id": "ses_214",
            "run_id": "run_018",
            "harness_session_id": self.payload.get("harness_session_id"),
            "source": self.source,
            "kind": self.kind,
            "level": self.level,
            "summary": self.summary,
            "payload": self.payload,
            "raw": self.raw,
        }


EVENTS: list[Event] = [
    Event(1, "run", "run.status", "info", "agent run started", {"status": "running", "kind": "agent"}),
    Event(2, "heartbeat", "heartbeat", "debug", "planner heartbeat", {"phase": "planning", "tokens": 1240}),
    Event(3, "chat", "message.delta", "info", "assistant drafting", {"delta": "I will inspect the route shape first."}),
    Event(4, "tool", "tool.start", "info", "search started", {"cmd": "rg 'agent_runs|harness' routes src"}),
    Event(5, "tool", "tool.output", "info", "search returned matches", {"matches": 18, "truncated": True}),
    Event(
        6,
        "harness",
        "harness.status",
        "info",
        "Pi harness attached",
        {"harness_session_id": "pi_92", "adapter": "pi", "can_steer": True},
        {"transport": "harness", "type": "attach"},
    ),
    Event(
        7,
        "service",
        "service.status",
        "warn",
        "model server slow",
        {"target": "cookbook-model-serving", "latency_ms": 1840, "can_restart": True},
    ),
    Event(
        8,
        "log",
        "log",
        "info",
        "backend emitted SSE frame",
        {"logger": "routes.chat", "line": "event: message_delta"},
        {"transport": "sse", "type": "message_delta", "body": {"delta": "..."}},
    ),
    Event(9, "error", "error", "error", "tool stream interrupted", {"recoverable": True, "retry_after_ms": 500}),
    Event(
        10,
        "control",
        "control.result",
        "info",
        "run stop requires confirmation",
        {"action": "run.stop", "requires_confirmation": True, "requires_yolo": False},
    ),
]


FILTERS = {
    "all": None,
    "run": {"run", "chat", "tool", "heartbeat", "error", "control"},
    "harness": {"harness", "control", "error"},
    "ops": {"service", "log", "error", "control"},
}


class ControlRail(Static):
    run_status = reactive("running")
    harness_status = reactive("attached")
    filter_name = reactive("all")
    queued_control = reactive("none")

    def render(self) -> str:
        return (
            "[b]Live Control[/b]\n\n"
            f"Run: [b]{self.run_status}[/b]\n"
            "Session: ses_214\n"
            f"Harness: [b]{self.harness_status}[/b]\n"
            f"Filter: [b]{self.filter_name}[/b]\n\n"
            "[b]Actions[/b]\n"
            "s  stop run\n"
            "h  queue harness command\n"
            "r  restart service\n"
            "f  cycle filter\n"
            "j/k or arrows select event\n\n"
            "[b]Last control[/b]\n"
            f"{self.queued_control}"
        )


class EventTable(DataTable[str]):
    class Selected(Message):
        def __init__(self, event: Event) -> None:
            super().__init__()
            self.event = event

    events: list[Event]

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns("seq", "level", "source", "kind", "summary")

    def load(self, events: list[Event]) -> None:
        self.events = events
        self.clear()
        for event in events:
            self.add_row(
                str(event.seq),
                event.level.upper(),
                event.source,
                event.kind,
                event.summary,
                key=str(event.seq),
            )
        if events:
            self.move_cursor(row=0)
            self.post_message(self.Selected(events[0]))

    def on_data_table_row_highlighted(self, message: DataTable.RowHighlighted) -> None:
        if message.row_key is None:
            return
        seq = int(message.row_key.value)
        event = next(event for event in self.events if event.seq == seq)
        self.post_message(self.Selected(event))


class LiveTuiPrototype(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    TabbedContent {
        height: 1fr;
    }

    #body {
        height: 1fr;
    }

    #timeline-pane {
        width: 56%;
        border: solid $primary;
    }

    #right-pane {
        width: 44%;
    }

    #detail {
        height: 2fr;
        border: solid $accent;
        padding: 1;
    }

    #control {
        height: 1fr;
        min-height: 14;
        border: solid $success;
        padding: 1;
    }

    #event-log {
        height: 8;
        border: solid $warning;
    }

    #repl-view {
        height: 1fr;
    }

    #repl-log {
        height: 1fr;
        border: solid $primary;
        padding: 1;
    }

    #repl-input {
        dock: bottom;
    }

    #inspect-view {
        height: 1fr;
    }

    #inspect-log {
        height: 1fr;
        border: solid $accent;
        padding: 1;
    }

    #browse-view {
        height: 1fr;
    }

    #chat-tree {
        width: 45%;
        border: solid $primary;
        padding: 1;
    }

    #tree-detail {
        width: 55%;
        border: solid $accent;
        padding: 1;
    }

    .pane-title {
        dock: top;
        height: 1;
        background: $panel;
        color: $text;
        padding-left: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j,down", "cursor_down", "Next"),
        Binding("k,up", "cursor_up", "Previous"),
        Binding("f", "cycle_filter", "Filter"),
        Binding("s", "stop_run", "Stop run"),
        Binding("h", "harness_command", "Harness"),
        Binding("r", "restart_service", "Restart service"),
        Binding("1", "show_tab('live')", "Live"),
        Binding("2", "show_tab('repl')", "REPL"),
        Binding("3", "show_tab('browse')", "Browse"),
        Binding("4", "show_tab('inspect')", "Inspect"),
        Binding("f1", "show_tab('live')", "Live"),
        Binding("f2", "show_tab('repl')", "REPL"),
        Binding("f3", "show_tab('browse')", "Browse"),
        Binding("f4", "show_tab('inspect')", "Inspect"),
    ]

    filter_name = reactive("all")

    def __init__(self) -> None:
        super().__init__()
        self.run_status = "running"
        self.harness_status = "attached"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="live"):
            with TabPane("Live", id="live"):
                with Horizontal(id="body"):
                    with Vertical(id="timeline-pane"):
                        yield Label("Timeline - normalized Event Envelope stream", classes="pane-title")
                        yield EventTable(id="timeline")
                        yield Log(id="event-log", highlight=True)
                    with Vertical(id="right-pane"):
                        yield RichLog(id="detail", wrap=True, markup=True, highlight=True)
                        yield ControlRail(id="control")
            with TabPane("REPL", id="repl"):
                with Vertical(id="repl-view"):
                    yield RichLog(id="repl-log", wrap=True, markup=True, highlight=True)
                    yield Input(
                        placeholder="try: status, events, filter ops, stop run, harness steer, help",
                        id="repl-input",
                    )
            with TabPane("Browse", id="browse"):
                with Horizontal(id="browse-view"):
                    yield Tree("ses_214 Current chat", id="chat-tree")
                    yield RichLog(id="tree-detail", wrap=True, markup=True, highlight=True)
            with TabPane("Inspect", id="inspect"):
                with Vertical(id="inspect-view"):
                    yield RichLog(id="inspect-log", wrap=True, markup=True, highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "ody-term tui prototype"
        self.sub_title = "throwaway live-control scope"
        self.refresh_events()
        self.write_log("Prototype started with one Session, one Run, one Harness Session, and one event stream.")
        self.write_repl("[b]ody-term repl[/b] - command-shaped control surface. Type 'help'.")
        self.populate_tree()
        self.refresh_inspect()

    def visible_events(self) -> list[Event]:
        allowed = FILTERS[self.filter_name]
        if allowed is None:
            return EVENTS
        return [event for event in EVENTS if event.source in allowed]

    def refresh_events(self) -> None:
        table = self.query_one("#timeline", EventTable)
        table.load(self.visible_events())
        control = self.query_one("#control", ControlRail)
        control.run_status = self.run_status
        control.harness_status = self.harness_status
        control.filter_name = self.filter_name
        self.refresh_inspect()

    def on_event_table_selected(self, message: EventTable.Selected) -> None:
        detail = self.query_one("#detail", RichLog)
        detail.clear()
        detail.write("[b]Selected Event Envelope[/b]")
        detail.write(Syntax(json.dumps(message.event.envelope(), indent=2), "json", word_wrap=True))

    def write_log(self, line: str) -> None:
        now = datetime.now(UTC).strftime("%H:%M:%S")
        self.query_one("#event-log", Log).write_line(f"{now} {line}")

    def write_repl(self, line: str) -> None:
        self.query_one("#repl-log", RichLog).write(line)

    def queue_control(self, label: str) -> None:
        control = self.query_one("#control", ControlRail)
        control.queued_control = label
        self.write_log(label)
        self.write_repl(f"[dim]{label}[/dim]")
        self.refresh_inspect()

    def refresh_inspect(self) -> None:
        inspect_log = self.query_one("#inspect-log", RichLog)
        inspect_log.clear()
        inspect_log.write("[b]Visible model[/b]")
        inspect_log.write(
            Syntax(
                json.dumps(
                    {
                        "session_id": "ses_214",
                        "run_id": "run_018",
                        "harness_session_id": "pi_92",
                        "run_status": self.run_status,
                        "harness_status": self.harness_status,
                        "active_filter": self.filter_name,
                        "visible_event_count": len(self.visible_events()),
                        "capabilities": {
                            "run.stop": "requires confirmation",
                            "harness.command": "allowed by adapter",
                            "service.restart": "requires service scope + confirmation",
                        },
                    },
                    indent=2,
                ),
                "json",
                word_wrap=True,
            )
        )
        inspect_log.write("[b]Envelope sample[/b]")
        inspect_log.write(Syntax(json.dumps(self.visible_events()[0].envelope(), indent=2), "json", word_wrap=True))

    def populate_tree(self) -> None:
        tree = self.query_one("#chat-tree", Tree)
        tree.show_root = True
        root = tree.root
        root.expand()
        run = root.add("run_018 agent run [running]", data={"kind": "run", "id": "run_018"})
        turn = run.add("turn 3: terminal-client design", data={"kind": "turn", "id": "turn_003"})
        turn.add("user: wants multiple tab-like views", data={"kind": "message", "role": "user"})
        assistant = turn.add("assistant: prototype response", data={"kind": "message", "role": "assistant"})
        assistant.add("tool call: rg route inventory", data={"kind": "tool", "status": "done"})
        assistant.add("tool output: 18 matches", data={"kind": "tool-output", "status": "done"})
        assistant.add("decision candidate: tabbed live-control surface", data={"kind": "decision-candidate"})
        harness = run.add("harness pi_92", data={"kind": "harness", "id": "pi_92"})
        harness.add("adapter: pi", data={"kind": "harness-adapter"})
        harness.add("command queue: empty", data={"kind": "harness-queue"})
        ops = run.add("operations", data={"kind": "ops"})
        ops.add("service warning: cookbook-model-serving slow", data={"event_seq": 7})
        ops.add("recoverable error: tool stream interrupted", data={"event_seq": 9})
        events = root.add("event envelopes", data={"kind": "event-group"})
        for event in EVENTS:
            events.add(f"{event.seq:02d} {event.source}: {event.summary}", data={"event_seq": event.seq})
        tree.cursor_line = 0
        self.write_tree_detail({"kind": "session", "id": "ses_214", "purpose": "tree-like chat navigation sketch"})

    def write_tree_detail(self, value: object) -> None:
        detail = self.query_one("#tree-detail", RichLog)
        detail.clear()
        detail.write("[b]Selected navigation node[/b]")
        detail.write(Syntax(json.dumps(value, indent=2), "json", word_wrap=True))

    def action_cursor_down(self) -> None:
        self.query_one("#timeline", EventTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#timeline", EventTable).action_cursor_up()

    def action_cycle_filter(self) -> None:
        names = list(FILTERS)
        self.filter_name = names[(names.index(self.filter_name) + 1) % len(names)]
        self.refresh_events()
        self.queue_control(f"filter switched to {self.filter_name}")

    def action_stop_run(self) -> None:
        self.run_status = "stopping"
        self.refresh_events()
        self.queue_control("run.stop requested; would require confirmation")

    def action_harness_command(self) -> None:
        self.harness_status = "command queued"
        self.refresh_events()
        self.queue_control("harness.command queued for pi_92")

    def action_restart_service(self) -> None:
        self.queue_control("service.restart cookbook-model-serving; would require scope + confirmation")

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id
        if tab_id == "repl":
            self.query_one("#repl-input", Input).focus()
        else:
            self.screen.set_focus(None)

    def on_input_submitted(self, message: Input.Submitted) -> None:
        if message.input.id != "repl-input":
            return
        command = message.value.strip()
        message.input.value = ""
        if not command:
            return

        self.write_repl(f"[b]> {command}[/b]")
        normalized = command.lower()
        if normalized in {"help", "?"}:
            self.write_repl("status | events | filter all|run|harness|ops | stop run | harness steer | restart service | clear")
        elif normalized == "status":
            self.write_repl(
                f"run_018={self.run_status} session=ses_214 harness=pi_92/{self.harness_status} filter={self.filter_name}"
            )
        elif normalized == "events":
            for event in self.visible_events()[-5:]:
                self.write_repl(f"{event.seq:02d} {event.level:<5} {event.source:<9} {event.summary}")
        elif normalized.startswith("filter "):
            requested = normalized.split(maxsplit=1)[1]
            if requested in FILTERS:
                self.filter_name = requested
                self.refresh_events()
                self.write_repl(f"filter switched to {requested}")
            else:
                self.write_repl(f"[red]unknown filter[/red] {requested}")
        elif normalized == "stop run":
            self.action_stop_run()
        elif normalized == "harness steer":
            self.action_harness_command()
        elif normalized == "restart service":
            self.action_restart_service()
        elif normalized == "clear":
            self.query_one("#repl-log", RichLog).clear()
        else:
            self.write_repl(f"[red]unknown command[/red] {command}")

    def on_tree_node_highlighted(self, message: Tree.NodeHighlighted[object]) -> None:
        data = message.node.data
        if isinstance(data, dict) and "event_seq" in data:
            event = next(event for event in EVENTS if event.seq == data["event_seq"])
            self.write_tree_detail(event.envelope())
            return
        self.write_tree_detail(data or {"label": str(message.node.label)})


if __name__ == "__main__":
    LiveTuiPrototype().run()
