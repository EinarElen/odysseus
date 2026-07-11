from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from routes import usage_routes
from src.usage_observability import RunContext, SpanContext, UsageObservation, UsageStore


def _client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    store = UsageStore(sessionmaker(bind=engine))
    monkeypatch.setattr(usage_routes, "usage_store", store)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = FastAPI()
    app.include_router(usage_routes.setup_usage_routes())
    return TestClient(app), store


def test_usage_routes_expose_summary_breakdown_runs_and_detail(monkeypatch):
    client, store = _client(monkeypatch)
    run = store.begin_run(RunContext(owner="local", kind="chat", source_surface="web"))
    span = run.begin_span(SpanContext(kind="model", name="model.generate", actual_model="test-model"))
    span.record_usage(UsageObservation(source="provider", input_tokens=20, output_tokens=5, cache_read_tokens=8))
    span.finish()
    run.finish()

    assert client.get("/api/usage/summary?from=24h").json()["totals"]["input_tokens"] == 20
    assert client.get("/api/usage/breakdown?group_by=model").json()["items"][0]["key"] == "test-model"
    assert client.get("/api/usage/runs").json()["runs"][0]["id"] == run.id
    detail = client.get(f"/api/usage/runs/{run.id}").json()
    assert detail["spans"][0]["usage"]["cache_read_tokens"] == 8


def test_usage_routes_reject_invalid_query_contracts(monkeypatch):
    client, _ = _client(monkeypatch)
    assert client.get("/api/usage/timeseries?bucket=minute").status_code == 400
    assert client.get("/api/usage/breakdown?group_by=owner").status_code == 400
    assert client.get("/api/usage/summary?from=not-a-time").status_code == 400
