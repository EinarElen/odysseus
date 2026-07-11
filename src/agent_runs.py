"""Detached agent-run manager.

Keeps an agent/chat stream running server-side after the SSE client disconnects
(tab close, navigate away, refresh). The streaming generator is drained by a
background asyncio task into a per-session replay buffer; SSE clients SUBSCRIBE
to that buffer (replay everything so far, then live). Closing the SSE only drops
the subscriber — the drain task keeps going.

The wrapped generator already persists the assistant message to the session on
completion, so reopening the session shows the finished result even if nobody
was connected when it finished. Reconnecting mid-run replays the buffer + streams
live (pick up where it is).

Durability scope: in-memory, survives as long as the server process runs (tab
close / navigation / refresh). It does NOT survive a server restart.
"""
import asyncio
import json
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import AsyncGenerator, Callable, Dict, Optional

from core.atomic_io import atomic_write_json
from src.constants import DATA_DIR

logger = logging.getLogger(__name__)
_STORE = Path(DATA_DIR) / "agent_runs.json"


class _Run:
    __slots__ = ("buffer", "subscribers", "status", "task", "evict_task", "on_event")

    def __init__(self, *, on_event: Callable[[int, str], None] | None = None) -> None:
        self.buffer: list = []          # ordered SSE event strings (replay log)
        self.subscribers: set = set()   # one asyncio.Queue per connected client
        self.status: str = "running"    # running | done | error | stopped
        self.task: Optional[asyncio.Task] = None
        self.evict_task: Optional[asyncio.Task] = None
        self.on_event = on_event


_RUNS: Dict[str, _Run] = {}
_EXTERNAL_RUNS: set[str] = set()
_LIFECYCLE_LOCK = threading.RLock()
_DRAINING = False
_SIGNAL_HANDLERS: dict[int, object] = {}
_SIGNAL_RELAY_STARTED = False


class RunDrainingError(RuntimeError):
    """Raised when a new run is submitted while shutdown is draining."""


def begin_drain() -> int:
    """Atomically stop accepting runs and return the active-run count.

    The restart helper calls this before waiting.  Sharing the same lock with
    start() closes the otherwise unavoidable race between observing zero runs
    and a request registering a new one.
    """
    global _DRAINING
    with _LIFECYCLE_LOCK:
        _DRAINING = True
        return active_run_count()


def cancel_drain() -> None:
    """Resume accepting runs when a scheduled restart could not proceed."""
    global _DRAINING
    with _LIFECYCLE_LOCK:
        _DRAINING = False


def is_draining() -> bool:
    with _LIFECYCLE_LOCK:
        return _DRAINING


def active_run_count() -> int:
    with _LIFECYCLE_LOCK:
        return sum(1 for run in _RUNS.values() if run.status == "running") + len(_EXTERNAL_RUNS)


def register_external_run(run_id: str) -> None:
    """Include a directly streamed run (such as Compare) in drain accounting."""
    with _LIFECYCLE_LOCK:
        if _DRAINING:
            raise RunDrainingError("Server restart is waiting for active runs to finish")
        _EXTERNAL_RUNS.add(run_id)


def unregister_external_run(run_id: str) -> None:
    with _LIFECYCLE_LOCK:
        _EXTERNAL_RUNS.discard(run_id)


def install_graceful_signal_drain(*, poll_interval_s: float = 0.1) -> bool:
    """Delay normal process termination until active AI runs are terminal.

    Uvicorn installs its own SIGTERM/SIGINT handlers before application
    startup.  We wrap (rather than replace) those handlers.  On the first
    signal, admission closes and a daemon thread waits for active runs.  It
    then sends the same signal again; the wrapper sees an empty registry and
    delegates to uvicorn's original handler on the main thread.
    """
    global _SIGNAL_RELAY_STARTED
    if threading.current_thread() is not threading.main_thread():
        return False
    installed = False
    for signum in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if signum is None or signum in _SIGNAL_HANDLERS:
            continue
        previous = signal.getsignal(signum)

        def _handle(received: int, frame, *, _previous=previous) -> None:
            global _SIGNAL_RELAY_STARTED
            # Close admission before observing the count. Otherwise a run can
            # register between a zero observation and delegation to uvicorn.
            active = begin_drain()
            if active == 0:
                if callable(_previous):
                    _previous(received, frame)
                elif _previous == signal.SIG_DFL:
                    signal.signal(received, signal.SIG_DFL)
                    os.kill(os.getpid(), received)
                return
            with _LIFECYCLE_LOCK:
                if _SIGNAL_RELAY_STARTED:
                    return
                _SIGNAL_RELAY_STARTED = True

            def _relay() -> None:
                global _SIGNAL_RELAY_STARTED
                while active_run_count() > 0:
                    time.sleep(max(0.01, poll_interval_s))
                with _LIFECYCLE_LOCK:
                    _SIGNAL_RELAY_STARTED = False
                os.kill(os.getpid(), received)

            threading.Thread(target=_relay, name="odysseus-run-drain", daemon=True).start()

        _SIGNAL_HANDLERS[signum] = previous
        signal.signal(signum, _handle)
        installed = True
    return installed


