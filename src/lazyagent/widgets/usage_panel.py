"""Right-side panel showing Claude Code usage statistics with tabbed views."""

from __future__ import annotations

from datetime import datetime, timedelta

from rich.style import Style
from rich.text import Text

from textual import events
from textual.binding import Binding
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

from lazyagent.claude_usage import (
    BudgetInfo,
    UsageData,
    UsageSection,
    TokenStats,
    estimate_cost,
    format_reset_compact,
    format_tokens,
    get_usage_data,
    short_model,
)
from lazyagent.styles import SCROLLBAR_CSS

_BAR_WIDTH = 20
_TABS = ["Usage", "Stats", "Tools"]


# ──────────────────────────────────────────────────────────────────────
# Colors
# ──────────────────────────────────────────────────────────────────────

def _budget_color(ratio: float) -> str:
    if ratio > 100:
        return "red"
    if ratio > 85:
        return "dark_red"
    if ratio > 60:
        return "yellow"
    return "green"


def _budget_bar(info: BudgetInfo) -> Text:
    color = _budget_color(info.budget_ratio)
    t = Text("  ")
    t.append("▰" * info.filled_blocks, style=color)
    t.append("▱" * (7 - info.filled_blocks), style="bright_black")
    t.append(
        f" Day {info.current_day}/{info.total_days}"
        f" · budget {info.cumulative_budget:.0f}%"
        f" · used ",
        style="bright_black",
    )
    t.append(f"{info.actual_percent}%", style=color)
    return t


def _usage_color(pct: int) -> str:
    if pct >= 90:
        return "red"
    if pct >= 75:
        return "dark_red"
    if pct >= 50:
        return "yellow"
    return "green"


def _progress_bar(pct: int, width: int = _BAR_WIDTH) -> Text:
    pct = max(0, min(100, pct))
    filled = round(pct / 100 * width)
    empty = width - filled
    color = _usage_color(pct)
    t = Text("[")
    t.append("█" * filled, style=color)
    t.append("░" * empty, style="bright_black")
    t.append("] ")
    t.append(f"{pct}%", style=color)
    return t


# ──────────────────────────────────────────────────────────────────────
# Tab builders
# ──────────────────────────────────────────────────────────────────────

_DIM = Style(color="bright_black")
_WHITE = Style(bold=True)


def _build_usage_tab(data: UsageData, width: int) -> list[Text]:
    """Tab 0: Session/weekly usage bars + budget."""
    lines: list[Text] = []

    if data.rate_limited:
        lines.append(Text(""))
        lines.append(Text("API rate limited — retrying in ~5m", style="bright_red"))

    sections: list[tuple[str, UsageSection | None, BudgetInfo | None]] = [
        ("Session (5h)", data.session, None),
        ("Weekly (all)", data.week, data.week_budget),
        ("Weekly (Son)", data.week_sonnet, data.week_sonnet_budget),
    ]

    shown = False
    for label, section, budget in sections:
        if section is None:
            continue
        lines.append(Text(""))
        t = Text()
        t.append(f"{label:<14}", style=_WHITE)
        t.append_text(_progress_bar(section.percent))
        reset = format_reset_compact(section.reset_time)
        if reset:
            t.append(f" ↺{reset}", style=_DIM)
        lines.append(t)
        if budget:
            lines.append(_budget_bar(budget))
        lines.append(Text(""))
        shown = True

    if not shown:
        lines.append(Text(""))
        lines.append(Text("No usage data from waybar cache", style=_DIM))
        lines.append(Text("Run Claude to populate", style=_DIM))


    return lines


def _date_label(day_offset: int) -> str:
    if day_offset == 0:
        return "Today"
    if day_offset == -1:
        return "Yesterday"
    target = datetime.now().date() + timedelta(days=day_offset)
    return target.strftime("%b %d")


