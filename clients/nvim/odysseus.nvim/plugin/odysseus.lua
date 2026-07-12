-- Command surface for odysseus.nvim. Loaded once on startup.

if vim.g.loaded_odysseus then
  return
end
vim.g.loaded_odysseus = true

local function cmd(name, fn, opts)
  vim.api.nvim_create_user_command(name, fn, opts or {})
end

cmd("Odysseus", function()
  require("odysseus").toggle()
end, { desc = "Toggle the Odysseus chat window" })

cmd("OdysseusOpen", function()
  require("odysseus").open()
end, { desc = "Open (and focus) the Odysseus chat window" })

cmd("OdysseusSend", function(a)
  require("odysseus").send(a.args)
end, { nargs = "+", desc = "Send a message to Odysseus" })

cmd("OdysseusPlan", function(a)
  require("odysseus").plan(a.args)
end, { nargs = "+", desc = "Propose a plan (agent plan mode)" })

cmd("OdysseusStop", function()
  require("odysseus").stop()
end, { desc = "Stop the active Odysseus run" })

cmd("OdysseusSessions", function()
  require("odysseus").pick_session()
end, { desc = "Open the floating session picker" })

cmd("OdysseusModels", function()
  require("odysseus").pick_model()
end, { desc = "Open the model picker" })

cmd("OdysseusDocOpen", function()
  require("odysseus").pick_document()
end, { desc = "Open a document into the live pane" })

cmd("OdysseusDocSave", function()
  require("odysseus").doc_save()
end, { desc = "Save the document pane back to the server" })

cmd("OdysseusNew", function()
  require("odysseus").new_conversation()
end, { desc = "Start a fresh Odysseus conversation" })

cmd("OdysseusStatus", function()
  require("odysseus").status()
end, { desc = "Check Odysseus server connectivity" })

cmd("OdysseusToken", function(a)
  require("odysseus").use_token(a.args)
end, { nargs = 1, desc = "Use a token created in the web app (Neovim token flow)" })

cmd("OdysseusLogin", function()
  require("odysseus").login()
end, { desc = "Log in to Odysseus with a username/password (full access)" })

cmd("OdysseusLogout", function()
  require("odysseus").logout()
end, { desc = "Log out of the Odysseus session" })

cmd("OdysseusGoto", function(a)
  require("odysseus").goto(a.args)
end, {
  nargs = 1,
  desc = "Open an Odysseus view",
  complete = function()
    return { "chat", "agents", "sessions", "harnesses", "tasks", "memory", "usage", "notes", "documents", "help" }
  end,
})
