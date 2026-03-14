# lazyagent

A lazygit-inspired TUI for managing coding agents across git worktrees.

## Features

- **Multi-worktree management** — create, remove, and navigate git worktrees from one screen
- **Real-time agent output** — watch coding agents stream output as they work
- **Automatic status detection** — detects when agents finish, need input, or hang
- **Visual attention indicators** — worktrees blink when an agent needs input or has exited
- **Scrollback buffer** — scroll through agent output history with PageUp/PageDown or mouse wheel
- **Diff tab** — view working tree changes (tracked + untracked) without leaving the TUI
- **PR/CI status** — see pull request state, review status, and CI check results per worktree (requires `gh` CLI)
- **Embedded terminal pane** — interact with worktrees directly without leaving the TUI
- **Claude usage panel** — live session/weekly token usage, cost breakdown, and burn rate (no external dependencies)
- **Configurable agent provider** — run `claude`, `codex`, or `gemini` per repository
- **Configurable worktree commands** — override create/remove commands via `.lazyagent.toml`
- **System theme detection** — automatically uses your OS dark/light mode preference

## Platform Support

| Platform | Status |
|----------|--------|
| Linux    | ✅ Full support |
| macOS    | ✅ Full support |
| Windows  | ❌ Not supported (requires Unix PTY) |

## Installation

### Standalone (CLI)

```bash
pipx install lazyagent   # recommended
# or
uv tool install lazyagent
# or
pip install lazyagent
```

### Neovim / LazyVim

lazyagent is a lazy.nvim-installable plugin. Add this to your `lua/plugins/lazyagent.lua`:

```lua
return {
  "SirAllap/lazyagent",
  build = "pipx install lazyagent",
  cmd = { "LazyAgent", "LazyAgentToggle" },
  keys = {
    { "<leader>la", "<cmd>LazyAgentToggle<cr>", desc = "LazyAgent" },
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
By default it launches `claude`; set `provider = "codex"` or `provider = "gemini"` in `.lazyagent.toml` to switch.

## Usage

### Layout

```
┌─[1] Worktrees──┬─[2] Agent  [3] Diff──────────────────────────┐
│                │                                                │
│  my-feature    │                                                │
│  fix-bug   ●   │   (agent output streams here)                  │
│                │                                                │
├────────────────┤                                                │
│  PR            ├─[4] Terminal──────────┬─[5] Usage─────────────┤
│  No PR         │                       │  Session  [██░░] 23%  │
│                │  $ _                  │  Weekly   [█░░░] 11%  │
└────────────────┴───────────────────────┴───────────────────────┘
```

A blinking `●` next to a worktree means the agent needs input or has exited.

### Keybindings

#### Global

| Key | Action |
|-----|--------|
| `j` / `k` | Move down / up in worktree list |
| `s` | Spawn agent in selected worktree |
| `S` | Continue last agent session |
| `R` | Resume a previous session |
| `x` | Stop agent (asks for confirmation) |
| `c` | Create new worktree |
| `d` | Remove selected worktree |
| `r` | Refresh worktree list and git status |
| `g` | Refresh git status only |
| `?` | Open help |
| `q` | Quit |

#### Navigation

| Key | Action |
|-----|--------|
| `1` – `5` | Jump directly to pane by number |
| `alt+h` / `alt+l` | Cycle panes left / right |
| `alt+j` / `alt+k` | Move between agent↔terminal or diff↔usage |
| `alt+u` / `alt+i` | Cycle to previous / next worktree |

#### Inside the scrollback buffer

| Key | Action |
|-----|--------|
| `PageUp` / `PageDown` | Scroll up / down |
| `y` | Copy selection to clipboard |

### Workflow

1. Open lazyagent in a git repository
2. Press `c` to create worktrees for parallel tasks
3. Press `s` to spawn a coding agent — optionally type an initial prompt in the dialog
4. Watch agent output in real time — the worktree blinks when it needs your attention
5. Press `alt+u` / `alt+i` to quickly jump between worktrees from any pane
6. Use `4` or `alt+j` to drop into the terminal pane for manual interaction
7. Press `d` to clean up worktrees when done

## Configuration

Create a `.lazyagent.toml` in your repository root:

```toml
# Branch to base new worktrees on (default: "master")
default_branch = "main"

[agent]
# Agent CLI to launch in each worktree: "claude" (default), "codex", or "gemini"
provider = "claude"

[worktree]
# Custom command template for creating worktrees
# Available placeholders: {branch}, {name}, {base}, {path}, {repo}
create = "git worktree add -b {branch} ../{name} {base}"

# Custom command template for removing worktrees
remove = "git worktree remove ../{name}"
```

### Theme

lazyagent automatically detects your OS dark/light mode preference (GNOME, KDE, macOS).
Override it by setting `TEXTUAL_THEME` before launching:

```bash
TEXTUAL_THEME=nord lazyagent
```

Available themes: `textual-dark`, `textual-light`, `nord`, `gruvbox`, `dracula`,
`tokyo-night`, `monokai`, `catppuccin-mocha`, `catppuccin-latte`, `solarized-dark`,
`solarized-light`, `rose-pine`, and more.

## Requirements

- Python 3.10+
- `git` with worktree support
- A Claude/Codex/Gemini CLI in your `PATH`
- `gh` CLI (optional — enables PR/CI status panel)

## Development

```bash
git clone https://github.com/SirAllap/lazyagent.git
cd lazyagent
uv sync --group dev
uv run pytest
```

## License

[AGPL-3.0](LICENSE)
