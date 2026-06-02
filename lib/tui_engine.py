#!/usr/bin/env python3
"""zClaude TUI Engine — OpenCode/Crush-inspired terminal UI framework.

Replicates the visual design language of OpenCode (Charm Bubble Tea):
  - SplitPane layout: 70% main | 30% sidebar (horizontal)
  - Container system: per-side borders + padding
  - Dark theme: #212121 bg, #fab283 primary (orange), #e0e0e0 text
  - RoundedBorder dialogs for overlays
  - Status bar (1 row) with segmented layout
  - Viewport-based list scrolling

Zero external dependencies. Pure Python stdlib.
Works on Linux, macOS, Windows, Termux, SSH.
"""

from __future__ import annotations

import os
import re
import sys
import time as _time
import threading
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# Theme — OpenCode default dark palette
# ═══════════════════════════════════════════════════════════════
# Source: internal/tui/theme/opencode.go from opencode-ai/opencode

class Theme:
    """OpenCode dark theme — exact hex values from source.

    Key difference from typical themes: Primary is ORANGE (#fab283),
    not blue. Blue is Secondary (#5c9cf5). This matches OpenCode's
    distinctive warm accent on cool background look.
    """

    # ── Raw ANSI codes ──────────────────────────────────────
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    STRIKETHROUGH = "\033[9m"
    REVERSED = "\033[7m"

    # ── Background shades ─────────────────────────────────
    BG = "\033[48;5;0m"               # #212121 → black (closest 256)
    BG_SECONDARY = "\033[48;5;236m"     # #252525 → dark gray
    BG_DARKER = "\033[48;5;16m"        # #121212 → near-black
    BG_HIGHLIGHT = "\033[48;5;238m"    # #303030 → selection bg

    # ── Text colors ───────────────────────────────────────
    TEXT = "\033[38;5;252m"            # #e0e0e0 → light gray
    TEXT_MUTED = "\033[38;5;242m"      # #6a6a6a → dim gray
    TEXT_EMPHASIZED = "\033[38;5;220m" # #e5c07b → gold/yellow

    # ── Semantic / accent colors ───────────────────────────
    PRIMARY = "\033[38;5;215m"         # #fab283 → orange/gold (MAIN ACCENT)
    SECONDARY = "\033[38;5;75m"       # #5c9cf5 → blue
    ACCENT = "\033[38;5;141m"         # #9d7cd8 → purple
    SUCCESS = "\033[38;5;77m"         # #7fd88f → green
    WARNING = "\033[38;5;215m"        # #f5a742 → orange
    ERROR = "\033[38;5;167m"          # #e06c75 → red
    INFO = "\033[38;5;74m"            # #56b6c2 → cyan

    # ── Border colors ─────────────────────────────────────
    BORDER = "\033[38;5;240m"          # #4b4c5c → subtle border
    BORDER_FOCUSED = "\033[38;5;215m"  # = PRIMARY (#fab283)
    BORDER_DIM = "\033[38;5;238m"      # #303030 → selection border

    _enabled: bool = True

    @classmethod
    def detect(cls) -> None:
        cls._enabled = getattr(sys.stdout, "isatty", lambda: False)()

    @classmethod
    def rgb(cls, r: int, g: int, b: int) -> str:
        return f"\033[38;2;{r};{g};{b}m"


T = Theme


# ═══════════════════════════════════════════════════════════════
# Color shortcut methods (staticmethod — call as T.bold("text"))
# ═══════════════════════════════════════════════════════════════

def _make_shortcuts(cls):
    def mk(name, ansi):
        def colorize(text: str) -> str:
            if not T._enabled:
                return text
            return f"{ansi}{text}{T.RESET}"
        setattr(cls, name, staticmethod(colorize))

    mk("bold", T.BOLD)
    mk("dim", T.DIM)
    mk("italic", T.ITALIC)
    mk("success", T.SUCCESS)
    mk("error", T.ERROR)
    mk("warn", T.WARNING)
    mk("info", T.INFO)
    mk("primary", T.PRIMARY)       # orange
    mk("secondary", T.SECONDARY)   # blue
    mk("muted", T.TEXT_MUTED)
    mk("emphasis", T.TEXT_EMPHASIZED)
    mk("highlight", T.PRIMARY)
    mk("text", T.TEXT)
    mk("accent", T.ACCENT)


