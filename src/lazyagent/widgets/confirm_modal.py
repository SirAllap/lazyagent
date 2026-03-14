from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

_NAV_ACTIONS = {
    "alt+h": "action_prev_pane",
    "alt+l": "action_next_pane",
    "alt+k": "action_pane_up",
    "alt+j": "action_pane_down",
}


class ConfirmModal(ModalScreen[bool]):
    """Generic yes/no confirmation modal."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }

    ConfirmModal > Vertical {
        width: 50;
        height: auto;
        border: round $accent;
        background: transparent;
        padding: 1 2;
    }

    ConfirmModal .modal-title {
        text-style: bold;
        margin-bottom: 1;
    }

    ConfirmModal .modal-body {
        margin-bottom: 1;
    }

    ConfirmModal .modal-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=False),
        Binding("n", "deny", "No", show=False),
        Binding("escape", "deny", "Cancel", show=False),
    ]

    def __init__(self, title: str, body: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._title_text = title
        self._body_text = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title_text, classes="modal-title")
            yield Static(self._body_text, classes="modal-body")
            yield Static(
                "[bold cyan]y[/bold cyan] yes  [bold cyan]n[/bold cyan] no",
                classes="modal-hint",
            )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def on_key(self, event: events.Key) -> None:
        action = _NAV_ACTIONS.get(event.key)
        if action:
            self.dismiss(False)
            getattr(self.app, action)()

    def action_deny(self) -> None:
        self.dismiss(False)
