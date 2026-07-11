"""Authoritative subscription quota snapshots and workload reconciliation."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from core.database import (
    ProviderAuthSession,
    ModelEndpoint,
    SessionLocal,
    SubscriptionAccount,
    SubscriptionSnapshot,
    UsageObservation,
    UsageRun,
    UsageSpan,
)
from src.chatgpt_subscription import (
    CHATGPT_SUBSCRIPTION_PROVIDER,
    _decode_jwt_payload,
    chatgpt_headers,
    resolve_runtime_credentials,
)

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _reset_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        return None


def _window(payload: dict, key: str) -> dict | None:
    limit = payload.get("rate_limit") if isinstance(payload.get("rate_limit"), dict) else {}
    value = limit.get(f"{key}_window")
    if not isinstance(value, dict):
        return None
    percent = value.get("used_percent")
    try:
        percent_value = float(percent) if percent is not None else None
    except (TypeError, ValueError):
        return None
    if percent_value is not None and not 0 <= percent_value <= 100:
        return None
    return {
        "key": key,
        "window_minutes": round(float(value["limit_window_seconds"]) / 60) if value.get("limit_window_seconds") is not None else None,
        "used_basis_points": round(percent_value * 100) if percent_value is not None else None,
        "resets_at": _reset_time(value.get("reset_at")),
    }


def provider_auth_id_for_endpoint(owner: str, endpoint_url: str) -> str | None:
    base = (endpoint_url or "").rstrip("/")
    with SessionLocal() as db:
        rows = db.query(ModelEndpoint).filter(
            ModelEndpoint.provider_auth_id.isnot(None),
            (ModelEndpoint.owner == owner) | (ModelEndpoint.owner.is_(None)),
        ).all()
        matches = []
        for row in rows:
            candidate = (row.base_url or "").rstrip("/")
            if candidate and (base == candidate or base.startswith(candidate + "/")):
                matches.append(row.provider_auth_id)
    unique = {value for value in matches if value}
    return next(iter(unique)) if len(unique) == 1 else None


class SubscriptionUsageStore:
    def __init__(self, session_factory=SessionLocal):
        self._session_factory = session_factory

    def refresh(self, *, owner: str, timeout: float = 15.0) -> dict:
        with self._session_factory() as db:
            auth_rows = db.query(ProviderAuthSession).filter(
                ProviderAuthSession.owner == owner,
                ProviderAuthSession.provider == CHATGPT_SUBSCRIPTION_PROVIDER,
            ).all()
            auth_ids = [row.id for row in auth_rows]
        if not auth_ids:
            result = self.query(owner=owner)
            result.update({"refreshed": 0, "errors": ["No ChatGPT Subscription account is connected."]})
            return result

        refreshed = 0
        errors: list[str] = []
        for auth_id in auth_ids:
            try:
                credentials = resolve_runtime_credentials(auth_id, owner=owner)
                token = credentials["api_key"]
                claims = _decode_jwt_payload(token)
                auth_claims = claims.get("https://api.openai.com/auth")
                if not isinstance(auth_claims, dict):
                    auth_claims = {}
                account_key = str(
                    claims.get("chatgpt_account_id")
                    or auth_claims.get("chatgpt_account_id")
                    or auth_id
                )
                headers = chatgpt_headers(token)
                if account_key != auth_id:
                    headers["ChatGPT-Account-Id"] = account_key
                response = httpx.get(USAGE_URL, headers=headers, timeout=timeout)
                response.raise_for_status()
                payload = response.json()
                self._record(owner=owner, auth_id=auth_id, account_key=account_key, payload=payload)
                refreshed += 1
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"{auth_id}: {type(exc).__name__}")
        result = self.query(owner=owner)
        result.update({"refreshed": refreshed, "errors": errors})
        return result

    def refresh_if_stale(self, *, owner: str, max_age_seconds: int = 300) -> dict:
        with self._session_factory() as db:
            auth_ids = {row.id for row in db.query(ProviderAuthSession.id).filter(
                ProviderAuthSession.owner == owner,
                ProviderAuthSession.provider == CHATGPT_SUBSCRIPTION_PROVIDER,
            ).all()}
            accounts = db.query(SubscriptionAccount).filter(SubscriptionAccount.owner == owner).all()
            latest_by_auth = {}
            for account in accounts:
                latest_by_auth[account.provider_auth_id] = db.query(SubscriptionSnapshot.observed_at).filter(
                    SubscriptionSnapshot.account_id == account.id
                ).order_by(SubscriptionSnapshot.observed_at.desc()).scalar()
        if any(auth_id not in latest_by_auth or latest_by_auth[auth_id] is None or (_now() - latest_by_auth[auth_id]).total_seconds() >= max_age_seconds for auth_id in auth_ids):
            return self.refresh(owner=owner)
        return self.query(owner=owner)

    def _record(self, *, owner: str, auth_id: str, account_key: str, payload: dict) -> None:
        observed = _now()
        account_hash = hashlib.sha256(account_key.encode()).hexdigest()
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self._session_factory() as db:
            account = db.query(SubscriptionAccount).filter(
                SubscriptionAccount.owner == owner,
                SubscriptionAccount.provider_auth_id == auth_id,
            ).first()
            if not account:
                account = SubscriptionAccount(
                    id=f"subacct_{uuid.uuid4().hex}", owner=owner,
                    provider=CHATGPT_SUBSCRIPTION_PROVIDER, provider_auth_id=auth_id,
                    account_key_hash=account_hash,
                )
                db.add(account)
            account.account_key_hash = account_hash
            account.plan = payload.get("plan_type")
            account.updated_at = observed
            for key in ("primary", "secondary"):
                window = _window(payload, key)
                if not window:
                    continue
                db.add(SubscriptionSnapshot(
                    id=f"subsnap_{uuid.uuid4().hex}", account_id=account.id, owner=owner,
                    window_key=window["key"], window_minutes=window["window_minutes"],
                    used_basis_points=window["used_basis_points"], resets_at=window["resets_at"],
                    observed_at=observed, source="oauth", payload_hash=payload_hash,
                ))
            additional = payload.get("additional_rate_limits")
            if isinstance(additional, list):
                for index, item in enumerate(additional):
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("limit_name") or item.get("name") or item.get("model") or index)
                    nested = item.get("rate_limit") if isinstance(item.get("rate_limit"), dict) else item
                    for key in ("primary", "secondary"):
                        window = _window({"rate_limit": nested}, key)
                        if window:
                            db.add(SubscriptionSnapshot(
                                id=f"subsnap_{uuid.uuid4().hex}", account_id=account.id, owner=owner,
                                window_key=f"additional:{name}:{key}", window_minutes=window["window_minutes"],
                                used_basis_points=window["used_basis_points"], resets_at=window["resets_at"],
                                observed_at=observed, source="oauth", payload_hash=payload_hash,
                            ))
            db.commit()

    def query(self, *, owner: str, limit: int = 200) -> dict:
        with self._session_factory() as db:
            accounts = db.query(SubscriptionAccount).filter(SubscriptionAccount.owner == owner).all()
            snapshots = db.query(SubscriptionSnapshot).filter(
                SubscriptionSnapshot.owner == owner
            ).order_by(SubscriptionSnapshot.observed_at.desc()).limit(limit).all()
            account_map = {account.id: account for account in accounts}
            latest: dict[tuple[str, str], SubscriptionSnapshot] = {}
            history: dict[tuple[str, str], list[SubscriptionSnapshot]] = {}
            for snapshot in snapshots:
                key = (snapshot.account_id, snapshot.window_key)
                latest.setdefault(key, snapshot)
                history.setdefault(key, []).append(snapshot)

            intervals = []
            for key, rows in history.items():
                ordered = list(reversed(rows))
                for previous, current in zip(ordered, ordered[1:]):
                    same_epoch = previous.resets_at == current.resets_at
                    delta = (current.used_basis_points - previous.used_basis_points) if same_epoch and current.used_basis_points is not None and previous.used_basis_points is not None else None
                    matching_spans = db.query(UsageSpan).filter(
                        UsageSpan.owner == owner,
                        UsageSpan.provider == CHATGPT_SUBSCRIPTION_PROVIDER,
                        UsageSpan.endpoint_id == account_map[key[0]].provider_auth_id,
                        UsageSpan.finished_at >= previous.observed_at,
                        UsageSpan.finished_at < current.observed_at,
                    ).all()
                    run_ids = list({span.run_id for span in matching_spans})
                    runs = db.query(UsageRun).filter(UsageRun.id.in_(run_ids)).all() if run_ids else []
                    span_ids = [span.id for span in matching_spans]
                    observations = db.query(UsageObservation).filter(
                        UsageObservation.owner == owner,
                        UsageObservation.span_id.in_(span_ids),
                        UsageObservation.is_final.is_(True),
                    ).all() if span_ids else []
                    if delta is None:
                        attribution, confidence, reason = "new_epoch", "none", "reset epoch changed"
                    elif delta < 0:
                        attribution, confidence, reason = "provider_correction", "low", "usage decreased without a reset change"
                    elif delta == 0:
                        attribution, confidence, reason = "no_detected_change", "high", "provider usage did not move"
                    elif runs:
                        attribution, confidence, reason = "mixed_or_unknown", "low", "account-wide change overlaps Odysseus activity"
                    else:
                        attribution, confidence, reason = "account_external_or_unattributed", "medium", "account changed without measured Odysseus activity; this includes Pi harness Codex and other clients"
                    intervals.append({
                        "account_id": key[0], "window_key": key[1],
                        "from": previous.observed_at.isoformat() + "Z", "to": current.observed_at.isoformat() + "Z",
                        "account_delta_basis_points": delta,
                        "odysseus_runs": len(runs),
                        "odysseus_input_tokens": sum(row.input_tokens or 0 for row in observations),
                        "odysseus_output_tokens": sum(row.output_tokens or 0 for row in observations),
                        "attribution": attribution, "confidence": confidence, "reason": reason,
                    })

        return {
            "schema_version": 1,
            "generated_at": _now().isoformat() + "Z",
            "consumer_scope": [
                {"key": "odysseus", "account_scope": "same_gpt_account", "measurement": "server_ledger"},
                {"key": "pi_harness_codex", "account_scope": "same_gpt_account", "measurement": "provider_total_or_linked_run"},
                {"key": "other_codex_clients", "account_scope": "same_gpt_account", "measurement": "provider_total_only"},
            ],
            "accounts": [{
                "id": account.id, "provider": account.provider, "label": account.label,
                "plan": account.plan,
                "windows": [{
                    "key": window_key,
                    "used_percent": snapshot.used_basis_points / 100 if snapshot.used_basis_points is not None else None,
                    "remaining_percent": (10000 - snapshot.used_basis_points) / 100 if snapshot.used_basis_points is not None else None,
                    "window_minutes": snapshot.window_minutes,
                    "resets_at": snapshot.resets_at.isoformat() + "Z" if snapshot.resets_at else None,
                    "observed_at": snapshot.observed_at.isoformat() + "Z",
                    "source": snapshot.source,
                } for (account_id, window_key), snapshot in latest.items() if account_id == account.id],
            } for account in accounts],
            "history": [{
                "account_id": snapshot.account_id, "window_key": snapshot.window_key,
                "observed_at": snapshot.observed_at.isoformat() + "Z",
                "used_percent": snapshot.used_basis_points / 100 if snapshot.used_basis_points is not None else None,
                "resets_at": snapshot.resets_at.isoformat() + "Z" if snapshot.resets_at else None,
            } for snapshot in reversed(snapshots)],
            "intervals": intervals[-100:],
        }


subscription_usage_store = SubscriptionUsageStore()
