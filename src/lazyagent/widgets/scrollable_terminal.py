"""ScrollableTerminal — a terminal widget with scrollback buffer.

Replaces textual-terminal's ``Terminal(Widget)`` with a ``ScrollView``-based
widget that captures lines scrolled off the top of the pyte screen into a
deque, and uses Textual's native scrolling (``virtual_size``, ``render_line``,
scrollbars) to let users scroll through history.
"""

from __future__ import annotations

import asyncio
import re
from collections import deque

import pyte
from pyte.screens import Char, Margins

from rich.color import ColorParseError
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

from textual import events, log
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

from lazyagent.pty_emulator import DECSET_PREFIX, RE_ANSI_SEQUENCE, PtyEmulator
from lazyagent.styles import SCROLLBAR_CSS

# ---------------------------------------------------------------------------
# ScrollbackScreen — lightweight pyte Screen subclass
# ---------------------------------------------------------------------------

_DEFAULT_MAX_SCROLLBACK = 5000
_DEFAULT_COLS = 80
_DEFAULT_ROWS = 24


class ScrollbackScreen(pyte.Screen):
    """pyte Screen that captures lines scrolled off the top into a deque.

    Only overrides ``index()`` (the method called when the cursor is at the
    bottom margin and a new line is needed).  No ``__getattribute__`` wrapper,
    no ``before_event``/``after_event``.  Cost: one ``dict()`` copy per line
    scrolled off.
    """

    def __init__(
        self,
        columns: int,
        lines: int,
        max_scrollback: int = _DEFAULT_MAX_SCROLLBACK,
    ) -> None:
        super().__init__(columns, lines)
        self.scrollback: deque[dict[int, Char]] = deque(maxlen=max_scrollback)

    def set_margins(self, *args, **kwargs):
        """TERM=linux compat — strip the ``private`` kwarg that pyte passes."""
        kwargs.pop("private", None)
        return super().set_margins(*args, **kwargs)

    def index(self):
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom and top == 0:
            # Only capture to scrollback when the scroll region starts at
            # row 0 (full-screen scroll).  When an app sets custom margins
            # (DECSTBM), lines scrolling within a sub-region should not be
            # saved — matching the behaviour of kitty, xterm, etc.
            self.scrollback.append(dict(self.buffer[0]))
        super().index()


# ---------------------------------------------------------------------------
# ScrollableTerminal — ScrollView-based terminal widget
# ---------------------------------------------------------------------------


