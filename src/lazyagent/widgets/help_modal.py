from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static


# (key, label, description) — None key = section header
_KEYBINDINGS: list[tuple[str | None, str, str]] = [
    # Navigation
    (None, "── Navigation ──", ""),
    ("1-5", "Focus worktrees / agent / diff / terminal / usage",
     "Press 1–5 to jump directly to a specific pane."),
    ("h / l", "Prev / next pane",
     "Cycle focus between panes left and right."),
    ("Alt+h/j/k/l", "Move panes (works inside terminals)",
     "Navigate between panes even from agent or terminal — no need to detach first."),
    ("j / k", "Move down / up",
     "Move selection in worktree list or scroll in focused pane."),
    ("[ / ]", "Previous / next tab",
     "Cycle between tabs in the Claude Usage panel (Usage, Stats, Tools)."),

    # Agents
    (None, "── Agents ──", ""),
    ("s", "Spawn new agent",
     "Launch a fresh agent session in the selected worktree."),
    ("S", "Continue last conversation",
     "Resume the most recent conversation with --continue flag."),
    ("R", "Resume a session",
     "Resume and pick from previous sessions with --resume flag."),
    ("x", "Stop agent in selected worktree",
     "Send kill signal to the running agent process."),
    ("alt+x", "Detach from agent / exit terminal",
     "Return focus from agent or terminal pane back to normal pane navigation."),

    # Worktrees
    (None, "── Worktrees ──", ""),
    ("c", "Create new worktree",
     "Open modal to create a new git worktree with a branch name."),
    ("d", "Remove selected worktree",
     "Remove the selected worktree (cannot remove the main worktree)."),
    ("r", "Refresh worktree list",
     "Reload worktree list from disk. Also happens automatically every 10s."),

    # Diff
    (None, "── Diff (vim motions) ──", ""),
    ("h/j/k/l", "Move cursor",
     "Navigate the diff view with vim-style cursor movement."),
    ("0 / $", "Start / end of line",
     "Jump to the beginning or end of the current line."),
    ("g / G", "Top / bottom",
     "Jump to the first or last line of the diff."),
    ("v / V / Ctrl+V", "Visual char / line / block mode",
     "Enter visual selection mode to highlight text for yanking."),
    ("y", "Yank selection to clipboard",
     "Copy the visually selected text to the system clipboard."),

    # Terminal
    (None, "── Terminal ──", ""),
    ("PageUp/Down", "Scroll terminal history",
     "Scroll through the terminal scrollback buffer."),
    ("Mouse wheel", "Scroll terminal history",
     "Use the mouse wheel to scroll terminal output."),
    ("Drag to select", "Select and copy text to clipboard",
     "Click and drag to select text, automatically copied on release."),

    # General
    (None, "── General ──", ""),
    ("?", "Show this help",
     "Toggle the keybindings reference panel."),
    ("q", "Quit",
     "Exit lazyagent. Prompts for confirmation if agents are still running."),
]


class _KeybindItem(ListItem):
    """A single keybinding entry or section header."""

    def __init__(self, key: str | None, label: str, description: str) -> None:
        super().__init__()
        self.key_str = key
        self.label_str = label
        self.description = description
        if key is None:
            self.add_class("--header")

    def compose(self) -> ComposeResult:
        if self.key_str is None:
            yield Static(f"[bold cyan]{self.label_str}[/bold cyan]", markup=True)
        else:
            yield Static(
                f"  [bold yellow]{self.key_str:<14}[/bold yellow] {self.label_str}",
                markup=True,
            )


class HelpModal(ModalScreen[None]):
    """Interactive keybinding browser with j/k navigation and descriptions."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }

    HelpModal > Vertical {
        width: 70;
        height: 85%;
        border: round $accent;
        background: transparent;
        padding: 0;
    }

    HelpModal .modal-title {
        text-style: bold;
        padding: 1 2 0 2;
    }

    HelpModal ListView {
        height: 1fr;
        padding: 0 1;
        background: transparent;
    }

    HelpModal ListView > ListItem {
        height: 1;
        padding: 0;
        background: transparent;
    }

    HelpModal ListView > ListItem.--header {
        color: $accent;
        height: 2;
        padding-top: 1;
    }

    HelpModal ListView > ListItem.-highlight {
        background: $accent 20%;
    }

    HelpModal #help-description {
        height: auto;
        min-height: 3;
        max-height: 5;
        border-top: solid $secondary;
        padding: 1 2;
        color: $text-muted;
    }

    HelpModal .modal-hint {
        color: $text-muted;
        padding: 0 2 1 2;
        height: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("question_mark", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        items = [_KeybindItem(key, label, desc) for key, label, desc in _KEYBINDINGS]
        with Vertical():
            yield Static("Keybindings", classes="modal-title")
            yield ListView(*items, id="help-list")
            yield Static("", id="help-description")
            yield Static(
                "[bold yellow]j/k[/bold yellow] navigate  "
                "[bold yellow]Esc[/bold yellow] close",
                classes="modal-hint",
                markup=True,
            )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        desc_widget = self.query_one("#help-description", Static)
        if event.item and isinstance(event.item, _KeybindItem) and event.item.description:
            desc_widget.update(event.item.description)
        else:
            desc_widget.update("")

    _NAV_ACTIONS = {
        "alt+h": "action_prev_pane",
        "alt+l": "action_next_pane",
        "alt+k": "action_pane_up",
        "alt+j": "action_pane_down",
    }

    def on_key(self, event: events.Key) -> None:
        action = self._NAV_ACTIONS.get(event.key)
        if action:
            self.dismiss(None)
            getattr(self.app, action)()

    def action_close(self) -> None:
        self.dismiss(None)
