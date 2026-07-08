import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from remote_access import pairing


def test_normalize_capabilities_drops_unknown_values():
    assert pairing.normalize_capabilities(["chat", "bogus", "models", "chat"]) == ["chat", "models"]
    assert pairing.normalize_capabilities("") == ["chat"]


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
