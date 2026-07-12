-- Chat/agent run orchestration: start a run, follow its event stream, and
-- drive the transcript renderer. Session id is reused across turns for context.

local api = require("odysseus.api")
local ui = require("odysseus.ui")
local render = require("odysseus.render")
local config = require("odysseus.config")

local M = {}

local state = {
  session_id = nil,
  session = nil,
  run_id = nil,
  stream = nil,
  busy = false,
  kind = "chat",
}

function M.current_session_id()
  return state.session_id
end

function M.busy()
  return state.busy
end

function M.set_kind(kind)
  state.kind = kind == "agent" and "agent" or "chat"
end

local function model_label()
  if state.session and state.session.model and state.session.model ~= "" then
    return state.session.model
  end
  return config.options.model or "default model"
end

function M.open_session(session_id, session_meta)
  state.session_id = session_id
  state.session = session_meta
  render.reset(session_meta)
  ui.status({ model = model_label(), state = "" })
  if not session_id then
    return
  end
  api.session_history(session_id, function(err, data)
    if err then
      ui.notify("history load failed: " .. err, vim.log.levels.WARN)
      return
    end
    state.session = data.session or session_meta
    render.reset(state.session)
    render.load_history(data.history or {})
    ui.status({ model = model_label() })
  end)
end

function M.new_conversation()
  if state.busy then
    ui.notify("finish or stop the active run first", vim.log.levels.WARN)
    return
  end
  state.session_id = nil
  state.session = nil
  render.reset(nil)
  ui.status({ model = model_label(), state = "" })
end

--- Submit a message. `extra_opts` may carry `plan_mode`, `approved_plan`,
--- `attachments` to pass through to the run.
function M.submit(message, extra_opts)
  if not message or message:gsub("%s", "") == "" then
    return
  end
  if state.busy then
    ui.notify("a run is in progress — :OdysseusStop to cancel", vim.log.levels.WARN)
    return
  end
  if not config.token() then
    ui.notify("no token — see :OdysseusStatus", vim.log.levels.ERROR)
    return
  end

  render.append_user(message)
  render.begin_assistant()
  state.busy = true
  ui.status({ state = "● running" })

  -- On agent turns, keep editing the document already on screen (if any).
  local opts = extra_opts and vim.deepcopy(extra_opts) or {}
  if state.kind == "agent" and not opts.active_doc_id then
    local doc_id = require("odysseus.doc").current_doc_id()
    if doc_id then
      opts.active_doc_id = doc_id
    end
  end
  api.start_run_kind(state.kind, message, state.session_id, function(err, data)
    if err or not data or not data.run then
      state.busy = false
      ui.status({ state = "✗ error" })
      render.end_assistant(err or "run did not start")
      return
    end
    state.session_id = data.run.session_id
    state.run_id = data.run.run_id
    state.stream = api.stream_run(state.run_id, {
      on_event = function(ev)
        render.on_event(ev)
        -- Document edits stream on the same channel; route them to the live
        -- document pane (opened lazily on the first doc event).
        if type(ev.kind) == "string" and ev.kind:sub(1, 4) == "doc_" then
          require("odysseus.doc").on_event(ev)
        end
      end,
      on_done = function(serr)
        state.busy = false
        state.stream = nil
        ui.status({ state = serr and "✗ error" or "✓ done" })
        render.end_assistant(serr)
        ui.focus_composer()
      end,
    })
  end, opts)
end

function M.stop()
  if state.stream then
    pcall(function()
      state.stream:kill(15)
    end)
  end
  if state.run_id then
    api.stop_run(state.run_id, function(err)
      if err then
        ui.notify("stop failed: " .. err, vim.log.levels.WARN)
      end
    end)
  end
end

return M
