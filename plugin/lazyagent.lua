vim.api.nvim_create_user_command("LazyAgent", function()
  require("lazyagent").open()
end, { desc = "Open LazyAgent TUI" })

-- Toggle: hides the window without killing the process, reopens on next call.
vim.api.nvim_create_user_command("LazyAgentToggle", function()
  require("lazyagent").toggle()
end, { desc = "Toggle LazyAgent TUI" })
