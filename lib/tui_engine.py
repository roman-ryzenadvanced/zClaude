#!/usr/bin/env python3
"""zClaude TUI Engine — Lipgloss/Bubble Tea inspired terminal UI.

Design language modeled after OpenCode (now Crush) by Charm:
  - Dark theme: #1e2327 background, #89b4fa accent, #cdd6f4 text
  - Sidebar + Main content split layout
  - Rounded border panels (╭╮╰╯) with double-line headers
  - Dialog overlays for selections
  - Status bar at bottom
  - Consistent spacing and typography

Zero external dependencies. Pure Python stdlib.
Works on Linux, macOS, Windows, Termux, SSH.
"""

from __future__ import annotations

import os
import shutil
import sys
import time as _time
import threading
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# Theme — OpenCode/Crush dark palette (Lipgloss-inspired)
# ═══════════════════════════════════════════════════════════════

class Theme:
    """OpenCode-inspired dark theme. All colors as ANSI escape strings.

    Palette reference (from Crush/OpenCode dark theme):
      Background:    #1e2327  (surface)
      Surface:       #282d35  (elevated surface)
      SurfaceDarker: #181825  (darker surface)
      Text:          #cdd6f4  (primary text)
      TextMuted:     #6c7086  (secondary text)
      Primary:       #89b4fa  (accent blue)
      Secondary:     #a6e3a1  (teal/green accent)
      Success:       #a6e3a1  (green)
      Warning:       #f9e2af  (yellow/gold)
      Error:         #f38ba8  (red/pink)
      Border:        #313244  (subtle border)
      BorderFocus:   #89b4fa  (accent border)
    """

    # ── Raw ANSI codes ──────────────────────────────────────
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    STRIKETHROUGH = "\033[9m"
    REVERSED = "\033[7m"

    # ── Theme colors (256-color approximations of hex values) ───
    # Background shades
    BG = "\033[48;5;16;23m"           # #1e2327
    BG_SURFACE = "\033[48;5;40;45m"     # #282d35
    BG_DARK = "\033[48;5;24;24m"         # #181825
    BG_HIGHLIGHT = "\033[48;5;41;49m"   # #313244

    # Text colors
    TEXT = "\033[38;5;205;212m"        # #cdd6f4
    TEXT_DIM = "\033[38;5;108;112m"      # #6c7086
    TEXT_SUBTLE = "\033[38;5;88;91m"     # #515562

    # Semantic / accent colors
    PRIMARY = "\033[38;5;137;180m"      # #89b4fa (sky blue)
    SECONDARY = "\033[38;5;166;227m"    # #a6e3a1 (green/teal)
    SUCCESS = "\033[38;5;166;227m"      # #a6e3a1
    WARNING = "\033[38;5;249;226m"      # #f9e2af
    ERROR = "\033[38;5;243;138m"        # #f38ba8
    INFO = "\033[38;5;137;180m"         # #89b4fa

    # Border colors
    BORDER = "\033[38;5;49;50m"          # #313244
    BORDER_FOCUS = "\033[38;5;137;180m"   # #89b4fa
    BORDER_DIM = "\033[38;5;88;91m"       # #515562

    # Special highlights
    ROSEWATER = "\033[38;5;243;138m"     # #f38ba8
    LAVENDER = "\033[38;5;180;190m"     # #b4befe
    MAUVE = "\033[38;5;203;166m"        # #cba6f7
    PEACH = "\033[38;5;235;160m"        # #eba0ac
    SKY = "\033[38;5;137;180m"          # #89b4fa
    GREEN = "\033[38;5;166;227m"        # #a6e3a1
    RED = "\033[38;5;243;138m"          # #f38ba8

    _enabled: bool = True

    @classmethod
    def detect(cls) -> None:
        cls._enabled = getattr(sys.stdout, "isatty", lambda: False)()

    @classmethod
    def rgb(cls, r: int, g: int, b: int) -> str:
        """Return 256-color ANSI for given RGB."""
        return f"\033[38;2;{r};{g};{b}m"

