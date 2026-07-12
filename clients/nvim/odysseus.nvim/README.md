# odysseus.nvim

A Neovim **workspace** for [Odysseus](../../..), built as a third client of the
suite alongside the web app and `ody-term`.

It opens a dedicated tabpage laid out like the desktop app: a navigation
sidebar, a main content pane, and a chat composer. Chat and agent runs stream
live; other panes browse sessions, harnesses, scheduled tasks, memory, usage,
notes, and documents.

It rides the **stable Terminal Client contract** — token-authenticated
`/api/terminal` HTTP plus the `ody.event.v1` NDJSON event stream — for chat,
and the scope-aware content APIs for the rest. No third-party Lua dependencies
(just `curl`).

## Views

| Key | View | Notes |
| --- | --- | --- |
| `c` | Chat | streaming replies, thinking, session sidebar |
| `a` | Agent | same input through the agent loop; tool calls inline |
| `s` | Sessions | browse/resume all sessions |
| `h` | Harnesses | registered adapters + defaults |
| `t` | Tasks | scheduled tasks |
| `m` | Memory | stored memories |
| `u` | Usage | 24h usage summary *(needs `usage:read`)* |
| `n` | Notes | *(needs `documents`/notes scope)* |
| `d` | Documents | *(needs `documents:read`)* |
| `g` | Help | keybindings |

Views marked *(needs …)* require scopes the default `terminal` token lacks;
they show a hint until you supply a full-scope token (see **Auth**).

## Requirements

- Neovim 0.10+ (uses `vim.system`, `vim.json`)
- `curl` on `PATH`
- A running Odysseus server and a Terminal Client API token

## Install

Point your plugin manager at this directory, e.g. with lazy.nvim:

```lua
{
  dir = "~/junk/odysseus/clients/nvim/odysseus.nvim",
  config = function()
    require("odysseus").setup({
      base_url = "http://127.0.0.1:7000",
      -- token is read from $ODY_TERM_TOKEN by default
    })
  end,
}
```

Or without a manager, add it to the runtimepath:

```vim
set runtimepath+=~/junk/odysseus/clients/nvim/odysseus.nvim
```

## Auth

With no setup at all, the plugin reuses the token `ody-term` already stored
(macOS keychain / `0600` file), which covers Chat, Agent, and Sessions.

### Unlock more views: create a token in the web app

Scope-gated views (Usage, Models, …) need scopes the default `terminal` token
lacks. In **Settings → Integrations → Add Integration → Terminal Client**, pick
**For: Neovim**, choose the scopes you want, and create it. The reveal shows a
command to paste straight into the editor:

```vim
:OdysseusToken ody_...
```

That stores the token (in a `0600` file under `stdpath('data')/odysseus/`) and
uses it immediately. The **ody-term** and **General** flavors of the same form
reveal an `ody-term auth login …` command and an `export ODY_NVIM_TOKEN=…` line
respectively. You can also edit any token's scopes later from its card.

Token resolution order: `setup{token=…}` → `$ODY_NVIM_TOKEN` →
`$ODY_TERM_TOKEN` → the `:OdysseusToken` file → `ody-term`'s stored credential.

### Full parity: log in

Some content routes (Notes, Documents, Email, Calendar) require a **user
session**, not a token — no scope reaches them. Run **`:OdysseusLogin`**, enter
your username and password (and 2FA code if enabled), and the client obtains a
session cookie that authenticates *everything*, exactly like the web app.
`:OdysseusLogout` ends it and reverts to token auth.

The password is read with `inputsecret` and handed straight to `curl`; the
plugin never stores it — only the resulting session cookie, in a `curl` cookie
jar under `stdpath('data')/odysseus/`. When a session is active the client
sends the cookie and no Bearer token. `:OdysseusStatus` shows the active
identity (session user vs token).

Server URL resolution mirrors `ody-term`: `setup{base_url=…}` → `$ODY_TERM_URL`
→ `$ODYSSEUS_URL` → `http://127.0.0.1:7000`. (On macOS, :7000 is often the
AirPlay Receiver, so a dev server usually runs elsewhere, e.g. `:7860` —
`export ODY_TERM_URL=http://127.0.0.1:7860`.)

## Use

| Command | Action |
| --- | --- |
| `:Odysseus` | Toggle the workspace |
| `:OdysseusOpen` | Open and focus the workspace |
| `:OdysseusGoto <view>` | Jump to a view (chat, sessions, …) |
| `:OdysseusSend <text>` | Send a message |
| `:OdysseusStop` | Cancel the active run |
| `:OdysseusNew` | Start a fresh conversation |
| `:OdysseusStatus` | Check server connectivity |

Inside the workspace, single keys in the sidebar/main pane switch views
(`c` chat, `s` sessions, `a` agent, `t` tasks, … `g` help). In Chat, type in
the bottom composer and press `<Enter>`; assistant tokens stream in live,
reasoning renders as a blockquote, agent tool calls render inline, and the
`session_id` is reused across turns. Pick a session from the sidebar to resume
it.

## Configuration

```lua
require("odysseus").setup({
  base_url = nil,            -- else $ODY_TERM_URL / $ODYSSEUS_URL / :7000
  token = nil,              -- else $ODY_NVIM_TOKEN / $ODY_TERM_TOKEN / keychain
  model = nil,               -- nil => owner's Default Model
  endpoint_url = nil,        -- requires model when set
  request_timeout = 30000,
  width_sidebar = 30,        -- navigation sidebar columns
  input_height = 6,          -- chat composer rows
})
```

## How it maps to the API

| Action | Request |
| --- | --- |
| Send a turn | `POST /api/terminal/runs` `{kind:"chat", message, session_id?}` |
| Stream reply | `GET /api/terminal/events/stream?run_id=…` (NDJSON `ody.event.v1`) |
| Stop | `POST /api/terminal/runs/{run_id}/stop` |
| Connectivity | `GET /api/health` |

Assistant text arrives as `kind:"message.delta"` with the token in
`payload.delta`; `kind:"run.status"` ends the stream.
