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
    }
)
EVENT_READ_SCOPES = frozenset({"event:read", "event:raw"})
EVENT_RAW_SCOPES = frozenset({"event:raw"})
RUN_READ_SCOPES = frozenset({"run:read"})
RUN_START_SCOPES = frozenset({"run:start"})
RUN_STOP_SCOPES = frozenset({"run:stop"})


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