# Shorthand
T = Theme


# ═══════════════════════════════════════════════════════════════
# Color shortcuts — convenience methods on Theme
# ═══════════════════════════════════════════════════════════════

def _make_shortcuts(cls):
    """Generate color shortcut methods after class creation."""
    def mk(name, ansi):
        def colorize(text: str) -> str:
            if not T._enabled: return text
            return f"{ansi}{text}{T.RESET}"
        setattr(cls, name, staticmethod(colorize))

    mk("bold", T.BOLD)
    mk("dim", T.DIM)
    mk("italic", T.ITALIC)
    mk("success", T.SUCCESS)
    mk("error", T.ERROR)
    mk("warn", T.WARNING)
    mk("info", T.INFO)
    mk("primary", T.PRIMARY)
    mk("secondary", T.SECONDARY)
    mk("muted", T.TEXT_DIM)
    mk("title", T.LAVENDER)
    mk("highlight", T.PRIMARY)
    mk("text", T.TEXT)
    mk("rose", T.ROSEWATER)
    mk("peach", T.PEACH)
    mk("sky", T.SKY)
    mk("green", T.GREEN)
    mk("red", T.RED)
    mk("mauve", T.MAUVE)


_make_shortcuts(Theme)


# Add dynamic color lookup method after shortcuts are created
def _c_method(cls, color_name: str, text: str) -> str:
    """Dynamic color: T.c('primary', 'hello') → themed hello."""
    if not cls._enabled:
        return text
    # Map color name to ANSI constant or method
    mapping = {
        "primary": cls.PRIMARY, "secondary": cls.SECONDARY,
        "success": cls.SUCCESS, "error": cls.ERROR,
        "warn": cls.WARNING, "warning": cls.WARNING,
        "info": cls.INFO, "muted": cls.TEXT_DIM,
        "title": cls.LAVENDER, "highlight": cls.PRIMARY,
        "text": cls.TEXT, "rose": cls.ROSEWATER,
        "peach": cls.PEACH, "sky": cls.SKY,
        "green": cls.GREEN, "red": cls.RED,
        "mauve": cls.MAUVE,
    }
    ansi = mapping.get(color_name, cls.TEXT)
    # If it's a method (like bold, dim), call it
    method = getattr(cls, color_name, None)
    if callable(method) and not isinstance(ansi, str):
        return method(text)
    return f"{ansi}{text}{cls.RESET}"

# Bind as classmethod
import types
Theme.c = classmethod(lambda cls, name, text: _c_method(cls, name, text))


# ═══════════════════════════════════════════════════════════════
# Terminal — size detection
# ═══════════════════════════════════════════════════════════════

class Term:
    """Terminal size and capability detection."""

    _w: int = 80
    _h: int = 24

    @classmethod
    def w(cls) -> int:
        if cls._w == 80:
            try: cls._w = os.get_terminal_size(sys.stdout.fileno()).columns
            except Exception: pass
        return max(cls._w, 40)

    @classmethod
    def h(cls) -> int:
        if cls._h == 24:
            try: cls._h = os.get_terminal_size(sys.stdout.fileno()).lines
            except Exception: pass
        return max(cls._h, 12)

    @classmethod
    def size(cls) -> Tuple[int, int]:
        return (cls.w(), cls.h())

    @classmethod
    def clear(cls) -> None:
        sys.stdout.write("\033[J\033[H")
        sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════
# Layout — Lipgloss-style layout primitives
# ═══════════════════════════════════════════════════════════════

