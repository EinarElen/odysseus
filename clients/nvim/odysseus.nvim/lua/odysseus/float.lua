-- Reusable floating frames for the Odysseus workspace.
--
-- The main UI is a tabpage of splits; floats are for transient, modal-ish
-- interactions that shouldn't disturb that layout: pickers (sessions, models),
-- a command palette, detail popups (event/tool output), and prompts. Neovim
-- floats (`nvim_open_win`, relative="editor") overlay the tabpage, take focus,
-- and close cleanly, so they compose with the shell without touching it.

local M = {}

--- Open a centered, bordered floating frame.
--- opts: { title, footer, lines, width, height, filetype, cursorline }
--- Returns { win, buf, close }.
function M.open(opts)
  opts = opts or {}
  local lines = opts.lines or {}
  local W, H = vim.o.columns, vim.o.lines
  local width = math.max(10, math.min(opts.width or 60, W - 4))
  local height = math.max(1, math.min(opts.height or #lines, H - 4))

  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].bufhidden = "wipe"
  if opts.filetype then
    vim.bo[buf].filetype = opts.filetype
  end
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false

  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    width = width,
    height = height,
    row = math.floor((H - height) / 2),
    col = math.floor((W - width) / 2),
    style = "minimal",
    border = "rounded",
    title = opts.title and (" " .. opts.title .. " ") or nil,
    title_pos = "center",
    footer = opts.footer and (" " .. opts.footer .. " ") or nil,
    footer_pos = "center",
    zindex = 200,
  })
  vim.wo[win].winhighlight = "FloatBorder:OdysseusTitle,FloatTitle:OdysseusTitle"
  vim.wo[win].cursorline = opts.cursorline ~= false
  vim.wo[win].wrap = false

  local function close()
    if vim.api.nvim_win_is_valid(win) then
      vim.api.nvim_win_close(win, true)
    end
  end
  for _, key in ipairs({ "q", "<Esc>" }) do
    vim.keymap.set("n", key, close, { buffer = buf, nowait = true, silent = true })
  end

  return { win = win, buf = buf, close = close }
end

--- A selectable list overlay.
--- opts: { title, items = {{label=, value=}, …}, on_select(value), width, max_height }
function M.picker(opts)
  local items = opts.items or {}
  local lines = {}
  for i, it in ipairs(items) do
    lines[i] = string.format(" %2d  %s", i, it.label)
  end
  if #lines == 0 then
    lines = { "  (nothing to pick)" }
  end

  local frame = M.open({
    title = opts.title or "Select",
    footer = "<CR> select   j/k move   <Esc> cancel",
    lines = lines,
    width = opts.width or 64,
    height = math.min(math.max(#items, 1), opts.max_height or 15),
    cursorline = true,
  })

  local function choose()
    local line = vim.api.nvim_win_get_cursor(frame.win)[1]
    local item = items[line]
    frame.close()
    if item and opts.on_select then
      opts.on_select(item.value)
    end
  end
  vim.keymap.set("n", "<CR>", choose, { buffer = frame.buf, nowait = true, silent = true })
  return frame
end

--- A read-only info/detail popup (e.g. event or tool-call payload).
function M.popup(title, lines, filetype)
  local width = 0
  for _, l in ipairs(lines) do
    width = math.max(width, #l + 2)
  end
  return M.open({
    title = title,
    footer = "q / <Esc> close",
    lines = lines,
    width = math.min(width, 100),
    height = #lines,
    filetype = filetype,
    cursorline = false,
  })
end

return M
