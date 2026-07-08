"""Remote access routes: endpoint status, pairing invites, known clients."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from remote_access import endpoints
from remote_access import pairing
from src.auth_helpers import get_current_user


def _json_error(status_code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=message)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise _json_error(400, "JSON object expected")
    return body


def _invalidate_token_cache(request: Request) -> None:
    invalidator = getattr(request.app.state, "invalidate_token_cache", None)
    if callable(invalidator):
        invalidator()


def _client_payload(body: dict[str, Any], request: Request) -> dict[str, Any]:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    return {
        "label": str(body.get("label") or body.get("name") or "Remote client").strip()[:100],
        "client_type": pairing.normalize_client_type(body.get("client_type")),
        "platform": str(body.get("platform") or metadata.get("platform") or "").strip()[:100] or None,
        "user_agent": request.headers.get("user-agent"),
        "last_endpoint": str(body.get("endpoint") or "").strip()[:500] or None,
        "metadata_json": pairing.metadata_to_json(metadata),
    }


def _find_invite_for_secret(db, raw_secret: str):
    from core.database import RemotePairingInvite

    prefix = pairing.token_prefix(raw_secret)
    candidates = db.query(RemotePairingInvite).filter(RemotePairingInvite.token_prefix == prefix).all()
    for invite in candidates:
        if pairing.verify_secret(raw_secret, invite.token_hash):
            return invite
    return None


def _remote_environment(request: Request | None = None) -> dict[str, Any]:
    from core.constants import APP_VERSION

    return {
        "name": "odysseus",
        "version": APP_VERSION,
        "auth": ["pairing_v2", "bearer"],
        "capabilities": ["chat", "models", "remote_access"],
        "endpoints": [endpoint.to_dict() for endpoint in endpoints.advertised_endpoints(request)],
    }


def setup_remote_access_routes() -> APIRouter:
    router = APIRouter(tags=["remote_access"])
    api = APIRouter(prefix="/api/remote-access", tags=["remote_access"])

    @router.get("/.well-known/odysseus/environment")
    def well_known_environment(request: Request):
        return _remote_environment(request)

    @api.get("/environment")
    def authenticated_environment(request: Request):
        return _remote_environment(request)

    @api.get("/status")
    def status(request: Request):
        require_admin(request)
        return {
            "tailscale": endpoints.tailscale_status(),
            "endpoints": [endpoint.to_dict() for endpoint in endpoints.advertised_endpoints(request)],
        }

    @api.post("/tailscale/serve")
    async def start_tailscale_serve(request: Request):
        require_admin(request)
        body = await _json_body(request)
        local_port = body.get("local_port") or getattr(request.url, "port", None) or 7000
        https_port = body.get("https_port") or 443
        local_host = str(body.get("local_host") or "127.0.0.1")
        result = endpoints.enable_tailscale_serve(
            int(local_port),
            https_port=int(https_port),
            local_host=local_host,
        )
        if not result.get("ok"):
            raise _json_error(400, result.get("error") or "Could not enable Tailscale Serve")
        return result

    @api.delete("/tailscale/serve")
    async def stop_tailscale_serve(request: Request):
        require_admin(request)
        body = await _json_body(request)
        result = endpoints.disable_tailscale_serve(https_port=int(body.get("https_port") or 443))
        if not result.get("ok"):
            raise _json_error(400, result.get("error") or "Could not disable Tailscale Serve")
        return result

    @api.get("/invites")
    def list_invites(request: Request):
        require_admin(request)
        current_user = get_current_user(request)
        from core.database import RemotePairingInvite, get_db_session

        with get_db_session() as db:
            query = db.query(RemotePairingInvite).order_by(RemotePairingInvite.created_at.desc())
            if current_user:
                query = query.filter(RemotePairingInvite.owner == current_user)
            return {"invites": [pairing.serialize_invite(invite) for invite in query.limit(100).all()]}

    @api.post("/invites")
    async def create_invite(request: Request):
        require_admin(request)
        body = await _json_body(request)
        current_user = get_current_user(request)
        raw_secret = pairing.new_invite_secret()
        endpoint_url = endpoints.choose_pairing_base_url(request, body.get("endpoint_url"))
        pairing_url = pairing.build_pairing_url(endpoint_url, raw_secret)
        capabilities = pairing.capabilities_to_storage(body.get("capabilities"))
        client_type = pairing.normalize_client_type(body.get("client_type"))
        label = str(body.get("label") or "Remote client").strip()[:100] or "Remote client"

        from core.database import RemotePairingInvite, get_db_session

        invite = RemotePairingInvite(
            id=pairing.new_short_id(),
            owner=current_user,
            created_by=current_user,
            label=label,
            client_type=client_type,
            token_hash=pairing.hash_secret(raw_secret),
            token_prefix=pairing.token_prefix(raw_secret),
            capabilities=capabilities,
            endpoint_url=endpoint_url,
            expires_at=pairing.expiry_from_ttl(body.get("ttl_minutes")),
        )
        invite_payload = pairing.serialize_invite(invite, include_secret=raw_secret, pairing_url=pairing_url)
        with get_db_session() as db:
            db.add(invite)

        qr = pairing.qr_png_data_uri(pairing_url)
        return {
            "invite": invite_payload,
            "qr": qr if qr and qr.startswith("data:image/png;base64,") else None,
        }

    @api.delete("/invites/{invite_id}")
    def revoke_invite(request: Request, invite_id: str):
        require_admin(request)
        current_user = get_current_user(request)
        from core.database import RemotePairingInvite, get_db_session

        with get_db_session() as db:
            invite = db.query(RemotePairingInvite).filter(RemotePairingInvite.id == invite_id).first()
            if not invite:
                raise _json_error(404, "Invite not found")
            if current_user and invite.owner != current_user:
                raise _json_error(403, "Not your invite")
            invite.revoked_at = pairing.now_utc()
            db.add(invite)
            return {"status": "revoked", "invite": pairing.serialize_invite(invite)}

    @api.post("/pair/exchange")
    async def exchange_invite(request: Request):
        body = await _json_body(request)
        raw_secret = str(body.get("token") or body.get("pairing_token") or "").strip()
        if not raw_secret.startswith(pairing.INVITE_SECRET_PREFIX):
            raise _json_error(401, "Invalid pairing token")

        from core.database import ApiToken, RemoteKnownClient, get_db_session

        with get_db_session() as db:
            invite = _find_invite_for_secret(db, raw_secret)
            if invite is None:
                raise _json_error(401, "Invalid pairing token")
            if not pairing.invite_is_usable(invite):
                raise _json_error(410, "Pairing invite is expired, revoked, or already used")

            client_payload = _client_payload(body, request)
            capabilities = pairing.capabilities_from_storage(invite.capabilities)
            raw_bearer = "ody_" + secrets.token_urlsafe(32)
            token_id = pairing.new_short_id()
            client_id = pairing.new_short_id()
            now = pairing.now_utc()

            db.add(ApiToken(
                id=token_id,
                owner=invite.owner,
                name=f"remote:{client_payload['label']}"[:100],
                token_hash=pairing.hash_secret(raw_bearer),
                token_prefix=raw_bearer[:8],
                scopes=pairing.capabilities_to_storage(capabilities),
                is_active=True,
            ))
            client = RemoteKnownClient(
                id=client_id,
                owner=invite.owner,
                name=client_payload["label"],
                client_type=client_payload["client_type"] or invite.client_type,
                platform=client_payload["platform"],
                user_agent=client_payload["user_agent"],
                capabilities=pairing.capabilities_to_storage(capabilities),
                api_token_id=token_id,
                invite_id=invite.id,
                last_seen_at=now,
                last_endpoint=client_payload["last_endpoint"],
                metadata_json=client_payload["metadata_json"],
                is_active=True,
            )
            db.add(client)
            invite.consumed_at = now
            invite.consumed_by_client_id = client_id
            db.add(invite)
            response_client = pairing.serialize_client(client)

        _invalidate_token_cache(request)
        return {
            "client": response_client,
            "credential": {
                "type": "bearer",
                "token": raw_bearer,
                "token_id": token_id,
                "scopes": capabilities,
            },
        }

    @api.get("/clients")
    def list_clients(request: Request):
        require_admin(request)
        current_user = get_current_user(request)
        from core.database import RemoteKnownClient, get_db_session

        with get_db_session() as db:
            query = db.query(RemoteKnownClient).order_by(RemoteKnownClient.created_at.desc())
            if current_user:
                query = query.filter(RemoteKnownClient.owner == current_user)
            return {"clients": [pairing.serialize_client(client) for client in query.limit(200).all()]}

    @api.get("/clients/me")
    def current_client(request: Request):
        if not getattr(request.state, "api_token", False):
            return {"client": None, "auth": "session"}
        token_id = getattr(request.state, "api_token_id", None)
        from core.database import RemoteKnownClient, get_db_session

        with get_db_session() as db:
            client = db.query(RemoteKnownClient).filter(
                RemoteKnownClient.api_token_id == token_id,
                RemoteKnownClient.is_active == True,  # noqa: E712
            ).first()
            if not client:
                return {"client": None, "auth": "token"}
            client.last_seen_at = pairing.now_utc()
            db.add(client)
            return {"client": pairing.serialize_client(client), "auth": "token"}

    @api.delete("/clients/{client_id}")
    def revoke_client(request: Request, client_id: str):
        require_admin(request)
        current_user = get_current_user(request)
        from core.database import ApiToken, RemoteKnownClient, get_db_session

        with get_db_session() as db:
            client = db.query(RemoteKnownClient).filter(RemoteKnownClient.id == client_id).first()
            if not client:
                raise _json_error(404, "Client not found")
            if current_user and client.owner != current_user:
                raise _json_error(403, "Not your client")
            client.is_active = False
            client.revoked_at = pairing.now_utc()
            db.add(client)
            token = db.query(ApiToken).filter(ApiToken.id == client.api_token_id).first()
            if token:
                token.is_active = False
                db.add(token)
            response_client = pairing.serialize_client(client)

        _invalidate_token_cache(request)
        return {"status": "revoked", "client": response_client}

    router.include_router(api)
    return router