class Layout:
    """Layout primitives inspired by Lipgloss Join/Render.

    Provides horizontal/vertical stacking, borders, padding,
    and alignment — all using string manipulation.
    """

    @staticmethod
    def join_horizontal(parts: List[str],
                        sep: str = "  ",
                        width: int = None) -> str:
        """Join parts horizontally with separator."""
        w = width or (Term.w() - 4)
        total_text_len = sum(len(p) for p in parts)
        padding = w - total_text_len - len(sep) * (len(parts) - 1)
        pad_per_gap = max(0, padding // max(1, len(parts) - 1))
        return sep.join(parts)

    @staticmethod
    def join_vertical(lines: List[str], padding: int = 0) -> str:
        """Stack lines vertically with optional left padding."""
        prefix = " " * padding
        return "\n".join(prefix + line if line else "" for line in lines)

    @staticmethod
    def center(text: str, width: int) -> str:
        """Center text within given width."""
        visible = len(text)
        # Strip ANSI codes for length calculation
        clean = _strip_ansi(text)
        pad = max(0, width - len(clean))
        left = pad // 2
        right = pad - left
        return " " * left + text + " " * right

    @staticmethod
    def left_align(text: str, width: int) -> str:
        """Left-align text within width."""
        clean = _strip_ansi(text)
        pad = max(0, width - len(clean))
        return text + " " * pad

    @staticmethod
    def right_align(text: str, width: int) -> str:
        """Right-align text within width."""
        clean = _strip_ansi(text)
        pad = max(0, width - len(clean))
        return " " * pad + text


def _strip_ansi(s: str) -> str:
    """Remove ANSI escape sequences from string for length calc."""
    import re
    return re.sub(r"\033\[[^m]*m", "", s)


# ═══════════════════════════════════════════════════════════════
# Box — Unicode bordered panels (Lipgloss style)
# ═══════════════════════════════════════════════════════════════

class Box:
    """Border panel renderer with rounded corners.

    Styles:
      'round'  → ╭╮╰╯ rounded corners (default, like Lipgloss RoundedBorder)
      'double' → ╔═╗╚═╝ double-line (for headers/focus)
      'single' → ┌┐└┘ single-line (for regular content)
      'ascii'  → +-+|+ ASCII fallback
    """

    # Character sets
    ROUND_TL = "╭"; ROUND_TR = "╮"; ROUND_BL = "╰"; ROUND_BR = "╯"
    ROUND_L = "│"; ROUND_R = "│"
    DOUBLE_TL = "╔"; DOUBLE_TR = "╗"; DOUBLE_BL = "╚"; DOUBLE_BR = "╝"
    DOUBLE_L = "║"; DOUBLE_R = "║"
    SINGLE_TL = "┌"; SINGLE_TR = "┐"; SINGLE_BL = "└"; SINGLE_BR = "┘"
    SINGLE_L = "│"; SINGLE_R = "│"
    ASCII_TL = "+"; ASCII_TR = "+"; ASCII_BL = "+"; ASCII_BR = "+"
    ASCII_L = "|"; ASCII_R = "|"

    @classmethod
    def chars(cls, style: str = "round") -> Dict[str, str]:
        """Get character set for given border style."""
        styles = {
            "round": {
                "tl": cls.ROUND_TL, "tr": cls.ROUND_TR,
                "bl": cls.ROUND_BL, "br": cls.ROUND_BR,
                "l": cls.ROUND_L, "r": cls.ROUND_R,
                "hl": "├", "hr": "┤", "vl": "╰", "vr": "╯",
            },
            "double": {
                "tl": cls.DOUBLE_TL, "tr": cls.DOUBLE_TR,
                "bl": cls.DOUBLE_BL, "br": cls.DOUBLE_BR,
                "l": cls.DOUBLE_L, "r": cls.DOUBLE_R,
                "hl": "╠", "hr": "╣", "vl": "╚", "vr": "╝",
            },
            "single": {
                "tl": cls.SINGLE_TL, "tr": cls.SINGLE_TR,
                "bl": cls.SINGLE_BL, "br": cls.SINGLE_BR,
                "l": cls.SINGLE_L, "r": cls.SINGLE_R,
                "hl": "├", "hr": "┤", "vl": "└", "vr": "┘",
            },
            "ascii": {
                "tl": cls.ASCII_TL, "tr": cls.ASCII_TR,
                "bl": cls.ASCII_BL, "br": cls.ASCII_BR,
                "l": cls.ASCII_L, "r": cls.ASCII_R,
                "hl": "+", "hr": "+", "vl": "+", "vr": "+",
            },
        }
        return styles.get(style, styles["round"])

    @staticmethod
    def panel(title: str, lines: List[str],
              width: int = None, style: str = "round",
              title_style: str = "double",
              color: str = "") -> str:
        """Render a titled bordered panel."""
        w = width or (Term.w() - 4)
        ch = Box.chars(style)
        inner = w - 4  # -2 borders, -2 padding
        out: List[str] = []

        # Top border with title
        tch = Box.chars(title_style)
        top = f"{ch['tl']}{'─' * (inner + 2)}{ch['tr']}"
        out.append(top)

        # Title row (if provided)
        if title:
            colored_title = f"{T.bold(T.title(title))}" if title else ""
            title_line = f"{ch['l']} {Layout.center(colored_title, inner + 2)} {ch['r']}"
            out.append(title_line)

            # Separator under title
            sep = f"{tch['hl']}{'─' * (inner + 2)}{tch['vr']}"
            out.append(sep)

        # Content lines
        for line in lines:
            out.append(f"{ch['l']} {_pad_line(line, inner)}{ch['r']}")

        # Bottom border
        bottom = f"{ch['vl']}{'─' * (inner + 2)}{ch['br']}"
        out.append(bottom)

        return "\n".join(out)

    @staticmethod
    def dialog(title: str, body: List[str],
               width: int = None, height: int = None) -> str:
        """Render a centered dialog overlay (like OpenCode's modal dialogs)."""
        w = width or min(60, Term.w() - 8)
        h = height or min(len(body) + 6, Term.h() - 8)
        ch = Box.chars("round")
        inner = w - 4

        out: List[str] = []

        # Top
        out.append(f"{ch['tl']}{'═' * inner}{ch['tr']}")

        # Title bar (double-line for emphasis)
        if title:
            out.append(f"{ch['l']} {T.bold(T.primary(title)):^{inner}s} {ch['r']}")
            out.append(f"{ch['l']}{'─' * inner}{ch['r']}")

        # Body
        for line in body:
            out.append(f"{ch['l']} {_pad_line(line, inner)}{ch['r']}")

        # Fill remaining space
        body_lines = len(body)
        fill_lines = max(0, h - body_lines - 4)
        for _ in range(fill_lines):
            out.append(f"{ch['l']}{' ' * inner}{ch['r']}")

        # Bottom hint
        out.append(f"{ch['vl']}{'─' * inner}{ch['br']}")
        hint = T.dim(" Esc close · ↑↓ navigate · Enter select ")
        out.append(f"{ch['l']} {hint:<{inner}s}{ch['r']}")

        return "\n".join(out)

    @staticmethod
    def simple(lines: List[str], width: int = None,
               style: str = "single") -> str:
        """Simple border around lines (no title)."""
        w = width or (Term.w() - 4)
        ch = Box.chars(style)
        inner = w - 4
        out = [f"{ch['tl']}{'─' * inner}{ch['tr']}"]
        for line in lines:
            out.append(f"{ch['l']} {_pad_line(line, inner)}{ch['r']}")
        out.append(f"{ch['bl']}{'─' * inner}{ch['br']}")
        return "\n".join(out)

    @staticmethod
    def horizontal_rule(width: int = None, char: str = "─",
                       double: bool = False) -> str:
        """A horizontal divider line."""
        w = width or (Term.w() - 4)
        c = "═" if double else char
        return c * w


def _pad_line(line: str, width: int) -> str:
    """Pad or truncate a line to fit within width."""
    clean = _strip_ansi(line)
    if len(clean) >= width:
        # Truncate with ellipsis
        return line[:width - 1] + "…" if len(line) > width else line[:width]
    return line + " " * (width - len(clean))


# ═══════════════════════════════════════════════════════════════
# Keyboard — cross-platform key reading
# ═══════════════════════════════════════════════════════════════

class Keyboard:
    """Cross-platform single-keystroke reader."""

    @staticmethod
    def has_tty() -> bool:
        return hasattr(sys.stdin, "isatty") and sys.stdin.isatty()

    @staticmethod
    def getkey() -> str:
        """Read one keystroke. Returns char or special key name."""
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

            # Escape sequences
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

                if seq.endswith("A"): return "up"
                if seq.endswith("B"): return "down"
                if seq.endswith("C"): return "right"
                if seq.endswith("D"): return "left"
                if seq.endswith("~"):
                    if "[5" in seq: return "page_up"
                    if "[6" in seq: return "page_down"
                    if "[1" in seq: return "home"
                    if "[3" in seq: return "delete"
                    if "[4" in seq: return "end"
                return "escape"

            if ch == "\r": return "enter"
            if ch == "\t": return "tab"
            if ch == "\x7f": return "backspace"
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\x04": raise KeyboardInterrupt  # Ctrl+D

            return ch

        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)

    @staticmethod
    def getkey_blocking(timeout: float = 30.0) -> str:
        """Blocking read with timeout."""
        import select
        if select.select([sys.stdin], [], [], timeout)[0]:
            return Keyboard.getkey()
        return ""


