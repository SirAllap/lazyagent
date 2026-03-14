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

-- Set <Esc><Esc> on any lazyagent terminal buffer to hide the window.
vim.api.nvim_create_autocmd("TermOpen", {
  callback = function(ev)
    if vim.api.nvim_buf_get_name(ev.buf):match("lazyagent") then
      vim.keymap.set("t", "<Esc><Esc>", function()
        M.toggle()
      end, { buffer = ev.buf, desc = "Hide LazyAgent" })
    end
  end,
})

-- ---------------------------------------------------------------------------
-- Persistent buffer state (survives window close)
-- ---------------------------------------------------------------------------

local _buf = nil -- terminal buffer kept alive between opens

local function _buf_valid()
  return _buf ~= nil and vim.api.nvim_buf_is_valid(_buf)
end

-- Return the window currently showing our buffer, or nil.
local function _find_win()
  if not _buf_valid() then return nil end
  for _, w in ipairs(vim.api.nvim_list_wins()) do
    if vim.api.nvim_win_get_buf(w) == _buf then
      return w
    end
  end
  return nil
end

-- ---------------------------------------------------------------------------
-- Backends
-- ---------------------------------------------------------------------------

local function open_snacks(cmd, cfg)
  ---@diagnostic disable-next-line: undefined-global
  Snacks.terminal.toggle(cmd, {
    win = {
      position = "float",
      width    = cfg.float.width,
      height   = cfg.float.height,
      border   = cfg.float.border,
      relative = "editor",
    },
  })
end

local function _open_float_win(cfg)
  local cols   = vim.o.columns
  local lines  = vim.o.lines
  local width  = math.floor(cols * cfg.float.width)
  local height = math.floor(lines * cfg.float.height)

  local win = vim.api.nvim_open_win(_buf, true, {
    relative = "editor",
    width    = width,
    height   = height,
    col      = math.floor((cols - width) / 2),
    row      = math.floor((lines - height) / 2),
    style    = "minimal",
    border   = cfg.float.border == "none" and "none" or cfg.float.border,
  })
  vim.cmd("startinsert")
  return win
end

local function open_builtin(cmd, cfg)
  -- Reuse existing buffer if the process is still running.
  if not _buf_valid() then
    _buf = vim.api.nvim_create_buf(false, true)
    vim.fn.termopen(cmd, {
      on_exit = function()
        -- Process exited — clean up and forget the buffer.
        local b = _buf
        _buf = nil
        for _, w in ipairs(vim.api.nvim_list_wins()) do
          if vim.api.nvim_win_is_valid(w) and vim.api.nvim_win_get_buf(w) == b then
            vim.api.nvim_win_close(w, true)
          end
        end
        if b and vim.api.nvim_buf_is_valid(b) then
          vim.api.nvim_buf_delete(b, { force = true })
        end
      end,
    })
  end

  -- If already visible, just focus it.
  local existing = _find_win()
  if existing then
    vim.api.nvim_set_current_win(existing)
    vim.cmd("startinsert")
    return
  end

  _open_float_win(cfg)
end

-- ---------------------------------------------------------------------------
-- Public API
-- ---------------------------------------------------------------------------

-- Open (or focus) the lazyagent window.
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

-- Toggle: hide the window if visible, show/create it if hidden.
function M.toggle()
  local cmd = M.config.cmd
  local win  = M.config.win

  -- snacks handles its own toggle
  if win == "float" and _G.Snacks and Snacks.terminal then
    if vim.fn.executable(cmd) == 0 then
      vim.notify("[lazyagent] " .. cmd .. " not found in PATH.", vim.log.levels.ERROR)
      return
    end
    open_snacks(cmd, M.config)
    return
  end

  -- builtin float: hide if visible, show if hidden
  if win == "float" then
    local w = _find_win()
    if w then
      -- Window is open — just close the window, keep the buffer alive.
      vim.api.nvim_win_close(w, false)
      return
    end
    M.open()
    return
  end

  -- For split/vsplit/tab there's no meaningful hide — just open.
  M.open()
end

return M
