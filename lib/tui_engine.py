#!/usr/bin/env python3
"""zClaude TUI Engine — Pure Python stdlib terminal UI primitives.

Zero external dependencies. Works on Linux, macOS, Windows (cmd/PowerShell),
Termux, and headless SSH. Auto-degrades gracefully on non-TTY or
limited terminals.

Provides: Colors, Terminal, Box, Keyboard, Menu, ProgressBar.
"""

from __future__ import annotations

import os
import shutil
import sys
import time as _time
from typing import Any, Dict, List, Optional, Tuple


# ════════════════════════════════════════════════════════════════════
# Colors — Catppuccin-inspired palette with auto-detection & fallback
# ════════════════════════════════════════════════════════════════════

class C:
    """ANSI color constants. Auto-disables on non-TTY or Windows cmd without VT."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    BLINK = "\033[5m"
    REVERSED = "\033[7m"
    STRIKETHROUGH = "\033[9m"

    # ── Foreground colors ────────────────────────────────────────
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # ── Background colors ────────────────────────────────────────
    BG_BLACK = "\033[40m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"

    # ── Semantic aliases (zClaude brand) ─────────────────────
    ACCENT = CYAN
    SUCCESS = GREEN
    WARNING = YELLOW
    ERROR = RED
    INFO = BLUE
    MUTED = DIM
    HIGHLIGHT = BRIGHT_CYAN
    TITLE = MAGENTA
    PRIMARY = CYAN
    SECONDARY = BRIGHT_BLUE

    # Catppuccin-inspired named colors (mapped to 256-color ANSI)
    ROSEWATER = "#f38ba8"   # pink
    LAVENDER = "#b4befe"   # light purple
    MAROON = "#eba0ac"     # peach/orange
    TEAL = "#94e2d5"       # teal
    SAPPHIRE = "#a6e3a1"    # green
    SKY = "#89b4fb"         # blue
    MAUVE = "#cba6f7"       # mauve/dark gray
    SUBTEXT1 = "#a6adc8"     # subtle text
    SURFACE0 = "#313244"     # surface 0
    SURFACE1 = "#45475a"     # surface 1
    SURFACE2 = "#585b70"     # surface 2
    TEXT = "#cdd6f4"          # main text
    OVERLAY0 = "#313244"      # overlay bg
    OVERLAY1 = "#45475a"      # overlay bg
    MANTLE = "#1e2030"        # mantle/panel bg
    CRUST = "#11111b"         # crust/darkest
    GREEN_LIME = "#a6e3a1"    # alias

    _enabled: bool = True
    _256_supported: bool = True

    @classmethod
    def _detect(cls) -> None:
        """Auto-detect terminal capabilities at import time."""
        cls._enabled = getattr(sys.stdout, "isatty", False)
        if not cls._enabled:
            return
        try:
            # Check for 256-color support via tput
            r = os.environ.get("TERM", "")
            if "256color" in r or "truecolor" in r:
                cls._256_supported = True
            elif "xterm-256color" in r:
                cls._256_supported = True
            else:
                # Try querying
                import subprocess
                try:
                    p = subprocess.run(
                        ["tput", "Tc"], capture_output=True,
                        timeout=2, env={**os.environ, "TERM": "xterm-256color"},
                    )
                    if b"\033[" in p.stdout and b"1" in p.stdout:
                        cls._256_supported = True
                except Exception:
                    pass
        except Exception:
            pass

    @classmethod
    def c(cls, color_name_or_hex: str, text: str) -> str:
        """Colorize text. Accepts named color or hex code."""
        if not cls._enabled:
            return text
        return f"{cls._resolve(color_name_or_hex)}{text}{cls.RESET}"

    @classmethod
    def _resolve(cls, name_or_hex: str) -> str:
        """Resolve a color name or hex to ANSI escape sequence."""
        # Named colors
        by_name = {
            "black": cls.BLACK, "red": cls.RED, "green": cls.GREEN,
            "yellow": cls.YELLOW, "blue": cls.BLUE, "magenta": cls.MAGENTA,
            "cyan": cls.CYAN, "white": cls.WHITE,
            "bright_black": cls.BRIGHT_BLACK, "bright_red": cls.BRIGHT_RED,
            "bright_green": cls.BRIGHT_GREEN, "bright_yellow": cls.BRIGHT_YELLOW,
            "bright_blue": cls.BRIGHT_BLUE, "bright_magenta": cls.BRIGHT_MAGENTA,
            "bright_cyan": cls.BRIGHT_CYAN, "bright_white": cls.BRIGHT_WHITE,
            "bg_black": cls.BG_BLACK, "bg_red": cls.BG_RED, "bg_green": cls.BG_GREEN,
            "bg_yellow": cls.BG_YELLOW, "bg_blue": cls.BG_BLUE,
            "bg_magenta": cls.BG_MAGENTA, "bg_cyan": cls.BG_CYAN,
            "bg_white": cls.BG_WHITE,
            "reset": cls.RESET, "bold": cls.BOLD, "dim": cls.DIM,
            "italic": cls.ITALIC, "underline": cls.UNDERLINE,
            "blink": cls.BLINK, "reverse": cls.REVERSED,
            "strikethrough": cls.STRIKETHROUGH,
        }
        lower = name_or_hex.lower().replace("-", "_")
        if lower in by_name:
            return by_name[lower]
        # Hex color
        if name_or_hex.startswith("#"):
            hex_val = name_or_hex.lstrip("#")
            try:
                # Parse hex to RGB
                r = int(hex_val[1:3], 16)
                g = int(hex_val[3:5], 16)
                b = int(hex_val[5:7], 16)
                if cls._256_supported:
                    return f"\033[38;5;{r};{g};{b}m"
                # Map to nearest 16-color
                return cls._nearest_16(r, g, b)
            except (ValueError, IndexError):
                pass
        return ""  # Unknown → no color

    @staticmethod
    def _nearest_16(r: int, g: int, b: int) -> str:
        """Map RGB to nearest 16-color ANSI code."""
        # Simple luminance-based mapping
        colors = [
            (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
            (0, 0, 128), (128, 0, 128), (128, 128, 128),
            (192, 192, 192),
        ]
        best = 0
        best_dist = 999999
        for i, (cr, cg, cb) in enumerate(colors):
            dist = abs(r - cr) * 0.299 + abs(g - cg) * 0.587 + abs(b - cb) * 0.114
            if dist < best_dist:
                best_dist = dist
                best = i
        codes = [30, 31, 32, 33, 34, 35, 36, 37, 90, 91, 92, 93, 94, 95, 96, 97]
        return f"\033[{codes[best]}m"

    # ── Convenience shortcuts ─────────────────────────────────────
    @classmethod
    def red(cls, t): return cls.c(cls.RED, t)

    @classmethod
    def green(cls, t): return cls.c(cls.GREEN, t)

    @classmethod
    def yellow(cls, t): return cls.c(cls.YELLOW, t)

    @classmethod
    def blue(cls, t): return cls.c(cls.BLUE, t)

    @classmethod
    def magenta(cls, t): return cls.c(cls.MAGENTA, t)

    @classmethod
    def cyan(cls, t): return cls.c(cls.CYAN, t)

    @classmethod
    def white(cls, t): return cls.c(cls.WHITE, t)

    @classmethod
    def bold(cls, t): return cls.c(cls.BOLD, t)

    @classmethod
    def dim(cls, t): return cls.c(cls.DIM, t)

    @classmethod
    def italic(cls, t): return cls.c(cls.ITALIC, t)

    @classmethod
    def success(cls, t): return cls.c(cls.SUCCESS, t)

    @classmethod
    def error(cls, t): return cls.c(cls.ERROR, t)

    @classmethod
    def warn(cls, t): return cls.c(cls.WARNING, t)

    @classmethod
    def info(cls, t): return cls.c(cls.INFO, t)

    @classmethod
    def muted(cls, t): return cls.c(cls.MUTED, t)

    @classmethod
    def title(cls, t): return cls.c(cls.TITLE, t)

    @classmethod
    def highlight(cls, t): return cls.c(cls.HIGHLIGHT, t)

    @classmethod
    def primary(cls, t): return cls.c(cls.PRIMARY, t)

    @classmethod
    def secondary(cls, t): return cls.c(cls.SECONDARY, t)


# ════════════════════════════════════════════════════════════════════
# Terminal — Size detection and capability probing
# ══════════════════════════════════════════════════════════════════

class Term:
    """Terminal size and capability detection."""

    _width: int = 80
    _height: int = 24
    _supports_ansi: bool = True
    _supports_unicode: bool = True

    @classmethod
    def width(cls) -> int:
        if cls._width == 80:
            try:
                size = os.get_terminal_size(sys.stdout.fileno())
                cls._width = size.columns
            except Exception:
                try:
                    cls._width = shutil.get_terminal_size((80, 24)).columns
                except Exception:
                    pass
        return max(cls._width, 40)

    @classmethod
    def height(cls) -> int:
        if cls._height == 24:
            try:
                size = os.get_terminal_size(sys.stdout.fileno())
                cls._height = size.lines
            except Exception:
                try:
                    cls._height = shutil.get_terminal_size((80, 24)).lines
                except Exception:
                    pass
        return max(cls._height, 12)

    @classmethod
    def supports_ansi(cls) -> bool:
        if cls._supports_ansi is None:
            cls._supports_ansi = C._enabled and (
                "COLORTERM" in os.environ.get("TERM", "") or
                "TrueColor" in os.environ.get("COLORTERM_TRUECOLOR", "") or
                sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False
            )
        return cls._supports_ansi

    @classmethod
    def supports_unicode(cls) -> bool:
        if cls._supports_unicode is None:
            enc = sys.getdefaultencoding()
            cls._supports_unicode = enc in ("utf-8", "utf-8-mac", "utf-8-linux",
            )
        return cls._supports_unicode

    @classmethod
    def clear(cls) -> None:
        """Clear screen (works on most terminals)."""
        if cls.supports_ansi():
            sys.stdout.write("\033[J\033[H")  # Clear + cursor home
        else:
            # Fallback: print enough newlines to scroll old content away
            lines = cls.height()
            sys.stdout.write("\n" * (lines + 2))
        sys.stdout.flush()

    @classmethod
    def size(cls) -> Tuple[int, int]:
        """Return (width, height)."""
        return (cls.width(), cls.height())


# ══════════════════════════════════════════════════════════════════
# Box — Unicode (or ASCII fallback) bordered panels
# ════════════════════════════════════════════════════════════════

class Box:
    """Draw bordered boxes and panels using Unicode box-drawing characters.

    Falls back to ASCII (+-|) on terminals without Unicode support.
    """

    # Unicode box characters (single-line, rounded corners)
    _UNI_TL = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
                   "l": "│", "r": "│", "hl": "├", "hr": "┤",
                   "vl": "├", "vr": "┤", "hb": "╰", "hd": "┬"}
    _UNI_BL = {"tl": "╭", "tr": "╮", "bl": "╯", "br": "╰",
                   "l": "║", "r": "║", "hl": "╟", "hr": "╪",
                   "vl": "╟", "vr": "╫", "hb": "╧", "hd": "╨"}

    # ASCII fallback
    _ASCII_TL = {"tl": "+", "tr": "+", "bl": "+", "br": "+",
                      "l": "|", "r": "|", "hl": "+", "hr": "-",
                      "vl": "+", "vr": "+", "hb": "+", "hd": "+"}
    _ASCII_BL = {"tl": "+", "tr": "+", "bl": "+", "br": "+",
                      "l": "|", "r": "|", "hl": "+", "hr": "-",
                      "vl": "+", "vr": "+", "hb": "+", "hd": "+"}

    @classmethod
    def _chars(cls) -> Dict[str, str]:
        if Term.supports_unicode() and Term.supports_ansi():
            return cls._UNI_TL if Term.height() >= 10 else cls._UNI_BL
        return cls._ASCII_TL

    @staticmethod
    def panel(title: str, content_lines: List[str],
              width: int = None, color: str = "accent") -> str:
        """Render a titled box with content inside."""
        w = width or (Term.width() - 4)
        ch = Box._chars()
        inner_width = w - 4  # -2 for borders, -2 for padding
        line_sep = ch["hr"] * (inner_width + 2)

        lines: list[str] = []
        lines.append(f"{ch['tl']}{line_sep}{ch['tr']}")
        if title:
            lines.append(f"{ch['l']} {C.bold(title):^{inner_width + 2}s} {ch['r']}")
            lines.append(f"{ch['hl']}{line_sep}{ch['hr']}")
        for line in content_lines:
            lines.append(f"{ch['l']} {line:<{inner_width}s}{ch['r']}")
        lines.append(f"{ch['bl']}{line_sep}{ch['br']}")
        return "\n".join(lines)

    @staticmethod
    def row(cells: List[Tuple[str, str, int]],
             widths: List[int] = None, separator: str = "│") -> str:
        """Render a formatted table row.

        Each cell: (text, color_name, min_width).
        Returns the complete row string.
        """
        if widths is None:
            widths = [c[2] for c in cells]
        ch = Box._chars()
        parts = []
        for i, (text, color, min_w) in enumerate(cells):
            colored = C.c(color, text) if color else text
            padded = colored[:min_w].ljust(min_w)[:widths[i] if i < len(widths) else colored]
            parts.append(padded)
        sep = separator.join(parts)
        return f"{ch['l']}{sep}{ch['r']}"

    @staticmethod
    def separator(width: int = None, char: str = "─",
                     label: str = "", top: bool = False) -> str:
        """Draw a horizontal separator line."""
        w = width or (Term.width() - 4)
        ch = Box._chars()
        if top:
            return f"{ch['hl']}{char * w}{ch['vr']}"
        prefix = f"{C.dim(label)} " if label else ""
        suffix = ""
        return f"{prefix}{ch['tl']}{char * w}{suffix}{ch['tr']}\n"


# ════════════════════════════════════════════════════════════════
# Keyboard — Cross-platform single-keystroke reading
# ════════════════════════════════════════════════════════════════

_SPECIAL_KEYS = {
    "up": "\033[A", "down": "\033[B", "right": "\033[C",
    "left": "\033[D", "enter": "\r", "escape": "\033",
    "tab": "\t", "backspace": "\x7f",
    "page_up": "\033[5~", "page_down": "\033[6~",
    "home": "\033[1~", "end": "\033[4~",
    "delete": "\033[3~", "insert": "\033[2~",
}

_KEY_NAMES = {v: k for k, v in _SPECIAL_KEYS.items()}


class Keyboard:
    """Cross-platform keyboard input reader."""

    _has_full: bool = True
    _fd: int = -1
    _old_settings: bytes = b""

    @classmethod
    def has_full_keyboard(cls) -> bool:
        if cls._has_full is None:
            # Check if we're in a real terminal (not piped/redirected)
            cls._has_full = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False
        return cls._has_full

    @classmethod
    def getkey(cls) -> str:
        """Read one keystroke. Returns character or special key name."""
        if not cls.has_full_keyboard():
            # Fallback: read a line and return first char
            try:
                line = input("")
                return line[0] if line else ""
            except EOFError:
                return ""

        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)

        try:
            # Set raw mode to read single keystrokes
            new_attrs = termios.tcgetattr(fd)
            new_attrs[3] = new_attrs[3] & ~termios.ICANON  # Disable canonical mode
            new_attrs[3] = new_attrs[3] & ~termios.ECHO     # Disable echo
            termios.tcsetattr(fd, termios.TCSANOW, new_attrs)

            ch = sys.stdin.read(1)
            if not ch:
                return ""

            if ch == "\x1b":
                # Escape sequence — read full sequence
                seq = ""
                for _ in range(6):
                    ch2 = sys.stdin.read(1)
                    if not ch2:
                        break
                    seq += ch2
                    if ch2.isalpha():
                        break
                    if len(seq) > 5:
                        break

                # Known escape sequences
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
                    if "[4" in seq:
                        return "end"
                    if "[3" in seq:
                        return "delete"
                return "escape"

            if ch == "\r":
                return "enter"
            if ch == "\t":
                return "tab"
            if ch == "\x7f":
                return "backspace"
            if ch == "\x03":
                raise KeyboardInterrupt

            return ch

        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)

    @classmethod
    def getkey_blocking(cls, timeout: float = 30.0) -> str:
        """Like getkey() but blocks up to `timeout` seconds."""
        import select
        if select.select([sys.stdin], [], [], timeout)[0]:
            return cls.getkey()
        return ""


# ════════════════════════════════════════════════════════════════
# Menu — Interactive selection list with keyboard navigation
# ══════════════════════════════════════════════════════════════

class Menu:
    """Interactive menu with keyboard navigation and optional pagination."""

    def __init__(self, items: List[str], title: str = "",
                 page_size: int = 12, show_indices: bool = True,
                 header: str = "", footer: str = ""):
        self.items = items
        self.title = title
        self.page_size = page_size
        self.show_indices = show_indices
        self.header = header
        self.footer = footer
        self.cursor = 0
        self.page = 0
        self.filter_text = ""

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.items) + self.page_size - 1) // self.page_size)

    @property
    def visible_items(self) -> List[str]:
        start = self.page * self.page_size
        return self.items[start:start + self.page_size]

    def render(self, cursor: int = -1) -> str:
        """Render the full menu. Returns the text to display."""
        if cursor < 0:
            cursor = self.cursor
        w = Term.width()
        lines: List[str] = []

        # Header
        if self.title:
            lines.append(C.bold(f"  {self.title}"))
            lines.append("")
        if self.header:
            lines.append(f"  {self.header}")

        # Filtered items
        items = self.visible_items
        if self.filter_text:
            items = [i for i in items if self.filter_text.lower() in i.lower()]

        if not items:
            lines.append(f"  {C.dim('No matching items.')}")
        else:
            for idx, item in enumerate(items):
                global_idx = self.page * self.page_size + idx + 1
                marker = "▸ " if idx == cursor else " "
                num = f"{global_idx}" if self.show_indices else " "
                status_icon = "●" if item else "?"
                lines.append(f"  {marker}{num} {C.c(status_icon, item)}")

        # Footer / page indicator
        if self.footer:
            lines.append("")
            lines.append(f"  {self.footer}")
        if self.total_pages > 1:
            page_text = C.dim("Page " + str(self.page + 1) + "/" + str(self.total_pages))
            lines.append(f"  {page_text}")
        if self.filter_text:
            filter_text = C.dim('Filter: "' + self.filter_text + '"')
            lines.append(f"  {filter_text}")

        return "\n".join(lines)

    def handle_key(self, key: str, cursor: int = 0) -> Tuple[int, str]:
        """Process a keystroke. Returns (new_cursor, action).

        Action is one of: 'select', 'page_up', 'page_down',
        'filter', 'back', 'quit', or None (no-op).
        """
        n_items = len(self.visible_items)

        if key == "enter" or key == " ":
            if n_items > 0:
                return (cursor, "select")
            return (cursor, "back")

        if key in ("escape", "q", "Q"):
            return (cursor, "back")

        if key == "up" or key == "k" or key == "K":
            return ((cursor - 1) % max(n_items, 1), None)

        if key == "down" or key == "j" or key == "J" or key == "\t":
            return ((cursor + 1) % max(n_items, 1), None)

        if key == "page_up" or key == "page_up" or key == "prior":
            self.page = max(0, self.page - 1)
            return (min(cursor, self.total_pages - 1), "page_up")

        if key == "page_down" or key == "page_down" or "next" or "n":
            self.page = min(self.total_pages - 1, self.page + 1)
            return (min(cursor, self.total_pages - 1), "page_down")

        if key == "home" or key == "g" or key == "^":
            self.page = 0
            return (0, "page_up")

        if key == "end" or key == "G" or key == "$":
            self.page = self.total_pages - 1
            last_page_start = (self.total_pages - 1) * self.page_size
            return (min(n_items - 1, last_page_start), "page_down")

        if key == "/" and self.filter_text:
            self.filter_text = ""
            return (cursor, "filter")

        if key in ("?", "h", "H", "help", "F1"):
            return (cursor, "help")  # TODO: show help overlay

        # Numeric jump
        try:
            num = int(key)
            if 1 <= num <= n_items:
                return (num - 1, "select")
        except ValueError:
            pass

        return (cursor, None)


# ════════════════════════════════════════════════════════════════
# Progress Bar — Visual feedback for async operations
# ══════════════════════════════════════════════════════════════

class ProgressBar:
    """Simple progress bar for proxy startup, scans, etc."""

    def __init__(self, total: int = 100, width: int = 40,
                 label: str = "", char: str = "█"):
        self.total = total
        self.current = 0
        self.width = width
        self.label = label
        self.char = char
        self.start_time = _time.time()

    def update(self, current: int, label: str = "") -> str:
        """Update progress. Returns the bar string (or empty if unchanged)."""
        self.current = current
        self.label = label or self.label
        pct = min(100, (current * 100 // max(self.total, 1)))
        filled = int(pct * self.width / 100)
        empty = self.width - filled
        bar = self.char * filled + C.DIM(self.char * empty) + C.muted(f" {pct}%")
        lbl = f" {self.label}" if self.label else ""
        elapsed = _time.time() - self.start_time
        return f"{bar}{lbl}{C.dim(f' ({elapsed:.1f}s)')}\n"

    def done(self, message: str = "Done.") -> str:
        """Show completed bar."""
        self.current = self.total
        out = self.update(self.total, message)
        return f"{out}{C.success(message)}\n"
