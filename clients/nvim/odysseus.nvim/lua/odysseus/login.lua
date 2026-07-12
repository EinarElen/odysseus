-- Interactive cookie-session login.
--
-- The password (and any 2FA code) are read straight from the user via
-- `inputsecret` and handed to `curl`; the plugin never stores them — only the
-- resulting session cookie is persisted, in the curl cookie jar.

local api = require("odysseus.api")
local ui = require("odysseus.ui")
local config = require("odysseus.config")

local M = {}

local function refresh()
  if ui.is_open() then
    local views = require("odysseus.views")
    views.switch(views.active or "chat")
  end
end

local function attempt(username, password, totp)
  ui.notify("logging in…")
  api.login(username, password, totp, function(err, data)
    if err then
      ui.notify("login failed: " .. err, vim.log.levels.ERROR)
      return
    end
    if data.requires_totp then
      vim.schedule(function()
        local code = vim.fn.inputsecret("Odysseus 2FA code: ")
        if not code or code == "" then
          ui.notify("login cancelled", vim.log.levels.WARN)
          return
        end
        attempt(username, password, code)
      end)
      return
    end
    if data.ok then
      config.set_cookie_mode(data.username or username)
      ui.notify("logged in as " .. (data.username or username) .. " — full access")
      refresh()
    else
      ui.notify("login failed: " .. tostring(data.detail or "invalid credentials"), vim.log.levels.ERROR)
    end
  end)
end

function M.start()
  vim.ui.input({ prompt = "Odysseus username: " }, function(username)
    if not username or username == "" then
      return
    end
    -- inputsecret masks the password; it is passed to curl and never stored.
    local password = vim.fn.inputsecret("Odysseus password: ")
    if not password or password == "" then
      ui.notify("login cancelled", vim.log.levels.WARN)
      return
    end
    attempt(username, password, nil)
  end)
end

function M.logout()
  api.logout(function()
    config.clear_cookie_mode()
    ui.notify("logged out — back to token auth")
    refresh()
  end)
end

return M