def _build_stats_tab(data: UsageData, width: int, day_offset: int) -> list[Text]:
    """Tab 1: Token stats, model breakdown, costs."""
    lines: list[Text] = []
    tokens = data.tokens

    if not tokens:
        lines.append(Text(""))
        lines.append(Text(f"{_date_label(day_offset)} — no data", style=_DIM))
        return lines

    lines.append(Text(""))
    total_msgs = tokens.message_count + tokens.user_msg_count
    t = Text()
    t.append(f"{_date_label(day_offset)} ", style=_WHITE)
    t.append(
        f"{tokens.session_count} sess · {total_msgs} msgs · {tokens.tool_call_count} tools",
        style=_DIM,
    )
    lines.append(t)
    lines.append(Text(""))

    # Per-model breakdown
    costs = estimate_cost(tokens)
    sorted_models = sorted(tokens.models, key=lambda m: tokens.models[m]["count"], reverse=True)
    for model in sorted_models:
        md = tokens.models[model]
        if md["count"] == 0:
            continue
        name = short_model(model)
        total_in = md["input"] + md["cache_read"] + md["cache_write"]
        t = Text("  ")
        t.append(f"{name:<8}{md['count']:<5}", style=_DIM)
        t.append(f"↓{format_tokens(total_in):<8}", style=_DIM)
        t.append(f"↑{format_tokens(md['output'])}", style=_DIM)
        if model in costs and costs[model] > 0.005:
            t.append(f" ~${costs[model]:.0f}", style="yellow")
        lines.append(t)

    lines.append(Text(""))

    # Avg turn + thinking
    parts: list[Text] = []
    if tokens.turn_count > 0:
        avg_s = tokens.turn_duration_ms / tokens.turn_count / 1000
        if avg_s >= 60:
            mins, secs = divmod(int(avg_s), 60)
            parts.append(Text(f"avg turn {mins}m{secs}s", style=_DIM))
        else:
            parts.append(Text(f"avg turn {int(avg_s)}s", style=_DIM))
    if tokens.thinking_blocks and tokens.message_count:
        think_pct = round(tokens.thinking_blocks / tokens.message_count * 100)
        parts.append(Text(f"◆ {tokens.thinking_blocks} ({think_pct}%)", style="magenta"))
    if parts:
        t = Text("  ")
        for i, p in enumerate(parts):
            if i > 0:
                t.append(" · ", style=_DIM)
            t.append_text(p)
        lines.append(t)
        lines.append(Text(""))

    # Tokens in/out
    t = Text("  ")
    t.append(f"↓{format_tokens(tokens.input_tokens)} in", style="cyan")
    t.append("  ")
    t.append(f"↑{format_tokens(tokens.output_tokens)} out", style="green")
    lines.append(t)
    lines.append(Text(""))

    # Cache
    if tokens.cache_read_tokens or tokens.cache_write_tokens:
        lines.append(Text("  Cache", style=_WHITE))

        cr = tokens.cache_read_tokens
        cw = tokens.cache_write_tokens
        total_cache = cr + cw

        # Read bar
        t = Text("    ")
        t.append("read  ", style=_DIM)
        t.append(f"{format_tokens(cr):>7}", style="green")
        if total_cache > 0:
            bar_len = round(cr / total_cache * 15)
            t.append("  ")
            t.append("█" * bar_len, style="green")
        lines.append(t)

        # Write bar
        t = Text("    ")
        t.append("write ", style=_DIM)
        t.append(f"{format_tokens(cw):>7}", style="yellow")
        if total_cache > 0:
            bar_len = round(cw / total_cache * 15)
            t.append("  ")
            t.append("█" * bar_len, style="yellow")
        lines.append(t)

        # Efficiency ratio
        if cw > 0:
            ratio = cr / cw
            color = "green" if ratio >= 10 else "yellow" if ratio >= 3 else "red"
            t = Text("    ")
            t.append("ratio ", style=_DIM)
            t.append(f"  {ratio:.1f}:1", style=color)
            t.append(f"  read/write", style=_DIM)
            lines.append(t)

        lines.append(Text(""))

    # Cost
    total_cost = costs.get("_total", 0)
    if total_cost > 0:
        lines.append(Text(""))
        lines.append(Text(f"  ~${total_cost:.2f}", style="yellow"))

    return lines


def _build_tools_tab(data: UsageData, width: int) -> list[Text]:
    """Tab 2: Tool usage breakdown."""
    lines: list[Text] = []
    tokens = data.tokens

    if not tokens or not tokens.tools:
        lines.append(Text(""))
        lines.append(Text("No tool data", style=_DIM))
        return lines

    lines.append(Text(""))
    sorted_tools = sorted(tokens.tools.items(), key=lambda x: x[1], reverse=True)
    total = sum(c for _, c in sorted_tools)

    for name, count in sorted_tools:
        pct = round(count / total * 100) if total else 0
        bar_len = round(count / sorted_tools[0][1] * 15) if sorted_tools[0][1] else 0
        t = Text("  ")
        t.append(f"{name:<12}", style=_WHITE)
        t.append(f"{count:>4}", style=_DIM)
        t.append("  ")
        t.append("█" * bar_len, style="cyan")
        t.append(f" {pct}%", style=_DIM)
        lines.append(t)
        lines.append(Text(""))

    lines.append(Text(""))
    lines.append(Text(f"  Total: {total} calls", style=_DIM))

    return lines


# ──────────────────────────────────────────────────────────────────────
# Compact (agent zoomed)
# ──────────────────────────────────────────────────────────────────────