# ═══════════════════════════════════════════════════════════════
# Menu — paginated list with cursor navigation
# ═══════════════════════════════════════════════════════════════

class Menu:
    """Interactive selection list with keyboard navigation."""

    def __init__(self, items: List[str], title: str = "",
                 page_size: int = 12):
        self.items = items
        self.title = title
        self.page_size = page_size
        self.cursor = 0
        self.page = 0

    @property
    def pages(self) -> int:
        return max(1, (len(self.items) + self.page_size - 1) // self.page_size)

    @property
    def visible(self) -> List[str]:
        start = self.page * self.page_size
        return self.items[start:start + self.page_size]

    def render(self) -> str:
        """Render menu to string."""
        lines: List[str] = []
        if self.title:
            lines.append(f"  {T.bold(self.title)}")
            lines.append("")
        for idx, item in enumerate(self.visible):
            marker = "▸ " if idx == self.cursor else "  "
            sel = item if idx == self.cursor else item
            if idx == self.cursor:
                lines.append(f"{marker}{T.PRIMARY(sel)}")
            else:
                lines.append(f"{marker}{T.text(sel)}")
        if self.pages > 1:
            lines.append("")
            lines.append(f"  {T.dim(f'Page {self.page + 1}/{self.pages}')}")
        return "\n".join(lines)

    def handle_key(self, key: str) -> Tuple[int, str]:
        """Process key. Returns (new_cursor, action)."""
        n = len(self.visible)
        if key in ("enter", " "):
            return (self.cursor, "select" if n > 0 else "none")
        if key in ("escape", "q"):
            return (self.cursor, "back")
        if key in ("up", "k", "K"):
            return (max(0, self.cursor - 1), "")
        if key in ("down", "j", "J", "\t"):
            return (min(n - 1, self.cursor + 1), "")
        if key == "home" or key == "g":
            self.page = 0; return (0, "")
        if key == "end" or key == "G":
            self.page = max(0, self.pages - 1); return (min(n - 1, (self.pages - 1) * self.page_size), "")
        try:
            num = int(key)
            if 1 <= num <= n:
                return (num - 1, "select")
        except ValueError:
            pass
        return (self.cursor, "")


# ═══════════════════════════════════════════════════════════════
# Spinner — animated loading indicator
# ═══════════════════════════════════════════════════════════════

class Spinner:
    """Animated spinner for async operations."""

    FRAMES = ["⠋", "⠙", "⠸", "⠴", "⠦", "⠖", "⠞", "⠟"]

    def __init__(self, label: str = ""):
        self.label = label
        self._frame = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=0.5)
            self._thread = None

    def _spin(self):
        while self._running:
            print(f"\r  {self.FRAMES[self._frame % len(self.FRAMES)]} "
                  f"{self.label}   ", end="", flush=True)
            self._frame += 1
            _time.sleep(0.08)

    @staticmethod
    def done(msg: str = "Done.") -> str:
        print(f"\r  {'✓':>8s}  {msg}")
        return msg