class ScrollableTerminal(ScrollView, can_focus=True):
    """Terminal widget with a scrollback buffer.

    Uses :class:`ScrollView` (Line API) for native scrollbar support and
    the :class:`ScrollbackScreen` to capture scrolled-off lines.
    """

    DEFAULT_CSS = f"""
    ScrollableTerminal {{
        overflow-y: auto;
        overflow-x: hidden;
        background: transparent;
{SCROLLBAR_CSS}
    }}
    """

    def __init__(
        self,
        command: str,
        default_colors: str | None = "system",
        restart_on_disconnect: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)

        self.command = command
        self.default_colors = default_colors
        self._restart_on_disconnect = restart_on_disconnect

        # Default terminal dimensions — updated on resize
        self.ncol = _DEFAULT_COLS
        self.nrow = _DEFAULT_ROWS
        self.mouse_tracking = False

        # PTY emulator (created in start())
        self.emulator: PtyEmulator | None = None
        self.send_queue: asyncio.Queue | None = None
        self.recv_queue: asyncio.Queue | None = None
        self.recv_task: asyncio.Task | None = None
        self._stopped = False
        self._follow_output = True  # tracks auto-scroll intent across visibility

        # Text selection state
        self._sel_start: tuple[int, int] | None = None  # (virtual_row, col)
        self._sel_end: tuple[int, int] | None = None
        self._selecting: bool = False

        # pyte screen + stream
        self._screen = ScrollbackScreen(_DEFAULT_COLS, _DEFAULT_ROWS)
        self.stream = pyte.Stream(self._screen)

        # Key translation table (same as textual-terminal)
        self.ctrl_keys = {
            "up": "\x1bOA",
            "down": "\x1bOB",
            "right": "\x1bOC",
            "left": "\x1bOD",
            "home": "\x1bOH",
            "end": "\x1b[F",
            "delete": "\x1b[3~",
            "shift+tab": "\x1b[Z",
            "f1": "\x1bOP",
            "f2": "\x1bOQ",
            "f3": "\x1bOR",
            "f4": "\x1bOS",
            "f5": "\x1b[15~",
            "f6": "\x1b[17~",
            "f7": "\x1b[18~",
            "f8": "\x1b[19~",
            "f9": "\x1b[20~",
            "f10": "\x1b[21~",
            "f11": "\x1b[23~",
            "f12": "\x1b[24~",
            "f13": "\x1b[25~",
            "f14": "\x1b[26~",
            "f15": "\x1b[28~",
            "f16": "\x1b[29~",
            "f17": "\x1b[31~",
            "f18": "\x1b[32~",
            "f19": "\x1b[33~",
            "f20": "\x1b[34~",
        }

    # ------------------------------------------------------------------
    # PTY lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the PTY subprocess and begin the recv loop."""
        if self.emulator is not None:
            return
        self._stopped = False
        self.emulator = PtyEmulator(command=self.command)
        self.emulator.start()
        self.send_queue = self.emulator.recv_queue
        self.recv_queue = self.emulator.send_queue
        self.recv_task = asyncio.create_task(self.recv())

    def stop(self) -> None:
        """Kill the PTY subprocess and cancel the recv loop."""
        if self.emulator is None:
            return
        self._stopped = True
        self.recv_task.cancel()
        self.emulator.stop()
        self.emulator = None

    # ------------------------------------------------------------------
    # Recv loop — reads PTY output and updates screen + scrollback
    # ------------------------------------------------------------------

    async def recv(self) -> None:
        try:
            while True:
                message = await self.recv_queue.get()
                if self._stopped:
                    break
                cmd = message[0]

                if cmd == "setup":
                    await self.send_queue.put(["set_size", self.nrow, self.ncol])

                elif cmd == "stdout":
                    chars = message[1]

                    # Hook for subclasses (e.g. MonitoredTerminal)
                    self._on_stdout(chars)

                    # Detect mouse tracking toggles
                    for sep_match in re.finditer(RE_ANSI_SEQUENCE, chars):
                        sequence = sep_match.group(0)
                        if sequence.startswith(DECSET_PREFIX):
                            parameters = sequence.removeprefix(
                                DECSET_PREFIX
                            ).split(";")
                            if "1000h" in parameters:
                                self.mouse_tracking = True
                            if "1000l" in parameters:
                                self.mouse_tracking = False

                    # Use the flag instead of is_vertical_scroll_end which
                    # returns unreliable values when the widget is hidden
                    # (e.g. after a ContentSwitcher worktree change).
                    was_at_bottom = self._follow_output

                    # Feed to pyte (may trigger index() → scrollback capture)
                    try:
                        self.stream.feed(chars)
                    except TypeError as error:
                        log.warning("could not feed:", error)

                    # Only update layout/scroll when the widget is visible.
                    # When hidden by ContentSwitcher (display: none) the
                    # widget's size is (0, 0); calling _update_virtual_size
                    # would trigger _refresh_scrollbars on a zero-sized
                    # region, and scroll_end would compute a bogus
                    # max_scroll_y.  on_show() catches up when the widget
                    # becomes visible again.
                    if self.size.height > 0:
                        self._update_virtual_size()
                        self.refresh()
                        if was_at_bottom:
                            self.scroll_end(
                                animate=False, immediate=True, x_axis=False
                            )

                    # Post-processing hook for subclasses (e.g. sentinel
                    # scanning — must run regardless of visibility)
                    self._after_stdout_processed()

                elif cmd == "disconnect":
                    self._on_recv_disconnect()
                    self.stop()

        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Hooks for subclasses
    # ------------------------------------------------------------------

    def _on_stdout(self, chars: str) -> None:
        """Called on each PTY stdout chunk, before processing.

        Override in subclasses for monitoring (e.g. agent status detection).
        """

    def _on_recv_disconnect(self) -> None:
        """Called when the PTY disconnects.

        Override in subclasses for cleanup.
        """
        if self._restart_on_disconnect:
            self.call_later(self._restart_session)

    def _restart_session(self) -> None:
        """Restart the PTY session after disconnect (e.g. Ctrl+D in shell)."""
        self._stopped = False
        self._follow_output = True
        self.start()
        self.notify("Shell restarted")

    def _after_stdout_processed(self) -> None:
        """Called after each stdout chunk is fed and scroll is updated.

        Override in subclasses for post-processing (e.g. sentinel scanning).
        """

    # ------------------------------------------------------------------
    # Auto-scroll tracking
    # ------------------------------------------------------------------

    def on_show(self) -> None:
        """Catch up after being hidden: sync virtual size and scroll."""

        def _restore() -> None:
            self._update_virtual_size()
            self.refresh()
            if self._follow_output:
                self.scroll_end(animate=False, immediate=True, x_axis=False)

        # Defer until after the first layout pass so the widget has
        # a real size and scrollable_content_region is valid.
        self.call_after_refresh(_restore)

    # ------------------------------------------------------------------
    # Virtual size management
    # ------------------------------------------------------------------

    def _update_virtual_size(self) -> None:
        """Set virtual_size to reflect scrollback + live screen."""
        total_lines = len(self._screen.scrollback) + self._screen.lines
        self.virtual_size = Size(self.ncol, total_lines)

    # ------------------------------------------------------------------
    # Line API rendering
    # ------------------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        """Render a single line.

        ``y`` is a widget-local coordinate (0 = top of visible area).
        We add ``scroll_offset.y`` to map into virtual space, then
        dispatch to scrollback or live screen rendering.
        """
        scroll_x, scroll_y = self.scroll_offset
        virtual_y = scroll_y + y
        scrollback_len = len(self._screen.scrollback)
        width = self.scrollable_content_region.width

        sel_cols = self._get_sel_cols_for_row(virtual_y, width)

        if virtual_y < scrollback_len:
            strip = self._render_scrollback_line(virtual_y, width, sel_cols)
        else:
            screen_y = virtual_y - scrollback_len
            strip = self._render_screen_line(screen_y, width, sel_cols)

        return strip.crop_extend(scroll_x, scroll_x + width, self.rich_style)

    def _render_scrollback_line(
        self, index: int, width: int, sel_cols: tuple[int, int] | None = None
    ) -> Strip:
        """Render a line from the scrollback buffer."""
        row = self._screen.scrollback[index]
        return self._row_to_strip(row, width, show_cursor=False, sel_cols=sel_cols)

    def _render_screen_line(
        self, screen_y: int, width: int, sel_cols: tuple[int, int] | None = None
    ) -> Strip:
        """Render a line from the live pyte screen buffer."""
        if screen_y < 0 or screen_y >= self._screen.lines:
            return Strip.blank(width, self.rich_style)
        row = self._screen.buffer[screen_y]
        show_cursor = self._screen.cursor.y == screen_y
        return self._row_to_strip(
            row, width, show_cursor=show_cursor, screen_y=screen_y, sel_cols=sel_cols
        )

    def _row_to_strip(
        self,
        row: dict[int, Char],
        width: int,
        *,
        show_cursor: bool = False,
        screen_y: int = -1,
        sel_cols: tuple[int, int] | None = None,
    ) -> Strip:
        """Convert a pyte row (dict of column→Char) to a textual Strip."""
        text = Text()
        ncols = max(width, self._screen.columns)
        style_change_pos: int = 0

        for x in range(ncols):
            char: Char = row.get(x, self._screen.default_char)
            text.append(char.data)

            if x > 0:
                last_char: Char = row.get(x - 1, self._screen.default_char)
                if (
                    not self._char_style_cmp(char, last_char)
                    or x == ncols - 1
                ):
                    last_style = self._char_rich_style(last_char)
                    text.stylize(last_style, style_change_pos, x + 1)
                    style_change_pos = x

        # Apply cursor AFTER all character styles so it is never overwritten.
        # Only show cursor when this widget has focus.
        if show_cursor and self.has_focus:
            cx = self._screen.cursor.x
            if 0 <= cx < ncols:
                text.stylize(Style(reverse=True), cx, cx + 1)

        # Apply selection highlight on top of everything.
        if sel_cols is not None:
            col_s, col_e = sel_cols
            text.stylize(Style(reverse=True), col_s, min(col_e, ncols))

        segments = list(text.render(self.app.console))
        return Strip(segments)

    # ------------------------------------------------------------------
    # Style helpers (ported from textual-terminal Terminal)
    # ------------------------------------------------------------------

    @staticmethod
    def _char_style_cmp(given: Char, other: Char) -> bool:
        """Return True if two pyte Chars have the same style."""
        return (
            given.fg == other.fg
            and given.bg == other.bg
            and given.bold == other.bold
            and given.italics == other.italics
            and given.underscore == other.underscore
            and given.strikethrough == other.strikethrough
            and given.reverse == other.reverse
            and given.blink == other.blink
            and given.dim == other.dim
        )

    @staticmethod
    def _detect_color(color: str) -> str:
        """Fix colour names/hex for Rich compatibility."""
        if color == "brown":
            return "yellow"
        if color == "brightblack":
            return "#808080"
        if re.match("[0-9a-f]{6}", color, re.IGNORECASE):
            return f"#{color}"
        return color

    def _char_rich_style(self, char: Char) -> Style:
        """Convert a pyte Char's attributes to a ``rich.Style``."""
        foreground = self._detect_color(char.fg) if char.fg != "default" else None
        background = self._detect_color(char.bg) if char.bg != "default" else None

        try:
            style = Style(
                color=foreground,
                bgcolor=background,
                bold=char.bold,
                dim=char.dim,
                italic=char.italics,
                underline=char.underscore,
                strike=char.strikethrough,
                reverse=char.reverse,
                blink=char.blink,
            )
        except ColorParseError as error:
            log.warning("color parse error:", error)
            style = Style()

        return style

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    async def on_key(self, event: events.Key) -> None:
        if self.emulator is None:
            return

        if event.key == "alt+x":
            self.app.set_focus(None)
            return

        # PageUp/Down scroll the widget (history) instead of sending to PTY
        if event.key == "pageup":
            event.stop()
            self._follow_output = False
            self.scroll_page_up(animate=False)
            return
        if event.key == "pagedown":
            event.stop()
            self.scroll_page_down(animate=False)
            self._follow_output = self.is_vertical_scroll_end
            return

        event.stop()
        char = self.ctrl_keys.get(event.key) or event.character
        if char:
            await self.send_queue.put(["stdin", char])

    async def on_paste(self, event: events.Paste) -> None:
        if self.emulator is None:
            return
        if event.text:
            await self.send_queue.put(["stdin", event.text])
        event.stop()

    async def on_click(self, event: events.MouseEvent) -> None:
        if self.emulator is None:
            return
        if not self.mouse_tracking:
            return
        if event.shift:
            return  # Shift+click is for selection
        await self.send_queue.put(["click", event.x, event.y, event.button])

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self.emulator is None:
            return
        if self.mouse_tracking:
            # Forward to PTY (vim, less, etc.)
            await self.send_queue.put(["scroll", "down", event.x, event.y])
        else:
            # Default ScrollView behavior — scroll the widget
            event.stop()
            self.scroll_down()
            self._follow_output = self.is_vertical_scroll_end

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self.emulator is None:
            return
        if self.mouse_tracking:
            # Forward to PTY
            await self.send_queue.put(["scroll", "up", event.x, event.y])
        else:
            # Default ScrollView behavior
            event.stop()
            self._follow_output = False
            self.scroll_up()

    async def on_mouse_down(self, event: events.MouseDown) -> None:
        if self.emulator is None:
            return
        if event.button != 1:
            return
        # When the PTY app has mouse tracking, only select with Shift held.
        if self.mouse_tracking and not event.shift:
            return
        self._selecting = True
        self._sel_start = self._sel_end = self._widget_to_virtual(event.x, event.y)
        self.capture_mouse()
        self.refresh()

    async def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._selecting:
            return
        event.stop()
        new_pos = self._widget_to_virtual(event.x, event.y)
        if new_pos == self._sel_end:
            return  # same cell — skip repaint
        self._sel_end = new_pos
        self.refresh()

    async def on_mouse_up(self, event: events.MouseUp) -> None:
        if not self._selecting or event.button != 1:
            return
        self._selecting = False
        self.release_mouse()
        self._sel_end = self._widget_to_virtual(event.x, event.y)
        text = self._get_selected_text()
        if text:
            await self._copy_to_clipboard(text)
            self.notify(f"Copied {len(text)} chars")
        else:
            self._sel_start = self._sel_end = None
        self.refresh()

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _widget_to_virtual(self, x: int, y: int) -> tuple[int, int]:
        """Convert widget-local (x, y) to (virtual_row, col)."""
        _, scroll_y = self.scroll_offset
        return (scroll_y + y, x)

    def _sel_normalized(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        """Return ((start_row, start_col), (end_row, end_col)) or None."""
        if self._sel_start is None or self._sel_end is None:
            return None
        if self._sel_start == self._sel_end:
            return None
        return (min(self._sel_start, self._sel_end), max(self._sel_start, self._sel_end))

    def _get_sel_cols_for_row(self, virtual_y: int, width: int) -> tuple[int, int] | None:
        """Return (col_start, col_end) to highlight on this row, or None."""
        sel = self._sel_normalized()
        if sel is None:
            return None
        (start_row, start_col), (end_row, end_col) = sel
        if virtual_y < start_row or virtual_y > end_row:
            return None
        col_s = start_col if virtual_y == start_row else 0
        col_e = end_col if virtual_y == end_row else width
        if col_s >= col_e:
            return None
        return (col_s, col_e)

    def _get_selected_text(self) -> str:
        """Extract selected text from scrollback and screen buffers."""
        sel = self._sel_normalized()
        if sel is None:
            return ""
        (start_row, start_col), (end_row, end_col) = sel
        scrollback_len = len(self._screen.scrollback)
        lines: list[str] = []
        for row_idx in range(start_row, end_row + 1):
            if row_idx < scrollback_len:
                row = self._screen.scrollback[row_idx]
            else:
                screen_y = row_idx - scrollback_len
                if 0 <= screen_y < self._screen.lines:
                    row = self._screen.buffer[screen_y]
                else:
                    continue
            col_s = start_col if row_idx == start_row else 0
            col_e = end_col if row_idx == end_row else self._screen.columns
            line = "".join(
                row.get(c, self._screen.default_char).data for c in range(col_s, col_e)
            )
            lines.append(line.rstrip())
        return "\n".join(lines)

    async def _copy_to_clipboard(self, text: str) -> None:
        """Write text to the system clipboard without blocking the event loop."""
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

    # ------------------------------------------------------------------
    # Resize
    # ------------------------------------------------------------------

    async def on_resize(self, event: events.Resize) -> None:
        if self.emulator is None:
            return

        ncol = self.scrollable_content_region.width or self.size.width
        nrow = self.scrollable_content_region.height or self.size.height

        # Skip zero dimensions — happens when widget is hidden by
        # ContentSwitcher.  Sending 0×0 to the PTY would cause the child
        # process to format output for a zero-column terminal, corrupting
        # the scrollback captured during that window.
        if ncol == 0 or nrow == 0:
            return

        self.ncol = ncol
        self.nrow = nrow
        await self.send_queue.put(["set_size", self.nrow, self.ncol])
        self._screen.resize(self.nrow, self.ncol)
        self._update_virtual_size()
