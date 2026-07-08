"""Pairing and known-client primitives for remote access."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

import bcrypt

DEFAULT_INVITE_TTL_MINUTES = 30
MAX_INVITE_TTL_MINUTES = 24 * 60
INVITE_SECRET_PREFIX = "odpair_"
DEFAULT_CAPABILITIES = ("chat",)
ALLOWED_CAPABILITIES = {
    "chat",
    "models",
    "sessions",
    "notes:read",
    "tasks:read",
    "memory:read",
    "remote_support:read",
    "remote_support:control",
}
ALLOWED_CLIENT_TYPES = {
    "browser",
    "desktop",
    "cli",
    "agent",
    "mobile",
    "support",
    "automation",
    "display",
}


def now_utc() -> datetime:
    """Naive UTC timestamp matching the existing database convention."""
    from core.database import utcnow_naive

    return utcnow_naive()


def normalize_client_type(value: str | None) -> str:
    client_type = (value or "browser").strip().lower().replace(" ", "_")
    return client_type if client_type in ALLOWED_CLIENT_TYPES else "browser"


def normalize_capabilities(value: Any) -> list[str]:
    if value is None:
        requested = list(DEFAULT_CAPABILITIES)
    elif isinstance(value, str):
        requested = [part.strip() for part in value.replace(" ", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        requested = [str(part).strip() for part in value]
    else:
        requested = list(DEFAULT_CAPABILITIES)

    out: list[str] = []
    for capability in requested:
        if not capability:
            continue
        if capability not in ALLOWED_CAPABILITIES:
            continue
        if capability not in out:
            out.append(capability)
    return out or list(DEFAULT_CAPABILITIES)


def capabilities_to_storage(capabilities: Any) -> str:
    return ",".join(normalize_capabilities(capabilities))


def capabilities_from_storage(value: str | None) -> list[str]:
    return normalize_capabilities(value or "")


def normalize_ttl_minutes(value: Any) -> int:
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        ttl = DEFAULT_INVITE_TTL_MINUTES
    return max(1, min(ttl, MAX_INVITE_TTL_MINUTES))


def new_invite_secret() -> str:
    return INVITE_SECRET_PREFIX + secrets.token_urlsafe(32)


def hash_secret(raw_secret: str) -> str:
    return bcrypt.hashpw(raw_secret.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_secret(raw_secret: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(raw_secret.encode("utf-8"), stored_hash.encode("utf-8"))
    except (TypeError, ValueError):
        return False


def token_prefix(raw_secret: str) -> str:
    return raw_secret[:16]


def new_short_id() -> str:
    return str(uuid.uuid4())[:8]


def metadata_to_json(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    cleaned = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[key[:80]] = value
    return json.dumps(cleaned, separators=(",", ":")) if cleaned else None


def metadata_from_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def create_bearer_token(owner: str | None, name: str, capabilities: Any) -> tuple[str, str]:
    """Create the durable bearer credential for a registered remote client."""
    from core.database import ApiToken, get_db_session

    raw_token = "ody_" + secrets.token_urlsafe(32)
    token_hash = hash_secret(raw_token)
    token_id = new_short_id()
    scopes = capabilities_to_storage(capabilities)

    with get_db_session() as db:
        db.add(ApiToken(
            id=token_id,
            owner=owner,
            name=name[:100] or "remote client",
            token_hash=token_hash,
            token_prefix=raw_token[:8],
            scopes=scopes,
            is_active=True,
        ))
    return token_id, raw_token


def qr_png_data_uri(value: str) -> str | None:
    """Render a pairing string as a QR image data URI when qrcode is installed."""
    try:
        import base64
        import io

        import qrcode

        img = qrcode.make(value)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def invite_is_usable(invite, at: datetime | None = None) -> bool:
    at = at or now_utc()
    return (
        invite is not None
        and getattr(invite, "revoked_at", None) is None
        and getattr(invite, "consumed_at", None) is None
        and getattr(invite, "expires_at", at) > at
    )


def build_pairing_url(base_url: str, invite_secret: str) -> str:
    """Return a URL with the secret in the fragment so it avoids access logs."""
    from urllib.parse import quote

    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = "http://127.0.0.1:7000"
    return f"{base}/api/remote-access/pair#token={quote(invite_secret, safe='')}"


def serialize_invite(invite, *, include_secret: str | None = None, pairing_url: str | None = None) -> dict[str, Any]:
    data = {
        "id": invite.id,
        "owner": getattr(invite, "owner", None),
        "label": getattr(invite, "label", ""),
        "client_type": getattr(invite, "client_type", "browser"),
        "capabilities": capabilities_from_storage(getattr(invite, "capabilities", "")),
        "endpoint_url": getattr(invite, "endpoint_url", None),
        "expires_at": invite.expires_at.isoformat() if getattr(invite, "expires_at", None) else None,
        "consumed_at": invite.consumed_at.isoformat() if getattr(invite, "consumed_at", None) else None,
        "revoked_at": invite.revoked_at.isoformat() if getattr(invite, "revoked_at", None) else None,
        "consumed_by_client_id": getattr(invite, "consumed_by_client_id", None),
    }
    if include_secret is not None:
        data["token"] = include_secret
    if pairing_url is not None:
        data["pairing_url"] = pairing_url
    return data


def serialize_client(client) -> dict[str, Any]:
    return {
        "id": client.id,
        "owner": getattr(client, "owner", None),
        "name": getattr(client, "name", ""),
        "client_type": getattr(client, "client_type", "browser"),
        "platform": getattr(client, "platform", None),
        "capabilities": capabilities_from_storage(getattr(client, "capabilities", "")),
        "api_token_id": getattr(client, "api_token_id", None),
        "invite_id": getattr(client, "invite_id", None),
        "last_seen_at": client.last_seen_at.isoformat() if getattr(client, "last_seen_at", None) else None,
        "last_endpoint": getattr(client, "last_endpoint", None),
        "metadata": metadata_from_json(getattr(client, "metadata_json", None)),
        "is_active": bool(getattr(client, "is_active", False)),
        "revoked_at": client.revoked_at.isoformat() if getattr(client, "revoked_at", None) else None,
        "created_at": client.created_at.isoformat() if getattr(client, "created_at", None) else None,
    }


def expiry_from_ttl(ttl_minutes: Any) -> datetime:
    return now_utc() + timedelta(minutes=normalize_ttl_minutes(ttl_minutes))