_make_shortcuts(Theme)


def _c_method(cls, color_name: str, text: str) -> str:
    """Dynamic color lookup: T.c('primary', 'hello')."""
    if not cls._enabled:
        return text
    mapping = {
        "primary": cls.PRIMARY, "secondary": cls.SECONDARY,
        "success": cls.SUCCESS, "error": cls.ERROR,
        "warn": cls.WARNING, "warning": cls.WARNING,
        "info": cls.INFO, "muted": cls.TEXT_MUTED,
        "emphasis": cls.TEXT_EMPHASIZED,
        "highlight": cls.PRIMARY, "text": cls.TEXT,
        "accent": cls.ACCENT,
    }
    ansi = mapping.get(color_name, cls.TEXT)
    method = getattr(cls, color_name, None)
    if callable(method) and not isinstance(ansi, str):
        return method(text)
    return f"{ansi}{text}{cls.RESET}"


Theme.c = classmethod(lambda cls, name, text: _c_method(cls, name, text))


# ═══════════════════════════════════════════════════════════════
# Terminal
# ═══════════════════════════════════════════════════════════════

class Term:
    _w: int = 80
    _h: int = 24

    @classmethod
    def w(cls) -> int:
        if cls._w == 80:
            try:
                cls._w = os.get_terminal_size(sys.stdout.fileno()).columns
            except Exception:
                pass
        return max(cls._w, 40)

    @classmethod
    def h(cls) -> int:
        if cls._h == 24:
            try:
                cls._h = os.get_terminal_size(sys.stdout.fileno()).lines
            except Exception:
                pass
        return max(cls._h, 12)

    @classmethod
    def size(cls) -> Tuple[int, int]:
        return (cls.w(), cls.h())

    @classmethod
    def clear(cls) -> None:
        sys.stdout.write("\033[J\033[H")
        sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════
# Layout helpers
# ═══════════════════════════════════════════════════════════════

def _strip_ansi(s: str) -> str:
    """Remove ANSI escapes for width calculation."""
    return re.sub(r"\033\[[^m]*m", "", s)


def _visible_len(s: str) -> int:
    """Get visible (non-ANSI) length of string."""
    return len(_strip_ansi(s))


