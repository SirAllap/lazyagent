from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Select, Static


@dataclass
class CreateWorktreeResult:
    """Result returned when the create worktree modal is confirmed."""

    branch: str
    base_branch: str


class CreateWorktreeModal(ModalScreen[CreateWorktreeResult | None]):
    """Modal for entering branch name and selecting a base branch."""

    DEFAULT_CSS = """
    CreateWorktreeModal {
        align: center middle;
    }

    CreateWorktreeModal > Vertical {
        width: 56;
        height: auto;
        border: round $accent;
        background: transparent;
        padding: 1 2;
    }

    CreateWorktreeModal Input {
        border: round $secondary;
        background: transparent;
    }

    CreateWorktreeModal .input-wrap {
        height: auto;
        padding: 1 0;
    }

    CreateWorktreeModal Input:focus {
        border: round $accent;
    }

    CreateWorktreeModal Select {
        border: round $secondary;
        background: transparent;
    }

    CreateWorktreeModal Select:focus {
        border: round $accent;
    }

    CreateWorktreeModal SelectCurrent {
        background: transparent;
        border: none;
        padding: 0 1;
    }

    CreateWorktreeModal .modal-title {
        text-style: bold;
        margin-bottom: 1;
    }

    CreateWorktreeModal .modal-label {
        margin-top: 1;
        margin-bottom: 0;
    }

    CreateWorktreeModal .modal-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        default_branch: str = "master",
        branches: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._default_branch = default_branch
        self._branches = branches or [default_branch]

    def compose(self) -> ComposeResult:
        options = [(b, b) for b in self._branches]
        initial = self._default_branch if self._default_branch in self._branches else Select.NULL
        with Vertical():
            yield Static("Create worktree", classes="modal-title")
            yield Static("Branch name:", classes="modal-label")
            with Container(classes="input-wrap"):
                yield Input(placeholder="my-feature-branch", id="branch-input")
            yield Static("Base branch:", classes="modal-label")
            yield Select(options, value=initial, allow_blank=True, id="base-select")
            yield Static(
                "[dim]enter to confirm · esc to cancel[/dim]",
                classes="modal-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#branch-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "branch-input":
            select = self.query_one("#base-select", Select)
            if select.value is Select.NULL:
                select.focus()
            else:
                self._confirm()


    def _confirm(self) -> None:
        branch = self.query_one("#branch-input", Input).value.strip()
        if not branch:
            self.notify("Branch name is required", severity="warning")
            self.query_one("#branch-input", Input).focus()
            return
        select = self.query_one("#base-select", Select)
        if select.value is Select.NULL:
            self.notify("Base branch is required", severity="warning")
            select.focus()
            return
        self.dismiss(CreateWorktreeResult(branch=branch, base_branch=str(select.value)))

    def action_cancel(self) -> None:
        self.dismiss(None)
