-- Chat transcript rendering into the shell's main buffer.
--
-- Streaming is the hot path, so the assistant answer is rendered
-- *incrementally*: each delta only rewrites the changed tail line(s) rather
-- than re-splitting and rewriting the whole reply (which was O(n²) and caused
-- visible slowdown mid-reply). Thinking/tool changes are less frequent and
-- reposition the answer, so they trigger a throttled full reflow.

local ui = require("odysseus.ui")

local M = {}

local cur = nil -- { head_start, answer_start, answer, thinking, tools, order }
local reflow_scheduled = false

local function split(text)
  return vim.split(text or "", "\n", { plain = true })
end

local function append(lines)
  local at = ui.main_line_count()
  if at == 1 and vim.api.nvim_buf_get_lines(ui.main_buf(), 0, 1, false)[1] == "" then
    ui.write_main(0, 1, lines)
  else
    ui.write_main(at, at, lines)
  end
  ui.scroll_main_bottom()
end

function M.reset(session)
  cur = nil
  reflow_scheduled = false
  local title = session and session.name and session.name ~= "" and session.name or "New conversation"
  local model = session and session.model or ""
  ui.set_main({
    "# " .. title,
    model ~= "" and ("`" .. model .. "`") or "",
    "",
  }, "markdown")
end

function M.load_history(history)
  for _, msg in ipairs(history or {}) do
    local role = tostring(msg.role or "")
    local content = tostring(msg.content or "")
    if role == "user" then
      M.append_user(content)
    elseif role == "assistant" and content ~= "" then
      append({ "## Odysseus", "" })
      local lines = split(content)
      table.insert(lines, "")
      append(lines)
    end
  end
end

function M.append_user(message)
  local lines = { "## You", "" }
  vim.list_extend(lines, split(message))
  table.insert(lines, "")
  append(lines)
end

function M.begin_assistant()
  append({ "## Odysseus", "" })
  cur = { head_start = ui.main_line_count(), answer_start = nil, answer = "", thinking = "", tools = {}, order = {} }
  ui.write_main(cur.head_start, cur.head_start, { "_…thinking…_" })
end

-- Thinking blockquote + tool rows (everything above the answer).
local function build_head()
  local head = {}
  if cur.thinking ~= "" then
    for _, l in ipairs(split(cur.thinking)) do
      head[#head + 1] = "> " .. l
    end
    head[#head + 1] = ""
  end
  for _, name in ipairs(cur.order) do
    local t = cur.tools[name]
    local icon = t.status == "done" and "✓" or (t.status == "error" and "✗" or "…")
    local row = string.format("`%s` **%s**", icon, name)
    if t.summary and t.summary ~= "" then
      row = row .. " — " .. t.summary
    end
    head[#head + 1] = row
  end
  if #cur.order > 0 then
    head[#head + 1] = ""
  end
  return head
end

-- Full re-render of the assistant block; resets the answer anchor.
local function reflow()
  if not cur then
    return
  end
  local head = build_head()
  local answer_lines
  if cur.answer ~= "" then
    answer_lines = split(cur.answer)
  elseif cur.thinking == "" and #cur.order == 0 then
    answer_lines = { "_…thinking…_" }
  else
    answer_lines = { "" }
  end
  local all = {}
  vim.list_extend(all, head)
  vim.list_extend(all, answer_lines)
  ui.write_main(cur.head_start, ui.main_line_count(), all)
  cur.answer_start = cur.head_start + #head
  ui.scroll_main_bottom()
end

local function schedule_reflow()
  if reflow_scheduled or not cur then
    return
  end
  reflow_scheduled = true
  vim.defer_fn(function()
    reflow_scheduled = false
    reflow()
  end, 40)
end

-- Incremental answer append: only the changed tail line(s) are rewritten.
local function append_answer(text)
  local old = cur.answer
  cur.answer = old .. text
  if cur.answer_start == nil then
    reflow() -- first answer content positions head + answer region
    return
  end
  local old_lines = split(old)
  local new_lines = split(cur.answer)
  local first = cur.answer_start + (#old_lines - 1)
  local repl = {}
  for i = #old_lines, #new_lines do
    repl[#repl + 1] = new_lines[i]
  end
  ui.write_main(first, first + 1, repl)
  ui.scroll_main_bottom()
end

local function tool_name(payload)
  return tostring(payload.tool or payload.name or payload.tool_name or "tool")
end

local function tool_upsert(name, status, summary)
  if not cur.tools[name] then
    cur.tools[name] = { status = status or "running", summary = summary }
    table.insert(cur.order, name)
  else
    if status then
      cur.tools[name].status = status
    end
    if summary then
      cur.tools[name].summary = summary
    end
  end
end

function M.on_event(ev)
  if not cur then
    return
  end
  local kind = ev.kind
  local p = ev.payload or {}

  if kind == "message.delta" then
    local text = p.delta or p.text or p.content or p.token
    if text and text ~= "" then
      if p.thinking then
        cur.thinking = cur.thinking .. text
        cur.answer_start = nil -- thinking grew above the answer; re-anchor on reflow
        schedule_reflow()
      else
        append_answer(text)
      end
    end
  elseif kind == "tool_start" then
    tool_upsert(tool_name(p), "running", p.summary)
    cur.answer_start = nil
    schedule_reflow()
  elseif kind == "tool_progress" or kind == "tool.progress" then
    tool_upsert(tool_name(p), "running", p.summary or p.status)
    cur.answer_start = nil
    schedule_reflow()
  elseif kind == "tool_output" or kind == "tool_result" or kind == "tool_end" then
    tool_upsert(tool_name(p), "done", p.summary)
    cur.answer_start = nil
    schedule_reflow()
  elseif kind == "tool_error" then
    tool_upsert(tool_name(p), "error", p.summary or p.message)
    cur.answer_start = nil
    schedule_reflow()
  elseif kind == "error" then
    cur.answer = cur.answer .. "\n\n_[error] " .. tostring(p.message or p.text or ev.summary or "unknown") .. "_"
    reflow()
  end
end

function M.end_assistant(err)
  if not cur then
    return
  end
  reflow()
  if err then
    cur.answer = (cur.answer ~= "" and (cur.answer .. "\n\n") or "") .. "_[" .. err .. "]_"
    reflow()
  elseif cur.answer == "" and cur.thinking == "" and #cur.order == 0 then
    cur.answer = "_[no output]_"
    reflow()
  end
  ui.write_main(ui.main_line_count(), ui.main_line_count(), { "" })
  cur = nil
end

return M