# ═══════════════════════════════════════════════════════════════
# ProgressBar — visual progress feedback
# ═══════════════════════════════════════════════════════════════

class ProgressBar:
    """Progress bar with label."""

    CHARS = "█▓▒░▏"

    def __init__(self, total: int = 100, width: int = 40, label: str = ""):
        self.total = total
        self.width = width
        self.label = label
        self.current = 0
        self.start = _time.time()

    def update(self, current: int, label: str = "") -> str:
        self.current = current
        self.label = label or self.label
        pct = min(100, current * 100 // max(self.total, 1))
        filled = int(pct * self.width / 100)
        empty = self.width - filled
        bar = T.PRIMARY("█" * filled) + T.DIM("░" * empty)
        lbl = f" {self.label}" if self.label else ""
        elapsed = _time.time() - self.start
        return f"{bar}{lbl}{T.dim(f' ({elapsed:.1f}s)')}\n"

    def done(self, msg: str = "Done.") -> str:
        out = self.update(self.total, msg)
        return f"{out}{T.success(' ✓ ' + msg)}\n"


# ═══════════════════════════════════════════════════════════════
# Sidebar — left navigation panel (OpenCode style)
# ═══════════════════════════════════════════════════════════════

class SidebarItem:
    """A single sidebar navigation entry."""
    def __init__(self, icon: str, label: str, key: str = "",
                 active: bool = False, badge: str = ""):
        self.icon = icon
        self.label = label
        self.key = key
        self.active = active
        self.badge = badge


class Sidebar:
    """Left sidebar navigation panel (like OpenCode's sidebar).

    Renders as a vertical bordered panel with icon+label items,
    highlight for active item.
    """

    def __init__(self, items: List[SidebarItem], width: int = 24,
                 title: str = " zClaude"):
        self.items = items
        self.width = width
        self.title = title
        self.cursor = 0

    @property
    def count(self) -> int:
        return len(self.items)

    def render(self, active_idx: int = 0) -> str:
        """Render the full sidebar panel."""
        ch = Box.chars("round")
        w = self.width
        inner = w - 2  # -2 for borders on each side
        lines: List[str] = []

        # Top border
        lines.append(f"{ch['tl']}{'═' * inner}{ch['tr']}")

        # Title
        title_str = f"{T.bold(T.mauve(' ◆ '))}{T.title(self.title)}"
        lines.append(f"{ch['l']}{Layout.center(title_str, inner)}{ch['r']}")
        lines.append(f"{ch['l']}{'─' * inner}{ch['r']}")

        # Nav items
        for idx, item in enumerate(self.items):
            is_active = (idx == active_idx)
            if is_active:
                prefix = f"{T.BG_HIGHLIGHT}{T.PRIMARY('▸')}"
                label_text = T.bold(item.label)
                suffix = T.RESET
            else:
                prefix = f" {item.icon}"
                label_text = T.text(item.label)
                suffix = ""

            key_hint = f" {T.dim(item.key)}" if item.key else ""
            line = f"{prefix} {label_text}{key_hint}"
            lines.append(f"{ch['l']}{line:<{inner}s}{ch['r']}{suffix}")

        # Fill remaining
        fill = max(0, Term.h() - len(lines) - 3)
        for _ in range(fill):
            lines.append(f"{ch['l']}{' ' * inner}{ch['r']}")

        # Bottom border
        lines.append(f"{ch['vl']}{'─' * inner}{ch['br']}")

        # Version footer
        ver = T.dim("v1.0")
        lines.append(f"{ch['l']}{Layout.center(ver, inner)}{ch['r']}")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# StatusBar — bottom status bar (OpenCode style)
# ═══════════════════════════════════════════════════════════════

class StatusBar:
    """Bottom status bar showing context info and key hints."""

    @staticmethod
    def render(left: str = "", right: str = "",
                hints: str = "") -> str:
        w = Term.w()
        left_part = left or ""
        right_part = right or ""
        hints_part = hints or " ↑↓ Navigate · Enter Select · Esc Back · q Quit"
        mid_pad = w - len(_strip_ansi(left_part)) - len(_strip_ansi(right_part)) - len(_strip_ansi(hints_part)) - 4
        mid = " " * max(0, mid_pad)
        return f"{T.BG_DARK}{T.BORDER}{T.TEXT(left_part)}{mid}" \
               f"{T.TEXT_DIM(right_part)}{T.TEXT_DIM(hints_part)}" \
               f"{T.BORDER}{T.RESET}"


# ═══════════════════════════════════════════════════════════════
# Screen buffer — virtual screen for frame rendering
# ═══════════════════════════════════════════════════════════════

class Screen:
    """Virtual screen buffer for frame-by-frame rendering.

    Manages the full viewport: header + body (sidebar+main) + statusbar.
    Handles clear-to-end-of-screen for flicker-free redraws.
    """

    def __init__(self):
        self.lines: List[str] = []
        self.w = Term.w()
        self.h = Term.h()

    def clear(self):
        """Reset screen buffer."""
        self.lines = []
        self.w = Term.w()
        self.h = Term.h()

    def write(self, text: str = ""):
        """Add text to buffer (auto-split on newlines)."""
        if text:
            for line in text.split("\n"):
                self.lines.append(line)
        else:
            self.lines.append("")

    def writeln(self, text: str = ""):
        """Write a line (adds newline)."""
        self.write(text + ("\n" if text else ""))

    def newlines(self, count: int = 1):
        """Add blank lines."""
        for _ in range(count):
            self.lines.append("")

    def flush(self, footer: str = "", hints: str = ""):
        """Render the complete screen to terminal.

        Clears screen, writes all lines, draws status bar.
        """
        Term.clear()

        # Write all content lines
        content = "\n".join(self.lines)
        if content:
            sys.stdout.write(content + "\n")

        # Draw status bar at bottom
        if footer or hints:
            sys.stdout.write(footer if footer else "")
        elif hints:
            sys.stdout.write(StatusBar.render(hints=hints))

        sys.stdout.flush()

    def render_frame(self, sidebar: str = "", main: str = "",
                      header: str = "", hints: str = ""):
        """Convenience: render a complete frame with sidebar+main layout."""
        self.clear()

        # Header (optional)
        if header:
            self.writeln(header)

        # Split view: sidebar | main
        if sidebar and main:
            # Calculate heights
            total_h = self.h - 3  # -3 for status bar
            side_lines = sidebar.count("\n") + 1
            main_h = total_h - side_lines

            # Render sidebar (truncated to fit)
            side_parts = sidebar.split("\n")
            for line in side_lines[:main_h]:
                self.writeln(line)

            # Main content fills remaining space
            main_parts = main.split("\n")
            for line in main_parts:
                self.writeln(line)
        elif main:
            for line in main.split("\n"):
                self.writeln(line)
        elif sidebar:
            for line in sidebar.split("\n"):
                self.writeln(line)

        self.flush(hints=hints)


# Auto-detect capabilities on import
Theme.detect()
