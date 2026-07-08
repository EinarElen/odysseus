import json
from types import SimpleNamespace

import pytest

from remote_access import endpoints
from remote_access.routes import setup_remote_access_routes


def test_tailscale_status_reports_missing_cli(monkeypatch):
    def _missing(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(endpoints.subprocess, "run", _missing)

    status = endpoints.tailscale_status()

    assert status["installed"] is False
    assert status["running"] is False


def test_tailscale_status_parses_self_identity(monkeypatch):
    payload = {
        "BackendState": "Running",
        "Self": {
            "HostName": "odysseus-host",
            "DNSName": "odysseus-host.tailnet.ts.net.",
            "TailscaleIPs": ["100.64.1.2", "fd7a::1"],
            "Online": True,
        },
    }

    def _run(cmd, capture_output, text, timeout, check):
        assert cmd == ["tailscale", "status", "--json"]
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(endpoints.subprocess, "run", _run)

    status = endpoints.tailscale_status()

    assert status["installed"] is True
    assert status["running"] is True
    assert status["self"]["hostname"] == "odysseus-host"
    assert status["self"]["dns_name"] == "odysseus-host.tailnet.ts.net"
    assert status["self"]["tailscale_ip"] == "100.64.1.2"


def test_advertised_endpoints_include_tailscale_candidates(monkeypatch):
    monkeypatch.setattr(endpoints, "_lan_ip_candidates", lambda: ["192.168.1.20"])
    monkeypatch.setattr(endpoints, "tailscale_status", lambda: {
        "installed": True,
        "running": True,
        "self": {
            "tailscale_ip": "100.64.1.2",
            "dns_name": "odysseus-host.tailnet.ts.net",
        },
    })
    request = SimpleNamespace(
        base_url="http://127.0.0.1:7000/",
        url=SimpleNamespace(port=7000),
    )

    advertised = endpoints.advertised_endpoints(request)
    by_kind = {endpoint.kind: endpoint for endpoint in advertised}

    assert by_kind["current"].url == "http://127.0.0.1:7000"
    assert by_kind["lan_http"].url == "http://192.168.1.20:7000"
    assert by_kind["tailscale_ip_http"].url == "http://100.64.1.2:7000"
    assert by_kind["tailscale_magicdns_https"].url == "https://odysseus-host.tailnet.ts.net"
    assert by_kind["tailscale_magicdns_https"].requires_serve is True


def test_enable_tailscale_serve_targets_loopback(monkeypatch):
    calls = []

    def _run(cmd, capture_output, text, timeout, check):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(endpoints.subprocess, "run", _run)

    result = endpoints.enable_tailscale_serve(7860, https_port=8443)

    assert result == {"ok": True, "https_port": 8443, "target": "http://127.0.0.1:7860"}
    assert calls == [["tailscale", "serve", "--bg", "--https=8443", "http://127.0.0.1:7860"]]


def _route(method: str, path: str):
    routes = list(setup_remote_access_routes().routes)
    for included in list(routes):
        routes.extend(getattr(getattr(included, "original_router", None), "routes", []) or [])
    for route in routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


@pytest.mark.asyncio
async def test_tailscale_serve_route_rejects_non_loopback_target(monkeypatch):
    import remote_access.routes as routes

    async def _json():
        return {"local_host": "0.0.0.0", "local_port": 7000}

    request = SimpleNamespace(
        json=_json,
        url=SimpleNamespace(port=7000),
        state=SimpleNamespace(current_user="alice"),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace()),
    )
    monkeypatch.setattr(routes, "require_admin", lambda request: None)

    with pytest.raises(Exception) as exc:
        await _route("POST", "/api/remote-access/tailscale/serve")(request)

    assert getattr(exc.value, "status_code", None) == 400
    assert "loopback" in exc.value.detail
