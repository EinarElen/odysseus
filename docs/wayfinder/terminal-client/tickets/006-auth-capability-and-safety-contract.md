# Specify Auth, Capability, And Safety Contract

Status: closed
Type: grilling
Blocked by: Inventory HTTP/API Gaps For `ody-term` V1
Assignee: Codex

## Question

How should `ody-term` authenticate to Odysseus, respect user ownership and API-token scopes, expose powerful operational mutations, and add explicit friction for extreme operations without becoming an auth bypass or an unusably cautious product surface?

## Resolution

Use the existing owner-attributed Odysseus `ody_` bearer API-token model as the `ody-term` authentication base. V1 should not introduce a separate Terminal Client credential family. Instead, add Terminal Client capability scopes to the existing API-token system.

Terminal Client capability scopes should be resource/action pairs rather than command spellings, so aliases and command grammar changes do not alter authorization semantics. Existing broad scopes such as `chat` must not implicitly grant Terminal Client operational visibility. Provide an admin-created token profile for common development use instead.

Suggested v1 scope families:

- `session:read`, `session:write`
- `run:read`, `run:start`, `run:stop`
- `event:read`, `event:raw`
- `harness:read`, `harness:control`
- `service:read`, `service:restart`, `service:kill`
- `auth:capabilities`

When Odysseus auth is disabled or localhost bypass is active, `ody-term` should mirror backend access behavior but make the mode explicit in `auth status`, structured output, and TUI chrome. It must not turn bypass access into a stored reusable credential.

V1 token creation remains admin-managed through the existing token-management surface, but token use is not inherently admin-only. Owner-attributed tokens may operate on the owner's own Sessions/Runs and event streams when scoped. Cross-owner visibility, host/service lifecycle controls, token management, and extreme operations remain admin-only.

Bounded mutations such as stopping a Run, sending a harness command, or restarting a clearly owned Lifecycle Target need the relevant scope and ordinary confirmation. Forceful or broad operations such as raw PID kill, process-tree kill, broad restarts, API-backed main-server stop, infrastructure disablement, event/replay deletion, or mutation of unclearly owned targets require a stronger scope plus elevated friction.

`--yes` may satisfy ordinary confirmations. Elevated-friction operations require `--yolo` in non-interactive CLI use or an explicit typed confirmation in human/TUI mode. Confirmation never bypasses missing auth, missing scope, ownership, admin-only policy, or server policy.

`ody-term auth capabilities` should report both raw auth facts and resolved policy facts: `auth_mode`, `user`, `owner`, `is_admin`, `token_scopes`, `terminal_scopes`, and resource/action capabilities with `allowed`, `requires_confirmation`, `requires_yolo`, `admin_only`, and `reason` when denied. CLI help and TUI affordances can use the same capability data to hide, disable, or annotate unavailable operations.
