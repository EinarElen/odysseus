from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_harness_startup_timeout_is_inactivity_based():
    source = (ROOT / "routes/chat_routes.py").read_text(encoding="utf-8")

    assert "_startup_last_activity = time.monotonic()" in source
    assert "time.monotonic() - _startup_last_activity >= _startup_inactivity_timeout" in source
    assert 'get("startup_activity_timeout_seconds") or 120' in source
    assert "_startup_last_activity = time.monotonic()" in source


def test_harness_session_ready_unblocks_startup():
    source = (ROOT / "routes/chat_routes.py").read_text(encoding="utf-8")

    assert 'phase") == "session_ready"' in source
    assert "asyncio.shield(_start_task)" in source
    assert "HarnessSessionRef(" in source
    assert "break" in source
