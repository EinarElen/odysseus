# Remote Access

Odysseus remote access has three separate pieces:

- **Endpoint exposure**: how another machine reaches this Odysseus server.
- **Pairing invites**: short-lived, single-use grants created by an admin.
- **Known clients**: durable client records with scoped bearer tokens.

Tailscale is private transport, not Odysseus identity. A request that arrives
over a tailnet still needs an Odysseus session or bearer token.

## Recommended Tailscale Setup

Keep Odysseus bound to loopback and expose it through Tailscale Serve:

```bash
APP_BIND=127.0.0.1 APP_PORT=7000 ./run.sh
tailscale serve --bg --https=443 http://127.0.0.1:7000
```

Then open **Settings -> Remote Access** and verify that the MagicDNS HTTPS
endpoint appears. Pairing invites should use that HTTPS endpoint when it is
available.

Avoid making `APP_BIND=0.0.0.0` the default remote-support path. It can be useful
for explicit LAN deployments, but Tailscale Serve keeps Odysseus listening only
on loopback.

## Pairing Flow

1. An admin opens **Settings -> Remote Access**.
2. The admin creates an invite with a client label, type, lifetime, and
   capabilities.
3. Odysseus stores only a bcrypt hash of the invite secret.
4. The client scans or receives a URL whose token is in the URL fragment.
5. The client exchanges the token at `/api/remote-access/pair/exchange`.
6. Odysseus consumes the invite, creates a known-client row, and returns a
   scoped bearer credential shown once.

Pairing invites expire, can be revoked, and are single-use. Known clients can be
revoked from the same settings page; revocation disables the underlying API
token and invalidates the auth token cache.

## Client Types

The same protocol is used for different client classes:

- `browser`
- `desktop`
- `cli`
- `agent`
- `support`
- `automation`
- `display`
- `mobile`

Phones are just one client type. The remote-access surface is intentionally not
mobile-specific.

## Capabilities

Current capability names are:

- `chat`
- `models`
- `sessions`
- `notes:read`
- `tasks:read`
- `memory:read`
- `remote_support:read`
- `remote_support:control`

Existing chat/model routes still enforce their own scopes. New remote-support
routes should check the known client and capability set before granting control
actions.

## Public Descriptor

`/.well-known/odysseus/environment` is intentionally unauthenticated and returns
only coarse server metadata:

- server name/version
- supported auth methods
- coarse capabilities
- advertised endpoint hints

It must not include model provider secrets, user data, API keys, rich owner
metadata, or any bearer credential.

## Security Posture

- Keep `AUTH_ENABLED=true`.
- Keep `LOCALHOST_BYPASS=false` for any networked deployment.
- Prefer Tailscale Serve to `http://127.0.0.1:<port>`.
- Do not treat Tailscale headers as login.
- Do not use Tailscale Funnel as the default support path.
- Keep invites short-lived and single-use.
- Revoke known clients rather than trying to reuse old pairing tokens.

Docker users usually need Tailscale running on the host, not only inside the
container, unless the container owns the network namespace being exposed.
