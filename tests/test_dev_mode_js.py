from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dev_mode_watcher_starts_by_default_when_loaded():
    source = (ROOT / "static/js/devMode.js").read_text(encoding="utf-8")

    assert "setHotReload(localStorage.getItem(SHUT_KEY) !== '1');" in source
    assert "setHotReload(status.reload_active || localStorage.getItem(HOT_KEY) === '1');" not in source


def test_dev_mode_surfaces_server_restart_poll_failures():
    source = (ROOT / "static/js/devMode.js").read_text(encoding="utf-8")

    assert "showReconnectToast();" in source
    assert "Server restarting" in source
