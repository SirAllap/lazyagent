# lazyagent

A lazygit-inspired TUI for managing coding agents across git worktrees.

## Features

- **Multi-worktree management** — create, remove, and navigate git worktrees from one screen
- **Real-time agent output** — watch coding agents stream output as they work
- **Sentinel-based status detection** — automatically detects when agents finish or need input
- **Scrollback buffer** — scroll through agent output history with PageUp/PageDown or mouse wheel
- **Diff tab** — view working tree changes (tracked + untracked) without leaving the TUI
- **PR/CI status** — see pull request state, review status, and CI check results per worktree (requires `gh` CLI)
- **Embedded terminal pane** — interact with worktrees directly without leaving the TUI
- **Configurable agent provider** — run `claude`, `codex`, or `gemini` per repository
- **Configurable worktree commands** — override create/remove commands via `.lazyagent.toml`

## Installation

### Standalone (CLI)

```bash
uv tool install lazyagent   # recommended
# or
pipx install lazyagent
# or
pip install lazyagent
```

### Neovim / LazyVim

lazyagent is a lazy.nvim-installable plugin. Add this to your `lua/plugins/lazyagent.lua`:

```lua
return {
  "gioalcamofly/lazyagent",
  build = "pipx install lazyagent",
  cmd = { "LazyAgent", "LazyAgentToggle" },
  keys = {
    { "<leader>la", "<cmd>LazyAgent<cr>", desc = "LazyAgent" },
  },
  opts = {},
}
```

That's it — `:LazyAgent` opens the TUI in a floating window and `:LazyAgentToggle` toggles it.
The `build` step installs the Python CLI automatically when the plugin is first installed.

#### snacks.nvim (LazyVim default)

If you have [snacks.nvim](https://github.com/folke/snacks.nvim) (included in LazyVim by default),
the plugin uses `Snacks.terminal` automatically for proper toggle behaviour — the TUI persists
between opens instead of restarting each time.

#### Customisation

```lua
opts = {
  cmd = "lazyagent",   -- override if not in PATH
  win = "float",       -- "float" | "split" | "vsplit" | "tab"
  float = {
    border = "rounded", -- any nvim border style, or "none"
    width  = 0.92,      -- fraction of editor width
    height = 0.92,      -- fraction of editor height
  },
},
```

## Quick Start

```bash
cd your-repo
lazyagent
```

lazyagent discovers existing worktrees and lets you spawn coding agents in each one.
By default it launches `claude`; set `provider = "codex"` or `provider = "gemini"` in config to switch providers.

## Usage

### Keybindings

| Key | Action |
|-----|--------|
| `j` / `k` | Move down / up in sidebar |
| `Ctrl+K` | Focus sidebar |
| `Ctrl+J` | Focus agent pane |
| `Ctrl+D` | Focus diff pane |
| `Ctrl+L` | Focus terminal pane |
| `s` | Spawn agent in selected worktree |
| `x` | Stop agent in selected worktree |
| `c` | Create new worktree |
| `d` | Remove selected worktree |
| `r` | Refresh worktree list |
| `PageUp` / `PageDown` | Scroll terminal history |
| `?` | Show help |
| `q` | Quit |

### Workflow

1. Open lazyagent in a git repository
2. Press `c` to create worktrees for parallel tasks
3. Press `s` to spawn a coding agent in a worktree
4. Watch agent output in real time — status updates automatically when the agent finishes or needs input
5. Use `Ctrl+L` to drop into the terminal pane for manual interaction
6. Press `d` to clean up worktrees when done

## Configuration

Create a `.lazyagent.toml` in your repository root:

```toml
# Branch to base new worktrees on (default: "master")
default_branch = "main"

[agent]
# Agent CLI to launch in each worktree: "claude" (default), "codex", or "gemini"
provider = "codex"

[worktree]
# Custom command template for creating worktrees
# Available placeholders: {branch}, {name}, {base}, {path}, {repo}
create = "git worktree add -b {branch} ../{name} {base}"

# Custom command template for removing worktrees
remove = "git worktree remove ../{name}"
```

## Development

```bash
git clone https://github.com/gioalcamofly/lazyagent.git
cd lazyagent
uv sync --group dev
uv run pytest
```

## License

[AGPL-3.0](LICENSE)
