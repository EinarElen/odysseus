"""Authoritative server-side usage ledger and read projections."""

from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func

from core.database import (
    SessionLocal,
    UsageObservation as DBUsageObservation,
    UsageRun as DBUsageRun,
    UsageSpan as DBUsageSpan,
    UsagePriceSnapshot as DBUsagePriceSnapshot,
    UsageDailyRollup as DBUsageDailyRollup,
    ChatMessage as DBChatMessage,
    Session as DBSession,
)


# Existing UI prices migrated server-side as immutable USD snapshots. Values
# are USD per million tokens; provider-specific prices can be added later
# without rewriting observations already priced with a snapshot.
DEFAULT_PRICE_CATALOG = {
    "claude-sonnet-4": (3.00, 15.00, 0.30, 3.75),
    "claude-opus-4": (15.00, 75.00, 1.50, 18.75),
    "claude-haiku": (0.80, 4.00, 0.08, 1.00),
    "gpt-5": (2.00, 8.00, 1.00, None),
    "gpt-4.1": (2.00, 8.00, 0.50, None),
    "gpt-4.1-mini": (0.40, 1.60, 0.10, None),
    "gpt-4.1-nano": (0.10, 0.40, 0.025, None),
    "gpt-4o": (2.50, 10.00, 1.25, None),
    "gpt-4o-mini": (0.15, 0.60, 0.075, None),
    "o3": (2.00, 8.00, 0.50, None),
    "o3-mini": (1.10, 4.40, 0.55, None),
    "o4-mini": (1.10, 4.40, 0.275, None),
    "deepseek-chat": (0.27, 1.10, None, None),
    "deepseek-reasoner": (0.55, 2.19, None, None),
    "gemini-2.5-pro": (1.25, 10.00, 0.3125, None),
    "gemini-2.5-flash": (0.15, 0.60, 0.0375, None),
    "mistral-large": (2.00, 6.00, None, None),
    "grok-4": (3.00, 15.00, None, None),
    "qwen3": (0.30, 1.20, None, None),
    "sonar-pro": (3.00, 15.00, None, None),
}

IMAGE_PRICE_USD = {
    ("gpt-image-1.5", "low", "1024x1024"): 0.009,
    ("gpt-image-1.5", "medium", "1024x1024"): 0.034,
    ("gpt-image-1.5", "high", "1024x1024"): 0.133,
    ("gpt-image-1", "low", "1024x1024"): 0.011,
    ("gpt-image-1", "medium", "1024x1024"): 0.042,
    ("gpt-image-1", "high", "1024x1024"): 0.167,
    ("gpt-image-1-mini", "low", "1024x1024"): 0.005,
    ("gpt-image-1-mini", "medium", "1024x1024"): 0.011,
    ("gpt-image-1-mini", "high", "1024x1024"): 0.036,
}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _elapsed_ms(started: datetime, finished: datetime) -> int:
    return max(0, round((finished - started).total_seconds() * 1000))


@dataclass(frozen=True)
class RunContext:
    owner: str
    kind: str
    source_surface: str = "internal"
    session_id: str | None = None
    task_id: str | None = None
    research_session_id: str | None = None
    comparison_id: str | None = None
    harness_session_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    incognito: bool = False


@dataclass(frozen=True)
class SpanContext:
    kind: str
    name: str
    parent_span_id: str | None = None
    agent_round: int | None = None
    provider: str | None = None
    endpoint_id: str | None = None
    requested_model: str | None = None
    actual_model: str | None = None
    tool_name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None


@dataclass(frozen=True)
class UsageObservation:
    source: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    fresh_input_tokens: int | None = None
    context_tokens: int | None = None
    context_length: int | None = None
    time_to_first_token_ms: int | None = None
    generation_tps_milli: int | None = None
    prefill_tps_milli: int | None = None
    audio_input_tokens: int | None = None
    audio_output_tokens: int | None = None
    image_input_units: int | None = None
    image_output_units: int | None = None
    request_count: int = 1
    currency: str = "USD"
    input_cost_micros: int | None = None
    output_cost_micros: int | None = None
    cache_read_cost_micros: int | None = None
    cache_write_cost_micros: int | None = None
    other_cost_micros: int | None = None
    total_cost_micros: int | None = None
    cost_source: str | None = None
    price_snapshot_id: str | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime | None = None
    is_final: bool = True
    reconciliation_version: int = 1


@dataclass(frozen=True)
class SpanOutcome:
    status: str = "succeeded"
    outcome_code: str | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    timing_known: bool = True


@dataclass(frozen=True)
class RunOutcome:
    status: str = "succeeded"
    error_code: str | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    timing_known: bool = True


class _NullSpan:
    id = ""

    def begin_span(self, context: SpanContext):
        return self

    def record_usage(self, observation: UsageObservation) -> None:
        return None

    def finish(self, outcome: SpanOutcome | None = None) -> None:
        return None


class _NullRun(_NullSpan):
    def finish(self, outcome: RunOutcome | None = None) -> None:
        return None


class SpanHandle:
    def __init__(self, store: "UsageStore", run_id: str, owner: str, span_id: str):
        self._store = store
        self.run_id = run_id
        self.owner = owner
        self.id = span_id

    def record_usage(self, observation: UsageObservation) -> None:
        self._store._record_usage(self.run_id, self.id, self.owner, observation)

    def finish(self, outcome: SpanOutcome | None = None) -> None:
        self._store._finish_span(self.id, self.owner, outcome or SpanOutcome())

    def set_route(self, *, actual_model: str | None = None, provider: str | None = None, endpoint_id: str | None = None) -> None:
        self._store._set_span_route(self.id, self.owner, actual_model, provider, endpoint_id)


class RunHandle:
    def __init__(self, store: "UsageStore", run_id: str, owner: str):
        self._store = store
        self.id = run_id
        self.owner = owner

    def begin_span(self, context: SpanContext) -> SpanHandle:
        return self._store._begin_span(self.id, self.owner, context)

    def finish(self, outcome: RunOutcome | None = None) -> None:
        self._store._finish_run(self.id, self.owner, outcome or RunOutcome())