def _pad(s: str, width: int, align: str = "left") -> str:
    """Pad or truncate string to fit width."""
    vlen = _visible_len(s)
    if vlen >= width:
        return s + "…" if len(s) > width else s[:width]
    pad = width - vlen
    if align == "center":
        return " " * (pad // 2) + s + " " * (pad - pad // 2)
    elif align == "right":
        return " " * pad + s
    return s + " " * pad


# ═══════════════════════════════════════════════════════════════
# SplitPane — OpenCode-style horizontal/vertical split layout
# ═══════════════════════════════════════════════════════════════

class SplitPane:
    """Split layout: horizontal (left|right) or vertical (top|bottom).

    Mimics OpenCode's NewSplitPane from internal/tui/layout/split.go.
    Default ratios match OpenCode: 0.7 main / 0.3 sidebar.
    """

    def __init__(self, ratio: float = 0.7):
        self.ratio = ratio          # horizontal: left gets this fraction
        self.vertical_ratio = 0.85  # vertical: top gets this fraction

    def render(self, left: str, right: str,
               w: int = None, h: int = None) -> str:
        """Render horizontal split: left panel | right panel."""
        w = w or Term.w()
        h = h or Term.h()

        side_w = max(24, int(w * (1 - self.ratio)))
        main_w = w - side_w - 1  # -1 for divider

        left_lines = left.split("\n")
        right_lines = right.split("\n")

        out: List[str] = []
        for i in range(max(len(left_lines), len(right_lines))):
            l_line = left_lines[i] if i < len(left_lines) else ""
            r_line = right_lines[i] if i < len(right_lines) else ""

            l_padded = _pad(l_line, side_w)
            divider = T.dim("│") if i == 0 or i < len(left_lines) or i < len(right_lines) else "│"
            r_padded = _pad(r_line, main_w)

            out.append(f"{l_padded} {divider} {r_padded}")

        return "\n".join(out)

    def render_vertical(self, top: str, bottom: str,
                        w: int = None, h: int = None) -> str:
        """Render vertical split: top panel / bottom panel."""
        w = w or Term.w()
        h = h or Term.h()

        top_h = max(3, int(h * self.vertical_ratio))
        bottom_h = h - top_h - 1  # -1 for divider

        top_lines = top.split("\n")
        bottom_lines = bottom.split("\n")

        out: List[str] = []

        # Top section
        for line in top_lines[:top_h]:
            out.append(_pad(line, w))

        # Divider
        out.append(T.dim("─" * (w // 2)) + "─" * (w - w // 2))

        # Bottom section
        for line in bottom_lines[:bottom_h]:
            out.append(_pad(line, w))

        return "\n".join(out)


# ═══════════════════════════════════════════════════════════════
# Container — padded box with optional per-side borders
# ═══════════════════════════════════════════════════════════════

class Container:
    """Wrap content with configurable padding and per-side borders.

    Mirrors OpenCode's layout.Container from internal/tui/layout/container.go.
    Used for messages panel, editor, sidebar sections.
    """

    def __init__(self, content: str = "",
                 pad_top: int = 0, pad_right: int = 0,
                 pad_bottom: int = 0, pad_left: int = 0,
                 border_top: bool = False,
                 border_right: bool = False,
                 border_bottom: bool = False,
                 border_left: bool = False,
                 width: int = None):
        self.content = content
        self.pad_top = pad_top
        self.pad_right = pad_right
        self.pad_bottom = pad_bottom
        self.pad_left = pad_left
        self.border_top = border_top
        self.border_right = border_right
        self.border_bottom = border_bottom
        self.border_left = border_left
        self.width = width

    def render(self) -> str:
        lines = self.content.split("\n") if self.content else [""]
        w = self.width or Term.w()

        # Apply borders (shrink inner width)
        inner_w = w
        if self.border_left:
            inner_w -= 1
        if self.border_right:
            inner_w -= 1

        out: List[str] = []

        # Top padding
        for _ in range(self.pad_top):
            out.append("")

        # Top border
        if self.border_top:
            bl = "┌" if self.border_left else "─"
            br = "┐" if self.border_right else "─"
            mid = "─" * max(0, inner_w)
            out.append(f"{bl}{mid}{br}")

        # Content lines with left/right borders and padding
        for line in lines:
            prefix = "│" if self.border_left else ""
            suffix = "│" if self.border_right else ""
            pad_l = " " * self.pad_left
            pad_r = " " * self.pad_right
            inner = _pad(line, inner_w - self.pad_left - self.pad_right)
            out.append(f"{prefix}{pad_l}{inner}{pad_r}{suffix}")

        # Bottom border
        if self.border_bottom:
            bl = "└" if self.border_left else "─"
            br = "┘" if self.border_right else "─"
            mid = "─" * max(0, inner_w)
            out.append(f"{bl}{mid}{br}")

        # Bottom padding
        for _ in range(self.pad_bottom):
            out.append("")

        return "\n".join(out)


# ═══════════════════════════════════════════════════════════════
# Box — bordered panels (rounded for dialogs)
# ═══════════════════════════════════════════════════════════════

class Box:
    """Border panels using Unicode box-drawing characters."""

    ROUND = {"tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
             "l": "│", "r": "│",
             "hl": "├", "hr": "┤", "vl": "╰", "vr": "╯"}
    DOUBLE = {"tl": "╔", "tr": "╗", "bl": "╚", "br": "╝",
              "l": "║", "r": "║",
              "hl": "╠", "hr": "╣", "vl": "╚", "vr": "╝"}
    SINGLE = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
              "l": "│", "r": "│",
              "hl": "├", "hr": "┤", "vl": "└", "vr": "┘"}

    @staticmethod
    def dialog(title: str, body: List[str],
               scroll_offset: int = 0) -> str:
        """Centered rounded-border dialog with viewport scrolling.

        Auto-sizes to fit terminal height. Shows ▲/▼ indicators
        when content overflows. This is the primary UI primitive.
        """
        term_w = Term.w()
        term_h = Term.h()

        # Dialog width: fit within terminal with margin
        w = min(term_w - 4, max(50, term_w - 10))

        ch = Box.ROUND
        inner = w - 4  # 2 padding each side

        out: List[str] = []
        out.append(f"{ch['tl']}{'─' * inner}{ch['tr']}")

        # Title bar
        title_text = f" {T.bold(T.primary(title))} "
        out.append(f"{ch['l']}{_pad(title_text, inner + 2, 'center')}{ch['r']}")
        out.append(f"{ch['l']}{'─' * inner}{ch['r']}")

        # Available height for body content
        overhead = 5  # top border + title + sep + hint + bottom border
        usable_h = term_h - overhead
        total_body = len(body)

        # Clamp scroll offset
        if scroll_offset < 0:
            scroll_offset = 0
        elif scroll_offset > 0 and scroll_offset >= total_body:
            scroll_offset = max(0, total_body - usable_h)

        # Determine visible window
        if total_body <= usable_h:
            visible = body
            show_above = False
            show_below = False
        else:
            end = scroll_offset + usable_h
            if end > total_body:
                end = total_body
                scroll_offset = max(0, total_body - usable_h)
            visible = body[scroll_offset:end]
            show_above = scroll_offset > 0
            show_below = end < total_body

        # Scroll indicator above
        if show_above:
            out.append(f"{ch['l']}"
                       f"{_pad(T.dim(f'▲ {scroll_offset} more'), inner)}{ch['r']}")

        # Body lines
        for line in visible:
            out.append(f"{ch['l']} {_pad(line, inner)}{ch['r']}")

        # Scroll indicator below
        if show_below:
            remaining = total_body - scroll_offset - len(visible)
            out.append(f"{ch['l']}"
                       f"{_pad(T.dim(f'▼ {remaining} more'), inner)}{ch['r']}")

        # Fill remaining space
        shown = len(visible) + (1 if show_above else 0) + (1 if show_below else 0)
        fill = max(0, usable_h - shown)
        for _ in range(fill):
            out.append(f"{ch['l']}{' ' * inner}{ch['r']}")

        # Hint line
        out.append(f"{ch['l']}{'─' * inner}{ch['r']}")
        out.append(f"{ch['vl']}{'─' * inner}{ch['br']}")

        return "\n".join(out)

    @staticmethod
    def panel(title: str, body: List[str], width: int = None) -> str:
        """Simple titled rounded panel (no scrolling)."""
        w = width or Term.w() - 4
        ch = Box.ROUND
        inner = w - 4
        out = [f"{ch['tl']}{'─' * inner}{ch['tr']}"]
        out.append(f"{ch['l']} {_pad(T.bold(title), inner + 2, 'center')} {ch['r']}")
        out.append(f"{ch['l']}{'─' * inner}{ch['r']}")
        for line in body:
            out.append(f"{ch['l']} {_pad(line, inner)}{ch['r']}")
        out.append(f"{ch['vl']}{'─' * inner}{ch['br']}")
        return "\n".join(out)


# ═══════════════════════════════════════════════════════════════
# StatusBar — single-row segmented status bar (OpenCode style)
# ═══════════════════════════════════════════════════════════════

class StatusBar:
    """Bottom status bar — matches OpenCode's component/core/status.go.

    Format: [help] [message......flex...] [info-right] [model]
    Always exactly 1 row tall.
    """

    @staticmethod
    def render(hints: str = "", message: str = "",
               model: str = "") -> str:
        w = Term.w()

        # Left: hints or empty
        left = hints or ""

        # Center: message (flexible, auto-truncated)
        msg = message or ""

        # Right: model name
        right = model or ""

        # Build the bar
        left_clean = _visible_len(left)
        right_clean = _visible_len(right)
        msg_clean = _visible_len(msg)
        mid_space = w - left_clean - right_clean - 2
        if mid_space < 0:
            mid_space = 0

        # Truncate message if needed
        if msg_clean > mid_space:
            msg = msg[:mid_space - 1] + "…"

        mid = " " * (mid_space - msg_clean) if mid_space > msg_clean else ""

        bar = (f"{T.BG_DARK}{T.BORDER}{left}"
                f"{msg}{mid}"
                f"{T.muted(right)}"
                f"{T.BORDER}{T.RESET}")
        return bar


# ═══════════════════════════════════════════════════════════════
# Keyboard — cross-platform key reader
# ═══════════════════════════════════════════════════════════════

class Keyboard:
    @staticmethod
    def has_tty() -> bool:
        return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()

    @staticmethod
    def getkey() -> str:
        if not Keyboard.has_tty():
            try:
                line = input("")
                return line[0] if line else ""
            except EOFError:
                return ""

        import termios, select
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)

        try:
            new_attrs = termios.tcgetattr(fd)
            new_attrs[3] = new_attrs[3] & ~termios.ICANON
            new_attrs[3] = new_attrs[3] & ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, new_attrs)

            ch = sys.stdin.read(1)
            if not ch:
                return ""

            if ch == "\x1b":
                seq = ""
                for _ in range(6):
                    c2 = sys.stdin.read(1)
                    if not c2:
                        break
                    seq += c2
                    if c2.isalpha():
                        break
                    if len(seq) > 5:
                        break

                if seq.endswith("A"):
                    return "up"
                if seq.endswith("B"):
                    return "down"
                if seq.endswith("C"):
                    return "right"
                if seq.endswith("D"):
                    return "left"
                if seq.endswith("~"):
                    if "[5" in seq:
                        return "page_up"
                    if "[6" in seq:
                        return "page_down"
                    if "[1" in seq:
                        return "home"
                    if "[3" in seq:
                        return "delete"
                    if "[4" in seq:
                        return "end"
                return "escape"

            if ch == "\r":
                return "enter"
            if ch == "\t":
                return "tab"
            if ch == "\x7f":
                return "backspace"
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\x04":
                raise KeyboardInterrupt

            return ch

        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)


