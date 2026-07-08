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


def test_dev_mode_server_reload_is_manual_from_toast_or_menu():
    source = (ROOT / "static/js/devMode.js").read_text(encoding="utf-8")

    assert "X-Odysseus-Dev-Client" in source
    assert "id=\"dev-server-reload\"" in source
    assert "Restart server" in source
    assert "/api/dev/server/reload" in source
    assert "X-Odysseus-Dev-Action" in source
    assert "server-reload" in source
    assert "autoMs: 0" in source


def test_dev_mode_confirms_busy_reload_before_disruptive_restart():
    source = (ROOT / "static/js/devMode.js").read_text(encoding="utf-8")

    assert "function reloadBlockers()" in source
    assert "confirmReloadIfBusy" in source
    assert "window.__odysseusChatBusy" in source
    assert "window.compareModule?.isActive?.()" in source
    assert "manual_reload_supported" in source