def _build_compact(data: UsageData) -> list[Text]:
    t = Text()
    if data.session:
        color = _usage_color(data.session.percent)
        t.append(f"{data.session.percent}%", style=color)
        reset = format_reset_compact(data.session.reset_time)
        if reset:
            t.append(f" ↺{reset}", style="bright_black")
    elif data.tokens:
        costs = estimate_cost(data.tokens)
        total = costs.get("_total", 0)
        t.append(f"~${total:.2f}", style="yellow")
    else:
        t.append("--", style="bright_black")

    budget = data.week_budget or data.week_sonnet_budget
    if budget:
        t.append(" ")
        bcolor = _budget_color(budget.budget_ratio)
        t.append("▰" * budget.filled_blocks, style=bcolor)
        t.append("▱" * (7 - budget.filled_blocks), style="bright_black")

    return [t]


# ──────────────────────────────────────────────────────────────────────
# Widget
# ──────────────────────────────────────────────────────────────────────

class UsagePanel(ScrollView, can_focus=True):
    """Right panel with tabbed Claude Code usage views."""

    DEFAULT_CSS = f"""
    UsagePanel {{
        width: 1fr;
        height: 1fr;
        border: round $secondary;
        border-title-color: $text-muted;
        background: transparent;
        padding: 0 1;
        {SCROLLBAR_CSS}
    }}
    UsagePanel:focus {{
        border: round $accent;
        border-title-color: $accent;
    }}
    """

    BINDINGS = [
        Binding("right_square_bracket", "next_tab", show=False),
        Binding("left_square_bracket", "prev_tab", show=False),
        Binding("j", "scroll_down", show=False),
        Binding("k", "scroll_up", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._lines: list[Text] = []
        self._data: UsageData | None = None
        self._compact = False
        self._tab_index = 0
        self._day_offset = 0

    def on_mount(self) -> None:
        self._update_title()
        self.refresh_data()

    def _build_tab_title(self) -> str:
        parts: list[str] = []
        for i, name in enumerate(_TABS):
            if i == self._tab_index:
                parts.append(f"[bold]{name}[/bold]")
            else:
                parts.append(f"[dim]{name}[/dim]")
        title = "[5] " + " — ".join(parts)
        if self._tab_index == 1 and self._day_offset != 0:
            title += f"  [{_date_label(self._day_offset)}]"
        return title

    def _update_title(self) -> None:
        self.border_title = self._build_tab_title()
        if self._data and self._data.cache_age > 0:
            self.border_subtitle = f"{self._data.cache_age}s ago"
        else:
            self.border_subtitle = ""

    def _select_tab(self, index: int) -> None:
        if index != self._tab_index:
            self._tab_index = index
            self._rebuild()
            self.scroll_home(animate=False)

    async def on_click(self, event: events.Click) -> None:
        """Click on border title area to switch tabs."""
        if event.screen_y == self.region.y:
            # Clicked on the top border — use screen x relative to widget
            local_x = event.screen_x - self.region.x
            # Border title: "╭─ [5] Usage — Stats — Tools ─╮"
            # Title text starts at offset 3 (after "╭─ ")
            prefix = "[5] "
            sep = " — "
            pos = 3 + len(prefix)  # after border + prefix
            for i, name in enumerate(_TABS):
                end = pos + len(name)
                if pos <= local_x < end:
                    self._select_tab(i)
                    return
                pos = end + len(sep)

    def action_next_tab(self) -> None:
        self._select_tab((self._tab_index + 1) % len(_TABS))

    def action_prev_tab(self) -> None:
        self._select_tab((self._tab_index - 1) % len(_TABS))

    def set_compact(self, compact: bool) -> None:
        if self._compact == compact:
            return
        self._compact = compact
        self._rebuild()

    def refresh_data(self) -> None:
        self._data = get_usage_data(self._day_offset)
        self._update_title()
        self._rebuild()

    def _rebuild(self) -> None:
        if self._data is None:
            self._data = get_usage_data(self._day_offset)
        width = self.scrollable_content_region.width or 40
        if self._compact:
            self._lines = _build_compact(self._data)
        elif self._tab_index == 0:
            self._lines = _build_usage_tab(self._data, width)
        elif self._tab_index == 1:
            self._lines = _build_stats_tab(self._data, width, self._day_offset)
        else:
            self._lines = _build_tools_tab(self._data, width)
        self._update_title()
        self.virtual_size = Size(width, max(len(self._lines), 1))
        self.refresh()

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        virtual_y = scroll_y + y
        width = self.scrollable_content_region.width or 40

        if virtual_y >= len(self._lines):
            return Strip.blank(width, self.rich_style)

        text = self._lines[virtual_y]
        segments = list(text.render(self.app.console))
        return Strip(segments).crop_extend(scroll_x, scroll_x + width, self.rich_style)