# ═══════════════════════════════════════════════════════════════
# Screen — full-frame renderer with SplitPane + StatusBar
# ═══════════════════════════════════════════════════════════════

class Screen:
    """Full-screen frame renderer.

    Layout (matches OpenCode root view):
    ┌──────────────────────────────────────────────┐
    │                  (content area)                │
    │   SplitPane:  sidebar(30%) │ main(70%)        │
    ├──────────────────────────────────────────────┤
    │ StatusBar: [hints...msg.............model]    │
    └──────────────────────────────────────────────┘
    """

    def __init__(self):
        self.w = Term.w()
        self.h = Term.h()

    def render(self, sidebar: str = "", main: str = "",
              hints: str = "", message: str = "",
              model: str = "") -> None:
        """Render complete frame: split pane + status bar."""
        Term.clear()

        w = self.w
        h = self.h

        # Content area (leave 1 row for status bar)
        content_h = h - 1

        if sidebar and main:
            # Render split pane, clipped to content height
            pane = SplitPane(ratio=0.70)
            full = pane.render(sidebar, main, w=w, h=content_h)
            lines = full.split("\n")
            for line in lines[:content_h]:
                print(line)
        elif main:
            for line in main.split("\n")[:content_h]:
                print(line)
        elif sidebar:
            for line in sidebar.split("\n")[:content_h]:
                print(line)

        # Status bar (always last row)
        print(StatusBar.render(hints=hints, message=message, model=model))
        sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════
