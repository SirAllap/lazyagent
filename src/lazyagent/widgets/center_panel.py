from __future__ import annotations

import hashlib
import os
import shlex

from rich.style import Style
from rich.text import Text
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import ContentSwitcher, Static, TabbedContent, TabPane

from lazyagent.agent_providers import (
    DEFAULT_AGENT_PROVIDER,
    env_exports,
    get_agent_provider,
)
from lazyagent.models import GitStatus
from lazyagent.styles import SCROLLBAR_CSS
from lazyagent.widgets.monitored_terminal import MonitoredTerminal
from lazyagent.widgets.scrollable_terminal import ScrollableTerminal

# Unset all history/preexec vars that the login shell may inherit.
# Uses bash ${!prefix@} expansion to catch everything at runtime.
_TERMINAL_UNSET_CMD = (
    'unset PROMPT_COMMAND ${!HIST@} ${!ATUIN_@} ${!MCFLY_@} ${!__@} 2>/dev/null;'
    ' true'
)


_DIFF_STYLES: list[tuple[tuple[str, ...], Style]] = [
    (("diff ", "index "), Style(dim=True)),
    (("--- ", "+++ "), Style(bold=True)),
    (("@@",), Style(color="cyan", bold=True)),
    (("+",), Style(color="green")),
    (("-",), Style(color="red")),
]


def _colorize_diff(diff_text: str) -> Text:
    """Parse a unified diff and return a Rich Text with syntax colours."""
    t = Text(no_wrap=False, overflow="fold")
    for line in diff_text.splitlines():
        style = next(
            (s for prefixes, s in _DIFF_STYLES if line.startswith(prefixes)),
            Style(),
        )
        t.append(line + "\n", style=style)
    return t


