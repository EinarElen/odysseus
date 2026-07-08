import json
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock

import pytest

from remote_access import pairing
from remote_access.routes import setup_remote_access_routes


def test_normalize_capabilities_drops_unknown_values():
    assert pairing.normalize_capabilities(["chat", "bogus", "models", "chat"]) == ["chat", "models"]
    assert pairing.normalize_capabilities("") == ["chat"]
    assert pairing.normalize_capabilities(["remote_support:control"]) == [
        "remote_support:read",
        "remote_support:control",
    ]


def test_invite_secret_hash_verifies_without_persisting_plaintext():
    raw = pairing.new_invite_secret()
    stored = pairing.hash_secret(raw)

    assert raw.startswith(pairing.INVITE_SECRET_PREFIX)
    assert stored != raw
    assert pairing.verify_secret(raw, stored) is True
    assert pairing.verify_secret(raw + "x", stored) is False


def test_invite_is_single_use_and_expiring():
    now = pairing.now_utc()
    invite = SimpleNamespace(
        revoked_at=None,
        consumed_at=None,
        expires_at=now + timedelta(minutes=5),
    )
    assert pairing.invite_is_usable(invite, now) is True

    invite.consumed_at = now
    assert pairing.invite_is_usable(invite, now) is False

    invite.consumed_at = None
    invite.expires_at = now - timedelta(seconds=1)
    assert pairing.invite_is_usable(invite, now) is False


def test_pairing_url_keeps_secret_in_fragment():
    url = pairing.build_pairing_url("https://odyssey.example/", "odpair_secret/value")

    assert url == "https://odyssey.example/api/remote-access/pair#token=odpair_secret%2Fvalue"
    assert "token=" not in url.split("#", 1)[0]


def test_pair_page_exchanges_fragment_token():
    response = _route("GET", "/api/remote-access/pair")(_req())
    body = response.body.decode()

    assert response.media_type == "text/html"
    assert "location.hash" in body
    assert "/api/remote-access/pair/exchange" in body
    assert "history.replaceState" in body


def test_metadata_json_keeps_scalar_values_only():
    encoded = pairing.metadata_to_json({
        "platform": "macOS",
        "nested": {"drop": True},
        "count": 2,
    })

    assert json.loads(encoded) == {"platform": "macOS", "count": 2}


def test_create_bearer_token_stores_api_token(monkeypatch):
    captured = {}

    class _Token:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _DB:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add(self, token):
            self.token = token

    monkeypatch.setattr("core.database.ApiToken", _Token)
    monkeypatch.setattr("core.database.get_db_session", lambda: _DB())

    token_id, raw = pairing.create_bearer_token("alice", "remote support", ["chat", "models"])

    assert token_id
    assert raw.startswith("ody_")
    assert captured["owner"] == "alice"
    assert captured["name"] == "remote support"
    assert captured["token_hash"] != raw
    assert captured["token_prefix"] == raw[:8]
    assert captured["scopes"] == "chat,models"
    assert captured["is_active"] is True


def _route(method: str, path: str):
    routes = list(setup_remote_access_routes().routes)
    for included in list(routes):
        routes.extend(getattr(getattr(included, "original_router", None), "routes", []) or [])
    for route in routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _req(body=None):
    async def _json():
        return body or {}

    return SimpleNamespace(
        json=_json,
        headers={"user-agent": "pytest-client"},
        state=SimpleNamespace(current_user="alice", api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(invalidate_token_cache=MagicMock())),
        base_url="https://odysseus.example/",
        url=SimpleNamespace(port=7000),
    )


@pytest.mark.asyncio
async def test_create_invite_returns_pairing_url_and_stores_hash(monkeypatch):
    import core.database as db_mod
    import remote_access.routes as routes

    added = []

    class _DB:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add(self, row):
            added.append(row)

    monkeypatch.setattr(routes, "require_admin", lambda request: None)
    monkeypatch.setattr(routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(routes.endpoints, "choose_pairing_base_url", lambda request, explicit_url=None: "https://tail.example")
    monkeypatch.setattr(routes.pairing, "qr_png_data_uri", lambda value: None)
    monkeypatch.setattr(db_mod, "get_db_session", lambda: _DB())

    response = await _route("POST", "/api/remote-access/invites")(
        _req({"label": "Support", "client_type": "support", "capabilities": ["chat", "models"]})
    )

    invite = response["invite"]
    assert invite["label"] == "Support"
    assert invite["client_type"] == "support"
    assert invite["capabilities"] == ["chat", "models"]
    assert invite["pairing_url"].startswith("https://tail.example/api/remote-access/pair#token=odpair_")
    assert "token=" not in invite["pairing_url"].split("#", 1)[0]
    token = parse_qs(urlparse(invite["pairing_url"]).fragment)["token"][0]
    assert "token" not in invite
    assert token.startswith("odpair_")
    assert added[0].token_hash != token
    assert added[0].token_prefix == added[0].token_hash[:16]


@pytest.mark.asyncio
async def test_exchange_invite_consumes_invite_and_creates_known_client(monkeypatch):
    import core.database as db_mod
    import remote_access.routes as routes

    now = pairing.now_utc()
    raw_secret = pairing.new_invite_secret()
    invite = SimpleNamespace(
        id="inv1",
        owner="alice",
        label="Support",
        client_type="support",
        token_hash=pairing.hash_secret(raw_secret),
        token_prefix=pairing.token_prefix(raw_secret),
        capabilities="chat,models",
        endpoint_url="https://tail.example",
        expires_at=now + timedelta(minutes=5),
        consumed_at=None,
        consumed_by_client_id=None,
        revoked_at=None,
    )
    added = []

    class _ApiToken:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Client:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.created_at = now
            self.revoked_at = None

    class _DB:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add(self, row):
            added.append(row)

    monkeypatch.setattr(routes, "_find_invite_for_secret", lambda db, secret: invite)
    monkeypatch.setattr(db_mod, "get_db_session", lambda: _DB())
    monkeypatch.setattr(db_mod, "ApiToken", _ApiToken)
    monkeypatch.setattr(db_mod, "RemoteKnownClient", _Client)

    request = _req({
        "token": raw_secret,
        "label": "Field laptop",
        "client_type": "desktop",
        "platform": "macOS",
        "metadata": {"build": "test"},
    })
    response = await _route("POST", "/api/remote-access/pair/exchange")(request)

    assert response["credential"]["type"] == "bearer"
    assert response["credential"]["token"].startswith("ody_")
    assert response["credential"]["scopes"] == ["chat", "models"]
    assert response["client"]["name"] == "Field laptop"
    assert response["client"]["client_type"] == "desktop"
    assert invite.consumed_at is not None
    assert invite.consumed_by_client_id == response["client"]["id"]
    assert any(isinstance(row, _ApiToken) for row in added)
    assert any(isinstance(row, _Client) for row in added)
    request.app.state.invalidate_token_cache.assert_called_once()
