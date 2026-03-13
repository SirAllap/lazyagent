local M = {}

local defaults = {
  -- CLI command to run (override if not in PATH)
  cmd = "lazyagent",
  -- Window style: "float" | "split" | "vsplit" | "tab"
  -- When snacks.nvim is available, "float" uses Snacks.terminal (toggle behaviour).
  win = "float",
  -- Floating window options
  float = {
    border = "none",
    width = 0.92,
    height = 0.92,
  },
}

M.config = vim.deepcopy(defaults)

function M.setup(opts)
  M.config = vim.tbl_deep_extend("force", defaults, opts or {})
end

-- ---------------------------------------------------------------------------
-- Backends
-- ---------------------------------------------------------------------------

local function open_snacks(cmd, cfg)
  ---@diagnostic disable-next-line: undefined-global
  Snacks.terminal.toggle(cmd, {
    win = {
      position = "float",
      width = cfg.float.width,
      height = cfg.float.height,
      border = cfg.float.border,
      relative = "editor",
    },
  })
end

local function open_builtin(cmd, cfg)
  local cols = vim.o.columns
  local lines = vim.o.lines
  local width = math.floor(cols * cfg.float.width)
  local height = math.floor(lines * cfg.float.height)

  local buf = vim.api.nvim_create_buf(false, true)
  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    width = width,
    height = height,
    col = math.floor((cols - width) / 2),
    row = math.floor((lines - height) / 2),
    style = "minimal",
    border = cfg.float.border == "none" and "none" or cfg.float.border,
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

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

function M.open()
  local cmd = M.config.cmd

  if vim.fn.executable(cmd) == 0 then
    vim.notify(
      string.format(
        "[lazyagent] %q not found in PATH.\n"
          .. "Install with one of:\n"
          .. "  pipx install lazyagent\n"
          .. "  uv tool install lazyagent\n"
          .. "  pip install lazyagent",
        cmd
      ),
      vim.log.levels.ERROR
    )
    return
  end

  local win = M.config.win

  if win == "float" and _G.Snacks and Snacks.terminal then
    open_snacks(cmd, M.config)
  elseif win == "float" then
    open_builtin(cmd, M.config)
  elseif win == "split" then
    vim.cmd("split | terminal " .. cmd)
    vim.cmd("startinsert")
  elseif win == "vsplit" then
    vim.cmd("vsplit | terminal " .. cmd)
    vim.cmd("startinsert")
  elseif win == "tab" then
    vim.cmd("tabnew | terminal " .. cmd)
    vim.cmd("startinsert")
  else
    open_builtin(cmd, M.config)
  end
end

return M
