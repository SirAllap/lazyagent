local M = {}

local defaults = {
  -- CLI command to run (override if not in PATH)
  cmd = "lazyagent",
  -- Window style: "float" | "split" | "vsplit" | "tab"
  win = "float",
  -- Floating window options (only used when win = "float")
  float = {
    border = "rounded",
    width = 0.92,
    height = 0.92,
  },
}

M.config = vim.deepcopy(defaults)

function M.setup(opts)
  M.config = vim.tbl_deep_extend("force", defaults, opts or {})
end

local function open_float(cmd)
  local cols = vim.o.columns
  local lines = vim.o.lines
  local width = math.floor(cols * M.config.float.width)
  local height = math.floor(lines * M.config.float.height)
  local col = math.floor((cols - width) / 2)
  local row = math.floor((lines - height) / 2)

  local buf = vim.api.nvim_create_buf(false, true)
  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    width = width,
    height = height,
    col = col,
    row = row,
    style = "minimal",
    border = M.config.float.border,
  })

  vim.fn.termopen(cmd, {
    on_exit = function()
      if vim.api.nvim_win_is_valid(win) then
        vim.api.nvim_win_close(win, true)
      end
      if vim.api.nvim_buf_is_valid(buf) then
        vim.api.nvim_buf_delete(buf, { force = true })
      end
    end,
  })

  vim.cmd("startinsert")
end

function M.open()
  local cmd = M.config.cmd

  if vim.fn.executable(cmd) == 0 then
    vim.notify(
      string.format(
        "[lazyagent] %q not found in PATH.\nInstall with: pipx install lazyagent",
        cmd
      ),
      vim.log.levels.ERROR
    )
    return
  end

  local win = M.config.win

  if win == "split" then
    vim.cmd("split | terminal " .. cmd)
    vim.cmd("startinsert")
  elseif win == "vsplit" then
    vim.cmd("vsplit | terminal " .. cmd)
    vim.cmd("startinsert")
  elseif win == "tab" then
    vim.cmd("tabnew | terminal " .. cmd)
    vim.cmd("startinsert")
  else
    open_float(cmd)
  end
end

return M
