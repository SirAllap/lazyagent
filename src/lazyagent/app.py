from __future__ import annotations

import argparse
import os
import subprocess
import sys

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from lazyagent.config import Config, format_command, load_config
from lazyagent.messages import AgentExited, AgentStatusChanged
from lazyagent.models import AgentState, AgentStatus, GitStatus, WorktreeInfo
from lazyagent.widgets.center_panel import CenterPanel, GitInfoBar
from lazyagent.widgets.confirm_modal import ConfirmModal
from lazyagent.widgets.help_modal import HelpModal
from lazyagent.widgets.create_worktree_modal import CreateWorktreeModal, CreateWorktreeResult
from lazyagent.widgets.pr_status_bar import PrStatusBar
from lazyagent.widgets.prompt_modal import SpawnModal
from lazyagent.widgets.usage_panel import UsagePanel
from lazyagent.widgets.monitored_terminal import MonitoredTerminal
from lazyagent.widgets.scrollable_terminal import ScrollableTerminal
from lazyagent.widgets.worktree_list import WorktreeList, WorktreeListItem
from lazyagent.worktree_manager import WorktreeManager, WorktreeManagerError, find_repo_root

def _system_color_scheme() -> str | None:
    """Detect system preferred color scheme via gsettings (GNOME/GTK).

    Returns 'dark', 'light', or None if undetectable.
    """
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            val = result.stdout.strip().strip("'")
            if "dark" in val:
                return "dark"
            if "light" in val:
                return "light"
    except Exception:
        pass
    return None


_CMD_SENT_MSG = "Command sent to terminal — press r to refresh when done"
_SEP = " [dim yellow]|[/dim yellow] "


def _hint(label: str, key: str) -> str:
    return f"[yellow]{label}:[/yellow] [bold yellow]{key}[/bold yellow]"


