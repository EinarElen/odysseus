from datetime import datetime, timedelta

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, Session, ChatMessage, UsageDailyRollup
from src.usage_observability import RunContext, SpanContext, UsageObservation, UsageStore, create_in_memory_usage_store


def _store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return UsageStore(sessionmaker(bind=engine))


def test_owner_summary_is_built_from_final_usage_observations():
    store = _store()
    with store._session_factory() as db:
        db.add(Session(id="s1", name="Chat", endpoint_url="http://local", model="test", owner="alice"))
        db.commit()
    run = store.begin_run(RunContext(owner="alice", kind="agent", source_surface="web", session_id="s1"))
    model = run.begin_span(SpanContext(kind="model", name="model.generate", provider="anthropic", actual_model="claude"))
    model.record_usage(UsageObservation(
        source="provider", input_tokens=100, output_tokens=20,
        cache_read_tokens=60, cache_write_tokens=10,
        fresh_input_tokens=40, total_cost_micros=1234, cost_source="provider",
    ))
    model.finish()
    run.finish()

    # A different owner's facts must never enter Alice's report.
    other = store.begin_run(RunContext(owner="bob", kind="chat", source_surface="api"))
    other_model = other.begin_span(SpanContext(kind="model", name="model.generate"))
    other_model.record_usage(UsageObservation(source="provider", input_tokens=999, output_tokens=999))
    other_model.finish()
    other.finish()

    report = store.query_summary(owner="alice")
    expected = {
        "input_tokens": 100,
        "output_tokens": 20,
        "reasoning_tokens": None,
        "cache_read_tokens": 60,
        "cache_write_tokens": 10,
        "fresh_input_tokens": 40,
        "total_cost_micros": 1234,
        "runs": 1,
        "failed_runs": 0,
    }
    assert {key: report["totals"][key] for key in expected} == expected
    assert report["quality"] == "exact"
    assert report["currency"] == "USD"


def test_incognito_run_is_a_noop_and_creates_no_records():
    store = _store()
    run = store.begin_run(RunContext(owner="alice", kind="chat", incognito=True))
    span = run.begin_span(SpanContext(kind="model", name="model.generate"))
    span.record_usage(UsageObservation(source="provider", input_tokens=10, output_tokens=2))
    span.finish()
    run.finish()

    assert store.query_summary(owner="alice")["totals"]["runs"] == 0


def test_timeseries_and_breakdown_use_owner_time_range_and_final_observations():
    store = _store()
    now = datetime.utcnow()
    run = store.begin_run(RunContext(owner="alice", kind="chat", started_at=now - timedelta(hours=1)))
    span = run.begin_span(SpanContext(kind="model", name="model.generate", provider="openai", actual_model="gpt-x"))
    span.record_usage(UsageObservation(source="estimated", input_tokens=8, output_tokens=2, is_final=False))
    span.record_usage(UsageObservation(source="provider", input_tokens=10, output_tokens=4))
    span.finish()
    run.finish()

    series = store.query_timeseries(owner="alice", start=now - timedelta(days=1), bucket="hour")
    assert sum(point["input_tokens"] for point in series["points"]) == 10
    breakdown = store.query_breakdown(owner="alice", group_by="model")
    assert breakdown["items"] == [{
        "key": "gpt-x", "input_tokens": 10, "output_tokens": 4,
        "cache_read_tokens": None, "cache_write_tokens": None,
        "total_cost_micros": None, "runs": 1,
    }]


def test_run_detail_preserves_span_tree_and_unknown_cache_values():
    store = _store()
    run = store.begin_run(RunContext(owner="alice", kind="agent"))
    turn = run.begin_span(SpanContext(kind="turn", name="chat.turn"))
    model = run.begin_span(SpanContext(kind="model", name="model.generate", parent_span_id=turn.id, agent_round=1))
    model.record_usage(UsageObservation(source="estimated", input_tokens=12, output_tokens=3))
    model.finish()
    turn.finish()
    run.finish()

    detail = store.get_run(owner="alice", run_id=run.id)
    assert [span["kind"] for span in detail["spans"]] == ["turn", "model"]
    assert detail["spans"][1]["parent_span_id"] == turn.id
    assert detail["spans"][1]["usage"]["cache_read_tokens"] is None
    assert store.get_run(owner="bob", run_id=run.id) is None


def test_catalog_cost_uses_fresh_and_cached_rates_and_pins_snapshot():
    store = _store()
    run = store.begin_run(RunContext(owner="alice", kind="chat"))
    span = run.begin_span(SpanContext(kind="model", name="model.generate", actual_model="gpt-4o-mini"))
    span.record_usage(UsageObservation(
        source="provider", input_tokens=1_000_000, fresh_input_tokens=750_000,
        cache_read_tokens=250_000, output_tokens=100_000,
    ))
    span.finish()
    run.finish()

    # 750k fresh @ $0.15/M + 250k cached @ $0.075/M + 100k output @ $0.60/M.
    assert store.query_summary(owner="alice")["totals"]["total_cost_micros"] == 191_250


