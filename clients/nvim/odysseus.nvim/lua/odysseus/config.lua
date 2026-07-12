-- Configuration resolution for the Odysseus Neovim client.
--
-- The client is a thin frontend over the stable Terminal Client contract:
-- token-authenticated `/api/terminal` HTTP + `ody.event.v1` NDJSON streams.
-- Nothing here depends on the browser UI's untyped SSE surface.

local M = {}

local defaults = {
  -- Base URL of the Odysseus server. When nil it is resolved like ody-term:
  --   $ODY_TERM_URL -> $ODYSSEUS_URL -> the fallback below.
  -- (Note: on macOS, :7000 is often taken by the AirPlay Receiver, so a dev
  -- server frequently runs on another port such as :7860 — set $ODY_TERM_URL.)
  base_url = nil,
  base_url_fallback = "http://127.0.0.1:7000",

  -- Owner-attributed API token ("ody_..."). Resolution order:
  --   1. this explicit `token`
  --   2. $ODY_NVIM_TOKEN   (a dedicated, ideally full-scope token for this UI)
  --   3. $ODY_TERM_TOKEN
  --   4. the credential `ody-term` already stored (macOS keychain / 0600 file)
  -- With just the ody-term login this reaches chat/sessions/runs; a full-scope
  -- token (see scripts/mint_full_token.py) also unlocks notes, documents,
  -- email, calendar, todos, memory, and usage.
  token = nil,
  token_env = "ODY_TERM_TOKEN",
  token_env_primary = "ODY_NVIM_TOKEN",

  -- Where `ody-term` keeps its login, mirrored so we can reuse it.
  keychain_service = "ody-term",
  config_file = nil, -- default: ~/.config/odysseus/ody-term.json
  secrets_file = nil, -- default: alongside config_file

  -- Model selection. nil `model` => the owner's configured Default Model.
  -- `endpoint_url` requires `model` (the server refuses endpoint-only runs).
  model = nil,
  endpoint_url = nil,

  -- Milliseconds for bounded (non-streaming) requests.
  request_timeout = 30000,

  -- Workspace geometry.
  width_sidebar = 30, -- columns for the navigation sidebar
  input_height = 6, -- rows for the chat composer
}

M.options = vim.deepcopy(defaults)

function M.setup(opts)
  M.options = vim.tbl_deep_extend("force", vim.deepcopy(defaults), opts or {})
end

local function config_path()
  return M.options.config_file
    or (vim.fn.expand("~/.config/odysseus/ody-term.json"))
end

local function secrets_path()
  return M.options.secrets_file
    or config_path():gsub("ody%-term%.json$", "ody-term-secrets.json")
end

local function read_json(path)
  if vim.fn.filereadable(path) == 0 then
    return nil
  end
  local ok, data = pcall(vim.json.decode, table.concat(vim.fn.readfile(path), "\n"))
  if ok and type(data) == "table" then
    return data
  end
  return nil
end

-- Mirror ody-term's _selected_token_ref: the default profile's token_ref,
-- else the platform default.
local function selected_token_ref()
  local cfg = read_json(config_path())
  if cfg and type(cfg.profiles) == "table" and type(cfg.default_profile) == "string" then
    local p = cfg.profiles[cfg.default_profile]
    if type(p) == "table" and type(p.token_ref) == "string" and p.token_ref ~= "" then
      return p.token_ref
    end
  end
  if vim.fn.has("mac") == 1 and vim.fn.executable("security") == 1 then
    return "keychain:" .. M.options.keychain_service .. "/default"
  end
  return "file:default"
end

-- ody-term's _keychain_account: the substring after the first ":".
local function keychain_account(ref)
  return ref:match("^[^:]+:(.+)$") or ref
end

