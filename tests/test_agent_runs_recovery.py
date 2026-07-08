import json

from src import agent_runs


class _SessionManager:
    def __init__(self):
        self.messages = []

    def add_message(self, session_id, message):
        self.messages.append((session_id, message))


def test_recover_stale_runs_marks_interrupted_and_adds_session_marker(tmp_path, monkeypatch):
    store = tmp_path / "agent_runs.json"
    store.write_text(json.dumps({"sess-1": {"session_id": "sess-1", "status": "running"}}), encoding="utf-8")
    monkeypatch.setattr(agent_runs, "_STORE", store)
    manager = _SessionManager()

    recovered = agent_runs.recover_stale_runs(manager)
    state = json.loads(store.read_text(encoding="utf-8"))

    assert recovered == 1
    assert state["sess-1"]["status"] == "interrupted"
    assert manager.messages[0][0] == "sess-1"
    assert manager.messages[0][1].metadata["interrupted"] is True