class UsageStore:
    """Deep accounting module: ingestion and owner-scoped read projections."""

    def __init__(self, session_factory=SessionLocal):
        self._session_factory = session_factory

    def begin_run(self, context: RunContext) -> RunHandle | _NullRun:
        if context.incognito:
            return _NullRun()
        owner = (context.owner or "").strip()
        if not owner:
            raise ValueError("usage Runs require an owner")
        run_id = _id("run")
        with self._session_factory() as db:
            db.add(DBUsageRun(
                id=run_id, owner=owner, kind=context.kind,
                source_surface=context.source_surface, session_id=context.session_id,
                task_id=context.task_id, research_session_id=context.research_session_id,
                comparison_id=context.comparison_id, harness_session_id=context.harness_session_id,
                attributes_json=dict(context.attributes), started_at=context.started_at or _now(),
            ))
            db.commit()
        return RunHandle(self, run_id, owner)

    def _begin_span(self, run_id: str, owner: str, context: SpanContext) -> SpanHandle:
        span_id = _id("span")
        with self._session_factory() as db:
            max_sequence = db.query(func.max(DBUsageSpan.sequence)).filter(DBUsageSpan.run_id == run_id).scalar()
            db.add(DBUsageSpan(
                id=span_id, run_id=run_id, owner=owner, sequence=(max_sequence or 0) + 1,
                kind=context.kind, name=context.name, parent_span_id=context.parent_span_id,
                agent_round=context.agent_round, provider=context.provider,
                endpoint_id=context.endpoint_id, requested_model=context.requested_model,
                actual_model=context.actual_model, tool_name=context.tool_name,
                attributes_json=dict(context.attributes), started_at=context.started_at or _now(),
            ))
            db.commit()
        return SpanHandle(self, run_id, owner, span_id)

    def _record_usage(self, run_id: str, span_id: str, owner: str, observation: UsageObservation) -> None:
        if observation.currency != "USD":
            raise ValueError("usage accounting currently supports USD only")
        with self._session_factory() as db:
            self._ensure_default_prices(db)
            duplicate = db.query(DBUsageObservation.id).filter(
                DBUsageObservation.span_id == span_id,
                DBUsageObservation.is_final.is_(True),
                DBUsageObservation.reconciliation_version == observation.reconciliation_version,
            ).first() if observation.is_final else None
            if duplicate is not None:
                raise ValueError("usage observation version already recorded for this span")
            max_sequence = db.query(func.max(DBUsageObservation.sequence)).filter(
                DBUsageObservation.span_id == span_id
            ).scalar()
            values = asdict(observation)
            values["raw_usage_json"] = self._sanitize_raw_usage(values.pop("raw_usage"))
            values["observed_at"] = values["observed_at"] or _now()
            if values["total_cost_micros"] is None:
                span = db.query(DBUsageSpan).filter(DBUsageSpan.id == span_id).first()
                price = self._matching_price(db, span.actual_model if span else None, values["observed_at"])
                if price:
                    fresh = values["fresh_input_tokens"]
                    cached = values["cache_read_tokens"]
                    ordinary_input = fresh if fresh is not None else values["input_tokens"]
                    values["input_cost_micros"] = self._meter(ordinary_input, price.input_per_million_micros)
                    values["output_cost_micros"] = self._meter(values["output_tokens"], price.output_per_million_micros)
                    values["cache_read_cost_micros"] = self._meter(cached, price.cache_read_per_million_micros)
                    values["cache_write_cost_micros"] = self._meter(values["cache_write_tokens"], price.cache_write_per_million_micros)
                    components = [values[key] for key in ("input_cost_micros", "output_cost_micros", "cache_read_cost_micros", "cache_write_cost_micros", "other_cost_micros") if values[key] is not None]
                    values["total_cost_micros"] = sum(components) if components else None
                    values["cost_source"] = "price_catalog"
                    values["price_snapshot_id"] = price.id
            db.add(DBUsageObservation(
                id=_id("obs"), run_id=run_id, span_id=span_id, owner=owner,
                sequence=(max_sequence or 0) + 1, **values,
            ))
            db.commit()

    @staticmethod
    def _sanitize_raw_usage(raw: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
            "cache_read_input_tokens", "cache_creation_input_tokens", "cached_tokens", "reasoning_tokens",
            "request_id", "service_tier", "input_semantics", "adapter_version",
        }
        return {
            str(key): value for key, value in (raw or {}).items()
            if key in allowed and (value is None or isinstance(value, (str, int, float, bool)))
        }

    @staticmethod
    def _meter(units: int | None, micros_per_million: int | None) -> int | None:
        if units is None or micros_per_million is None:
            return None
        return round(units * micros_per_million / 1_000_000)

    @staticmethod
    def _ensure_default_prices(db) -> None:
        if db.query(DBUsagePriceSnapshot.id).first():
            return
        effective = datetime(2026, 1, 1)
        for pattern, (input_usd, output_usd, cache_read_usd, cache_write_usd) in DEFAULT_PRICE_CATALOG.items():
            db.add(DBUsagePriceSnapshot(
                id=f"price_default_{pattern.replace('.', '_')}", provider="any", model_pattern=pattern,
                currency="USD", input_per_million_micros=round(input_usd * 1_000_000),
                output_per_million_micros=round(output_usd * 1_000_000),
                cache_read_per_million_micros=round(cache_read_usd * 1_000_000) if cache_read_usd is not None else None,
                cache_write_per_million_micros=round(cache_write_usd * 1_000_000) if cache_write_usd is not None else None,
                effective_from=effective, source_url="migrated:static/js/chatRenderer.js",
            ))
        db.flush()

    @staticmethod
    def _matching_price(db, model: str | None, observed_at: datetime):
        if not model:
            return None
        lowered = model.lower()
        candidates = db.query(DBUsagePriceSnapshot).filter(
            DBUsagePriceSnapshot.currency == "USD",
            DBUsagePriceSnapshot.effective_from <= observed_at,
        ).all()
        matches = [row for row in candidates if row.model_pattern.lower() in lowered and (row.effective_to is None or row.effective_to > observed_at)]
        return max(matches, key=lambda row: len(row.model_pattern), default=None)

    def _finish_span(self, span_id: str, owner: str, outcome: SpanOutcome) -> None:
        with self._session_factory() as db:
            row = db.query(DBUsageSpan).filter(DBUsageSpan.id == span_id, DBUsageSpan.owner == owner).first()
            if not row or row.finished_at:
                return
            finished = outcome.finished_at or _now()
            row.status = outcome.status
            row.outcome_code = outcome.outcome_code
            row.finished_at = finished
            row.duration_ms = (
                outcome.duration_ms if outcome.duration_ms is not None
                else (_elapsed_ms(row.started_at, finished) if outcome.timing_known else None)
            )
            db.commit()

    def _set_span_route(self, span_id: str, owner: str, actual_model: str | None, provider: str | None, endpoint_id: str | None) -> None:
        with self._session_factory() as db:
            row = db.query(DBUsageSpan).filter(DBUsageSpan.id == span_id, DBUsageSpan.owner == owner).first()
            if not row:
                return
            if actual_model:
                row.actual_model = actual_model
            if provider:
                row.provider = provider
            if endpoint_id:
                row.endpoint_id = endpoint_id
            db.commit()

    def _finish_run(self, run_id: str, owner: str, outcome: RunOutcome) -> None:
        with self._session_factory() as db:
            row = db.query(DBUsageRun).filter(DBUsageRun.id == run_id, DBUsageRun.owner == owner).first()
            if not row or row.finished_at:
                return
            finished = outcome.finished_at or _now()
            row.status = outcome.status
            row.error_code = outcome.error_code
            row.finished_at = finished
            row.duration_ms = (
                outcome.duration_ms if outcome.duration_ms is not None
                else (_elapsed_ms(row.started_at, finished) if outcome.timing_known else None)
            )
            db.commit()

    def finish_active_run(self, *, owner: str, run_id: str | None, status: str = "interrupted") -> None:
        """Finalize one explicitly identified unfinished Run and its spans."""
        if not run_id:
            return
        with self._session_factory() as db:
            run = db.query(DBUsageRun).filter(
                DBUsageRun.owner == (owner or "local"),
                DBUsageRun.id == run_id,
                DBUsageRun.status == "running",
            ).first()
            if not run:
                return
            finished = _now()
            for span in db.query(DBUsageSpan).filter(
                DBUsageSpan.run_id == run.id, DBUsageSpan.status == "running"
            ).all():
                span.status = status
                span.finished_at = finished
                span.duration_ms = _elapsed_ms(span.started_at, finished)
            run.status = status
            run.finished_at = finished
            run.duration_ms = _elapsed_ms(run.started_at, finished)
            db.commit()

    def backfill_legacy_messages(self) -> dict[str, int]:
        """Idempotently convert historical assistant metrics into ledger facts."""
        created = skipped = 0
        with self._session_factory() as db:
            rows = db.query(DBChatMessage, DBSession).join(
                DBSession, DBSession.id == DBChatMessage.session_id
            ).filter(DBChatMessage.role == "assistant", DBChatMessage.meta_data.isnot(None)).all()
            for message, session in rows:
                run_id = f"run_legacy_{message.id}"
                if db.query(DBUsageRun.id).filter(DBUsageRun.id == run_id).first():
                    skipped += 1
                    continue
                try:
                    metadata = json.loads(message.meta_data or "{}")
                except (TypeError, json.JSONDecodeError):
                    skipped += 1
                    continue
                if not isinstance(metadata, dict):
                    skipped += 1
                    continue
                if metadata.get("usage_run_id"):
                    skipped += 1
                    continue
                input_tokens = metadata.get("input_tokens")
                output_tokens = metadata.get("output_tokens")
                if input_tokens is None and output_tokens is None:
                    skipped += 1
                    continue
                owner = (session.owner or "local").strip() or "local"
                started = message.timestamp or _now()
                response_seconds = metadata.get("response_time")
                try:
                    duration_ms = round(float(response_seconds) * 1000) if response_seconds is not None else None
                except (TypeError, ValueError):
                    duration_ms = None
                kind = "agent" if metadata.get("tool_events") or metadata.get("round_usage") else "chat"
                db.add(DBUsageRun(
                    id=run_id, owner=owner, kind=kind, status="succeeded",
                    source_surface="web", session_id=session.id, started_at=started,
                    finished_at=started + timedelta(milliseconds=duration_ms or 0),
                    duration_ms=duration_ms, attributes_json={"legacy_message_id": message.id},
                ))
                db.flush()
                turn_id = f"span_legacy_turn_{message.id}"
                model_id = f"span_legacy_model_{message.id}"
                db.add(DBUsageSpan(
                    id=turn_id, run_id=run_id, owner=owner, kind="turn", name=f"{kind}.turn",
                    status="succeeded", sequence=1, started_at=started,
                    finished_at=started + timedelta(milliseconds=duration_ms or 0), duration_ms=duration_ms,
                    attributes_json={"legacy": True},
                ))
                db.flush()
                db.add(DBUsageSpan(
                    id=model_id, run_id=run_id, parent_span_id=turn_id, owner=owner,
                    kind="model", name="model.generate", status="succeeded", sequence=2,
                    started_at=started, finished_at=started + timedelta(milliseconds=duration_ms or 0),
                    duration_ms=duration_ms, requested_model=metadata.get("requested_model") or session.model,
                    actual_model=metadata.get("model") or session.model, attributes_json={"legacy": True},
                ))
                db.flush()
                db.add(DBUsageObservation(
                    id=f"obs_legacy_{message.id}", run_id=run_id, span_id=model_id, owner=owner,
                    observed_at=started, sequence=1, is_final=True, reconciliation_version=1,
                    source="estimated", input_tokens=input_tokens, output_tokens=output_tokens,
                    reasoning_tokens=metadata.get("reasoning_tokens"),
                    cache_read_tokens=metadata.get("cache_read_tokens"),
                    cache_write_tokens=metadata.get("cache_write_tokens"),
                    fresh_input_tokens=metadata.get("fresh_input_tokens"), currency="USD",
                    raw_usage_json={"adapter_version": "legacy-message-v1"},
                ))
                for index, event in enumerate(metadata.get("tool_events") or [], 3):
                    if not isinstance(event, dict):
                        continue
                    db.add(DBUsageSpan(
                        id=f"span_legacy_tool_{message.id}_{index}", run_id=run_id,
                        parent_span_id=turn_id, owner=owner, kind="tool",
                        name=f"tool.{event.get('tool') or 'unknown'}", status="failed" if event.get("exit_code") not in (None, 0) else "succeeded",
                        sequence=index, agent_round=event.get("round"), started_at=started,
                        finished_at=started, duration_ms=None, tool_name=event.get("tool"),
                        attributes_json={"legacy": True},
                    ))
                metadata["usage_run_id"] = run_id
                message.meta_data = json.dumps(metadata)
                created += 1
            db.commit()
        return {"created": created, "skipped": skipped}

    def rebuild_daily_rollups(self, *, owner: str | None = None) -> int:
        """Rebuild rollups from authoritative latest final observations."""
        with self._session_factory() as db:
            if owner:
                db.query(DBUsageDailyRollup).filter(DBUsageDailyRollup.owner == owner).delete()
            else:
                db.query(DBUsageDailyRollup).delete()
            query = db.query(DBUsageObservation, DBUsageSpan, DBUsageRun).join(
                DBUsageSpan, DBUsageSpan.id == DBUsageObservation.span_id
            ).join(DBUsageRun, DBUsageRun.id == DBUsageObservation.run_id).filter(
                DBUsageObservation.is_final.is_(True)
            )
            if owner:
                query = query.filter(DBUsageObservation.owner == owner)
            joined = query.all()
            latest_ids = {row.id for row in self._latest_final([item[0] for item in joined])}
            groups: dict[tuple, list[tuple[Any, Any, Any]]] = {}
            for observation, span, run in joined:
                if observation.id not in latest_ids:
                    continue
                day = observation.observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
                key = (run.owner, day, run.kind, span.provider or "unknown", span.actual_model or "unknown", run.source_surface)
                groups.setdefault(key, []).append((observation, span, run))
            for key, group in groups.items():
                observations = [item[0] for item in group]
                runs = {item[2].id: item[2] for item in group}
                durations = [run.duration_ms for run in runs.values() if run.duration_ms is not None]
                db.add(DBUsageDailyRollup(
                    id=_id("rollup"), owner=key[0], date=key[1], kind=key[2], provider=key[3],
                    actual_model=key[4], source_surface=key[5],
                    input_tokens=sum(row.input_tokens or 0 for row in observations),
                    output_tokens=sum(row.output_tokens or 0 for row in observations),
                    reasoning_tokens=self._sum_nullable(observations, "reasoning_tokens"),
                    cache_read_tokens=self._sum_nullable(observations, "cache_read_tokens"),
                    cache_write_tokens=self._sum_nullable(observations, "cache_write_tokens"),
                    fresh_input_tokens=self._sum_nullable(observations, "fresh_input_tokens"),
                    total_cost_micros=self._sum_nullable(observations, "total_cost_micros"),
                    run_count=len(runs), failed_run_count=sum(run.status == "failed" for run in runs.values()),
                    duration_total_ms=sum(durations), duration_max_ms=max(durations, default=0),
                ))
            db.commit()
            return len(groups)

    def delete_usage(self, *, owner: str, run_id: str | None = None, before: datetime | None = None, all_usage: bool = False) -> int:
        """Explicit owner-scoped deletion; session deletion never calls this."""
        if not (run_id or before or all_usage):
            raise ValueError("usage deletion requires run_id, before, or all_usage")
        with self._session_factory() as db:
            query = db.query(DBUsageRun).filter(DBUsageRun.owner == owner)
            if run_id:
                query = query.filter(DBUsageRun.id == run_id)
            if before:
                query = query.filter(DBUsageRun.started_at < before)
            count = query.count()
            query.delete(synchronize_session=False)
            db.query(DBUsageDailyRollup).filter(DBUsageDailyRollup.owner == owner).delete()
            db.commit()
            return count

    def apply_retention(self, *, days: int) -> int:
        """Apply explicit opt-in raw-fact retention across owners."""
        if days <= 0:
            return 0
        cutoff = _now() - timedelta(days=days)
        self.rebuild_daily_rollups()
        with self._session_factory() as db:
            query = db.query(DBUsageRun).filter(DBUsageRun.started_at < cutoff)
            count = query.count()
            query.delete(synchronize_session=False)
            db.commit()
            return count

    def session_totals(self, *, owner: str) -> dict[str, dict[str, int | None]]:
        """Project authoritative tokens/cost by durable Session."""
        with self._session_factory() as db:
            rows = db.query(DBUsageObservation, DBUsageRun).join(
                DBUsageRun, DBUsageRun.id == DBUsageObservation.run_id
            ).filter(DBUsageObservation.owner == owner, DBUsageObservation.is_final.is_(True), DBUsageRun.session_id.isnot(None)).all()
            latest_ids = {row.id for row in self._latest_final([item[0] for item in rows])}
            grouped: dict[str, list[Any]] = {}
            for observation, run in rows:
                if observation.id in latest_ids:
                    grouped.setdefault(run.session_id, []).append(observation)
        return {session_id: {
            "input_tokens": sum(row.input_tokens or 0 for row in observations),
            "output_tokens": sum(row.output_tokens or 0 for row in observations),
            "total_tokens": sum((row.input_tokens or 0) + (row.output_tokens or 0) for row in observations),
            "total_cost_micros": self._sum_nullable(observations, "total_cost_micros"),
        } for session_id, observations in grouped.items()}

    @staticmethod
    def _sum_nullable(rows: list[Any], name: str) -> int | None:
        values = [getattr(row, name) for row in rows if getattr(row, name) is not None]
        return sum(values) if values else None

    def _final_rows(self, db, owner: str, start: datetime | None = None, end: datetime | None = None):
        query = db.query(DBUsageObservation).filter(
            DBUsageObservation.owner == owner, DBUsageObservation.is_final.is_(True)
        )
        if start:
            query = query.filter(DBUsageObservation.observed_at >= start)
        if end:
            query = query.filter(DBUsageObservation.observed_at < end)
        return self._latest_final(query.all())

    def _filtered_joined(
        self, db, *, owner: str, start: datetime | None = None, end: datetime | None = None,
        session_id: str | None = None, run_id: str | None = None, kind: str | None = None,
        surface: str | None = None, provider: str | None = None, model: str | None = None,
        tool: str | None = None, status: str | None = None, usage_source: str | None = None,
        cache_status: str | None = None,
    ) -> list[tuple[Any, Any, Any]]:
        query = db.query(DBUsageObservation, DBUsageSpan, DBUsageRun).join(
            DBUsageSpan, DBUsageSpan.id == DBUsageObservation.span_id
        ).join(DBUsageRun, DBUsageRun.id == DBUsageObservation.run_id).filter(
            DBUsageObservation.owner == owner, DBUsageObservation.is_final.is_(True)
        )
        if start: query = query.filter(DBUsageObservation.observed_at >= start)
        if end: query = query.filter(DBUsageObservation.observed_at < end)
        if session_id: query = query.filter(DBUsageRun.session_id == session_id)
        if run_id: query = query.filter(DBUsageRun.id == run_id)
        if kind: query = query.filter(DBUsageRun.kind == kind)
        if surface: query = query.filter(DBUsageRun.source_surface == surface)
        if provider: query = query.filter(DBUsageSpan.provider == provider)
        if model: query = query.filter(DBUsageSpan.actual_model == model)
        if tool:
            tool_runs = db.query(DBUsageSpan.run_id).filter(DBUsageSpan.tool_name == tool).subquery()
            query = query.filter(DBUsageRun.id.in_(tool_runs))
        if status: query = query.filter(DBUsageRun.status == status)
        if usage_source: query = query.filter(DBUsageObservation.source == usage_source)
        if cache_status == "hit": query = query.filter(DBUsageObservation.cache_read_tokens > 0)
        if cache_status == "miss": query = query.filter(DBUsageObservation.cache_read_tokens == 0)
        if cache_status == "unknown": query = query.filter(DBUsageObservation.cache_read_tokens.is_(None))
        joined = query.all()
        latest_ids = {row.id for row in self._latest_final([item[0] for item in joined])}
        return [item for item in joined if item[0].id in latest_ids]

    @staticmethod
    def _latest_final(rows: list[Any]) -> list[Any]:
        latest: dict[str, Any] = {}
        for row in rows:
            current = latest.get(row.span_id)
            if current is None or row.reconciliation_version > current.reconciliation_version:
                latest[row.span_id] = row
        return list(latest.values())

    def query_summary(self, *, owner: str, start: datetime | None = None, end: datetime | None = None, **filters) -> dict[str, Any]:
        with self._session_factory() as db:
            joined = self._filtered_joined(db, owner=owner, start=start, end=end, **filters)
            rows = [item[0] for item in joined]
            run_query = db.query(DBUsageRun).filter(DBUsageRun.owner == owner)
            if start: run_query = run_query.filter(DBUsageRun.started_at >= start)
            if end: run_query = run_query.filter(DBUsageRun.started_at < end)
            if filters.get("session_id"): run_query = run_query.filter(DBUsageRun.session_id == filters["session_id"])
            if filters.get("run_id"): run_query = run_query.filter(DBUsageRun.id == filters["run_id"])
            if filters.get("kind"): run_query = run_query.filter(DBUsageRun.kind == filters["kind"])
            if filters.get("surface"): run_query = run_query.filter(DBUsageRun.source_surface == filters["surface"])
            if filters.get("status"): run_query = run_query.filter(DBUsageRun.status == filters["status"])
            observation_filters = any(filters.get(key) for key in ("provider", "model", "tool", "usage_source", "cache_status"))
            if observation_filters:
                matching_ids = {item[2].id for item in joined}
                runs = run_query.filter(DBUsageRun.id.in_(matching_ids)).all() if matching_ids else []
            else:
                runs = run_query.all()
            price_ids = {row.price_snapshot_id for row in rows if row.price_snapshot_id}
            prices = {row.id: row for row in db.query(DBUsagePriceSnapshot).filter(DBUsagePriceSnapshot.id.in_(price_ids)).all()} if price_ids else {}
            cache_savings = 0
            has_savings = False
            for row in rows:
                price = prices.get(row.price_snapshot_id)
                if price and row.cache_read_tokens is not None and price.input_per_million_micros is not None and price.cache_read_per_million_micros is not None:
                    cache_savings += self._meter(row.cache_read_tokens, price.input_per_million_micros - price.cache_read_per_million_micros) or 0
                    has_savings = True
        sources = {row.source for row in rows}
        quality = "none" if not sources else ("estimated" if sources == {"estimated"} else ("exact" if "estimated" not in sources else "mixed"))
        return {
            "schema_version": 1, "generated_at": _now().isoformat() + "Z",
            "quality": quality, "currency": "USD",
            "totals": {
                "input_tokens": sum(row.input_tokens or 0 for row in rows),
                "output_tokens": sum(row.output_tokens or 0 for row in rows),
                "reasoning_tokens": self._sum_nullable(rows, "reasoning_tokens"),
                "cache_read_tokens": self._sum_nullable(rows, "cache_read_tokens"),
                "cache_write_tokens": self._sum_nullable(rows, "cache_write_tokens"),
                "fresh_input_tokens": self._sum_nullable(rows, "fresh_input_tokens"),
                "total_cost_micros": self._sum_nullable(rows, "total_cost_micros"),
                "cache_savings_micros": cache_savings if has_savings else None,
                "cache_hit_runs": len({item[2].id for item in joined if (item[0].cache_read_tokens or 0) > 0}),
                "cache_unknown_runs": len({item[2].id for item in joined if item[0].cache_read_tokens is None}),
                "runs": len(runs), "failed_runs": sum(run.status == "failed" for run in runs),
                "duration_median_ms": self._percentile([run.duration_ms for run in runs if run.duration_ms is not None], 0.5),
                "duration_p95_ms": self._percentile([run.duration_ms for run in runs if run.duration_ms is not None], 0.95),
                "near_context_limit_runs": len({item[2].id for item in joined if item[0].context_tokens is not None and item[0].context_length and item[0].context_tokens / item[0].context_length >= 0.8}),
                "generation_tps_median": self._percentile([row.generation_tps_milli for row in rows if row.generation_tps_milli is not None], 0.5) / 1000 if any(row.generation_tps_milli is not None for row in rows) else None,
            },
            "filters": {key: value for key, value in {"from": start.isoformat() if start else None, "to": end.isoformat() if end else None, **filters}.items() if value is not None},
        }

    @staticmethod
    def _percentile(values: list[int], fraction: float) -> int | None:
        if not values:
            return None
        ordered = sorted(values)
        return ordered[min(round((len(ordered) - 1) * fraction), len(ordered) - 1)]

    def query_timeseries(self, *, owner: str, start: datetime | None = None, end: datetime | None = None, bucket: str = "day", timezone_name: str = "UTC", **filters) -> dict[str, Any]:
        if bucket not in {"hour", "day", "week"}:
            raise ValueError("bucket must be hour, day, or week")
        try:
            timezone_value = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown timezone") from exc
        with self._session_factory() as db:
            joined = self._filtered_joined(db, owner=owner, start=start, end=end, **filters)
            rows = [item[0] for item in joined]
        points: dict[datetime, list[Any]] = {}
        joined_by_observation = {item[0].id: item for item in joined}
        for row in rows:
            dt = row.observed_at.replace(tzinfo=timezone.utc).astimezone(timezone_value)
            if bucket == "hour":
                key = dt.replace(minute=0, second=0, microsecond=0)
            elif bucket == "week":
                day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
                key = day - timedelta(days=day.weekday())
            else:
                key = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            points.setdefault(key, []).append(row)
        rendered = []
        for key, group in sorted(points.items()):
            runs = {joined_by_observation[row.id][2].id: joined_by_observation[row.id][2] for row in group}
            durations = [run.duration_ms for run in runs.values() if run.duration_ms is not None]
            rendered.append({
                "time": key.isoformat(),
                "input_tokens": sum(row.input_tokens or 0 for row in group),
                "output_tokens": sum(row.output_tokens or 0 for row in group),
                "cache_read_tokens": self._sum_nullable(group, "cache_read_tokens"),
                "cache_write_tokens": self._sum_nullable(group, "cache_write_tokens"),
                "fresh_input_tokens": self._sum_nullable(group, "fresh_input_tokens"),
                "total_cost_micros": self._sum_nullable(group, "total_cost_micros"),
                "runs": len(runs),
                "duration_p95_ms": self._percentile(durations, 0.95),
            })
        return {"bucket": bucket, "timezone": timezone_name, "points": rendered}

    def query_breakdown(self, *, owner: str, group_by: str = "model", start: datetime | None = None, end: datetime | None = None, **filters) -> dict[str, Any]:
        dimensions = {
            "model": DBUsageSpan.actual_model, "provider": DBUsageSpan.provider,
            "kind": DBUsageRun.kind, "surface": DBUsageRun.source_surface,
            "session": DBUsageRun.session_id, "tool": DBUsageSpan.tool_name,
        }
        if group_by not in dimensions:
            raise ValueError("unsupported breakdown dimension")
        if group_by == "tool":
            with self._session_factory() as db:
                allowed_run_ids = None
                if filters:
                    allowed_run_ids = {item[2].id for item in self._filtered_joined(db, owner=owner, start=start, end=end, **{k: v for k, v in filters.items() if k != "tool"})}
                query = db.query(DBUsageSpan).filter(
                    DBUsageSpan.owner == owner, DBUsageSpan.kind == "tool"
                )
                if start:
                    query = query.filter(DBUsageSpan.started_at >= start)
                if end:
                    query = query.filter(DBUsageSpan.started_at < end)
                if allowed_run_ids is not None:
                    query = query.filter(DBUsageSpan.run_id.in_(allowed_run_ids))
                if filters.get("tool"):
                    query = query.filter(DBUsageSpan.tool_name == filters["tool"])
                spans = query.all()
            groups: dict[str, list[Any]] = {}
            for span in spans:
                groups.setdefault(span.tool_name or "unknown", []).append(span)
            items = [{
                "key": key, "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": None, "cache_write_tokens": None,
                "total_cost_micros": None, "runs": len({span.run_id for span in group}),
                "invocations": len(group),
                "failed": sum(span.status == "failed" for span in group),
                "duration_ms": sum(span.duration_ms or 0 for span in group),
            } for key, group in groups.items()]
            items.sort(key=lambda item: item["invocations"], reverse=True)
            return {"group_by": group_by, "items": items}
        with self._session_factory() as db:
            rows = self._filtered_joined(db, owner=owner, start=start, end=end, **filters)
        groups: dict[str, list[tuple[Any, Any, Any]]] = {}
        for observation, span, run in rows:
            source = span if group_by in {"model", "provider", "tool"} else run
            attr = {"model": "actual_model", "provider": "provider", "kind": "kind", "surface": "source_surface", "session": "session_id", "tool": "tool_name"}[group_by]
            groups.setdefault(str(getattr(source, attr) or "unknown"), []).append((observation, span, run))
        items = []
        for key, group in groups.items():
            observations = [item[0] for item in group]
            items.append({
                "key": key, "input_tokens": sum(row.input_tokens or 0 for row in observations),
                "output_tokens": sum(row.output_tokens or 0 for row in observations),
                "cache_read_tokens": self._sum_nullable(observations, "cache_read_tokens"),
                "cache_write_tokens": self._sum_nullable(observations, "cache_write_tokens"),
                "total_cost_micros": self._sum_nullable(observations, "total_cost_micros"),
                "runs": len({item[2].id for item in group}),
            })
        items.sort(key=lambda item: item["input_tokens"] + item["output_tokens"], reverse=True)
        return {"group_by": group_by, "items": items}

    def list_runs(self, *, owner: str, start: datetime | None = None, end: datetime | None = None, limit: int = 100, offset: int = 0, **filters) -> dict[str, Any]:
        with self._session_factory() as db:
            query = db.query(DBUsageRun).filter(DBUsageRun.owner == owner)
            if start:
                query = query.filter(DBUsageRun.started_at >= start)
            if end:
                query = query.filter(DBUsageRun.started_at < end)
            if filters.get("session_id"): query = query.filter(DBUsageRun.session_id == filters["session_id"])
            if filters.get("run_id"): query = query.filter(DBUsageRun.id == filters["run_id"])
            if filters.get("kind"): query = query.filter(DBUsageRun.kind == filters["kind"])
            if filters.get("surface"): query = query.filter(DBUsageRun.source_surface == filters["surface"])
            if filters.get("status"): query = query.filter(DBUsageRun.status == filters["status"])
            span_filters = []
            if filters.get("provider"): span_filters.append(DBUsageSpan.provider == filters["provider"])
            if filters.get("model"): span_filters.append(DBUsageSpan.actual_model == filters["model"])
            if filters.get("tool"): span_filters.append(DBUsageSpan.tool_name == filters["tool"])
            if span_filters:
                matching_runs = db.query(DBUsageSpan.run_id).filter(*span_filters)
                query = query.filter(DBUsageRun.id.in_(matching_runs))
            if filters.get("usage_source") or filters.get("cache_status"):
                obs_query = db.query(DBUsageObservation.run_id).filter(DBUsageObservation.is_final.is_(True))
                if filters.get("usage_source"): obs_query = obs_query.filter(DBUsageObservation.source == filters["usage_source"])
                if filters.get("cache_status") == "hit": obs_query = obs_query.filter(DBUsageObservation.cache_read_tokens > 0)
                if filters.get("cache_status") == "miss": obs_query = obs_query.filter(DBUsageObservation.cache_read_tokens == 0)
                if filters.get("cache_status") == "unknown": obs_query = obs_query.filter(DBUsageObservation.cache_read_tokens.is_(None))
                query = query.filter(DBUsageRun.id.in_(obs_query))
            total = query.count()
            runs = query.order_by(DBUsageRun.started_at.desc()).offset(offset).limit(min(max(limit, 1), 500)).all()
            results = []
            for run in runs:
                observations = self._latest_final(db.query(DBUsageObservation).filter(
                    DBUsageObservation.run_id == run.id, DBUsageObservation.is_final.is_(True)
                ).all())
                results.append(self._serialize_run(run, observations))
        return {"total": total, "limit": limit, "offset": offset, "runs": results}

    def get_run(self, *, owner: str, run_id: str) -> dict[str, Any] | None:
        with self._session_factory() as db:
            run = db.query(DBUsageRun).filter(DBUsageRun.id == run_id, DBUsageRun.owner == owner).first()
            if not run:
                return None
            spans = db.query(DBUsageSpan).filter(DBUsageSpan.run_id == run_id).order_by(DBUsageSpan.sequence).all()
            observations = db.query(DBUsageObservation).filter(DBUsageObservation.run_id == run_id).order_by(DBUsageObservation.sequence).all()
            by_span = {span.id: [] for span in spans}
            for observation in observations:
                by_span.setdefault(observation.span_id, []).append(observation)
            result = self._serialize_run(run, [row for row in observations if row.is_final])
            result["spans"] = [self._serialize_span(span, by_span.get(span.id, [])) for span in spans]
            return result

    def query_anomalies(self, *, owner: str, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
        runs = self.list_runs(owner=owner, start=start, end=end, limit=500)["runs"]
        durations = [run["duration_ms"] for run in runs if run["duration_ms"] is not None]
        costs = [run["total_cost_micros"] for run in runs if run["total_cost_micros"] is not None]
        duration_threshold = self._percentile(durations, 0.95)
        cost_threshold = self._percentile(costs, 0.95)
        anomalies = []
        for run in runs:
            reasons = []
            if duration_threshold is not None and run["duration_ms"] is not None and run["duration_ms"] >= duration_threshold and len(durations) >= 5:
                reasons.append("duration_p95")
            if cost_threshold is not None and run["total_cost_micros"] is not None and run["total_cost_micros"] >= cost_threshold and len(costs) >= 5:
                reasons.append("cost_p95")
            if run["status"] in {"failed", "interrupted"}:
                reasons.append(run["status"])
            if reasons:
                anomalies.append({"run_id": run["id"], "started_at": run["started_at"], "reasons": reasons, "duration_ms": run["duration_ms"], "total_cost_micros": run["total_cost_micros"]})
        return {"thresholds": {"duration_p95_ms": duration_threshold, "cost_p95_micros": cost_threshold}, "anomalies": anomalies}

    def export(self, *, owner: str, format: str = "jsonl", **filters) -> Iterator[bytes]:
        import json
        runs = []
        offset = 0
        while True:
            page = self.list_runs(owner=owner, limit=500, offset=offset, **filters)
            batch = page["runs"]
            runs.extend(batch)
            offset += len(batch)
            if not batch or offset >= page["total"]:
                break
        if format == "jsonl":
            for run in runs:
                yield (json.dumps(run, separators=(",", ":")) + "\n").encode()
            return
        if format != "csv":
            raise ValueError("format must be jsonl or csv")
        output = io.StringIO()
        fields = ["id", "kind", "status", "source_surface", "session_id", "started_at", "duration_ms", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_cost_micros"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            writer.writerow({key: run.get(key) for key in fields})
        yield output.getvalue().encode()

    def _serialize_run(self, run, observations) -> dict[str, Any]:
        return {
            "id": run.id, "kind": run.kind, "status": run.status,
            "source_surface": run.source_surface, "session_id": run.session_id,
            "started_at": run.started_at.isoformat() + "Z", "finished_at": run.finished_at.isoformat() + "Z" if run.finished_at else None,
            "duration_ms": run.duration_ms,
            "input_tokens": sum(row.input_tokens or 0 for row in observations),
            "output_tokens": sum(row.output_tokens or 0 for row in observations),
            "cache_read_tokens": self._sum_nullable(observations, "cache_read_tokens"),
            "cache_write_tokens": self._sum_nullable(observations, "cache_write_tokens"),
            "total_cost_micros": self._sum_nullable(observations, "total_cost_micros"),
        }

    def _serialize_span(self, span, observations) -> dict[str, Any]:
        final = next((row for row in reversed(observations) if row.is_final), None)
        usage = None if final is None else {
            "source": final.source, "input_tokens": final.input_tokens,
            "output_tokens": final.output_tokens, "reasoning_tokens": final.reasoning_tokens,
            "cache_read_tokens": final.cache_read_tokens, "cache_write_tokens": final.cache_write_tokens,
            "fresh_input_tokens": final.fresh_input_tokens, "total_cost_micros": final.total_cost_micros,
            "cost_source": final.cost_source,
            "context_tokens": final.context_tokens, "context_length": final.context_length,
            "time_to_first_token_ms": final.time_to_first_token_ms,
            "generation_tps": final.generation_tps_milli / 1000 if final.generation_tps_milli is not None else None,
            "prefill_tps": final.prefill_tps_milli / 1000 if final.prefill_tps_milli is not None else None,
        }
        return {
            "id": span.id, "parent_span_id": span.parent_span_id, "kind": span.kind,
            "name": span.name, "status": span.status, "sequence": span.sequence,
            "agent_round": span.agent_round, "provider": span.provider,
            "requested_model": span.requested_model, "actual_model": span.actual_model,
            "tool_name": span.tool_name, "started_at": span.started_at.isoformat() + "Z",
            "finished_at": span.finished_at.isoformat() + "Z" if span.finished_at else None,
            "duration_ms": span.duration_ms, "outcome_code": span.outcome_code,
            "attributes": span.attributes_json or {}, "usage": usage,
        }


usage_store = UsageStore()


def create_in_memory_usage_store() -> UsageStore:
    """Create an isolated in-memory adapter for interface-level tests/tools."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from core.database import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return UsageStore(sessionmaker(bind=engine))


def record_image_generation(
    *, owner: str, session_id: str | None, model: str, quality: str | None,
    size: str | None, succeeded: bool, duration_ms: int, incognito: bool = False,
) -> str | None:
    run = usage_store.begin_run(RunContext(
        owner=owner or "local", kind="image", source_surface="web",
        session_id=session_id, incognito=incognito,
    ))
    if not run.id:
        return None
    span = run.begin_span(SpanContext(
        kind="model", name="image.generate", requested_model=model,
        actual_model=model, attributes={"quality": quality or "medium", "size": size or "1024x1024"},
    ))
    key = (model, (quality or "medium").lower(), size or "1024x1024")
    usd = IMAGE_PRICE_USD.get(key)
    span.record_usage(UsageObservation(
        source="provider", image_output_units=1,
        other_cost_micros=round(usd * 1_000_000) if usd is not None else None,
        total_cost_micros=round(usd * 1_000_000) if usd is not None else None,
        cost_source="price_catalog" if usd is not None else None,
    ))
    status = "succeeded" if succeeded else "failed"
    span.finish(SpanOutcome(status=status, duration_ms=duration_ms))
    run.finish(RunOutcome(status=status, duration_ms=duration_ms))
    return run.id


def record_completed_turn(
    *, owner: str, session_id: str | None, kind: str, source_surface: str,
    metrics: dict[str, Any], incognito: bool = False,
) -> str | None:
    """Persist a completed chat/agent turn from its normalized metrics.

    This compatibility ingestion path lets existing runtimes adopt the ledger
    without learning its ORM or projection rules. Rich runtimes may provide
    ``round_usage`` and ``tool_events`` for per-activity spans.
    """
    run = usage_store.begin_run(RunContext(
        owner=owner or "local", kind=kind, source_surface=source_surface,
        session_id=session_id, incognito=incognito,
    ))
    if not run.id:
        return None
    turn = run.begin_span(SpanContext(kind="turn", name=f"{kind}.turn"))
    for timing_name, seconds in (metrics.get("agent_prep_breakdown") or {}).items():
        try:
            started = _now() - timedelta(seconds=float(seconds))
        except (TypeError, ValueError):
            continue
        prep = run.begin_span(SpanContext(
            kind="preparation", name=f"agent.{timing_name}",
            parent_span_id=turn.id, started_at=started,
        ))
        prep.finish(SpanOutcome(duration_ms=round(float(seconds) * 1000)))

    round_usage = metrics.get("round_usage") or []
    if not round_usage:
        round_usage = [{
            key: metrics.get(key) for key in (
                "input_tokens", "output_tokens", "reasoning_tokens",
                "cache_read_tokens", "cache_write_tokens", "fresh_input_tokens",
            )
        }]
    for index, item in enumerate(round_usage, 1):
        if not isinstance(item, dict):
            continue
        model = run.begin_span(SpanContext(
            kind="model", name="model.generate", parent_span_id=turn.id,
            agent_round=int(item.get("round") or index),
            provider=item.get("provider") or metrics.get("provider"),
            requested_model=item.get("requested_model") or metrics.get("requested_model"),
            actual_model=item.get("model") or metrics.get("model"),
        ))
        source = item.get("usage_source") or metrics.get("usage_source") or "estimated"
        if source == "real":
            source = "provider"
        model.record_usage(UsageObservation(
            source=source,
            input_tokens=item.get("input_tokens"), output_tokens=item.get("output_tokens"),
            reasoning_tokens=item.get("reasoning_tokens"),
            cache_read_tokens=item.get("cache_read_tokens"),
            cache_write_tokens=item.get("cache_write_tokens"),
            fresh_input_tokens=item.get("fresh_input_tokens"),
            context_tokens=item.get("context_tokens") or item.get("input_tokens"),
            context_length=item.get("context_length") or metrics.get("context_length"),
            time_to_first_token_ms=round(float(item.get("time_to_first_token") or metrics.get("time_to_first_token")) * 1000) if (item.get("time_to_first_token") is not None or metrics.get("time_to_first_token") is not None) else None,
            generation_tps_milli=round(float(item.get("gen_tps") or metrics.get("tokens_per_second")) * 1000) if (item.get("gen_tps") is not None or metrics.get("tokens_per_second") is not None) else None,
            prefill_tps_milli=round(float(item.get("prefill_tps")) * 1000) if item.get("prefill_tps") is not None else None,
            total_cost_micros=item.get("total_cost_micros"),
            cost_source=item.get("cost_source"), raw_usage=item.get("raw_usage") or {},
        ))
        model.finish(SpanOutcome(timing_known=False))

    for event in metrics.get("tool_events") or []:
        if not isinstance(event, dict):
            continue
        tool = run.begin_span(SpanContext(
            kind="tool", name=f"tool.{event.get('tool') or 'unknown'}",
            parent_span_id=turn.id, agent_round=event.get("round"),
            tool_name=event.get("tool"),
            attributes={"exit_code": event.get("exit_code")},
        ))
        tool.finish(SpanOutcome(status="failed" if event.get("exit_code") not in (None, 0) else "succeeded", timing_known=False))
    _turn_ms = metrics.get("response_time")
    turn.finish(SpanOutcome(duration_ms=round(float(_turn_ms) * 1000) if _turn_ms is not None else None, timing_known=_turn_ms is not None))
    run.finish(RunOutcome(
        duration_ms=round(float(_turn_ms) * 1000) if _turn_ms is not None else None,
        timing_known=_turn_ms is not None,
    ))
    return run.id
