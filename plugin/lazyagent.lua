vim.api.nvim_create_user_command("LazyAgent", function()
  require("lazyagent").open()
end, { desc = "Open LazyAgent TUI" })

-- Alias for toggle semantics (same behaviour, friendlier name)
vim.api.nvim_create_user_command("LazyAgentToggle", function()
  require("lazyagent").open()
end, { desc = "Toggle LazyAgent TUI" })
