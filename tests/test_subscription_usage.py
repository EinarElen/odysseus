from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, SubscriptionAccount, SubscriptionSnapshot, UsageRun, UsageSpan
from src.subscription_usage import SubscriptionUsageStore
from src import subscription_usage


def _store():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return SubscriptionUsageStore(sessionmaker(bind=engine)), sessionmaker(bind=engine)


def test_subscription_query_keeps_quota_and_workload_separate():
    store, sessions = _store()
    start = datetime(2026, 7, 11, 10)
    with sessions() as db:
        db.add(SubscriptionAccount(id="a1", owner="alice", provider="chatgpt-subscription", provider_auth_id="auth1", account_key_hash="hash", plan="plus"))
        db.add_all([
            SubscriptionSnapshot(id="s1", account_id="a1", owner="alice", window_key="primary", window_minutes=300, used_basis_points=1000, resets_at=start + timedelta(hours=5), observed_at=start),
            SubscriptionSnapshot(id="s2", account_id="a1", owner="alice", window_key="primary", window_minutes=300, used_basis_points=1800, resets_at=start + timedelta(hours=5), observed_at=start + timedelta(minutes=10)),
        ])
        db.add(UsageRun(id="r1", owner="alice", kind="chat", status="succeeded", source_surface="web", started_at=start + timedelta(minutes=5)))
        db.add(UsageSpan(id="sp1", run_id="r1", owner="alice", kind="model", name="model.generate", status="succeeded", sequence=1, provider="chatgpt-subscription", endpoint_id="auth1", started_at=start + timedelta(minutes=5), finished_at=start + timedelta(minutes=6)))
        db.commit()

    result = store.query(owner="alice")
    window = result["accounts"][0]["windows"][0]
    interval = result["intervals"][0]
    assert window["used_percent"] == 18
    assert window["remaining_percent"] == 82
    assert interval["account_delta_basis_points"] == 800
    assert interval["odysseus_runs"] == 1
    assert interval["attribution"] == "mixed_or_unknown"


def test_subscription_query_is_owner_scoped():
    store, sessions = _store()
    with sessions() as db:
        db.add(SubscriptionAccount(id="a1", owner="bob", provider="chatgpt-subscription", provider_auth_id="auth1", account_key_hash="hash"))
        db.commit()
    assert store.query(owner="alice")["accounts"] == []


def test_refresh_persists_provider_percent_and_reset(monkeypatch):
    store, sessions = _store()
    with sessions() as db:
        from core.database import ProviderAuthSession
        db.add(ProviderAuthSession(id="auth1", provider="chatgpt-subscription", owner="alice", base_url="https://chatgpt.com/backend-api/codex"))
        db.commit()

    monkeypatch.setattr(subscription_usage, "resolve_runtime_credentials", lambda *args, **kwargs: {"api_key": "token"})
    monkeypatch.setattr(subscription_usage, "_decode_jwt_payload", lambda token: {"chatgpt_account_id": "account-1"})

    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"plan_type": "plus", "rate_limit": {
                "primary_window": {"used_percent": 25.5, "reset_at": 1783782000, "limit_window_seconds": 18000},
                "secondary_window": {"used_percent": 42, "reset_at": 1784214000, "limit_window_seconds": 604800},
            }}

    monkeypatch.setattr(subscription_usage.httpx, "get", lambda *args, **kwargs: Response())
    result = store.refresh(owner="alice")
    assert result["refreshed"] == 1
    assert {window["key"]: window["used_percent"] for window in result["accounts"][0]["windows"]} == {"primary": 25.5, "secondary": 42}


def test_shared_subscription_url_is_not_used_as_account_identity(monkeypatch):
    _, sessions = _store()
    from core.database import ModelEndpoint
    with sessions() as db:
        db.add_all([
            ModelEndpoint(id="ep1", name="one", base_url="https://chatgpt.com/backend-api/codex", provider_auth_id="auth1", owner="alice"),
            ModelEndpoint(id="ep2", name="two", base_url="https://chatgpt.com/backend-api/codex", provider_auth_id="auth2", owner="alice"),
        ])
        db.commit()
    monkeypatch.setattr(subscription_usage, "SessionLocal", sessions)
    assert subscription_usage.provider_auth_id_for_endpoint("alice", "https://chatgpt.com/backend-api/codex/responses") is None