class DiffScroll(VerticalScroll, can_focus=True):
    """Scrollable diff view with vim-style j/k/ctrl+d/ctrl+u motions."""

    BINDINGS = [
        Binding("j", "scroll_down", show=False),
        Binding("k", "scroll_up", show=False),
        Binding("g", "scroll_home", show=False),
        Binding("G", "scroll_end", show=False),
        Binding("ctrl+d", "half_down", show=False),
        Binding("ctrl+u", "half_up", show=False),
    ]

    def action_half_down(self) -> None:
        self.scroll_relative(y=self.size.height // 2, animate=False)

    def action_half_up(self) -> None:
        self.scroll_relative(y=-(self.size.height // 2), animate=False)


def _panel_id(worktree_path: str) -> str:
    """Derive a DOM-safe ID from a worktree path."""
    return "wp-" + hashlib.md5(worktree_path.encode()).hexdigest()[:8]


class GitInfoBar(Horizontal):
    """Full-width status bar: branch/hash/subject on the left, git stats on the right."""

    DEFAULT_CSS = """
    GitInfoBar {
        height: 3;
        width: 1fr;
        background: transparent;
        border: round $secondary;
        border-title-color: $text-muted;
    }
    GitInfoBar #git-left {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
        color: $text;
    }
    GitInfoBar #git-right {
        width: auto;
        height: 1fr;
        padding: 0 1;
        color: $text;
    }
    """

    def compose(self):
        yield Static("", id="git-left", markup=True)
        yield Static("", id="git-right", markup=True)

    def on_mount(self) -> None:
        self.border_title = "Status"

    def update_status(self, git_status: GitStatus, branch: str, short_head: str = "") -> None:
        b = branch[:35] + "\u2026" if len(branch) > 35 else branch
        subj = git_status.last_commit_subject

        left_parts: list[str] = [f"[bold]{b}[/bold]"]
        if short_head:
            left_parts.append(f"[dim]{short_head}[/dim]")
        if subj:
            left_parts.append(f"[dim]{subj}[/dim]")

        right_parts: list[str] = []
        if git_status.dirty_count == 0:
            right_parts.append("[green]\u2713 clean[/green]")
        else:
            if git_status.staged:
                right_parts.append(f"[green]+{git_status.staged}[/green]")
            if git_status.unstaged:
                right_parts.append(f"[yellow]~{git_status.unstaged}[/yellow]")
            if git_status.untracked:
                right_parts.append(f"[dim]?{git_status.untracked}[/dim]")

        if git_status.has_upstream:
            if git_status.ahead == 0 and git_status.behind == 0:
                right_parts.append("[green]\u21910 \u21930[/green]")
            else:
                if git_status.ahead:
                    right_parts.append(f"[cyan]\u2191{git_status.ahead}[/cyan]")
                if git_status.behind:
                    right_parts.append(f"[red]\u2193{git_status.behind}[/red]")
        else:
            right_parts.append("[dim]no upstream[/dim]")

        try:
            self.query_one("#git-left", Static).update("  ".join(left_parts))
            self.query_one("#git-right", Static).update("  ".join(right_parts))
        except Exception:
            pass


class WorktreePanel(Container):
    """Per-worktree panel with Agent and Terminal panes in a vertical split."""

    DEFAULT_CSS = f"""
    WorktreePanel {{
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }}
    #agent-tabs {{
        height: 2fr;
        border: round $secondary;
        border-title-color: $text-muted;
    }}
    #agent-tabs:focus-within {{
        border: round $accent;
        border-title-color: $accent;
    }}
    #agent-tabs Tabs {{
        display: none;
    }}
    #agent-tab {{
        height: 1fr;
    }}
    #diff-tab {{
        height: 1fr;
    }}
    DiffScroll {{
        height: 1fr;
        width: 1fr;
        overflow-y: auto;
        overflow-x: hidden;
        background: transparent;
{SCROLLBAR_CSS}
    }}
    #diff-content {{
        width: 1fr;
        height: auto;
        padding: 0 1;
    }}
    #terminal-pane {{
        height: 1fr;
        border: round $secondary;
        border-title-color: $text-muted;
    }}
    #terminal-pane:focus-within {{
        border: round $accent;
        border-title-color: $accent;
    }}
    #agent-placeholder {{
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }}
    #terminal-placeholder {{
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }}
    ScrollableTerminal {{ height: 1fr; width: 1fr; }}
    """

    def __init__(self, worktree_path: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.worktree_path = worktree_path
        self._agent_terminal: MonitoredTerminal | None = None

    def compose(self):
        with TabbedContent(id="agent-tabs"):
            with TabPane("Agent", id="agent-tab"):
                yield Static(
                    "Press [bold]s[/bold] or [bold]2[/bold] to spawn agent",
                    id="agent-placeholder",
                )
            with TabPane("Diff", id="diff-tab"):
                with DiffScroll(id="diff-scroll"):
                    yield Static(
                        Text("No changes"),
                        id="diff-content",
                    )
        with Container(id="terminal-pane"):
            yield Static(
                "Terminal",
                id="terminal-placeholder",
            )

    def on_mount(self) -> None:
        terminal_pane = self.query_one("#terminal-pane", Container)
        terminal_pane.border_title = "[4] Terminal"
        self._update_tab_title("agent-tab")
        self._try_start_terminal()

    def _update_tab_title(self, active_pane_id: str) -> None:
        agent = "\\[2] Agent" if active_pane_id == "agent-tab" else "[dim]\\[2] Agent[/dim]"
        diff = "\\[3] Diff" if active_pane_id == "diff-tab" else "[dim]\\[3] Diff[/dim]"
        try:
            self.query_one("#agent-tabs", TabbedContent).border_title = f"{agent}  {diff}"
        except Exception:
            pass

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane is not None:
            self._update_tab_title(event.pane.id or "agent-tab")

    def _try_start_terminal(self) -> None:
        """Try to mount a real terminal widget."""
        try:
            placeholder = self.query_one("#terminal-placeholder", Static)
            pane = self.query_one("#terminal-pane", Container)
            placeholder.remove()
            shell = os.environ.get("SHELL", "bash")
            script = (
                f"{env_exports()}"
                f" && cd {shlex.quote(self.worktree_path)}"
                f" && {_TERMINAL_UNSET_CMD}"
                f" && export HISTFILE=/dev/null"
                f" && exec {shlex.quote(shell)} -l"
            )
            terminal = ScrollableTerminal(
                command=f"bash -c {shlex.quote(script)}",
                id="terminal-widget",
            )
            pane.mount(terminal)
            terminal.start()
        except Exception:
            pass

    def update_diff(self, diff_text: str) -> None:
        """Update the diff tab content."""
        try:
            diff_widget = self.query_one("#diff-content", Static)
            if diff_text:
                diff_widget.update(_colorize_diff(diff_text))
            else:
                diff_widget.update(Text("No changes"))
        except Exception:
            pass

    def switch_to_tab(self, tab_id: str) -> None:
        """Switch the TabbedContent to the given tab."""
        try:
            self.query_one("#agent-tabs", TabbedContent).active = tab_id
            self._update_tab_title(tab_id)
        except Exception:
            pass

    @property
    def agent_terminal(self) -> MonitoredTerminal | None:
        return self._agent_terminal

    @property
    def has_agent(self) -> bool:
        return (
            self._agent_terminal is not None
            and self._agent_terminal.emulator is not None
        )

    async def cleanup_agent(self) -> None:
        """Remove the agent terminal widget and restore the placeholder."""
        if self._agent_terminal is not None:
            self._agent_terminal.stop()
            await self._agent_terminal.remove()
            self._agent_terminal = None

        pane = self.query_one("#agent-tab", TabPane)
        try:
            pane.query_one("#agent-placeholder")
        except Exception:
            pane.mount(
                Static(
                    "Press [bold]s[/bold] or [bold]2[/bold] to spawn agent",
                    id="agent-placeholder",
                )
            )

    async def spawn_agent(
        self,
        skip_permissions: bool = False,
        agent_provider: str = DEFAULT_AGENT_PROVIDER,
    ) -> None:
        """Spawn the configured coding agent process in the Agent pane."""
        pane = self.query_one("#agent-tab", TabPane)

        # Remove previous terminal or placeholder (await to ensure DOM is clean
        # before mounting the new widget with the same ID).
        if self._agent_terminal is not None:
            self._agent_terminal.stop()
            await self._agent_terminal.remove()
            self._agent_terminal = None

        try:
            placeholder = self.query_one("#agent-placeholder", Static)
            await placeholder.remove()
        except Exception:
            pass

        provider = get_agent_provider(agent_provider)
        command = provider.build_command(
            self.worktree_path,
            skip_permissions=skip_permissions,
        )

        terminal = MonitoredTerminal(
            command=command,
            worktree_path=self.worktree_path,
            id="agent-terminal",
        )
        self._agent_terminal = terminal
        pane.mount(terminal)
        terminal.start()

        # Focus the terminal so the user can type immediately
        terminal.focus()


class CenterPanel(Container):
    """Container managing a ContentSwitcher of WorktreePanels."""

    DEFAULT_CSS = """
    CenterPanel {
        width: 1fr;
        height: 1fr;
    }
    ContentSwitcher { height: 1fr; }
    #center-placeholder {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._panels: dict[str, str] = {}  # worktree_path -> panel DOM id

    def compose(self):
        yield Static(
            "Select a worktree to begin",
            id="center-placeholder",
        )
        yield ContentSwitcher(id="panel-switcher", initial=None)

    def ensure_panel(self, worktree_path: str) -> WorktreePanel:
        """Get or lazily create a WorktreePanel for the given worktree."""
        if worktree_path in self._panels:
            panel_id = self._panels[worktree_path]
            return self.query_one(f"#{panel_id}", WorktreePanel)

        panel_id = _panel_id(worktree_path)
        panel = WorktreePanel(worktree_path, id=panel_id)
        switcher = self.query_one("#panel-switcher", ContentSwitcher)
        switcher.mount(panel)
        self._panels[worktree_path] = panel_id
        return panel

    def switch_to(self, worktree_path: str) -> WorktreePanel:
        """Switch the visible panel to the given worktree (creating if needed)."""
        panel = self.ensure_panel(worktree_path)
        panel_id = self._panels[worktree_path]

        placeholder = self.query_one("#center-placeholder", Static)
        placeholder.display = False

        switcher = self.query_one("#panel-switcher", ContentSwitcher)
        switcher.current = panel_id
        return panel

    def get_panel(self, worktree_path: str) -> WorktreePanel | None:
        """Get existing panel or None."""
        if worktree_path not in self._panels:
            return None
        panel_id = self._panels[worktree_path]
        return self.query_one(f"#{panel_id}", WorktreePanel)
