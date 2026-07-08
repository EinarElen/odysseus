"""Endpoint advertisement and Tailscale exposure helpers."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from src.constants import internal_api_base

TAILSCALE_SERVE_LOCAL_HOST = "127.0.0.1"
DEFAULT_TAILSCALE_HTTPS_PORT = 443


@dataclass
class AdvertisedEndpoint:
    kind: str
    label: str
    url: str
    reachable: str = "unknown"
    private: bool = True
    requires_serve: bool = False
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "url": self.url,
            "reachable": self.reachable,
            "private": self.private,
            "requires_serve": self.requires_serve,
            "warnings": list(self.warnings),
        }


def _default_port() -> int:
    try:
        parsed = urlparse(internal_api_base())
        return int(parsed.port or 80)
    except (TypeError, ValueError):
        return 80


def request_base_url(request) -> str:
    try:
        return str(request.base_url).rstrip("/")
    except Exception:
        return internal_api_base()


def _lan_ip_candidates() -> list[str]:
    candidates: list[str] = []

    def add(ip: str | None) -> None:
        if ip and not ip.startswith("127.") and ip not in candidates:
            candidates.append(ip)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        add(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass

    return candidates


def _first_ipv4(values: Any) -> str | None:
    if not isinstance(values, list):
        return None
    for value in values:
        if isinstance(value, str) and "." in value:
            return value
    return None


def _run_tailscale(args: list[str], timeout: int = 5) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tailscale", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def tailscale_status() -> dict[str, Any]:
    try:
        result = _run_tailscale(["status", "--json"])
    except FileNotFoundError:
        return {"installed": False, "running": False, "error": "tailscale command not found"}
    except subprocess.TimeoutExpired:
        return {"installed": True, "running": False, "error": "tailscale status timed out"}
    except Exception as exc:
        return {"installed": True, "running": False, "error": str(exc)}

    if result.returncode != 0:
        return {
            "installed": True,
            "running": False,
            "error": (result.stderr or result.stdout or "tailscale status failed").strip(),
        }
    try:
        data = json.loads(result.stdout)
    except (TypeError, ValueError):
        return {"installed": True, "running": False, "error": "tailscale returned invalid JSON"}

    self_data = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    dns_name = (self_data.get("DNSName") or "").strip().rstrip(".") or None
    return {
        "installed": True,
        "running": True,
        "self": {
            "hostname": self_data.get("HostName"),
            "dns_name": dns_name,
            "tailscale_ip": _first_ipv4(self_data.get("TailscaleIPs")),
            "online": self_data.get("Online"),
        },
        "raw_backend_state": data.get("BackendState"),
    }


def advertised_endpoints(request=None, *, port: int | None = None) -> list[AdvertisedEndpoint]:
    port = port or getattr(getattr(request, "url", None), "port", None) or _default_port()
    out = [
        AdvertisedEndpoint(
            kind="current",
            label="Current browser URL",
            url=request_base_url(request) if request is not None else f"http://127.0.0.1:{port}",
            reachable="current-session",
            private=True,
        )
    ]

    for ip in _lan_ip_candidates():
        out.append(AdvertisedEndpoint(
            kind="lan_http",
            label=f"LAN HTTP ({ip})",
            url=f"http://{ip}:{port}",
            reachable="depends-on-bind-host",
            private=True,
            warnings=("Odysseus must be bound to a non-loopback interface for this URL to work.",),
        ))

    ts = tailscale_status()
    if ts.get("running"):
        self_data = ts.get("self") or {}
        ts_ip = self_data.get("tailscale_ip")
        dns_name = self_data.get("dns_name")
        if ts_ip:
            out.append(AdvertisedEndpoint(
                kind="tailscale_ip_http",
                label="Tailscale IP HTTP",
                url=f"http://{ts_ip}:{port}",
                reachable="depends-on-bind-host",
                private=True,
                warnings=("Prefer Tailscale Serve over binding Odysseus to the tailnet interface.",),
            ))
        if dns_name:
            out.append(AdvertisedEndpoint(
                kind="tailscale_magicdns_https",
                label="Tailscale MagicDNS HTTPS",
                url=f"https://{dns_name}",
                reachable="requires-serve",
                private=True,
                requires_serve=True,
            ))

    return out


def choose_pairing_base_url(request=None, explicit_url: str | None = None) -> str:
    if explicit_url:
        return explicit_url.rstrip("/")
    endpoints = advertised_endpoints(request)
    for kind in ("tailscale_magicdns_https", "tailscale_ip_http", "lan_http", "current"):
        for endpoint in endpoints:
            if endpoint.kind == kind and endpoint.reachable not in {"requires-serve", "depends-on-bind-host"}:
                return endpoint.url.rstrip("/")
    return request_base_url(request)


def enable_tailscale_serve(
    local_port: int,
    *,
    https_port: int = DEFAULT_TAILSCALE_HTTPS_PORT,
    local_host: str = TAILSCALE_SERVE_LOCAL_HOST,
) -> dict[str, Any]:
    target = f"http://{local_host}:{int(local_port)}"
    try:
        result = _run_tailscale(["serve", "--bg", f"--https={int(https_port)}", target], timeout=10)
    except FileNotFoundError:
        return {"ok": False, "error": "tailscale command not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "tailscale serve timed out"}
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout or "tailscale serve failed").strip()}
    return {"ok": True, "https_port": int(https_port), "target": target}


def disable_tailscale_serve(*, https_port: int = DEFAULT_TAILSCALE_HTTPS_PORT) -> dict[str, Any]:
    try:
        result = _run_tailscale(["serve", f"--https={int(https_port)}", "off"], timeout=10)
    except FileNotFoundError:
        return {"ok": False, "error": "tailscale command not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "tailscale serve disable timed out"}
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout or "tailscale serve disable failed").strip()}
    return {"ok": True, "https_port": int(https_port)}
