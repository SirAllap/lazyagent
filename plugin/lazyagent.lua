vim.api.nvim_create_user_command("LazyAgent", function()
  require("lazyagent").open()
end, { desc = "Open LazyAgent TUI" })
