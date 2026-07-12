-- odysseus.nvim — a Neovim workspace for Odysseus.
--
-- A dedicated tabpage with a navigation sidebar, a main content pane, and a
-- chat composer. Chat and agent runs stream live over the Terminal Client
-- contract (token-auth `/api/terminal` + `ody.event.v1` NDJSON); the other
-- panes read sessions, harnesses, tasks, memory, usage, notes, and documents.

local config = require("odysseus.config")
local ui = require("odysseus.ui")
local views = require("odysseus.views")
local chat = require("odysseus.chat")
local api = require("odysseus.api")

local M = {}

function M.setup(opts)
  config.setup(opts)
end

function M.open()
  if not ui.is_open() then
    ui.open()
    views.install_nav()
  end
  views.switch(views.active or "chat")
end

function M.toggle()
  if ui.is_open() then
    ui.close()
  else
    M.open()
  end
end

--- Jump straight to a named view (chat, sessions, tasks, …).
function M.goto(view)
  M.open()
  views.switch(view)
end

function M.send(text)
  M.open()
  views.switch("chat")
  chat.submit(text)
end

--- Propose a plan for `text` (agent run with plan_mode). Emits plan_update;
--- the model ends its turn with a plan you can then execute.
function M.plan(text)
  M.open()
  views.switch("agents")
  chat.submit(text, { plan_mode = true })
end

--- Open the floating session picker (works from any pane in normal mode).
function M.pick_session()
  require("odysseus.views").session_picker()
end

--- Open the floating document picker (opens the chosen doc into the pane).
function M.pick_document()
  require("odysseus.views").document_picker()
end

--- Open the floating model picker (pins the chosen model for runs).
function M.pick_model()
  require("odysseus.views").model_picker()
end

--- Save the document pane back to the server.
function M.doc_save()
  require("odysseus.doc").save()
end

function M.stop()
  chat.stop()
end

function M.new_conversation()
  chat.new_conversation()
end

--- Store a token pasted from the web app's Neovim token flow (the command it
--- reveals is `:OdysseusToken ody_…`). Persists it and refreshes the view.
function M.use_token(token)
  token = (token or ""):gsub("%s+", "")
  if token == "" then
    ui.notify("usage: :OdysseusToken ody_…", vim.log.levels.WARN)
    return
  end
  config.set_persisted_token(token)
  ui.notify("token stored (" .. token:sub(1, 8) .. "…) — now in use")
  if ui.is_open() then
    views.switch(views.active or "chat")
  end
end

--- Log in with a username/password for a full user session (unlocks the
--- cookie-only content routes: notes, documents, email, calendar).
function M.login()
  require("odysseus.login").start()
end

function M.logout()
  require("odysseus.login").logout()
end

function M.status()
  api.health(function(err, data)
    if err then
      ui.notify("server unreachable at " .. config.base_url() .. ": " .. err, vim.log.levels.ERROR)
    else
      local who
      if config.is_cookie_mode() then
        who = "session: " .. (config.cookie_user() or "user")
      else
        who = config.token() and "token" or "NO AUTH"
      end
      ui.notify(("connected to %s (%s) — %s"):format(config.base_url(), who, vim.json.encode(data)))
    end
  end)
end

return M
