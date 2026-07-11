import json
import signal

import pytest

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


@pytest.mark.asyncio
async def test_drain_atomically_rejects_new_runs():
    agent_runs.reset_for_tests()

    async def stream():
        yield "data: hello\n\n"

    assert agent_runs.begin_drain() == 0
    assert agent_runs.is_draining() is True
    rejected_stream = stream()
    with pytest.raises(agent_runs.RunDrainingError):
        agent_runs.start("sess-draining", rejected_stream)
    await rejected_stream.aclose()

    agent_runs.cancel_drain()
    run = agent_runs.start("sess-after-drain", stream())
    await run.task
    assert run.status == "done"
    agent_runs.reset_for_tests()


def test_graceful_signal_waits_for_active_runs_then_relays(monkeypatch):
    agent_runs.restore_signal_handlers()
    agent_runs.reset_for_tests()
    relayed = []
    previous_calls = []
    handlers = {}
    counts = iter((1, 0, 0))

    monkeypatch.setattr(agent_runs, "active_run_count", lambda: next(counts))
    monkeypatch.setattr(agent_runs.os, "kill", lambda pid, sig: relayed.append((pid, sig)))
    monkeypatch.setattr(agent_runs.signal, "getsignal", lambda sig: lambda received, frame: previous_calls.append(received))
    monkeypatch.setattr(agent_runs.signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler))
    monkeypatch.setattr(agent_runs.time, "sleep", lambda seconds: None)

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(agent_runs.threading, "Thread", ImmediateThread)

    assert agent_runs.install_graceful_signal_drain() is True
    handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert agent_runs.is_draining() is True
    assert relayed == [(agent_runs.os.getpid(), signal.SIGTERM)]
    assert previous_calls == []

    # The relayed signal arrives after the count reaches zero and is delegated
    # to the server's original handler.
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert previous_calls == [signal.SIGTERM]
    agent_runs.restore_signal_handlers()
    agent_runs.reset_for_tests()


def test_zero_run_signal_closes_admission_before_delegating(monkeypatch):
    agent_runs.restore_signal_handlers()
    agent_runs.reset_for_tests()
    previous_calls = []
    handlers = {}
    monkeypatch.setattr(agent_runs.signal, "getsignal", lambda sig: lambda received, frame: previous_calls.append(received))
    monkeypatch.setattr(agent_runs.signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler))

    assert agent_runs.install_graceful_signal_drain() is True
    handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert agent_runs.is_draining() is True
    assert previous_calls == [signal.SIGTERM]
    agent_runs.restore_signal_handlers()
    agent_runs.reset_for_tests()
