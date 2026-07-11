"""Interactive renderer for the ody-term shared TUI model."""

from __future__ import annotations

import json
from typing import Callable, cast

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, Static, Tree

VIEWS = ("Live", "REPL", "Browse", "Inspect")


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _sequence(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


class TerminalClientApp(App[None]):
    """Full-screen Textual view over an immutable ``ody.tui.v1`` snapshot."""

    CSS = """
    Screen { layout: vertical; }
    #view-bar { height: 3; padding: 0 1; }
    #view-bar Button { width: 1fr; min-width: 12; }
    #workspace { height: 1fr; }
    .pane { width: 1fr; border: solid $primary; padding: 1; overflow: auto; }
    .hidden { display: none; }
    #control-bar { height: 3; }
    #control-bar Button { width: 1fr; }
    #control-log { height: 6; border: solid $accent; padding: 1; overflow: auto; }
    #repl-history { height: 1fr; }
    #repl-input { dock: bottom; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("f1,1", "show_view('Live')", "Live"),
        Binding("f2,2", "show_view('REPL')", "REPL"),
        Binding("f3,3", "show_view('Browse')", "Browse"),
        Binding("f4,4", "show_view('Inspect')", "Inspect"),
        Binding("s", "control('stop')", "Stop"),
        Binding("h", "control('harness')", "Harness"),
        Binding("r", "control('service')", "Service"),
    ]

    def __init__(self, model: dict[str, object], attempt: Callable[[str], dict[str, object]]) -> None:
        super().__init__()
        if model.get("schema") != "ody.tui.v1":
            raise ValueError("TerminalClientApp requires an ody.tui.v1 model")
        self.model = model
        self.attempt = attempt
        requested_view = str(model.get("active_view") or "Live")
        self.active_view = requested_view if requested_view in VIEWS else "Live"

    def compose(self) -> ComposeResult:
        views = _mapping(self.model.get("views"))
        live = _mapping(views.get("Live"))
        repl = _mapping(views.get("REPL"))
        browse = _mapping(views.get("Browse"))
        inspect = _mapping(views.get("Inspect"))
        selected = live.get("selected_event")
        control_log = _sequence(live.get("control_log"))

        yield Header(show_clock=True)
        with Horizontal(id="view-bar"):
            for view in VIEWS:
                yield Button(view, id=f"view-{view.lower()}", variant="primary" if view == self.active_view else "default")
        with Vertical(id="workspace"):
            with Horizontal(id="panel-live"):
                yield DataTable(id="event-table", classes="pane")
                yield Static(_json(selected or live.get("selected_event")), id="event-detail", classes="pane")
            with Horizontal(id="panel-repl", classes="hidden"):
                with Vertical(classes="pane"):
                    yield Static(_json(repl.get("history", [])), id="repl-history")
                    yield Input(placeholder="status | tail | filter | stop | harness | service", id="repl-input")
            with Horizontal(id="panel-browse", classes="hidden"):
                yield Tree("Odysseus", id="browse-tree", classes="pane")
                yield Static(_json(browse.get("lifecycle_targets", [])), id="browse-detail", classes="pane")
            with Horizontal(id="panel-inspect", classes="hidden"):
                yield Static(_json(inspect), id="inspect-detail", classes="pane")
            with Horizontal(id="control-bar"):
                yield Button("Stop Run", id="control-stop", variant="warning")
                yield Button("Harness", id="control-harness")
                yield Button("Service", id="control-service")
            yield Static("\n".join(str(line) for line in control_log), id="control-log")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#event-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("seq", "level", "source", "kind", "summary")
        live = _mapping(_mapping(self.model.get("views")).get("Live"))
        self._load_events([_mapping(event) for event in _sequence(live.get("timeline"))])
        browse = _mapping(_mapping(self.model.get("views")).get("Browse"))
        tree = self.query_one("#browse-tree", Tree)
        for raw_node in _sequence(browse.get("tree")):
            self._add_tree_node(tree.root, _mapping(raw_node))
        tree.root.expand()
        self._apply_view(self.active_view)

    def _add_tree_node(self, parent: object, node: dict[str, object]) -> None:
        label = str(node.get("label") or node.get("id") or "unknown")
        child = parent.add(label, data=node, expand=True)  # type: ignore[attr-defined]
        for raw_child in _sequence(node.get("children")):
            self._add_tree_node(child, _mapping(raw_child))

    def action_show_view(self, view: str) -> None:
        self.active_view = view
        self._apply_view(view)

    def action_control(self, command: str) -> None:
        self._record_attempt(command)

    def _apply_view(self, view: str) -> None:
        for candidate in VIEWS:
            panel = self.query_one(f"#panel-{candidate.lower()}")
            panel.set_class(candidate != view, "hidden")
            button = self.query_one(f"#view-{candidate.lower()}", Button)
            button.variant = "primary" if candidate == view else "default"
        if view == "Browse":
            self.query_one("#browse-tree", Tree).focus()
        elif view == "REPL":
            self.query_one("#repl-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("view-"):
            requested = next(
                (view for view in VIEWS if view.lower() == button_id.removeprefix("view-")),
                "",
            )
            if requested in VIEWS:
                self.active_view = requested
                self._apply_view(requested)
            return
        commands = {
            "control-stop": "stop",
            "control-harness": "harness",
            "control-service": "service",
        }
        if button_id in commands:
            self._record_attempt(commands[button_id])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        if command:
            self._record_attempt(command)
        event.input.value = ""

    def _record_attempt(self, command: str) -> None:
        verb, _, expression = command.partition(" ")
        result = self.attempt(verb.lower())
        if verb.lower() == "filter" and expression and result.get("status") != "denied":
            result = {**result, "filter": self._apply_filter(expression)}
        history = self.query_one("#repl-history", Static)
        prior = history.render().plain.strip()
        history.update(f"{prior}\nody-term> {command}\n{_json(result)}".strip())
        control_log = self.query_one("#control-log", Static)
        control_prior = control_log.render().plain.strip()
        control_log.update(f"{control_prior}\n{command}: {result.get('status', 'unknown')}".strip())

    def _apply_filter(self, expression: str) -> dict[str, object]:
        field, separator, value = expression.partition("=")
        if separator != "=" or field not in {"source", "kind", "level", "run_id"} or not value:
            return {"status": "invalid", "usage": "filter source|kind|level|run_id=value"}
        live = _mapping(_mapping(self.model.get("views")).get("Live"))
        events = [
            _mapping(event)
            for event in _sequence(live.get("timeline"))
            if str(_mapping(event).get(field)) == value
        ]
        self._load_events(events)
        return {"status": "applied", "field": field, "value": value, "event_count": len(events)}

    def _load_events(self, events: list[dict[str, object]]) -> None:
        table = self.query_one("#event-table", DataTable)
        table.clear(columns=False)
        for event in events:
            event_id = str(event.get("id") or event.get("seq") or len(table.rows))
            table.add_row(
                str(event.get("seq") or ""),
                str(event.get("level") or ""),
                str(event.get("source") or ""),
                str(event.get("kind") or ""),
                str(event.get("summary") or ""),
                key=event_id,
            )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        selected_id = str(event.row_key.value)
        selected = next(
            (
                _mapping(raw_event)
                for raw_event in _sequence(_mapping(_mapping(self.model.get("views")).get("Live")).get("timeline"))
                if str(_mapping(raw_event).get("id") or _mapping(raw_event).get("seq")) == selected_id
            ),
            None,
        )
        if selected is not None:
            self.query_one("#event-detail", Static).update(_json(selected))

    def on_tree_node_selected(self, event: Tree.NodeSelected[dict[str, object]]) -> None:
        node = event.node.data
        if isinstance(node, dict):
            self.query_one("#browse-detail", Static).update(_json(node))


def run_tui(model: dict[str, object], attempt: Callable[[str], dict[str, object]]) -> None:
    """Run the full-screen renderer until the user exits."""

    TerminalClientApp(model, attempt).run()
