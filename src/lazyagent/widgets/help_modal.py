from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


_HELP_TEXT = """\
[bold cyan]Navigation[/bold cyan]
  [bold yellow]1 / 2 / 3 / 4[/bold yellow]  Focus worktrees / agent / diff / terminal
  [bold yellow]h / l[/bold yellow]           Prev / next pane
  [bold yellow]j / k[/bold yellow]           Move down / up in worktrees

[bold cyan]Agents[/bold cyan]
  [bold yellow]s[/bold yellow]               Spawn agent in selected worktree
  [bold yellow]x[/bold yellow]               Stop agent in selected worktree

[bold cyan]Worktrees[/bold cyan]
  [bold yellow]c[/bold yellow]               Create new worktree
  [bold yellow]d[/bold yellow]               Remove selected worktree
  [bold yellow]r[/bold yellow]               Refresh worktree list

[bold cyan]Terminal[/bold cyan]
  [bold yellow]alt+x[/bold yellow]              Exit terminal focus (back to panes)
  [bold yellow]PageUp / PageDown[/bold yellow]  Scroll terminal history
  [bold yellow]Mouse wheel[/bold yellow]        Scroll terminal history
  [bold yellow]Drag to select[/bold yellow]     Select and copy text to clipboard

[bold cyan]General[/bold cyan]
  [bold yellow]?[/bold yellow]               Show this help
  [bold yellow]q[/bold yellow]               Quit\
"""


class HelpModal(ModalScreen[None]):
    """Modal showing all keyboard shortcuts."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }

    HelpModal > Vertical {
        width: 52;
        height: auto;
        border: round $accent;
        background: transparent;
        padding: 1 2;
    }

    HelpModal .modal-title {
        text-style: bold;
        margin-bottom: 1;
    }

    HelpModal .modal-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("question_mark", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Keyboard Shortcuts", classes="modal-title")
            yield Static(_HELP_TEXT)
            yield Static(
                "[bold yellow]Esc[/bold yellow] / [bold yellow]q[/bold yellow] / [bold yellow]?[/bold yellow]  close",
                classes="modal-hint",
            )

    def action_close(self) -> None:
        self.dismiss(None)
