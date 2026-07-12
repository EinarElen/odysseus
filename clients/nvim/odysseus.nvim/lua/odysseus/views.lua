-- View router: navigation + per-domain views rendered into the app shell.

local api = require("odysseus.api")
local ui = require("odysseus.ui")
local chat = require("odysseus.chat")
local float = require("odysseus.float")

local M = {}
M.active = nil

local NAV = {
  { key = "c", label = "Chat", name = "chat" },
  { key = "a", label = "Agent", name = "agents" },
  { key = "s", label = "Sessions", name = "sessions" },
  { key = "h", label = "Harnesses", name = "harnesses" },
  { key = "t", label = "Tasks", name = "tasks" },
  { key = "m", label = "Memory", name = "memory" },
  { key = "u", label = "Usage", name = "usage" },
  { key = "n", label = "Notes", name = "notes" },
  { key = "d", label = "Documents", name = "documents" },
  { key = "g", label = "Help", name = "help" },
}

local function nav(active)
  local out = {}
  for _, n in ipairs(NAV) do
    out[#out + 1] = { key = n.key, label = n.label, name = n.name, active = n.name == active }
  end
  return out
end

-- Shared display helpers so the sidebar lists and the floating pickers render
-- session/document items the same way (no drift between the two sites).
local function session_name(s)
  return (s.name ~= "" and s.name) or s.session_id
end

local function doc_name(d)
  return (d.title ~= "" and d.title) or d.id
end

local function session_item(s)
  return {
    label = string.format("%-28s %s (%d)", session_name(s):sub(1, 28), s.model or "", s.message_count or 0),
    value = s,
  }
end

local function doc_item(d)
  return {
    label = string.format("%-30s %s v%s", doc_name(d):sub(1, 30), d.language or "", d.version_count or 1),
    value = d,
  }
end

local function loading(active, title)
  ui.set_sidebar(nav(active), nil, {}, nil)
  ui.set_main({ "# " .. title, "", "_loading…_" }, "markdown")
end

-- Turn an API error into a friendly, scope-aware note.
local function err_lines(title, err)
  local hint = ""
  if err and (err:match("scope") or err:match("token") or err:match("[Nn]ot [Aa]uthenticated")) then
    hint = "\n\nThis view needs broader access than the current token.\n"
      .. "Either grant the scopes to your token in the web app "
      .. "(Settings → Integrations → your Terminal Client token), or run "
      .. "`:OdysseusLogin` for a full user session."
  end
  return vim.split("# " .. title .. "\n\n> " .. tostring(err) .. hint, "\n", { plain = true })
end

-- Views ----------------------------------------------------------------------

function M.render_chat(name)
  ui.show_composer(chat.submit)
  chat.set_kind(name == "agents" and "agent" or "chat")
  ui.status({ view = name == "agents" and "Agent" or "Chat" })
  api.sessions(function(err, data)
    local items = {}
    if not err and data and data.sessions then
      for _, s in ipairs(data.sessions) do
        items[#items + 1] = {
          label = session_name(s),
          value = s,
          hint = "(" .. (s.message_count or 0) .. ")",
        }
      end
    end
    ui.set_sidebar(nav(name), "Sessions", items, function(s)
      chat.open_session(s.session_id, s)
      ui.focus_composer()
    end)
  end)
  if not chat.current_session_id() then
    chat.new_conversation()
  end
  ui.focus_composer()
end

function M.render_sessions()
  loading("sessions", "Sessions")
  api.sessions(function(err, data)
    if err then
      ui.set_main(err_lines("Sessions", err), "markdown")
      return
    end
    local lines, items = { "# Sessions", "" }, {}
    for _, s in ipairs(data.sessions or {}) do
      local nm = session_name(s)
      lines[#lines + 1] = string.format("- **%s**  `%s` · %d msgs", nm, s.model or "", s.message_count or 0)
      items[#items + 1] = { label = nm, value = s, hint = "(" .. (s.message_count or 0) .. ")" }
    end
    if #items == 0 then
      lines[#lines + 1] = "_no sessions yet — start one from Chat_"
    end
    ui.set_main(lines, "markdown")
    ui.set_sidebar(nav("sessions"), "Open in chat", items, function(s)
      chat.open_session(s.session_id, s)
      M.switch("chat")
    end)
  end)
end

function M.render_harnesses()
  loading("harnesses", "Harnesses")
  api.harnesses(function(err, data)
    if err then
      ui.set_main(err_lines("Harnesses", err), "markdown")
      return
    end
    local lines = { "# Harnesses", "" }
    for _, h in ipairs((data and data.harnesses) or {}) do
      lines[#lines + 1] = string.format("## %s  `%s`", h.label or h.id, h.id or "")
      local d = h.defaults or {}
      lines[#lines + 1] = string.format("- model: `%s`  mode: `%s`", d.model or "?", d.mode or "?")
      lines[#lines + 1] = ""
    end
    if #lines == 2 then
      lines[#lines + 1] = "_no harness adapters registered_"
    end
    ui.set_main(lines, "markdown")
    ui.set_sidebar(nav("harnesses"), nil, {}, nil)
  end)
end

function M.render_tasks()
  loading("tasks", "Scheduled Tasks")
  api.tasks(function(err, data)
    if err then
      ui.set_main(err_lines("Scheduled Tasks", err), "markdown")
      return
    end
    local lines = { "# Scheduled Tasks", "" }
    for _, t in ipairs((data and data.tasks) or {}) do
      lines[#lines + 1] = string.format("- **%s**  _%s_", t.name or t.id, t.task_type or "")
    end
    if #lines == 2 then
      lines[#lines + 1] = "_no tasks_"
    end
    ui.set_main(lines, "markdown")
    ui.set_sidebar(nav("tasks"), nil, {}, nil)
  end)
end

function M.render_memory()
  loading("memory", "Memory")
  api.memory(function(err, data)
    if err then
      ui.set_main(err_lines("Memory", err), "markdown")
      return
    end
    local lines = { "# Memory", "" }
    for _, m in ipairs((data and data.memory) or {}) do
      local text = type(m) == "table" and (m.content or m.text or vim.json.encode(m)) or tostring(m)
      lines[#lines + 1] = "- " .. tostring(text):gsub("\n", " ")
    end
    if #lines == 2 then
      lines[#lines + 1] = "_no memories stored_"
    end
    ui.set_main(lines, "markdown")
    ui.set_sidebar(nav("memory"), nil, {}, nil)
  end)
end

function M.render_usage()
  loading("usage", "Usage")
  api.usage_summary(function(err, data)
    if err then
      ui.set_main(err_lines("Usage", err), "markdown")
      return
    end
    local lines = { "# Usage — last 24h", "" }
    local totals = data.totals or data.summary or data
    if type(totals) == "table" then
      for _, key in ipairs({ "requests", "runs", "input_tokens", "output_tokens", "cache_read_tokens", "total_tokens", "cost", "cost_usd" }) do
        if totals[key] ~= nil then
          lines[#lines + 1] = string.format("- **%s**: %s", key:gsub("_", " "), tostring(totals[key]))
        end
      end
    end
    lines[#lines + 1] = ""
    lines[#lines + 1] = "```"
    vim.list_extend(lines, vim.split(vim.inspect(data), "\n", { plain = true }))
    lines[#lines + 1] = "```"
    ui.set_main(lines, "markdown")
    ui.set_sidebar(nav("usage"), nil, {}, nil)
  end)
end

-- Find the payload's list: the named key, else the first array-valued field.
local function first_list(data, key)
  if type(data) ~= "table" then
    return {}
  end
  if type(data[key]) == "table" then
    return data[key]
  end
  for _, v in pairs(data) do
    if type(v) == "table" and (#v > 0 or next(v) == nil) then
      return v
    end
  end
  return {}
end

local function render_simple_list(active, title, loader, key, fmt)
  loading(active, title)
  loader(function(err, data)
    if err then
      ui.set_main(err_lines(title, err), "markdown")
      return
    end
    local lines = { "# " .. title, "" }
    for _, item in ipairs(first_list(data, key)) do
      lines[#lines + 1] = "- " .. fmt(item)
    end
    if #lines == 2 then
      lines[#lines + 1] = "_none_"
    end
    ui.set_main(lines, "markdown")
    ui.set_sidebar(nav(active), nil, {}, nil)
  end)
end

function M.render_notes()
  render_simple_list("notes", "Notes", api.notes, "notes", function(n)
    return "**" .. (n.title or n.id or "note") .. "**"
  end)
end

function M.render_documents()
  loading("documents", "Documents")
  api.documents(function(err, data)
    if err then
      ui.set_main(err_lines("Documents", err), "markdown")
      return
    end
    local lines, items = { "# Documents", "" }, {}
    for _, d in ipairs((data and data.documents) or {}) do
      lines[#lines + 1] = string.format("- **%s**  `%s` · v%s", doc_name(d), d.language or "", d.version_count or 1)
      items[#items + 1] = doc_item(d)
    end
    if #items == 0 then
      lines[#lines + 1] = "_no documents yet — switch to Agent and ask it to write one_"
    end
    ui.set_main(lines, "markdown")
    ui.set_sidebar(nav("documents"), "Open in pane", items, function(d)
      require("odysseus.doc").open(d)
    end)
  end)
end

-- A floating model picker. Selecting a model pins it for subsequent runs,
-- which also avoids the server's default-model fallback round-trip.
function M.model_picker()
  if not ui.is_open() then
    ui.open()
    M.install_nav()
  end
  local config = require("odysseus.config")
  api.models(function(err, data)
    if err then
      ui.notify("could not load models: " .. err, vim.log.levels.WARN)
      return
    end
    local items = {}
    for _, m in ipairs((data and data.models) or {}) do
      local mark = (data.default_model == m.model) and " (default)" or ""
      items[#items + 1] = {
        label = string.format("%-28s %s%s", m.model, m.endpoint_name or "", mark),
        value = m,
      }
    end
    float.picker({
      title = "Models",
      items = items,
      on_select = function(m)
        config.options.model = m.model
        config.options.endpoint_url = m.endpoint_url
        ui.status({ model = m.model })
        ui.notify("model set to " .. m.model)
      end,
    })
  end)
end

-- A floating document picker (open a document into the live pane).
function M.document_picker()
  if not ui.is_open() then
    ui.open()
    M.install_nav()
  end
  api.documents(function(err, data)
    if err then
      ui.notify("could not load documents: " .. err, vim.log.levels.WARN)
      return
    end
    local items = {}
    for _, d in ipairs((data and data.documents) or {}) do
      items[#items + 1] = doc_item(d)
    end
    float.picker({
      title = "Documents",
      items = items,
      on_select = function(d)
        require("odysseus.doc").open(d)
      end,
    })
  end)
end

function M.render_help()
  local lines = {
    "# Odysseus — Help",
    "",
    "## Navigation (press in the sidebar or main pane)",
  }
  for _, n in ipairs(NAV) do
    lines[#lines + 1] = string.format("- `%s`  %s", n.key, n.label)
  end
  vim.list_extend(lines, {
    "",
    "## Commands",
    "- `:Odysseus`        toggle the workspace",
    "- `:OdysseusSend …`  send a message",
    "- `:OdysseusStop`    cancel the active run",
    "- `:OdysseusNew`     new conversation",
    "- `:OdysseusStatus`  connectivity check",
    "",
    "## Chat",
    "- Type in the bottom composer, `<Enter>` sends.",
    "- Pick a session from the sidebar to resume it.",
    "- `Agent` runs the same input through the agent loop (tools shown inline).",
  })
  ui.set_main(lines, "markdown")
  ui.set_sidebar(nav("help"), nil, {}, nil)
end

-- A floating session picker (overlays the workspace, doesn't disturb it).
function M.session_picker()
  if not ui.is_open() then
    ui.open()
    M.install_nav()
  end
  api.sessions(function(err, data)
    if err then
      ui.notify("could not load sessions: " .. err, vim.log.levels.WARN)
      return
    end
    local items = {}
    for _, s in ipairs((data and data.sessions) or {}) do
      items[#items + 1] = session_item(s)
    end
    float.picker({
      title = "Sessions",
      items = items,
      on_select = function(s)
        chat.open_session(s.session_id, s)
        M.switch("chat")
        ui.focus_composer()
      end,
    })
  end)
end

-- Router ---------------------------------------------------------------------

function M.switch(name)
  if not ui.is_open() then
    ui.open()
    M.install_nav()
  end
  M.active = name
  if name == "chat" or name == "agents" then
    M.render_chat(name)
    return
  end
  ui.hide_composer()
  local fn = ({
    sessions = M.render_sessions,
    harnesses = M.render_harnesses,
    tasks = M.render_tasks,
    memory = M.render_memory,
    usage = M.render_usage,
    notes = M.render_notes,
    documents = M.render_documents,
    help = M.render_help,
  })[name]
  if fn then
    ui.status({ view = name:sub(1, 1):upper() .. name:sub(2), state = "" })
    fn()
  end
end

-- Bind single-key nav on the main + sidebar buffers.
function M.install_nav()
  for _, buf in ipairs({ ui.main_buf(), ui.side_buf() }) do
    if buf then
      for _, n in ipairs(NAV) do
        vim.keymap.set("n", n.key, function()
          M.switch(n.name)
        end, { buffer = buf, nowait = true, silent = true, desc = "Odysseus: " .. n.label })
      end
      vim.keymap.set("n", "p", function()
        M.session_picker()
      end, { buffer = buf, nowait = true, silent = true, desc = "Odysseus: session picker (float)" })
      vim.keymap.set("n", "M", function()
        M.model_picker()
      end, { buffer = buf, nowait = true, silent = true, desc = "Odysseus: model picker (float)" })
    end
  end
end

return M
