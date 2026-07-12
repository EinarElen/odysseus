"""Server-side capability gates for Terminal Client API routes."""

from fastapi import HTTPException, Request

from src.auth_helpers import require_user


TERMINAL_CLIENT_SCOPES = frozenset(
    {
        "session:read",
        "session:write",
        "run:read",
        "run:start",
        "run:stop",
        "event:read",
        "event:raw",
        "harness:read",
        "harness:control",
        "service:read",
        "service:restart",
        "service:kill",
        "auth:capabilities",
        "usage:read",
        "usage:export",
        "documents:read",
        "documents:write",
    }
)
EVENT_READ_SCOPES = frozenset({"event:read", "event:raw"})
EVENT_RAW_SCOPES = frozenset({"event:raw"})
RUN_READ_SCOPES = frozenset({"run:read"})
RUN_START_SCOPES = frozenset({"run:start"})
RUN_STOP_SCOPES = frozenset({"run:stop"})
SESSION_READ_SCOPES = frozenset({"session:read"})
HARNESS_CONTROL_SCOPES = frozenset({"harness:control"})
# Accept the terminal session scopes too so the default ody-term token reads
# usage out of the box (consistent with the other content domains); a
# usage-only token still works via its dedicated scopes.
USAGE_READ_SCOPES = frozenset({"usage:read", "usage:export", "session:read"})
USAGE_EXPORT_SCOPES = frozenset({"usage:export", "session:write"})
# Document CRUD. Accepts either the content scopes or the terminal session
# scopes, so the default terminal token works out of the box during dev
# without minting a documents-scoped token.
DOCUMENT_READ_SCOPES = frozenset({"documents:read", "documents:write", "session:read"})
DOCUMENT_WRITE_SCOPES = frozenset({"documents:write", "session:write"})
# Broad owner-scoped content domains (notes, tasks, memory, email, calendar,
# presets, prefs, search, skills, mcp, compare, research, gallery, voice, …).
# Accept the terminal session scopes so the default ody-term token works during
# dev without minting a per-domain token.
CONTENT_READ_SCOPES = frozenset({"session:read", "session:write"})
CONTENT_WRITE_SCOPES = frozenset({"session:write"})


def require_terminal_scope(request: Request, allowed: frozenset[str]) -> str:
    """Return the caller owner after enforcing a Terminal Client token scope."""
    if getattr(request.state, "api_token", False):
        scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        if not scopes.intersection(allowed):
            required = " or ".join(sorted(allowed))
            raise HTTPException(403, f"API token missing required scope: {required}")
        owner = getattr(request.state, "api_token_owner", None)
        if not owner:
            raise HTTPException(403, "API token has no owner")
        return str(owner)
    return require_user(request)