local function token_for_ref(ref)
  if ref:match("^keychain:") and vim.fn.executable("security") == 1 then
    local out = vim.fn.system({
      "security", "find-generic-password",
      "-s", M.options.keychain_service,
      "-a", keychain_account(ref),
      "-w",
    })
    if vim.v.shell_error == 0 then
      out = (out or ""):gsub("%s+$", "")
      if out ~= "" then
        return out
      end
    end
  end
  -- File fallback: tokens[ref].token in the 0600 secrets file.
  local secrets = read_json(secrets_path())
  if secrets and type(secrets.tokens) == "table" then
    local entry = secrets.tokens[ref]
    if type(entry) == "table" and type(entry.token) == "string" and entry.token ~= "" then
      return entry.token
    end
  end
  return nil
end

-- A token pasted into the client with :OdysseusToken (e.g. from the web app's
-- "Neovim" token flow), persisted to a 0600 file so it survives restarts.
local function persisted_token_path()
  local dir = vim.fn.stdpath("data") .. "/odysseus"
  vim.fn.mkdir(dir, "p")
  return dir .. "/token"
end

local function read_persisted_token()
  local p = persisted_token_path()
  if vim.fn.filereadable(p) == 1 then
    local t = (vim.fn.readfile(p)[1] or ""):gsub("%s+$", "")
    if t ~= "" then
      return t
    end
  end
  return nil
end

local _cached
function M.token()
  if M.options.token and M.options.token ~= "" then
    return M.options.token
  end
  -- A dedicated token for this client (e.g. one created in the web app's
  -- Settings → Integrations with the scopes you want).
  local primary = M.options.token_env_primary and vim.env[M.options.token_env_primary]
  if primary and primary ~= "" then
    return primary
  end
  local env = vim.env[M.options.token_env]
  if env and env ~= "" then
    return env
  end
  if _cached then
    return _cached
  end
  -- A token stored via :OdysseusToken, then ody-term's stored credential.
  _cached = read_persisted_token() or token_for_ref(selected_token_ref())
  return _cached
end

--- Store a token pasted with :OdysseusToken: use it now and persist it.
function M.set_persisted_token(token)
  M.options.token = token
  _cached = token
  local p = persisted_token_path()
  pcall(vim.fn.writefile, { token }, p)
  pcall(vim.fn.setfperm, p, "rw-------")
end

--- Forget a persisted token.
function M.clear_persisted_token()
  M.options.token = nil
  _cached = nil
  pcall(vim.fn.delete, persisted_token_path())
end

-- Cookie-session auth ---------------------------------------------------------
--
-- Logging in with a username/password yields a full user session (a cookie),
-- which authenticates every route — including the `require_user` content
-- routes (notes, documents, email, calendar) that no API token can reach.
-- When a cookie session is active the client sends the cookie and no Bearer
-- token, so the server treats it as the logged-in user.

local function state_dir()
  local dir = vim.fn.stdpath("data") .. "/odysseus"
  vim.fn.mkdir(dir, "p")
  return dir
end

function M.cookie_jar()
  return M.options.cookie_jar or (state_dir() .. "/cookies.txt")
end

local function cookie_user_file()
  return M.cookie_jar() .. ".user"
end

function M.is_cookie_mode()
  -- The sidecar marker is written only on a successful login, so it (not the
  -- jar, which curl creates even for a failed attempt) is the source of truth.
  return vim.fn.filereadable(cookie_user_file()) == 1
    and vim.fn.filereadable(M.cookie_jar()) == 1
end

function M.cookie_user()
  local f = cookie_user_file()
  if vim.fn.filereadable(f) == 1 then
    return ((vim.fn.readfile(f)[1] or ""):gsub("%s+$", ""))
  end
  return nil
end

function M.set_cookie_mode(username)
  pcall(vim.fn.writefile, { username or "" }, cookie_user_file())
end

function M.clear_cookie_mode()
  pcall(vim.fn.delete, M.cookie_jar())
  pcall(vim.fn.delete, cookie_user_file())
end

function M.base_url()
  local url = M.options.base_url
  if not url or url == "" then
    url = vim.env.ODY_TERM_URL
  end
  if not url or url == "" then
    url = vim.env.ODYSSEUS_URL
  end
  if not url or url == "" then
    url = M.options.base_url_fallback or defaults.base_url_fallback
  end
  return url:gsub("/+$", "")
end

return M