def restore_signal_handlers() -> None:
    """Restore wrapped handlers, primarily for embedded servers and tests."""
    global _SIGNAL_RELAY_STARTED
    if threading.current_thread() is not threading.main_thread():
        return
    for signum, previous in list(_SIGNAL_HANDLERS.items()):
        signal.signal(signum, previous)
    _SIGNAL_HANDLERS.clear()
    _SIGNAL_RELAY_STARTED = False

# How long a FINISHED run (and its full replay buffer) is retained after the
# last subscriber disconnects, so a reconnect within the window can still
# replay the result. After this, the run is evicted to bound memory — without
# it, every session that ever streamed kept its entire event log forever.
_EVICT_GRACE_S = 180


def _load_state() -> Dict[str, dict]:
    try:
        if _STORE.exists():
            data = json.loads(_STORE.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        logger.debug("[agent-run] state load failed", exc_info=True)
    return {}


def _save_state(state: Dict[str, dict]) -> None:
    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(str(_STORE), state, indent=2)
    except Exception:
        logger.debug("[agent-run] state save failed", exc_info=True)


def _set_persisted_status(session_id: str, status: str, **extra) -> None:
    state = _load_state()
    rec = state.get(session_id, {})
    rec.update({"session_id": session_id, "status": status, **extra})
    if status == "running" and "started_at" not in rec:
        rec["started_at"] = time.time()
    if status != "running":
        rec["finished_at"] = time.time()
    state[session_id] = rec
    _save_state(state)


def get_persisted_status(session_id: str) -> Optional[str]:
    rec = _load_state().get(session_id)
    return str(rec.get("status")) if rec else None


def recover_stale_runs(session_manager) -> int:
    """Mark persisted running runs interrupted after a server restart."""
    state = _load_state()
    changed = False
    recovered = 0
    for session_id, rec in list(state.items()):
        if rec.get("status") != "running":
            continue
        rec["status"] = "interrupted"
        rec["finished_at"] = time.time()
        rec["recovered_at"] = time.time()
        rec["reason"] = "Server restarted before this response completed."
        changed = True
        try:
            from core.models import ChatMessage
            session_manager.add_message(
                session_id,
                ChatMessage(
                    "assistant",
                    "[Interrupted: server restarted before this response completed.]",
                    metadata={"interrupted": True, "reason": rec["reason"]},
                ),
            )
            recovered += 1
        except Exception:
            logger.debug("[agent-run] stale run recovery skipped for %s", session_id, exc_info=True)
    if changed:
        _save_state(state)
    return recovered


def _publish(run: _Run, ev: str) -> None:
    """Append one SSE event and fan it out to every live subscriber."""
    run.buffer.append(ev)
    seq = len(run.buffer) - 1
    if run.on_event is not None:
        try:
            run.on_event(seq + 1, ev)
        except Exception:
            logger.debug("[agent-run] event persistence callback failed", exc_info=True)
    for q in list(run.subscribers):
        try:
            q.put_nowait((seq, ev))
        except Exception:
            pass


def _schedule_evict(session_id: str) -> None:
    """(Re)arm a grace-period eviction for a terminal run with no subscribers.
    Identity-checked so a run that gets replaced/reused is never evicted by a
    stale timer."""
    run = _RUNS.get(session_id)
    if run is None:
        return
    if run.evict_task and not run.evict_task.done():
        run.evict_task.cancel()

    async def _evict(run_ref: _Run) -> None:
        try:
            await asyncio.sleep(_EVICT_GRACE_S)
        except asyncio.CancelledError:
            return
        cur = _RUNS.get(session_id)
        if cur is run_ref and cur.status != "running" and not cur.subscribers:
            _RUNS.pop(session_id, None)

    run.evict_task = asyncio.create_task(_evict(run))


def is_active(session_id: str) -> bool:
    r = _RUNS.get(session_id)
    return bool(r and r.status == "running")


def get_status(session_id: str) -> Optional[str]:
    r = _RUNS.get(session_id)
    return r.status if r else None


def buffered_event_count(session_id: str) -> int:
    """Return the current replay-buffer size for a detached run."""
    run = _RUNS.get(session_id)
    return len(run.buffer) if run else 0


def reset_for_tests() -> None:
    global _DRAINING
    with _LIFECYCLE_LOCK:
        _RUNS.clear()
        _EXTERNAL_RUNS.clear()
        _DRAINING = False


async def _drain(session_id: str, agen: AsyncGenerator[str, None],
                 prev_task: Optional[asyncio.Task] = None) -> None:
    """Pull every event from the wrapped generator into the run buffer, fanning
    each out to live subscribers. Runs to completion regardless of subscribers."""
    run = _RUNS.get(session_id)
    if run is None:
        return
    # If this run replaced an in-flight one (rapid double-send), wait for that
    # one to fully finish first. Its CancelledError handler calls aclose(), which
    # persists its partial response — letting it complete before we start writing
    # keeps the two runs' session saves sequential instead of interleaved.
    if prev_task is not None and not prev_task.done():
        try:
            await asyncio.wait({prev_task})
        except asyncio.CancelledError:
            raise            # our own cancellation — propagate
        except Exception:
            pass
    try:
        async for ev in agen:
            _publish(run, ev)
        if run.status == "running":
            run.status = "done"
            _set_persisted_status(session_id, "done")
    except asyncio.CancelledError:
        run.status = "stopped"
        _set_persisted_status(session_id, "stopped")
        # Let the wrapped generator's own CancelledError handler run (it saves
        # the partial response to the session).
        try:
            await agen.aclose()
        except Exception:
            pass
    except Exception as e:
        logger.error("[agent-run] %s failed: %s", session_id, e, exc_info=True)
        run.status = "error"
        _set_persisted_status(session_id, "error", error=str(e)[:1000])
        _publish(
            run,
            "event: error\n"
            f"data: {json.dumps({'error': 'Agent run failed before completion.', 'status': 500})}\n\n",
        )
        _publish(run, "data: [DONE]\n\n")
    finally:
        # Wake every subscriber with the end sentinel so their SSE closes.
        for q in list(run.subscribers):
            try:
                q.put_nowait((None, None))
            except Exception:
                pass
        # Run is terminal — arm the grace timer so it (and its buffer) is
        # eventually freed even if nobody ever reconnects. subscribe() cancels
        # this on connect and re-arms on disconnect.
        _schedule_evict(session_id)


def start(
    session_id: str,
    agen: AsyncGenerator[str, None],
    *,
    on_event: Callable[[int, str], None] | None = None,
) -> _Run:
    """Start a detached run draining `agen` for a session. If a run is already in
    flight for this session (e.g. a rapid double-send), it's cancelled first."""
    with _LIFECYCLE_LOCK:
        if _DRAINING:
            raise RunDrainingError("Server restart is waiting for active runs to finish")
        prev = _RUNS.get(session_id)
        prev_task: Optional[asyncio.Task] = None
        if prev:
            if prev.task and not prev.task.done():
                prev.task.cancel()
                prev_task = prev.task   # new run awaits this before it starts writing
            if prev.evict_task and not prev.evict_task.done():
                prev.evict_task.cancel()
        run = _Run(on_event=on_event)
        _RUNS[session_id] = run
        _set_persisted_status(session_id, "running", started_at=time.time())
        run.task = asyncio.create_task(_drain(session_id, agen, prev_task))
        return run


async def subscribe(session_id: str) -> AsyncGenerator[str, None]:
    """Replay the run's buffer from the start, then stream live until it ends.
    Safe to call repeatedly (reconnect) and from multiple clients at once."""
    run = _RUNS.get(session_id)
    if run is None:
        return
    q: asyncio.Queue = asyncio.Queue()
    run.subscribers.add(q)            # register BEFORE replaying so nothing is missed
    # A live subscriber is connected — don't let a pending grace timer evict
    # the run out from under it mid-replay.
    if run.evict_task and not run.evict_task.done():
        run.evict_task.cancel()
    try:
        next_seq = 0
        while next_seq < len(run.buffer):
            yield run.buffer[next_seq]
            next_seq += 1
        if run.status != "running":
            return
        heartbeat_idx = 0
        while True:
            try:
                seq, ev = await asyncio.wait_for(q.get(), timeout=10.0)
            except asyncio.TimeoutError:
                # Keep slow local models/proxies alive while they prefill before
                # the first token. SSE comments are ignored by the UI but reset
                # browser/proxy idle timers, which prevents "empty response"
                # disconnects on llama.cpp first-token latencies of 30s+.
                if run.status == "running":
                    heartbeat_idx += 1
                    yield f": heartbeat {heartbeat_idx}\n\n"
                    continue
                seq, ev = (None, None)
            if seq is None:            # end sentinel
                while next_seq < len(run.buffer):   # flush any tail the sentinel raced
                    yield run.buffer[next_seq]
                    next_seq += 1
                break
            if seq >= next_seq:        # skip events already replayed from the buffer
                yield ev
                next_seq = seq + 1
    finally:
        run.subscribers.discard(q)
        # Last subscriber gone on a finished run — (re)arm eviction so the
        # buffer doesn't linger indefinitely.
        if not run.subscribers and run.status != "running":
            _schedule_evict(session_id)


def stop(session_id: str) -> bool:
    """Cancel an in-flight run (the wrapped generator saves its partial)."""
    run = _RUNS.get(session_id)
    if run and run.task and not run.task.done():
        run.task.cancel()
        return True
    return False
