-- HTTP transport for the Odysseus Terminal Client API.
--
-- Uses `curl` via `vim.system` so there is no third-party Lua dependency.
-- Bounded calls decode a single JSON object; the event stream yields one
-- `ody.event.v1` envelope per newline-delimited line.

local config = require("odysseus.config")

local M = {}

local function auth_args(args)
  -- Force identity encoding: the server's gzip middleware will otherwise
  -- compress responses (including the NDJSON event stream) while a BaseHTTP
  -- middleware strips the Content-Encoding header, leaving an undecodable
  -- body. Asking for identity sidesteps that entirely.
  vim.list_extend(args, { "-H", "Accept-Encoding: identity" })
  if config.is_cookie_mode() then
    -- Full user session: send the cookie, and no Bearer (the auth middleware
    -- checks Bearer first, so a token would shadow the cookie).
    local jar = config.cookie_jar()
    vim.list_extend(args, { "-b", jar, "-c", jar })
  else
    local token = config.token()
    if token then
      vim.list_extend(args, { "-H", "Authorization: Bearer " .. token })
    end
  end
  return args
end

--- Bounded JSON request. Calls `cb(err, data)` on the main loop.
---@param method string
---@param path string  server-relative, e.g. "/api/terminal/runs"
---@param body table|nil  encoded as JSON when non-nil
---@param cb fun(err: string|nil, data: table|nil)
function M.request(method, path, body, cb)
  local args = auth_args({
    "curl", "-sS", "-X", method,
    config.base_url() .. path,
    "-H", "Accept: application/json",
  })
  if body ~= nil then
    vim.list_extend(args, {
      "-H", "Content-Type: application/json",
      "--data-binary", vim.json.encode(body),
    })
  end

  vim.system(args, { text = true, timeout = config.options.request_timeout },
    vim.schedule_wrap(function(res)
      if res.code ~= 0 then
        return cb(("curl exited %d: %s"):format(res.code, (res.stderr or ""):gsub("%s+$", "")), nil)
      end
      local raw = res.stdout or ""
      if raw == "" then
        return cb(nil, {})
      end
      local ok, decoded = pcall(vim.json.decode, raw)
      if not ok or type(decoded) ~= "table" then
        return cb("unexpected non-JSON response: " .. raw:sub(1, 200), nil)
      end
      if decoded.detail ~= nil and decoded.run == nil and decoded.status == nil then
        -- FastAPI error envelope ({"detail": ...}).
        local detail = decoded.detail
        if type(detail) == "table" then detail = vim.json.encode(detail) end
        return cb(tostring(detail), nil)
      end
      cb(nil, decoded)
    end))
end

--- Stream an NDJSON event endpoint.
---@param path string
---@param handlers { on_event: fun(ev: table), on_done: fun(err: string|nil)|nil }
---@return vim.SystemObj handle  call handle:kill(sig) to stop early
function M.stream(path, handlers)
  local args = auth_args({
    "curl", "-sS", "-N",
    config.base_url() .. path,
    "-H", "Accept: application/x-ndjson",
  })

  local pending = ""
  local function feed(chunk)
    pending = pending .. chunk
    while true do
      local nl = pending:find("\n", 1, true)
      if not nl then break end
      local line = pending:sub(1, nl - 1):gsub("%s+$", "")
      pending = pending:sub(nl + 1)
      if line ~= "" then
        local ok, ev = pcall(vim.json.decode, line)
        if ok and type(ev) == "table" then
          handlers.on_event(ev)
        end
      end
    end
  end

  return vim.system(args, {
    text = true,
    stdout = vim.schedule_wrap(function(err, data)
      if err or not data then return end
      feed(data)
    end),
  }, vim.schedule_wrap(function(res)
    if handlers.on_done then
      local err = nil
      -- 143 = SIGTERM (our own :kill on stop); treat as clean.
      if res.code ~= 0 and res.code ~= 143 then
        err = ("stream ended (%d): %s"):format(res.code, (res.stderr or ""):gsub("%s+$", ""))
      end
      handlers.on_done(err)
    end
  end))
end

-- High-level Terminal Client operations -------------------------------------

function M.health(cb)
  M.request("GET", "/api/health", nil, cb)
end

