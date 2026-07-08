"""Remote access support for Odysseus.

This package is intentionally client-agnostic: browser, desktop, CLI, mobile,
and support clients all pair through the same invite and known-client model.
Transport-specific logic such as Tailscale lives behind endpoint providers.
"""

from remote_access.routes import setup_remote_access_routes

__all__ = ["setup_remote_access_routes"]