def test_same_final_reconciliation_version_cannot_double_count():
    store = _store()
    run = store.begin_run(RunContext(owner="alice", kind="chat"))
    span = run.begin_span(SpanContext(kind="model", name="model.generate"))
    span.record_usage(UsageObservation(source="provider", input_tokens=10, output_tokens=2))
    with pytest.raises(ValueError, match="already recorded"):
        span.record_usage(UsageObservation(source="provider", input_tokens=10, output_tokens=2))
    assert store.query_summary(owner="alice")["totals"]["input_tokens"] == 10


def test_newer_reconciliation_replaces_older_final_in_projections():
    store = _store()
    run = store.begin_run(RunContext(owner="alice", kind="chat"))
    span = run.begin_span(SpanContext(kind="model", name="model.generate"))
    span.record_usage(UsageObservation(source="provider", input_tokens=10, output_tokens=2))
    span.record_usage(UsageObservation(source="reconciled", input_tokens=12, output_tokens=3, reconciliation_version=2))

    totals = store.query_summary(owner="alice")["totals"]
    assert totals["input_tokens"] == 12
    assert totals["output_tokens"] == 3


def test_multiple_partial_observations_do_not_enter_totals():
    store = _store()
    run = store.begin_run(RunContext(owner="alice", kind="chat"))
    span = run.begin_span(SpanContext(kind="model", name="model.generate"))
    span.record_usage(UsageObservation(source="provider", input_tokens=5, is_final=False))
    span.record_usage(UsageObservation(source="provider", input_tokens=6, is_final=False))
    assert store.query_summary(owner="alice")["totals"]["input_tokens"] == 0


def test_legacy_backfill_is_idempotent_and_projects_session_totals():
    store = _store()
    with store._session_factory() as db:
        db.add(Session(id="legacy-s", name="Old", endpoint_url="http://local", model="old-model", owner="alice"))
        db.add(ChatMessage(
            id="legacy-m", session_id="legacy-s", role="assistant", content="answer",
            meta_data='{"input_tokens": 40, "output_tokens": 10, "usage_source": "real"}',
        ))
        db.commit()

    assert store.backfill_legacy_messages()["created"] == 1
    assert store.backfill_legacy_messages()["created"] == 0
    assert store.session_totals(owner="alice")["legacy-s"]["total_tokens"] == 50


def test_rollups_rebuild_and_explicit_deletion_are_owner_scoped():
    store = _store()
    run = store.begin_run(RunContext(owner="alice", kind="chat"))
    span = run.begin_span(SpanContext(kind="model", name="model.generate", actual_model="gpt-4o-mini"))
    span.record_usage(UsageObservation(source="provider", input_tokens=10, output_tokens=2))
    span.finish()
    run.finish()

    assert store.rebuild_daily_rollups(owner="alice") == 1
    with store._session_factory() as db:
        assert db.query(UsageDailyRollup).filter(UsageDailyRollup.owner == "alice").count() == 1
    assert store.delete_usage(owner="bob", run_id=run.id) == 0
    assert store.delete_usage(owner="alice", run_id=run.id) == 1
    assert store.query_summary(owner="alice")["totals"]["runs"] == 0


def test_public_in_memory_adapter_and_session_deletion_retention():
    store = create_in_memory_usage_store()
    with store._session_factory() as db:
        db.add(Session(id="delete-s", name="Delete", endpoint_url="http://local", model="m", owner="alice"))
        db.commit()
    run = store.begin_run(RunContext(owner="alice", kind="chat", session_id="delete-s"))
    span = run.begin_span(SpanContext(kind="model", name="model.generate"))
    span.record_usage(UsageObservation(source="provider", input_tokens=1, output_tokens=1))
    span.finish(); run.finish()
    with store._session_factory() as db:
        db.query(Session).filter(Session.id == "delete-s").delete()
        db.commit()
    detail = store.get_run(owner="alice", run_id=run.id)
    assert detail is not None
    assert detail["session_id"] is None


def test_timezone_buckets_preserve_dst_offsets():
    store = _store()
    for observed in (datetime(2026, 3, 29, 0, 30), datetime(2026, 3, 29, 1, 30)):
        run = store.begin_run(RunContext(owner="alice", kind="chat", started_at=observed))
        span = run.begin_span(SpanContext(kind="model", name="model.generate", started_at=observed))
        span.record_usage(UsageObservation(source="provider", input_tokens=1, observed_at=observed))
        span.finish(); run.finish()
    points = store.query_timeseries(owner="alice", bucket="hour", timezone_name="Europe/Stockholm")["points"]
    assert [point["time"][-6:] for point in points] == ["+01:00", "+02:00"]
