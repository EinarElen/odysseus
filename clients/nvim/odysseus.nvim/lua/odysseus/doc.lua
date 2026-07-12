-- Live document pane: renders the AI's document edits as they stream.
--
-- During an agent run the server emits document events over the same
-- ody.event.v1 stream:
--   doc_stream_open  {title, language}          -- (re)start a document
--   doc_stream_delta {content}                  -- cumulative content so far
--   doc_update       {doc_id, content, version, -- finalized document
--                     title, language}
--   doc_suggestions  {doc_id, suggestions}
-- We open a right-hand pane the first time a document appears and rewrite it
-- on each event (content is cumulative, so we replace rather than append),
-- with the right filetype for syntax highlighting.

local ui = require("odysseus.ui")

local M = {}

local S = { win = nil, buf = nil, doc_id = nil, version = nil, title = nil, language = nil, content = "" }

-- Map server "language" values to Neovim filetypes for highlighting.
local LANG_FT = {
  markdown = "markdown", md = "markdown", text = "text", plain = "text",
  python = "python", py = "python", javascript = "javascript", js = "javascript",
  typescript = "typescript", ts = "typescript", html = "html", css = "css",
  json = "json", yaml = "yaml", toml = "toml", csv = "csv", sql = "sql",
  bash = "sh", sh = "sh", lua = "lua", rust = "rust", go = "go", c = "c", cpp = "cpp",
}

local function valid(win)
  return win and vim.api.nvim_win_is_valid(win)
end

local function winbar()
  if not valid(S.win) then
    return
  end
  local bits = { "%#OdysseusTitle# " .. (S.title ~= nil and S.title ~= "" and S.title or "Document") .. " %*" }
  if S.language and S.language ~= "" then
    table.insert(bits, "  %#OdysseusDim#" .. S.language .. "%*")
  end
  if S.version then
    table.insert(bits, "  v" .. tostring(S.version))
  end
  if S.doc_id then
    table.insert(bits, "  %#OdysseusDim#" .. tostring(S.doc_id):sub(1, 10) .. "%*")
  end
  vim.wo[S.win].winbar = table.concat(bits, "")
end

local function ensure_pane()
  if valid(S.win) then
    return
  end
  if not (S.buf and vim.api.nvim_buf_is_valid(S.buf)) then
    S.buf = vim.api.nvim_create_buf(false, true)
    -- acwrite: `:w` fires BufWriteCmd, which we route to a server save.
    vim.bo[S.buf].buftype = "acwrite"
    vim.bo[S.buf].bufhidden = "hide"
    vim.bo[S.buf].swapfile = false
    vim.bo[S.buf].modifiable = true
    pcall(vim.api.nvim_buf_set_name, S.buf, "odysseus://document")
    vim.api.nvim_create_autocmd("BufWriteCmd", {
      buffer = S.buf,
      callback = function()
        M.save()
      end,
    })
  end
  local restore = vim.api.nvim_get_current_win()
  local main = ui.main_win()
  if valid(main) then
    vim.api.nvim_set_current_win(main)
  end
  vim.cmd("rightbelow vsplit")
  S.win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(S.win, S.buf)
  vim.wo[S.win].number = false
  vim.wo[S.win].relativenumber = false
  vim.wo[S.win].signcolumn = "no"
  vim.wo[S.win].wrap = true
  vim.wo[S.win].linebreak = true
  vim.wo[S.win].winfixwidth = true
  winbar()
  -- Keep focus where it was (usually the composer) so the user keeps typing.
  if valid(restore) then
    vim.api.nvim_set_current_win(restore)
  end
end

local function set_ft(language)
  if language and language ~= "" then
    vim.bo[S.buf].filetype = LANG_FT[language:lower()] or language:lower()
  end
end

