-- App shell for odysseus.nvim.
--
-- Owns a dedicated tabpage laid out as: a left sidebar (navigation + a
-- context list), a central main area, and an optional bottom composer
-- (prompt buffer, chat only). Views render into main + sidebar through the
-- small API at the bottom; the shell knows nothing about any specific view.

local config = require("odysseus.config")

local M = {}

local ns = vim.api.nvim_create_namespace("odysseus_ui")

local S = {
  tab = nil,
  main_win = nil,
  side_win = nil,
  input_win = nil,
  main_buf = nil,
  side_buf = nil,
  input_buf = nil,
  side_map = {}, -- line (1-indexed) -> value passed to on_select
  side_select = nil,
  status = { view = "", model = "", state = "" },
}

-- Highlights -----------------------------------------------------------------

local function setup_highlights()
  local links = {
    OdysseusTitle = "Title",
    OdysseusNavActive = "PmenuSel",
    OdysseusNav = "Normal",
    OdysseusDim = "Comment",
    OdysseusUser = "Function",
    OdysseusAssistant = "Identity",
    OdysseusTool = "Special",
    OdysseusError = "DiagnosticError",
    OdysseusOk = "DiagnosticOk",
  }
  for name, link in pairs(links) do
    if vim.fn.hlexists(name) == 0 then
      vim.api.nvim_set_hl(0, name, { link = link, default = true })
    end
  end
end

-- Window / buffer helpers ----------------------------------------------------

local function valid_win(w)
  return w and vim.api.nvim_win_is_valid(w)
end

local function scratch(name, filetype)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.bo[buf].modifiable = false
  if filetype then
    vim.bo[buf].filetype = filetype
  end
  if name then
    pcall(vim.api.nvim_buf_set_name, buf, name)
  end
  return buf
end

local function write(buf, first, last, lines)
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, first, last, false, lines)
  vim.bo[buf].modifiable = false
end

function M.is_open()
  return valid_win(S.main_win) and valid_win(S.side_win)
end

-- Status winbar --------------------------------------------------------------

local function render_status()
  if not valid_win(S.main_win) then
    return
  end
  local st = S.status
  local parts = { "%#OdysseusTitle# Odysseus %*" }
  if st.view ~= "" then
    table.insert(parts, "  " .. st.view)
  end
  if st.model ~= "" then
    table.insert(parts, "  %#OdysseusDim#" .. st.model .. "%*")
  end
  if st.state ~= "" then
    table.insert(parts, "   " .. st.state)
  end
  vim.wo[S.main_win].winbar = table.concat(parts, "")
end

function M.status(fields)
  S.status = vim.tbl_extend("force", S.status, fields or {})
  render_status()
end

-- Layout ---------------------------------------------------------------------

function M.open()
  setup_highlights()
  if M.is_open() then
    vim.api.nvim_set_current_tabpage(S.tab)
    return
  end

  S.main_buf = S.main_buf and vim.api.nvim_buf_is_valid(S.main_buf) and S.main_buf or scratch("odysseus://main", "markdown")
  S.side_buf = scratch("odysseus://nav", "odysseus_nav")

  vim.cmd("tabnew")
  S.tab = vim.api.nvim_get_current_tabpage()
  S.main_win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(S.main_win, S.main_buf)

  vim.cmd("leftabove vsplit")
  S.side_win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(S.side_win, S.side_buf)
  vim.api.nvim_win_set_width(S.side_win, config.options.width_sidebar or 30)

  for _, win in ipairs({ S.main_win, S.side_win }) do
    vim.wo[win].number = false
    vim.wo[win].relativenumber = false
    vim.wo[win].signcolumn = "no"
    vim.wo[win].wrap = win == S.main_win
    vim.wo[win].linebreak = win == S.main_win
    vim.wo[win].cursorline = win == S.side_win
    vim.wo[win].winfixwidth = win == S.side_win
  end
  vim.wo[S.side_win].winbar = "%#OdysseusTitle# ☰ Menu %*"

  -- Sidebar selection: <CR> activates the item on the cursor line.
  vim.keymap.set("n", "<CR>", function()
    local line = vim.api.nvim_win_get_cursor(S.side_win)[1]
    local value = S.side_map[line]
    if value ~= nil and S.side_select then
      S.side_select(value)
    end
  end, { buffer = S.side_buf, nowait = true, silent = true })

  render_status()
end

function M.close()
  if S.tab and vim.api.nvim_tabpage_is_valid(S.tab) then
    -- Close the whole tab in one go.
    pcall(vim.cmd, "tabclose")
  end
  S.main_win, S.side_win, S.input_win = nil, nil, nil
end

function M.focus_main()
  if valid_win(S.main_win) then
    vim.api.nvim_set_current_win(S.main_win)
  end
end

