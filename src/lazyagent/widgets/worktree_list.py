from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.widgets import ListItem, ListView, Static

from lazyagent.models import AgentState, AgentStatus, GitStatus, WorktreeInfo


class WorktreeListItem(ListItem):
    """A single worktree entry in the sidebar list."""

    DEFAULT_CSS = """
    WorktreeListItem {
        height: 6;
    }
    """

    def __init__(self, worktree: WorktreeInfo, agent_state: AgentState | None = None) -> None:
        super().__init__()
        self.worktree = worktree
        self._agent_state = agent_state or AgentState()
        self._git_status: GitStatus | None = None
        if worktree.is_main:
            self.add_class("--main")

    def watch_highlighted(self, value: bool) -> None:
        super().watch_highlighted(value)
        self.styles.background = "transparent"

    def on_enter(self, event: events.Enter) -> None:
        self.styles.background = "transparent"

    def on_leave(self, event: events.Leave) -> None:
        self.styles.background = "transparent"

    def compose(self):
        yield Static(self._build_label(), markup=True, id="wt-label")

    def _build_label(self) -> str:
        label = self.worktree.display_label
        branch = self.worktree.display_branch
        status = self._status_line()
        git = self._git_status_line()
        return f"[bold]{label}[/bold]\n[dim]{branch}[/dim]\n{status}\n{git}"

    def _status_line(self) -> str:
        match self._agent_state.status:
            case AgentStatus.RUNNING:
                return "[green]running[/green]"
            case AgentStatus.WAITING:
                return "[bold yellow]waiting[/bold yellow]"
            case AgentStatus.POSSIBLY_HANGED:
                return "[bold red]hanged?[/bold red]"
            case _:
                return "[dim]---[/dim]"

    def _git_status_line(self) -> str:
        gs = self._git_status
        if gs is None:
            return "[dim]...[/dim]"
        parts: list[str] = []
        if gs.dirty_count > 0:
            parts.append(f"[yellow]*{gs.dirty_count}[/yellow]")
        else:
            parts.append("[green]clean[/green]")
        if gs.has_upstream:
            if gs.ahead:
                parts.append(f"[cyan]\u2191{gs.ahead}[/cyan]")
            if gs.behind:
                parts.append(f"[red]\u2193{gs.behind}[/red]")
        return " ".join(parts)

    def _refresh_label(self) -> None:
        try:
            self.query_one("#wt-label", Static).update(self._build_label())
        except Exception:
            pass

    def update_agent_state(self, state: AgentState) -> None:
        self._agent_state = state
        self._refresh_label()

    def update_git_status(self, git_status: GitStatus) -> None:
        self._git_status = git_status
        self._refresh_label()


class WorktreeList(ListView):
    """Sidebar list of git worktrees with j/k navigation."""

    DEFAULT_CSS = """
    WorktreeList {
        height: 1fr;
        border: round $secondary;
        border-title-color: $text-muted;
        background: transparent;
    }
    WorktreeList:focus-within {
        border: round $accent;
        border-title-color: $accent;
    }
    WorktreeList WorktreeListItem {
        background: transparent;
        border: round transparent;
        color: $text-muted;
        padding: 0 1;
    }
    WorktreeList WorktreeListItem.--main {
        border: round $secondary;
        color: $text;
    }
    WorktreeList WorktreeListItem.-highlight {
        background: transparent;
        border: round $accent;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def on_mount(self) -> None:
        self.border_title = "[1] Worktrees"

    def set_worktrees(self, worktrees: list[WorktreeInfo]) -> None:
        """Replace the list contents with the given worktrees."""
        self.clear()
        for wt in worktrees:
            self.append(WorktreeListItem(wt))

    def update_agent_state(self, worktree_path: str, state: AgentState) -> None:
        """Find the item for the given worktree path and update its agent state."""
        for child in self.children:
            if isinstance(child, WorktreeListItem) and child.worktree.path == worktree_path:
                child.update_agent_state(state)
                break

    def update_all_git_statuses(self, statuses: dict[str, GitStatus]) -> None:
        """Bulk update git status on all items."""
        for child in self.children:
            if isinstance(child, WorktreeListItem) and child.worktree.path in statuses:
                child.update_git_status(statuses[child.worktree.path])