local function set_content(text)
  S.content = text or ""
  vim.api.nvim_buf_set_lines(S.buf, 0, -1, false, vim.split(S.content, "\n", { plain = true }))
  vim.bo[S.buf].modified = false -- server state == buffer state
  if valid(S.win) then
    pcall(vim.api.nvim_win_set_cursor, S.win, { vim.api.nvim_buf_line_count(S.buf), 0 })
  end
end

-- doc_stream_delta sends the full cumulative content each time; coalesce rapid
-- deltas so we rewrite the buffer ~25x/sec instead of on every token.
local pending_content = nil
local flush_scheduled = false

local function schedule_flush()
  if flush_scheduled then
    return
  end
  flush_scheduled = true
  vim.defer_fn(function()
    flush_scheduled = false
    if pending_content ~= nil then
      set_content(pending_content)
      pending_content = nil
    end
  end, 40)
end

--- Handle a doc_* event from the run stream.
function M.on_event(ev)
  local kind = ev.kind
  local p = ev.payload or {}
  if kind == "doc_stream_open" then
    ensure_pane()
    S.title = p.title or S.title
    S.language = p.language or S.language
    set_ft(S.language)
    pending_content = nil
    set_content("")
    winbar()
  elseif kind == "doc_stream_delta" then
    ensure_pane()
    pending_content = p.content or S.content -- cumulative; flushed on a timer
    schedule_flush()
  elseif kind == "doc_update" then
    pending_content = nil
    ensure_pane()
    S.doc_id = p.doc_id or S.doc_id
    S.version = p.version
    S.title = p.title or S.title
    S.language = p.language or S.language
    set_ft(S.language)
    set_content(p.content or S.content)
    winbar()
  elseif kind == "doc_suggestions" then
    local n = type(p.suggestions) == "table" and #p.suggestions or 0
    ui.notify(("document has %d suggested edit(s)"):format(n))
  end
end

--- Save the pane's current content back to the server (`:w` or :OdysseusDocSave).
function M.save()
  if not (S.buf and vim.api.nvim_buf_is_valid(S.buf)) then
    return
  end
  local api = require("odysseus.api")
  local text = table.concat(vim.api.nvim_buf_get_lines(S.buf, 0, -1, false), "\n")
  if S.doc_id then
    api.document_update(S.doc_id, text, { summary = "Edited in Neovim" }, function(err, data)
      if err then
        ui.notify("save failed: " .. err, vim.log.levels.ERROR)
        return
      end
      S.version = (data and data.version_count) or S.version
      S.content = text
      vim.bo[S.buf].modified = false
      winbar()
      ui.notify("document saved (v" .. tostring(S.version) .. ")")
    end)
  else
    -- No server document yet: create one from the buffer.
    api.document_create(S.title or "Untitled", text, S.language, function(err, data)
      if err or not data then
        ui.notify("save failed: " .. (err or "no response"), vim.log.levels.ERROR)
        return
      end
      S.doc_id = data.id
      S.version = data.version_count
      S.content = text
      vim.bo[S.buf].modified = false
      winbar()
      ui.notify("document created (" .. tostring(S.doc_id):sub(1, 8) .. "…)")
    end)
  end
end

--- Open an existing document (from `doc` summary {id,title,language,…}) into the pane.
function M.open(doc)
  local api = require("odysseus.api")
  api.document_get(doc.id or doc.doc_id, function(err, data)
    if err or not data then
      ui.notify("could not open document: " .. (err or "not found"), vim.log.levels.ERROR)
      return
    end
    ensure_pane()
    S.doc_id = data.id
    S.version = data.version_count
    S.title = data.title
    S.language = data.language
    set_ft(S.language)
    set_content(data.current_content or "")
    winbar()
    M.focus()
  end)
end

function M.is_open()
  return valid(S.win)
end

function M.current_doc_id()
  return S.doc_id
end

function M.close()
  if valid(S.win) then
    vim.api.nvim_win_close(S.win, true)
  end
  S.win = nil
end

function M.focus()
  if valid(S.win) then
    vim.api.nvim_set_current_win(S.win)
  end
end

return M
