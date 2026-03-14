from __future__ import annotations

import asyncio
import hashlib
import os
import shlex

from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
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
from lazyagent.widgets.usage_panel import UsagePanel

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


class DiffView(ScrollView, can_focus=True):
    """Diff viewer with vim cursor, visual mode (v/V/Ctrl+V), and yank."""

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("h", "cursor_left", show=False),
        Binding("l", "cursor_right", show=False),
        Binding("g", "go_top", show=False),
        Binding("G", "go_bottom", show=False),
        Binding("ctrl+d", "half_down", show=False),
        Binding("ctrl+u", "half_up", show=False),
        Binding("v", "visual_char", show=False),
        Binding("V", "visual_line", show=False),
        Binding("y", "yank", show=False),
        Binding("escape", "escape_visual", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._lines: list[str] = []
        self._cursor_row: int = 0
        self._cursor_col: int = 0
        self._mode: str | None = None  # None=normal, "char", "line", "block"
        self._anchor: tuple[int, int] = (0, 0)

    # ------------------------------------------------------------------
    # Content management
    # ------------------------------------------------------------------

    def set_diff(self, diff_text: str) -> None:
        """Replace the diff content.  Preserves cursor if unchanged."""
        new_lines = diff_text.splitlines() if diff_text else []
        if new_lines == self._lines:
            return
        self._lines = new_lines
        self._mode = None
        self._cursor_row = min(self._cursor_row, self._max_row)
        self._clamp_col()
        self._update_virtual_size()
        self.refresh()

    def on_mount(self) -> None:
        self._update_virtual_size()

    def on_resize(self, event: events.Resize) -> None:
        self._update_virtual_size()

    def _update_virtual_size(self) -> None:
        w = self.scrollable_content_region.width or self.size.width or 80
        self.virtual_size = Size(w, max(len(self._lines), 1))

    # ------------------------------------------------------------------
    # Cursor helpers
    # ------------------------------------------------------------------

    @property
    def _max_row(self) -> int:
        return max(0, len(self._lines) - 1)

    def _line_len(self, row: int) -> int:
        if 0 <= row < len(self._lines):
            return len(self._lines[row])
        return 0

    def _clamp_col(self) -> None:
        max_c = max(0, self._line_len(self._cursor_row) - 1)
        self._cursor_col = max(0, min(self._cursor_col, max_c))

    def _move_cursor(self, row: int | None = None, col: int | None = None) -> None:
        if row is not None:
            self._cursor_row = max(0, min(row, self._max_row))
        if col is not None:
            self._cursor_col = col
        self._clamp_col()
        self._scroll_cursor_visible()
        self.refresh()

    def _scroll_cursor_visible(self) -> None:
        sy = self.scroll_offset.y
        h = self.scrollable_content_region.height or self.size.height or 1
        if self._cursor_row < sy:
            self.scroll_to(y=self._cursor_row, animate=False)
        elif self._cursor_row >= sy + h:
            self.scroll_to(y=self._cursor_row - h + 1, animate=False)

    # ------------------------------------------------------------------
    # Actions — movement
    # ------------------------------------------------------------------

    def action_cursor_down(self) -> None:
        self._move_cursor(row=self._cursor_row + 1)

    def action_cursor_up(self) -> None:
        self._move_cursor(row=self._cursor_row - 1)

    def action_cursor_left(self) -> None:
        self._move_cursor(col=self._cursor_col - 1)

    def action_cursor_right(self) -> None:
        self._move_cursor(col=self._cursor_col + 1)

    def action_go_top(self) -> None:
        self._move_cursor(row=0, col=0)

    def action_go_bottom(self) -> None:
        self._move_cursor(row=self._max_row, col=0)

    def action_half_down(self) -> None:
        h = self.scrollable_content_region.height or self.size.height or 1
        self._move_cursor(row=self._cursor_row + h // 2)

    def action_half_up(self) -> None:
        h = self.scrollable_content_region.height or self.size.height or 1
        self._move_cursor(row=self._cursor_row - h // 2)

    # ------------------------------------------------------------------
    # Actions — visual modes & yank
    # ------------------------------------------------------------------

    def action_visual_char(self) -> None:
        if self._mode == "char":
            self._mode = None
        else:
            self._mode = "char"
            self._anchor = (self._cursor_row, self._cursor_col)
        self.refresh()

    def action_visual_line(self) -> None:
        if self._mode == "line":
            self._mode = None
        else:
            self._mode = "line"
            self._anchor = (self._cursor_row, self._cursor_col)
        self.refresh()

    def action_escape_visual(self) -> None:
        self._mode = None
        self.refresh()

    async def action_yank(self) -> None:
        if self._mode is None:
            return
        text = self._get_selected_text()
        if text:
            await self._copy_to_clipboard(text)
            self.notify(f"Yanked {len(text)} chars")
        self._mode = None
        self.refresh()

    # ------------------------------------------------------------------
    # Key handler for special keys (Ctrl+V, $, 0)
    # ------------------------------------------------------------------

    async def on_key(self, event: events.Key) -> None:
        if event.key == "ctrl+v":
            event.stop()
            event.prevent_default()
            if self._mode == "block":
                self._mode = None
            else:
                self._mode = "block"
                self._anchor = (self._cursor_row, self._cursor_col)
            self.refresh()
            return
        if event.character == "$":
            event.stop()
            event.prevent_default()
            self._move_cursor(col=max(0, self._line_len(self._cursor_row) - 1))
            return
        if event.character == "0":
            event.stop()
            event.prevent_default()
            self._move_cursor(col=0)
            return

    # ------------------------------------------------------------------
    # Focus events — redraw cursor visibility
    # ------------------------------------------------------------------

    def on_focus(self, event: events.Focus) -> None:
        self.refresh()

    def on_blur(self, event: events.Blur) -> None:
        self.refresh()

    # ------------------------------------------------------------------
    # Line API rendering
    # ------------------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        virtual_y = scroll_y + y
        width = self.scrollable_content_region.width or self.size.width or 80

        if virtual_y >= len(self._lines):
            if virtual_y == 0 and not self._lines:
                # Show placeholder when empty
                txt = Text("No changes", style=Style(dim=True))
                txt = txt.plain.center(width)
                return Strip(list(Text(txt, style=Style(dim=True)).render(self.app.console)))
            return Strip.blank(width, self.rich_style)

        raw = self._lines[virtual_y]
        style = next(
            (s for prefixes, s in _DIFF_STYLES if raw.startswith(prefixes)),
            Style(),
        )
        # Pad to full width so cursor/selection is visible on short lines
        display = raw.ljust(width)
        text = Text(display, no_wrap=True)
        text.stylize(style, 0, len(display))

        if self.has_focus:
            # Selection highlight (visual mode)
            if self._mode is not None:
                sel = self._sel_cols_for_row(virtual_y, width)
                if sel:
                    c0, c1 = sel
                    text.stylize(Style(reverse=True), c0, min(c1, len(display)))

            # Cursor — only in normal mode (in visual mode it blends
            # into the selection highlight, matching vim behaviour)
            if self._mode is None and virtual_y == self._cursor_row:
                cx = self._cursor_col
                if 0 <= cx < len(display):
                    text.stylize(Style(reverse=True), cx, cx + 1)

        segments = list(text.render(self.app.console))
        return Strip(segments).crop_extend(scroll_x, scroll_x + width, self.rich_style)

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _sel_cols_for_row(self, row: int, width: int) -> tuple[int, int] | None:
        """Return (col_start, col_end) to highlight on *row*, or None."""
        if self._mode is None:
            return None
        ar, ac = self._anchor
        cr, cc = self._cursor_row, self._cursor_col

        if self._mode == "line":
            r0, r1 = min(ar, cr), max(ar, cr)
            return (0, width) if r0 <= row <= r1 else None

        if self._mode == "block":
            r0, r1 = min(ar, cr), max(ar, cr)
            c0, c1 = min(ac, cc), max(ac, cc)
            return (c0, c1 + 1) if r0 <= row <= r1 else None

        # char mode
        start, end = (ar, ac), (cr, cc)
        if start > end:
            start, end = end, start
        sr, sc = start
        er, ec = end
        if row < sr or row > er:
            return None
        if sr == er:
            return (sc, ec + 1)
        if row == sr:
            return (sc, width)
        if row == er:
            return (0, ec + 1)
        return (0, width)

    def _get_selected_text(self) -> str:
        """Extract the text covered by the current visual selection."""
        if self._mode is None or not self._lines:
            return ""
        ar, ac = self._anchor
        cr, cc = self._cursor_row, self._cursor_col

        if self._mode == "line":
            r0, r1 = min(ar, cr), max(ar, cr)
            return "\n".join(self._lines[r] for r in range(r0, r1 + 1) if r < len(self._lines))

        if self._mode == "block":
            r0, r1 = min(ar, cr), max(ar, cr)
            c0, c1 = min(ac, cc), max(ac, cc)
            parts: list[str] = []
            for r in range(r0, r1 + 1):
                ln = self._lines[r] if r < len(self._lines) else ""
                parts.append(ln[c0 : c1 + 1].rstrip())
            return "\n".join(parts)

        # char mode
        start, end = (ar, ac), (cr, cc)
        if start > end:
            start, end = end, start
        sr, sc = start
        er, ec = end
        if sr == er:
            return self._lines[sr][sc : ec + 1]
        lines: list[str] = [self._lines[sr][sc:]]
        for r in range(sr + 1, er):
            lines.append(self._lines[r])
        lines.append(self._lines[er][: ec + 1])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------

    async def _copy_to_clipboard(self, text: str) -> None:
        encoded = text.encode()
        for cmd in (
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.communicate(encoded), timeout=2.0)
                if proc.returncode == 0:
                    return
            except Exception:
                continue


class _FocusableStatic(Static, can_focus=True):
    """Static that can receive focus (for placeholder panes)."""


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
    #agent-tabs ContentSwitcher {{
        height: 1fr;
    }}
    #agent-tabs TabPane {{
        height: 1fr;
    }}
    #agent-tab {{
        height: 1fr;
    }}
    #diff-tab {{
        height: 1fr;
    }}
    DiffView {{
        height: 1fr;
        width: 1fr;
        overflow-y: auto;
        overflow-x: hidden;
        background: transparent;
{SCROLLBAR_CSS}
    }}
    #bottom-row {{
        height: 1fr;
        layout: horizontal;
    }}
    #terminal-pane {{
        width: 2fr;
        height: 1fr;
        border: round $secondary;
        border-title-color: $text-muted;
    }}
    #terminal-pane:focus-within {{
        border: round $accent;
        border-title-color: $accent;
    }}
    #usage-panel {{
        width: 1fr;
        height: 1fr;
    }}
    #agent-placeholder {{
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }}
    #agent-placeholder:focus {{
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
                yield _FocusableStatic(
                    "[bold]s[/bold] spawn · [bold]S[/bold] continue · [bold]R[/bold] resume",
                    id="agent-placeholder",
                )
            with TabPane("Diff", id="diff-tab"):
                yield DiffView(id="diff-scroll")
        with Horizontal(id="bottom-row"):
            with Container(id="terminal-pane"):
                yield Static(
                    "Terminal",
                    id="terminal-placeholder",
                )
            yield UsagePanel(id="usage-panel")

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

    def on_click(self, event: events.Click) -> None:
        """Click on agent-tabs border title to switch between Agent and Diff."""
        tabs = self.query_one("#agent-tabs", TabbedContent)
        if event.screen_y == tabs.region.y:
            # Clicked on the top border — find Agent vs Diff
            local_x = event.screen_x - tabs.region.x
            # Title: "╭─ [2] Agent  [3] Diff ─╮"
            # Agent starts around pos 3, Diff starts around pos 15
            title_text = "[2] Agent  [3] Diff"
            agent_end = 3 + len("[2] Agent")
            diff_start = agent_end + 2  # two spaces
            if 3 <= local_x < agent_end:
                self.switch_to_tab("agent-tab")
            elif diff_start <= local_x < diff_start + len("[3] Diff"):
                self.switch_to_tab("diff-tab")

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
                restart_on_disconnect=True,
                id="terminal-widget",
            )
            pane.mount(terminal)
            terminal.start()
        except Exception:
            pass

    def update_diff(self, diff_text: str) -> None:
        """Update the diff tab content."""
        try:
            self.query_one("#diff-scroll", DiffView).set_diff(diff_text)
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
                    "[bold]s[/bold] spawn · [bold]S[/bold] continue · [bold]R[/bold] resume",
                    id="agent-placeholder",
                )
            )

    async def spawn_agent(
        self,
        skip_permissions: bool = False,
        agent_provider: str = DEFAULT_AGENT_PROVIDER,
        resume: bool = False,
        continue_last: bool = False,
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
            resume=resume,
            continue_last=continue_last,
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