class _StopConfirmModal(ModalScreen[bool]):
    """Quick confirm: press x again to stop, c/esc to cancel."""

    DEFAULT_CSS = """
    _StopConfirmModal {
        align: center middle;
    }
    _StopConfirmModal > Vertical {
        width: 45;
        height: auto;
        border: round $accent;
        background: transparent;
        padding: 1 2;
    }
    _StopConfirmModal .modal-title {
        text-style: bold;
        margin-bottom: 1;
    }
    _StopConfirmModal .modal-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("x", "confirm", show=False),
        Binding("c", "cancel", show=False),
        Binding("escape", "cancel", show=False),
    ]

    _NAV_ACTIONS = {
        "alt+h": "action_prev_pane",
        "alt+l": "action_next_pane",
        "alt+k": "action_pane_up",
        "alt+j": "action_pane_down",
    }

    def __init__(self, worktree_label: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._label = worktree_label

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"Stop agent in [bold]{self._label}[/bold]?",
                classes="modal-title",
            )
            yield Static(
                "[bold yellow]x[/bold yellow] confirm stop  "
                "[bold cyan]c[/bold cyan] cancel",
                classes="modal-hint",
            )

    def on_key(self, event: events.Key) -> None:
        action = self._NAV_ACTIONS.get(event.key)
        if action:
            self.dismiss(False)
            getattr(self.app, action)()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class LazyAgent(App):
    """Textual TUI for managing coding agents across git worktrees."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    App {
        background: transparent;
    }
    Screen {
        layout: vertical;
        background: transparent;
    }
    #global-status-bar {
        height: 3;
        background: transparent;
    }
    #main-area {
        height: 1fr;
        layout: horizontal;
        padding-bottom: 0;
        background: transparent;
    }
    #sidebar {
        width: 36;
        layout: vertical;
        background: transparent;
    }
    #pr-status-bar {
        height: auto;
        min-height: 5;
        max-height: 9;
    }
    #key-hints {
        height: 1;
        background: transparent;
        padding: 0 1;
    }

    /* Agent zoom mode — triggered when MonitoredTerminal has focus */
    .agent-zoomed #sidebar {
        width: 7;
    }
    .agent-zoomed #pr-status-bar {
        display: none;
    }
    .agent-zoomed WorktreeList {
        border: round $secondary;
    }
    .agent-zoomed WorktreeList WorktreeListItem {
        height: 3;
        border: round transparent;
        padding: 0;
    }
    .agent-zoomed WorktreeList WorktreeListItem.--main {
        border: round $secondary;
    }
    .agent-zoomed WorktreeList WorktreeListItem.-highlight {
        border: round $accent;
    }
    .agent-zoomed WorktreeList WorktreeListItem.--attention {
        background: $warning 30%;
        border: round $warning;
    }
    .agent-zoomed WorktreeList WorktreeListItem.--attention.-highlight {
        background: $warning 30%;
        border: round $warning;
    }
    .agent-zoomed #bottom-row {
        height: 3;
    }
    .agent-zoomed #terminal-pane {
        height: 3;
    }
    .agent-zoomed #agent-tabs {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("s", "spawn_agent", "Spawn"),
        Binding("S", "continue_agent", show=False),
        Binding("R", "resume_agent", show=False),
        Binding("x", "stop_agent", "Stop"),
        Binding("c", "create_worktree", "Create"),
        Binding("d", "remove_worktree", "Remove"),
        Binding("1", "focus_sidebar", show=False),
        Binding("2", "focus_agent", show=False),
        Binding("3", "focus_diff", show=False),
        Binding("4", "focus_terminal", show=False),
        Binding("5", "focus_usage", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("l", "next_pane", show=False),
        Binding("h", "prev_pane", show=False),
        Binding("alt+l", "next_pane", show=False, priority=True),
        Binding("alt+h", "prev_pane", show=False, priority=True),
        Binding("alt+k", "pane_up", show=False, priority=True),
        Binding("alt+j", "pane_down", show=False, priority=True),
        Binding("alt+u", "prev_worktree", show=False, priority=True),
        Binding("alt+i", "next_worktree", show=False, priority=True),
    ]

    def __init__(self, repo_path: str | None = None) -> None:
        super().__init__()
        self.repo_path = repo_path
        self.worktrees: list[WorktreeInfo] = []
        self._agent_states: dict[str, AgentState] = {}
        self._git_statuses: dict[str, GitStatus] = {}
        self._selected_worktree: WorktreeInfo | None = None
        self._config: Config = Config()
        self._repo_root: str = ""
        self._gh_available: bool | None = None
        self._current_focus_pane: int = 1

    def compose(self) -> ComposeResult:
        yield GitInfoBar(id="global-status-bar")
        with Horizontal(id="main-area"):
            with Vertical(id="sidebar"):
                yield WorktreeList()
                yield PrStatusBar(id="pr-status-bar")
            yield CenterPanel()
        yield Static("", id="key-hints", markup=True)

    def on_mount(self) -> None:
        if not os.environ.get("TEXTUAL_THEME"):
            scheme = _system_color_scheme()
            if scheme == "light":
                self.theme = "textual-light"
            elif scheme == "dark":
                self.theme = "textual-dark"
        self._load_worktrees()
        self._load_config()
        self.set_interval(60, self._check_hangs)
        self.set_interval(10, self._poll_worktrees)
        self.set_interval(30, self._refresh_git_statuses)
        self.set_interval(30, self._refresh_selected_diff)
        self.set_interval(60, self._refresh_pr_status)
        self.set_interval(60, self._refresh_usage_panel)

    def on_descendant_focus(self, event) -> None:
        zoomed = isinstance(event.widget, MonitoredTerminal)
        self.set_class(zoomed, "agent-zoomed")
        try:
            wt_list = self.query_one(WorktreeList)
            wt_list.set_compact(zoomed)
            wt_list.border_title = "" if zoomed else "[1] Worktrees"
        except Exception:
            pass
        try:
            for panel in self.query(UsagePanel):
                panel.set_compact(zoomed)
        except Exception:
            pass
        self._update_key_hints(event.widget)

    def _update_key_hints(self, focused_widget=None) -> None:
        """Build contextual key hints based on focused pane and worktree state."""
        wt = self._selected_worktree
        agent_status = self._get_agent_state(wt.path).status if wt else AgentStatus.NO_AGENT
        has_agent = agent_status in (AgentStatus.RUNNING, AgentStatus.WAITING, AgentStatus.POSSIBLY_HANGED)
        is_main = wt.is_main if wt else True

        hints: list[str] = []

        if isinstance(focused_widget, MonitoredTerminal):
            # Agent pane focused
            hints.append(_hint("Detach", "alt+x"))
            hints.append(_hint("Navigate", "alt+hjkl"))
            hints.append(_hint("Prev/Next WT", "alt+u/i"))
        elif isinstance(focused_widget, ScrollableTerminal):
            # Terminal pane focused
            hints.append(_hint("Exit terminal", "alt+x"))
            hints.append(_hint("Navigate", "alt+hjkl"))
            hints.append(_hint("Prev/Next WT", "alt+u/i"))
        elif isinstance(focused_widget, UsagePanel):
            # Usage pane focused
            tab_names = ["Usage", "Stats", "Tools"]
            current = tab_names[focused_widget._tab_index]
            hints.append(_hint(f"Tab", f"[bold]{current}[/bold]"))
            hints.append(_hint("Prev tab", "\["))
            hints.append(_hint("Next tab", "]"))
            hints.append(_hint("Scroll", "j/k"))
            hints.append(_hint("Navigate", "alt+hjkl"))
        else:
            # Sidebar, diff, or other pane
            hints.append(_hint("Up/Down", "j/k"))
            hints.append(_hint("Navigate", "alt+hjkl"))

            if not has_agent:
                hints.append(_hint("Spawn", "s"))
                hints.append(_hint("Continue", "S"))
                hints.append(_hint("Resume", "R"))
            else:
                hints.append(_hint("Stop", "x"))

            hints.append(_hint("Refresh", "r"))
            hints.append(_hint("Create", "c"))

            if not is_main:
                hints.append(_hint("Remove", "d"))

        hints.append(_hint("Quit", "q"))
        hints.append(_hint("Help", "?"))

        try:
            self.query_one("#key-hints", Static).update(
                " " + _SEP.join(hints)
            )
        except Exception:
            pass

    def _load_config(self) -> None:
        if self._repo_root:
            self._config = load_config(self._repo_root)
        else:
            self._config = Config()

    def _load_worktrees(self, preserve_selection: bool = False) -> None:
        prev_path = self._selected_worktree.path if (preserve_selection and self._selected_worktree) else None
        try:
            if self.repo_path:
                root = WorktreeManager(self.repo_path).repo_path
            else:
                root = find_repo_root()
            self._repo_root = str(root)
            manager = WorktreeManager(root)
            self.worktrees = manager.list()
        except WorktreeManagerError as e:
            self.notify(str(e), severity="error", timeout=5)
            return

        wt_list = self.query_one(WorktreeList)
        wt_list.set_worktrees(self.worktrees)

        if self.worktrees:
            # Restore previous selection if it still exists
            match = next((wt for wt in self.worktrees if wt.path == prev_path), None) if prev_path else None
            selected = match or self.worktrees[0]
            idx = self.worktrees.index(selected)
            self._selected_worktree = selected
            wt_list.index = idx
            self.query_one(CenterPanel).switch_to(selected.path)

        self._refresh_git_statuses()

    def _poll_worktrees(self) -> None:
        """Detect externally added/removed worktrees and reload silently."""
        if not self._repo_root:
            return
        try:
            new = {wt.path for wt in WorktreeManager(self._repo_root).list()}
            current = {wt.path for wt in self.worktrees}
            if new != current:
                self._load_worktrees(preserve_selection=True)
        except Exception:
            pass

    def _get_selected_worktree(self) -> WorktreeInfo | None:
        """Get the currently selected worktree."""
        return self._selected_worktree

    def _get_agent_state(self, worktree_path: str) -> AgentState:
        if worktree_path not in self._agent_states:
            self._agent_states[worktree_path] = AgentState()
        return self._agent_states[worktree_path]

    def _refresh_git_statuses(self) -> None:
        """Fetch git statuses for all worktrees and push to UI."""
        if not self._repo_root or not self.worktrees:
            return
        try:
            manager = WorktreeManager(self._repo_root)
            self._git_statuses = manager.get_all_git_statuses(self.worktrees)
        except WorktreeManagerError:
            return

        self.query_one(WorktreeList).update_all_git_statuses(self._git_statuses)
        self._push_git_status_to_selected_panel()

    def _push_git_status_to_selected_panel(self) -> None:
        """Push cached git status to the global status bar."""
        wt = self._selected_worktree
        if wt is None:
            return
        gs = self._git_statuses.get(wt.path)
        if gs is None:
            return
        try:
            self.query_one("#global-status-bar", GitInfoBar).update_status(
                gs, wt.display_branch, wt.short_head
            )
        except Exception:
            pass

    def _refresh_selected_diff(self) -> None:
        """Refresh the diff tab for the currently selected worktree."""
        wt = self._selected_worktree
        if wt is None:
            return
        center = self.query_one(CenterPanel)
        panel = center.get_panel(wt.path)
        if panel:
            diff_text = WorktreeManager.get_diff(wt.path)
            panel.update_diff(diff_text)

    @work(thread=True)
    def _refresh_usage_panel(self) -> None:
        try:
            for panel in self.query(UsagePanel):
                panel.refresh_data()
        except Exception:
            pass

    @work(thread=True, exclusive=True, group="pr_status")
    def _refresh_pr_status(self) -> None:
        """Refresh PR/CI status for the selected worktree (background thread)."""
        wt = self._selected_worktree
        if wt is None:
            return

        if self._gh_available is None:
            self._gh_available = WorktreeManager.is_gh_available()
        if not self._gh_available:
            return

        pr_info = WorktreeManager.get_pr_info(wt.path)
        self.call_from_thread(self._apply_pr_info, pr_info)

    def _apply_pr_info(self, pr_info) -> None:
        """Apply PR info to the status bar (must run on main thread)."""
        try:
            bar = self.query_one("#pr-status-bar", PrStatusBar)
            bar.update_pr_info(pr_info)
        except Exception:
            pass

    # --- Navigation ---

    def on_list_view_highlighted(self, event: WorktreeList.Highlighted) -> None:
        center = self.query_one(CenterPanel)
        if event.item is not None and isinstance(event.item, WorktreeListItem):
            self._selected_worktree = event.item.worktree
            # Clear attention when user selects this worktree
            event.item.set_attention(False)
            center.switch_to(event.item.worktree.path)
            self._push_git_status_to_selected_panel()
            self._refresh_selected_diff()
            self._refresh_pr_status()
        else:
            self._selected_worktree = None
        self._update_key_hints(self.focused)

    # --- Agent message handlers ---

    def _set_attention(self, worktree_path: str) -> None:
        """Blink the worktree item if it's not currently selected."""
        if self._selected_worktree and self._selected_worktree.path == worktree_path:
            return
        wt_list = self.query_one(WorktreeList)
        for child in wt_list.children:
            if isinstance(child, WorktreeListItem) and child.worktree.path == worktree_path:
                child.set_attention(True)
                break

    def on_agent_status_changed(self, event: AgentStatusChanged) -> None:
        state = self._get_agent_state(event.worktree_path)
        state.status = event.status
        if event.status == AgentStatus.RUNNING:
            center = self.query_one(CenterPanel)
            panel = center.get_panel(event.worktree_path)
            if panel and panel.agent_terminal:
                state.last_output_time = panel.agent_terminal.last_output_time
        elif event.status == AgentStatus.WAITING:
            self._set_attention(event.worktree_path)
        self.query_one(WorktreeList).update_agent_state(event.worktree_path, state)
        self._update_key_hints(self.focused)

    async def on_agent_exited(self, event: AgentExited) -> None:
        state = self._get_agent_state(event.worktree_path)
        state.status = AgentStatus.NO_AGENT
        state.last_output_time = None
        self.query_one(WorktreeList).update_agent_state(event.worktree_path, state)
        self._set_attention(event.worktree_path)
        self._update_key_hints(self.focused)

        center = self.query_one(CenterPanel)
        panel = center.get_panel(event.worktree_path)
        if panel is not None:
            await panel.cleanup_agent()

        self.notify(
            "Agent exited — press s to spawn again",
            severity="warning",
            timeout=5,
        )

    # --- Hang detection ---

    def _check_hangs(self) -> None:
        """Periodic timer callback: check all active agents for hangs."""
        center = self.query_one(CenterPanel)
        for worktree_path, state in self._agent_states.items():
            if state.status == AgentStatus.RUNNING:
                panel = center.get_panel(worktree_path)
                if panel and panel.agent_terminal:
                    panel.agent_terminal.check_hang()

    # --- Actions ---

    def action_spawn_agent(self) -> None:
        worktree = self._get_selected_worktree()
        if worktree is None:
            self.notify("No worktree selected", severity="warning")
            return

        center = self.query_one(CenterPanel)
        panel = center.get_panel(worktree.path)
        if panel and panel.has_agent:
            self.notify("Agent already running in this worktree", severity="warning")
            return

        async def on_spawn_dismiss(result: bool | None) -> None:
            if result is not None and worktree is not None:
                center = self.query_one(CenterPanel)
                # switch_to (not just ensure_panel) so the panel is visible
                panel = center.switch_to(worktree.path)
                await panel.spawn_agent(
                    skip_permissions=result,
                    agent_provider=self._config.agent.provider,
                )

        self.push_screen(SpawnModal(worktree.display_label), on_spawn_dismiss)

    def _spawn_with_flag(self, resume: bool = False, continue_last: bool = False) -> None:
        worktree = self._get_selected_worktree()
        if worktree is None:
            self.notify("No worktree selected", severity="warning")
            return
        center = self.query_one(CenterPanel)
        panel = center.get_panel(worktree.path)
        if panel and panel.has_agent:
            self.notify("Agent already running in this worktree", severity="warning")
            return

        async def on_dismiss(result: bool | None) -> None:
            if result is not None and worktree is not None:
                center = self.query_one(CenterPanel)
                panel = center.switch_to(worktree.path)
                await panel.spawn_agent(
                    skip_permissions=result,
                    agent_provider=self._config.agent.provider,
                    resume=resume,
                    continue_last=continue_last,
                )

        label = "Continue" if continue_last else "Resume"
        self.push_screen(SpawnModal(worktree.display_label, title=f"{label} agent in"), on_dismiss)

    def action_continue_agent(self) -> None:
        self._spawn_with_flag(continue_last=True)

    def action_resume_agent(self) -> None:
        self._spawn_with_flag(resume=True)

    def action_stop_agent(self) -> None:
        worktree = self._get_selected_worktree()
        if worktree is None:
            self.notify("No worktree selected", severity="warning")
            return

        center = self.query_one(CenterPanel)
        panel = center.get_panel(worktree.path)
        if panel is None or not panel.has_agent:
            self.notify("No running agent in this worktree", severity="warning")
            return

        async def on_confirm(ok: bool) -> None:
            if not ok:
                return
            state = self._get_agent_state(worktree.path)
            state.status = AgentStatus.NO_AGENT
            state.last_output_time = None
            self.query_one(WorktreeList).update_agent_state(worktree.path, state)
            center = self.query_one(CenterPanel)
            panel = center.get_panel(worktree.path)
            if panel:
                await panel.cleanup_agent()
            self.notify("Agent stopped")
            self._update_key_hints(self.focused)

        self.push_screen(
            _StopConfirmModal(worktree.display_label),
            callback=on_confirm,
        )

    def _cycle_worktree(self, delta: int) -> None:
        """Cycle worktree selection by delta (+1/-1), staying on current pane."""
        if not self.worktrees:
            return
        wt_list = self.query_one(WorktreeList)
        current = wt_list.index or 0
        new_idx = (current + delta) % len(self.worktrees)
        wt_list.index = new_idx
        # The highlighted event will update _selected_worktree and switch panel

    def action_prev_worktree(self) -> None:
        self._cycle_worktree(-1)

    def action_next_worktree(self) -> None:
        self._cycle_worktree(1)

    def action_focus_sidebar(self) -> None:
        self._current_focus_pane = 1
        self.query_one(WorktreeList).focus()

    def action_focus_agent(self, auto_spawn: bool = True) -> None:
        self._current_focus_pane = 2
        wt = self._get_selected_worktree()
        if not wt:
            return
        panel = self.query_one(CenterPanel).get_panel(wt.path)
        if panel:
            panel.switch_to_tab("agent-tab")
            if panel.agent_terminal:
                panel.agent_terminal.focus()
            else:
                try:
                    panel.query_one("#agent-placeholder").focus()
                except Exception:
                    pass

    def action_focus_diff(self) -> None:
        self._current_focus_pane = 3
        wt = self._get_selected_worktree()
        if not wt:
            return
        panel = self.query_one(CenterPanel).get_panel(wt.path)
        if panel:
            panel.switch_to_tab("diff-tab")
            try:
                panel.query_one("#diff-scroll").focus()
            except Exception:
                pass

    def action_focus_terminal(self) -> None:
        self._current_focus_pane = 4
        wt = self._get_selected_worktree()
        if not wt:
            return
        panel = self.query_one(CenterPanel).get_panel(wt.path)
        if panel:
            try:
                panel.query_one("#terminal-widget").focus()
            except Exception:
                pass

    def action_focus_usage(self) -> None:
        self._current_focus_pane = 5
        try:
            self.query_one(UsagePanel).focus()
        except Exception:
            pass

    _PANE_ACTIONS = ["focus_sidebar", "focus_agent", "focus_diff", "focus_terminal", "focus_usage"]

    def _focus_pane(self, index: int) -> None:
        """Focus a pane by index (1-based), without auto-spawning modals."""
        action = self._PANE_ACTIONS[index - 1]
        fn = getattr(self, f"action_{action}")
        if action == "focus_agent":
            fn(auto_spawn=False)
        else:
            fn()

    def action_next_pane(self) -> None:
        n = len(self._PANE_ACTIONS)
        self._focus_pane((self._current_focus_pane % n) + 1)

    # Vertical mapping: top row panes ↔ bottom row panes
    _UP_MAP = {4: 2, 5: 3, 1: 1, 2: 2, 3: 3}    # terminal→agent, usage→diff
    _DOWN_MAP = {1: 1, 2: 4, 3: 5, 4: 4, 5: 5}   # agent→terminal, diff→usage

    def action_pane_up(self) -> None:
        self._focus_pane(self._UP_MAP.get(self._current_focus_pane, 2))

    def action_pane_down(self) -> None:
        self._focus_pane(self._DOWN_MAP.get(self._current_focus_pane, 4))

    def action_prev_pane(self) -> None:
        n = len(self._PANE_ACTIONS)
        self._focus_pane(((self._current_focus_pane - 2) % n) + 1)

    def _has_running_agents(self) -> bool:
        active = {AgentStatus.RUNNING, AgentStatus.WAITING, AgentStatus.POSSIBLY_HANGED}
        return any(s.status in active for s in self._agent_states.values())

    def action_quit(self) -> None:
        if self._has_running_agents():
            count = sum(
                1 for s in self._agent_states.values()
                if s.status in {AgentStatus.RUNNING, AgentStatus.WAITING, AgentStatus.POSSIBLY_HANGED}
            )
            self.push_screen(
                ConfirmModal(
                    "Agents still running",
                    f"{count} agent{'s' if count > 1 else ''} still working. Quit and stop them all?",
                ),
                callback=lambda ok: self.exit() if ok else None,
            )
        else:
            self.exit()

    def action_refresh(self) -> None:
        self._load_worktrees()
        self.notify("Refreshed worktrees")

    def action_create_worktree(self) -> None:
        def on_modal_dismiss(result: CreateWorktreeResult | None) -> None:
            if result is None:
                return
            self._do_create_worktree(result)

        branches = WorktreeManager.list_local_branches(self._repo_root) if self._repo_root else []
        self.push_screen(
            CreateWorktreeModal(
                default_branch=self._config.default_branch,
                branches=branches or [self._config.default_branch],
            ),
            on_modal_dismiss,
        )

    def _do_create_worktree(self, result: CreateWorktreeResult) -> None:
        if self._config.has_custom_create:
            repo_name = os.path.basename(self._repo_root) if self._repo_root else ""
            wt_name = f"{repo_name}-{result.branch}" if repo_name else result.branch
            wt_path = str(
                (self._repo_root and os.path.join(os.path.dirname(self._repo_root), wt_name))
                or wt_name
            )
            cmd = format_command(
                self._config.worktree.create,  # type: ignore[arg-type]
                branch=result.branch,
                name=wt_name,
                base=result.base_branch,
                path=wt_path,
                repo=self._repo_root,
            )
            self._send_to_terminal(cmd)
            self.notify(_CMD_SENT_MSG, timeout=5)
        else:
            try:
                manager = WorktreeManager(self._repo_root)
                new_path = manager.create(result.branch, result.base_branch)
                self._load_worktrees()
                self.notify(f"Created worktree: {os.path.basename(new_path)}")
            except WorktreeManagerError as e:
                self.notify(str(e), severity="error", timeout=5)

    def action_remove_worktree(self) -> None:
        worktree = self._get_selected_worktree()
        if worktree is None:
            self.notify("No worktree selected", severity="warning")
            return
        if worktree.is_main:
            self.notify("Cannot remove the main worktree", severity="error")
            return

        state = self._get_agent_state(worktree.path)
        if state.status in (AgentStatus.RUNNING, AgentStatus.WAITING):
            self.notify(
                "Agent is running in this worktree — stop it first (x)",
                severity="warning",
            )
            return

        def on_confirm(confirmed: bool) -> None:
            if confirmed and worktree is not None:
                self._do_remove_worktree(worktree)

        self.push_screen(
            ConfirmModal(
                title="Remove worktree",
                body=f"Remove [bold]{worktree.display_label}[/bold] ({worktree.name})?",
            ),
            on_confirm,
        )

    def _do_remove_worktree(self, worktree: WorktreeInfo) -> None:
        if self._config.has_custom_remove:
            cmd = format_command(
                self._config.worktree.remove,  # type: ignore[arg-type]
                branch=worktree.branch or "",
                name=worktree.name,
                base="",
                path=worktree.path,
                repo=self._repo_root,
            )
            self._send_to_terminal(f"cd {self._repo_root} && {cmd}")
            self.action_focus_terminal()
            self.notify(_CMD_SENT_MSG, timeout=5)
        else:
            try:
                manager = WorktreeManager(self._repo_root)
                manager.remove(worktree.path)
                self._load_worktrees()
                self.notify(f"Removed worktree: {worktree.name}")
            except WorktreeManagerError as e:
                self.notify(str(e), severity="error", timeout=5)

    def _send_to_terminal(self, cmd: str) -> None:
        """Send a command string to the active worktree's terminal pane."""
        def _no_terminal() -> None:
            self.notify(f"No terminal available. Run manually:\n{cmd}", severity="warning", timeout=8)

        wt = self._get_selected_worktree()
        if not wt:
            _no_terminal()
            return
        panel = self.query_one(CenterPanel).get_panel(wt.path)
        if panel is None:
            panel = self.query_one(CenterPanel).switch_to(wt.path)
        try:
            terminal = panel.query_one("#terminal-widget")
            # send_queue is an asyncio.Queue — must use put_nowait from sync context
            terminal.send_queue.put_nowait(["stdin", cmd + "\n"])
        except Exception:
            _no_terminal()

    def action_open_pr_url(self, url: str) -> None:
        self.open_url(url)

    def action_help(self) -> None:
        self.push_screen(HelpModal())


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="lazyagent",
        description="TUI for managing coding agents across git worktrees",
    )
    parser.add_argument(
        "repo",
        nargs="?",
        default=None,
        help="Path to git repository (default: auto-detect from cwd)",
    )
    args = parser.parse_args()

    try:
        app = LazyAgent(repo_path=args.repo)
        app.run()
    except WorktreeManagerError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