function M.focus_sidebar()
  if valid_win(S.side_win) then
    vim.api.nvim_set_current_win(S.side_win)
  end
end

-- Main content ---------------------------------------------------------------

function M.main_buf()
  return S.main_buf
end

function M.side_buf()
  return S.side_buf
end

function M.main_win()
  return S.main_win
end

-- Flatten any multi-line entries so nvim_buf_set_lines never rejects them.
local function flatten(lines)
  local out = {}
  for _, l in ipairs(lines) do
    if type(l) == "string" and l:find("\n", 1, true) then
      vim.list_extend(out, vim.split(l, "\n", { plain = true }))
    else
      out[#out + 1] = l
    end
  end
  return out
end

--- Replace the whole main buffer. `lines` is a list of strings.
function M.set_main(lines, filetype)
  if not S.main_buf then
    return
  end
  if filetype then
    vim.bo[S.main_buf].filetype = filetype
  end
  write(S.main_buf, 0, -1, flatten(lines))
  if valid_win(S.main_win) then
    pcall(vim.api.nvim_win_set_cursor, S.main_win, { 1, 0 })
  end
end

--- Low-level line write into main (used by the streaming chat renderer).
function M.write_main(first, last, lines)
  write(S.main_buf, first, last, lines)
end

function M.main_line_count()
  return vim.api.nvim_buf_line_count(S.main_buf)
end

function M.scroll_main_bottom()
  if valid_win(S.main_win) then
    pcall(vim.api.nvim_win_set_cursor, S.main_win, { M.main_line_count(), 0 })
  end
end

-- Sidebar --------------------------------------------------------------------

--- Render the navigation + a context list.
--- nav:   list of { key=<char>, label=<str>, name=<view>, active=<bool> }
--- title: heading above the context list
--- items: list of { label=<str>, value=<any>, hint=<str?> }
--- on_select(value) fires when <CR> is pressed on an item row.
function M.set_sidebar(nav, title, items, on_select)
  S.side_map = {}
  S.side_select = on_select
  local lines, highlights = {}, {}
  local function push(text, hl)
    table.insert(lines, text)
    if hl then
      highlights[#lines] = hl
    end
  end

  push(" NAVIGATE", "OdysseusDim")
  for _, n in ipairs(nav or {}) do
    local marker = n.active and "▸ " or "  "
    push(string.format(" %s%s  %s", marker, n.key or " ", n.label), n.active and "OdysseusNavActive" or "OdysseusNav")
  end
  push("", nil)

  if title then
    push(" " .. title:upper(), "OdysseusDim")
  end
  local first_item_line = #lines + 1
  for i, it in ipairs(items or {}) do
    local text = string.format(" %2d. %s", i, it.label)
    if it.hint then
      text = text .. "  " .. it.hint
    end
    push(text, nil)
    S.side_map[#lines] = it.value
  end
  if #(items or {}) == 0 and title then
    push("    (none)", "OdysseusDim")
  end

  write(S.side_buf, 0, -1, lines)
  vim.api.nvim_buf_clear_namespace(S.side_buf, ns, 0, -1)
  for line, hl in pairs(highlights) do
    vim.api.nvim_buf_add_highlight(S.side_buf, ns, hl, line - 1, 0, -1)
  end

  -- Park the cursor on the first selectable item if any.
  if valid_win(S.side_win) and next(S.side_map) then
    pcall(vim.api.nvim_win_set_cursor, S.side_win, { first_item_line, 0 })
  end
end

-- Composer (chat input) ------------------------------------------------------

function M.show_composer(on_submit)
  if valid_win(S.input_win) then
    return
  end
  S.input_buf = vim.api.nvim_create_buf(false, true)
  vim.bo[S.input_buf].buftype = "prompt"
  vim.bo[S.input_buf].bufhidden = "wipe"
  vim.fn.prompt_setprompt(S.input_buf, "❯ ")
  vim.fn.prompt_setcallback(S.input_buf, on_submit)

  M.focus_main()
  vim.cmd("belowright split")
  S.input_win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(S.input_win, S.input_buf)
  vim.api.nvim_win_set_height(S.input_win, config.options.input_height or 6)
  vim.wo[S.input_win].number = false
  vim.wo[S.input_win].relativenumber = false
  vim.wo[S.input_win].signcolumn = "no"
  vim.wo[S.input_win].winbar = "%#OdysseusDim# message — <Enter> to send %*"
end

function M.hide_composer()
  if valid_win(S.input_win) then
    vim.api.nvim_win_close(S.input_win, true)
  end
  S.input_win = nil
end

function M.focus_composer()
  if valid_win(S.input_win) then
    vim.api.nvim_set_current_win(S.input_win)
    vim.cmd.startinsert()
  end
end

function M.notify(msg, level)
  vim.notify("[odysseus] " .. msg, level or vim.log.levels.INFO)
end

return M
