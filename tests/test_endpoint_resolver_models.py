"""Tests for endpoint_resolver — endpoint/model selection and enabled-model filtering."""
import json
from types import SimpleNamespace

import src.endpoint_resolver as endpoint_resolver
from src.endpoint_resolver import (
    _first_chat_model,
    _endpoint_hidden_models,
    _endpoint_enabled_models,
    resolve_endpoint_for_model,
)


class _FakeColumn:
    def __init__(self, name):
        self.name = name

    def is_(self, value):
        return ("is", self.name, value)


class _FakeModelEndpoint:
    is_enabled = _FakeColumn("is_enabled")


class _FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *conditions):
        for operator, field, value in conditions:
            if operator == "is":
                self.rows = [row for row in self.rows if getattr(row, field) is value]
        return self

    def all(self):
        return list(self.rows)


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def query(self, _model):
        return _FakeQuery(self.rows)

    def close(self):
        self.closed = True


def _resolver_endpoint(endpoint_id, model, *, owner=None, enabled=True, hidden=None):
    return SimpleNamespace(
        id=endpoint_id,
        owner=owner,
        base_url=f"https://{endpoint_id}.example/v1",
        api_key=f"key-{endpoint_id}",
        cached_models=json.dumps([model]),
        pinned_models=None,
        hidden_models=json.dumps(hidden or []),
        is_enabled=enabled,
    )


def _install_model_resolver_fakes(monkeypatch, endpoints):
    monkeypatch.setattr(endpoint_resolver, "ModelEndpoint", _FakeModelEndpoint)
    monkeypatch.setattr(endpoint_resolver, "SessionLocal", lambda: _FakeDb(endpoints))
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url)

    # ModelEndpoint ownership is normally enforced in SQL by owner_filter.
    # Keep the fake query deliberately small while preserving its public scope:
    # an owner's private endpoints plus legacy shared endpoints, never another
    # owner's private endpoint.
    import src.auth_helpers as auth_helpers
    monkeypatch.setattr(
        auth_helpers,
        "owner_filter",
        lambda query, _model, owner: _FakeQuery(
            row for row in query.rows if row.owner in (None, owner)
        ),
    )


class _Ep:
    """Minimal ModelEndpoint stand-in for the model-picking helpers."""
    def __init__(self, cached=None, hidden=None):
        self.cached_models = json.dumps(cached) if cached is not None else None
        self.hidden_models = json.dumps(hidden) if hidden is not None else None


class TestFirstChatModel:
    def test_skips_embedding_and_tts(self):
        models = ["text-embedding-ada-002", "whisper-large-v3", "gpt-4o"]
        assert _first_chat_model(models) == "gpt-4o"

    def test_falls_back_to_first_when_all_non_chat(self):
        assert _first_chat_model(["whisper-large-v3"]) == "whisper-large-v3"

    def test_empty(self):
        assert _first_chat_model([]) is None


class TestEnabledModels:
    def test_excludes_hidden(self):
        # The Groq repro: 16 models, only gpt-oss-120b enabled.
        cached = [
            "openai/gpt-oss-safeguard-20b", "canopylabs/orpheus-arabic-saudi",
            "whisper-large-v3", "openai/gpt-oss-120b",
        ]
        hidden = [
            "openai/gpt-oss-safeguard-20b", "canopylabs/orpheus-arabic-saudi",
            "whisper-large-v3",
        ]
        ep = _Ep(cached=cached, hidden=hidden)
        assert _endpoint_enabled_models(ep) == ["openai/gpt-oss-120b"]

    def test_no_hidden_returns_all(self):
        ep = _Ep(cached=["a", "b"], hidden=None)
        assert _endpoint_enabled_models(ep) == ["a", "b"]

    def test_picker_never_selects_disabled_model(self):
        # Regression: a disabled model listed first must not be auto-picked.
        cached = ["canopylabs/orpheus-arabic-saudi", "openai/gpt-oss-120b"]
        hidden = ["canopylabs/orpheus-arabic-saudi"]
        ep = _Ep(cached=cached, hidden=hidden)
        assert _first_chat_model(_endpoint_enabled_models(ep)) == "openai/gpt-oss-120b"

    def test_stale_configured_model_is_discarded(self):
        # A configured model that's been disabled is dropped, falling through
        # to the first enabled chat model.
        ep = _Ep(
            cached=["canopylabs/orpheus-arabic-saudi", "openai/gpt-oss-120b"],
            hidden=["canopylabs/orpheus-arabic-saudi"],
        )
        configured = "canopylabs/orpheus-arabic-saudi"
        if configured in _endpoint_hidden_models(ep):
            configured = ""
        if not configured:
            configured = _first_chat_model(_endpoint_enabled_models(ep))
        assert configured == "openai/gpt-oss-120b"


class TestResolveEndpointForModel:
    def test_unique_enabled_visible_model_resolves_url_model_and_headers(self, monkeypatch):
        _install_model_resolver_fakes(
            monkeypatch,
            [_resolver_endpoint("terra", "gpt-5.6-terra", owner="alice")],
        )

        assert resolve_endpoint_for_model("gpt-5.6-terra", owner="alice") == (
            "https://terra.example/v1/chat/completions",
            "gpt-5.6-terra",
            {"Authorization": "Bearer key-terra"},
        )

    def test_duplicate_visible_model_names_are_ambiguous(self, monkeypatch):
        _install_model_resolver_fakes(
            monkeypatch,
            [
                _resolver_endpoint("one", "gpt-5.6-terra", owner="alice"),
                _resolver_endpoint("two", "gpt-5.6-terra", owner="alice"),
            ],
        )

        assert resolve_endpoint_for_model("gpt-5.6-terra", owner="alice") is None

    def test_owner_scope_does_not_expose_another_owners_endpoint(self, monkeypatch):
        _install_model_resolver_fakes(
            monkeypatch,
            [_resolver_endpoint("bob-private", "gpt-5.6-terra", owner="bob")],
        )

        assert resolve_endpoint_for_model("gpt-5.6-terra", owner="alice") is None

    def test_disabled_endpoint_does_not_resolve(self, monkeypatch):
        _install_model_resolver_fakes(
            monkeypatch,
            [_resolver_endpoint("disabled", "gpt-5.6-terra", owner="alice", enabled=False)],
        )

        assert resolve_endpoint_for_model("gpt-5.6-terra", owner="alice") is None