-- Read helpers used by the various views. Each calls cb(err, data).
function M.sessions(cb)
  M.request("GET", "/api/terminal/sessions", nil, cb)
end

function M.session_history(session_id, cb)
  M.request("GET", "/api/terminal/sessions/" .. session_id .. "/history", nil, cb)
end

function M.harnesses(cb)
  M.request("GET", "/api/harnesses", nil, cb)
end

function M.tasks(cb)
  M.request("GET", "/api/terminal/tasks", nil, cb)
end

function M.memory(cb)
  M.request("GET", "/api/memory", nil, cb)
end

function M.usage_summary(cb)
  M.request("GET", "/api/terminal/usage/summary?from=24h", nil, cb)
end

function M.usage_subscription(cb)
  M.request("GET", "/api/terminal/usage/subscription", nil, cb)
end

function M.models(cb)
  M.request("GET", "/api/terminal/models", nil, cb)
end

function M.notes(cb)
  M.request("GET", "/api/terminal/notes", nil, cb)
end

-- Documents: the token-scoped Terminal Client document API.
function M.documents(cb)
  M.request("GET", "/api/terminal/documents", nil, cb)
end

function M.document_get(doc_id, cb)
  M.request("GET", "/api/terminal/documents/" .. doc_id, nil, cb)
end

function M.document_update(doc_id, content, opts, cb)
  local body = { content = content }
  if opts then
    if opts.summary then body.summary = opts.summary end
    if opts.force_version then body.force_version = true end
  end
  M.request("PUT", "/api/terminal/documents/" .. doc_id, body, cb)
end

function M.document_create(title, content, language, cb)
  M.request("POST", "/api/terminal/documents", { title = title, content = content, language = language }, cb)
end

--- Start a run of `kind` ("chat" or "agent"). Reuse `session_id` for context.
--- `opts.active_doc_id` targets a document for the agent to edit.
function M.start_run_kind(kind, message, session_id, cb, opts)
  local body = { kind = kind or "chat", message = message }
  if session_id then body.session_id = session_id end
  if config.options.model then body.model = config.options.model end
  if config.options.endpoint_url then body.endpoint_url = config.options.endpoint_url end
  if opts then
    if opts.active_doc_id then body.active_doc_id = opts.active_doc_id end
    if opts.plan_mode then body.plan_mode = true end
    if opts.approved_plan then body.approved_plan = opts.approved_plan end
    if opts.attachments then body.attachments = opts.attachments end
  end
  M.request("POST", "/api/terminal/runs", body, cb)
end

--- Start a chat run. Reuse `session_id` across turns to keep conversation state.
function M.start_run(message, session_id, cb)
  M.start_run_kind("chat", message, session_id, cb)
end

function M.stream_run(run_id, handlers)
  return M.stream("/api/terminal/events/stream?run_id=" .. run_id, handlers)
end

function M.stop_run(run_id, cb)
  M.request("POST", "/api/terminal/runs/" .. run_id .. "/stop", {}, cb or function() end)
end

--- Log in with username/password, saving the session cookie to the jar.
--- Calls cb(err, data) where data may be {ok=true}, {requires_totp=true}, or
--- an error envelope {detail=…}.
function M.login(username, password, totp, cb)
  local body = { username = username, password = password, remember = true }
  if totp and totp ~= "" then
    body.totp_code = totp
  end
  local jar = config.cookie_jar()
  local args = {
    "curl", "-sS", "-X", "POST", config.base_url() .. "/api/auth/login",
    "-H", "Accept: application/json",
    "-H", "Content-Type: application/json",
    "-H", "Accept-Encoding: identity",
    "-c", jar, "-b", jar,
    "--data-binary", vim.json.encode(body),
  }
  vim.system(args, { text = true, timeout = config.options.request_timeout },
    vim.schedule_wrap(function(res)
      if res.code ~= 0 then
        return cb(("curl exited %d: %s"):format(res.code, (res.stderr or ""):gsub("%s+$", "")), nil)
      end
      local ok, data = pcall(vim.json.decode, res.stdout or "")
      if not ok or type(data) ~= "table" then
        return cb("unexpected login response: " .. (res.stdout or ""):sub(1, 160), nil)
      end
      cb(nil, data)
    end))
end

function M.logout(cb)
  M.request("POST", "/api/auth/logout", {}, cb or function() end)
end

return M
