"""Remote access routes: endpoint status, pairing invites, known clients."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

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
    raw_client_type = body.get("client_type")
    return {
        "label": str(body.get("label") or body.get("name") or "Remote client").strip()[:100],
        "client_type": pairing.normalize_client_type(raw_client_type) if raw_client_type else None,
        "platform": str(body.get("platform") or metadata.get("platform") or "").strip()[:100] or None,
        "user_agent": request.headers.get("user-agent"),
        "last_endpoint": str(body.get("endpoint") or "").strip()[:500] or None,
        "metadata_json": pairing.metadata_to_json(metadata),
    }


def _find_invite_for_secret(db, raw_secret: str):
    from core.database import RemotePairingInvite

    candidates = db.query(RemotePairingInvite).filter(
        RemotePairingInvite.consumed_at == None,  # noqa: E711
        RemotePairingInvite.revoked_at == None,  # noqa: E711
    ).order_by(RemotePairingInvite.created_at.desc()).limit(100).all()
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

    @api.get("/pair")
    def pair_page(request: Request):
        nonce = getattr(request.state, "csp_nonce", "")
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair Odysseus Client</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#16161a;color:#e8e8e8;max-width:560px;margin:48px auto;padding:0 20px}}
.card{{background:#1f1f25;border:1px solid #2c2c35;border-radius:8px;padding:24px}}
input,select,button{{font:inherit;border-radius:6px;border:1px solid #3a3a44;background:#101015;color:#e8e8e8;padding:9px}}
button{{background:#7c9cff;color:#0e0e12;border:0;font-weight:650;cursor:pointer}}
code{{word-break:break-all;background:#101015;border-radius:6px;padding:2px 5px}}
.row{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}}.muted{{color:#aaa;font-size:13px}}.err{{color:#ff7b7b}}
</style></head>
<body><div class="card">
<h1>Pair Odysseus Client</h1>
<p class="muted">The invite token stays in this page's URL fragment until you exchange it.</p>
<div class="row"><input id="label" placeholder="Client label" autocomplete="off"><select id="clientType"><option value="browser">Browser</option><option value="desktop">Desktop</option><option value="cli">CLI</option><option value="agent">Agent</option><option value="support">Support</option><option value="automation">Automation</option><option value="display">Display</option><option value="mobile">Mobile</option></select><button id="pairBtn">Pair</button></div>
<div id="msg" class="muted"></div>
<div id="result" style="display:none;margin-top:14px"><div class="muted">Bearer token</div><code id="token"></code></div>
</div>
<script nonce="{nonce}">
(function(){{
  var token = new URLSearchParams((location.hash || '').replace(/^#/, '')).get('token') || '';
  var msg = document.getElementById('msg');
  var btn = document.getElementById('pairBtn');
  if (!token) {{ msg.textContent = 'No pairing token found in the URL fragment.'; msg.className = 'err'; btn.disabled = true; }}
  btn.addEventListener('click', async function(){{
    msg.textContent = 'Pairing...'; msg.className = 'muted';
    try {{
      var res = await fetch('/api/remote-access/pair/exchange', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{token: token, label: document.getElementById('label').value, client_type: document.getElementById('clientType').value, platform: navigator.platform || ''}})
      }});
      var data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Pairing failed');
      document.getElementById('token').textContent = data.credential.token;
      document.getElementById('result').style.display = '';
      history.replaceState(null, '', location.pathname);
      msg.textContent = 'Paired. Store this bearer token now; it is shown once.';
    }} catch (err) {{ msg.textContent = err.message || 'Pairing failed'; msg.className = 'err'; }}
  }});
}})();
</script></body></html>"""
        return HTMLResponse(page)

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
        https_port = body.get("https_port") or endpoints.DEFAULT_TAILSCALE_HTTPS_PORT
        local_host = str(body.get("local_host") or endpoints.TAILSCALE_SERVE_LOCAL_HOST).strip()
        if local_host not in {endpoints.TAILSCALE_SERVE_LOCAL_HOST, "localhost"}:
            raise _json_error(400, "Tailscale Serve target must be loopback")
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
        result = endpoints.disable_tailscale_serve(
            https_port=int(body.get("https_port") or endpoints.DEFAULT_TAILSCALE_HTTPS_PORT)
        )
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

        secret_hash = pairing.hash_secret(raw_secret)
        invite = RemotePairingInvite(
            id=pairing.new_short_id(),
            owner=current_user,
            created_by=current_user,
            label=label,
            client_type=client_type,
            token_hash=secret_hash,
            token_prefix=pairing.hash_lookup(secret_hash),
            capabilities=capabilities,
            endpoint_url=endpoint_url,
            expires_at=pairing.expiry_from_ttl(body.get("ttl_minutes")),
        )
        invite_payload = pairing.serialize_invite(invite, pairing_url=pairing_url)
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

        from core.database import RemoteKnownClient, get_db_session

        with get_db_session() as db:
            invite = _find_invite_for_secret(db, raw_secret)
            if invite is None:
                raise _json_error(401, "Invalid pairing token")
            if not pairing.invite_is_usable(invite):
                raise _json_error(410, "Pairing invite is expired, revoked, or already used")

            client_payload = _client_payload(body, request)
            capabilities = pairing.capabilities_from_storage(invite.capabilities)
            token_id, raw_bearer = pairing.new_bearer_token_values()
            client_id = pairing.new_short_id()
            now = pairing.now_utc()

            db.add(pairing.build_bearer_token_row(
                token_id=token_id,
                raw_token=raw_bearer,
                owner=invite.owner,
                name=f"remote:{client_payload['label']}",
                capabilities=capabilities,
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