# Sidebar — left nav panel (OpenCode style)
# ═══════════════════════════════════════════════════════════════

class SidebarItem:
    def __init__(self, icon: str, label: str, key: str = "",
                 active: bool = False, badge: str = ""):
        self.icon = icon
        self.label = label
        self.key = key
        self.active = active
        self.badge = badge


class Sidebar:
    """Left navigation panel — OpenCode style with header + items + footer."""

    def __init__(self, items: List[SidebarItem], title: str = " zClaude",
                 active_idx: int = 0):
        self.items = items
        self.title = title
        self.active_idx = active_idx

    def render(self) -> str:
        w = 26  # fixed sidebar width (matches OpenCode ~30% of 80col)
        inner = w - 2
        ch = Box.ROUND
        lines: List[str] = []

        # Top border
        lines.append(f"{ch['tl']}{'─' * inner}{ch['tr']}")

        # Header
        header = f" {T.primary('◆')} {T.bold(self.title)}"
        lines.append(f"{ch['l']}{_pad(header, inner)}{ch['r']}")
        lines.append(f"{ch['l']}{'─' * inner}{ch['r']}")

        # Nav items
        for idx, item in enumerate(self.items):
            is_active = (idx == self.active_idx)
            if is_active:
                marker = T.primary("▸")
                lbl = T.bold(item.label)
                bg = T.BG_HIGHLIGHT
            else:
                marker = item.icon
                lbl = T.text(item.label)
                bg = ""

            key_str = f" {T.dim(item.key)}" if item.key else ""
            badge_str = f" {T.secondary(item.badge)}" if item.badge else ""

            line = f"{bg}{marker} {lbl}{key_str}{badge_str}{T.RESET}"
            lines.append(f"{ch['l']}{_pad(line, inner)}{ch['r']}")

        # Fill remaining space
        total_items = len(lines) - 3  # minus top border, header, separator
        fill = max(0, Term.h() - total_items - 4)  # -4 for bottom elements
        for _ in range(fill):
            lines.append(f"{ch['l']}{' ' * inner}{ch['r']}")

        # Separator before footer
        lines.append(f"{ch['l']}{'─' * inner}{ch['r']}")

        # Footer
        footer = f" {T.dim('v' + '1.0' if True else '')}"
        lines.append(f"{ch['l']}{_pad(footer, inner)}{ch['r']}")
        lines.append(f"{ch['vl']}{'─' * inner}{ch['br']}")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Welcome banner (shown once at startup)
# ═══════════════════════════════════════════════════════════════

def welcome_banner() -> str:
    """Render welcome banner (rounded border, shown once)."""
    w = Term.w()
    ch = Box.ROUND
    inner = w - 4
    lines = []
    lines.append(f"{ch['tl']}{'═' * inner}{ch['tr']}")
    title = f" {T.primary(' ◆ ')}{T.bold(' zClaude ')}"
    sub = T.dim("Universal AI Launcher — Modern TUI")
    lines.append(f"{ch['l']}{_pad(title, inner + 2, 'center')}{ch['r']}")
    lines.append(f"{ch['hl']}{'═' * inner}{ch['vr']}")
    lines.append(f"{ch['l']}{_pad(sub, inner + 2, 'center')}{ch['r']}")
    lines.append(f"{ch['vl']}{'─' * inner}{ch['br']}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Scroll helpers for list views
# ═══════════════════════════════════════════════════════════════

def auto_scroll(state: Dict, total_items: int) -> None:
    """Keep cursor visible within scroll window."""
    cursor = state["list_cursor"]
    offset = state.get("scroll_offset", 0)
    usable = Term.h() - 9  # space for dialog chrome
    if usable < 3:
        usable = 3

    if cursor < offset:
        state["scroll_offset"] = cursor
    elif cursor >= offset + usable:
        state["scroll_offset"] = cursor - usable + 1

    state["scroll_offset"] = max(0, state["scroll_offset"])
    state["scroll_offset"] = min(
        state["scroll_offset"], max(0, total_items - usable))


def handle_scroll(state: Dict, key: str, total_items: int) -> bool:
    """Handle Page Up / Page Down. Returns True if consumed."""
    if key not in ("page_up", "page_down"):
        return False
    usable = Term.h() - 9
    if usable < 3:
        usable = 3
    offset = state.get("scroll_offset", 0)
    if key == "page_up":
        state["scroll_offset"] = max(0, offset - usable)
    else:
        state["scroll_offset"] = min(
            max(0, total_items - usable), offset + usable)
    return True


# Auto-detect on import
Theme.detect()
