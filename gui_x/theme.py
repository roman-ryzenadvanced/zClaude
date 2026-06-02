#!/usr/bin/env python3
"""X Edition Theme — Catppuccin Mocha dark palette + ttk style application.

This module provides:
  - CATPPUCCIN color palette (same as _usage_theme but centralized)
  - apply_theme(root)  — configures all ttk styles for dark mode
  - ThemedToplevel     — base class for sub-windows that auto-themes
  - themed_canvas_rect  — helper for rounded-rect cards on Canvas

WHY: The original GUI had NO dark theme on the main window. Only the Usage
Dashboard had one. This module fixes that by providing a single, shared theme
that every widget and sub-window can use.

Think of it like this: if the app were a house, the old GUI had one room
painted nicely (Usage Dashboard) and the rest were bare drywall. This module
is the paint that makes every room match.
"""
import tkinter as tk
from tkinter import ttk
import sys

# ═══════════════════════════════════════════════════════════════════════
# Catppuccin Mocha palette
# ═══════════════════════════════════════════════════════════════════════
# These are the same colors as _usage_theme() in lib/utils.py,
# but centralized here so the X Edition GUI doesn't depend on
# importing private functions from the library.

CATPPUCCIN = {
    # Base layers (darkest → lightest)
    "base":       "#0C0E16",   # Main background — the "night sky"
    "mantle":     "#0A0C14",   # Slightly darker, for title bars
    "crust":      "#080A10",   # Darkest, for absolute backgrounds
    "surface0":   "#161928",   # Card / panel background
    "surface1":   "#1E2235",   # Elevated surfaces, hover states
    "surface2":   "#2A2F47",   # Borders, dividers, subtle lines

    # Text layers
    "text":       "#E4E6F0",   # Primary text — bright and readable
    "subtext":    "#B0B4C8",   # Secondary text — descriptions
    "dim":        "#5C6180",   # Tertiary text — timestamps, hints

    # Accent colors
    "accent":     "#7EB8F7",   # Primary accent — buttons, highlights
    "blue":       "#5DA4E8",   # Interactive elements
    "sapphire":   "#4EC5C1",   # Success indicators, links
    "green":      "#59D4A0",   # Online, healthy, confirmed
    "yellow":     "#F0C75E",   # Warnings, pending states
    "red":        "#F06A77",   # Errors, offline, danger
    "peach":      "#F09860",   # Secondary highlight
    "teal":       "#4EC5C1",   # Info indicators
    "lavender":   "#A899F0",   # Specialty accents
    "sky":        "#70C8E8",   # Light info, secondary success
    "maroon":     "#C44B5C",   # Deep error, critical
    "flamingo":   "#E878B0",   # Tertiary accent
    "rosewater":  "#F0D0C0",   # Warm highlight

    # Convenience aliases (matching _usage_theme keys)
    "overlay0":   "#3A3F5C",   # Scrollbar tracks, disabled fills

    # Model chart palette (for Usage Dashboard bars)
    "model_palette": [
        "#F09860", "#4EC5C1", "#5DA4E8", "#59D4A0",
        "#F0C75E", "#A899F0", "#70C8E8", "#E878B0",
        "#C44B5C", "#F0D0C0", "#7EB8F7", "#F06A77",
    ],
}


# ═══════════════════════════════════════════════════════════════════════
# Theme application
# ═══════════════════════════════════════════════════════════════════════

def apply_theme(root):
    """Apply the Catppuccin dark theme to a root tk window and all ttk widgets.

    This MUST be called BEFORE creating any widgets, because ttk styles
    are applied at widget creation time (not retroactively).

    Usage in gui_x/__init__.py:
        root = tk.Tk()
        apply_theme(root)   # <-- before LauncherWinX(root)
        app = LauncherWinX(root)

    What it does:
      1. Sets the root window background to dark
      2. Configures every ttk style (TButton, TLabel, TFrame, etc.)
         with the right colors for text, background, hover, and active states
      3. Sets Treeview to dark mode
      4. Makes comboboxes and entries dark-themed
    """
    C = CATPPUCCIN
    root.configure(bg=C["base"])

    style = ttk.Style(root)

    # ── Try to use a cleaner ttk theme as the base ─────────────────
    # 'clam' is the most customizable ttk theme — it lets us set
    # background/foreground on everything. Other themes like 'default'
    # or 'alt' ignore many style settings.
    available = style.theme_names()
    for preferred in ("clam", "alt", "default"):
        if preferred in available:
            style.theme_use(preferred)
            break

    # ── Global defaults ─────────────────────────────────────────────
    style.configure(".", background=C["base"], foreground=C["text"],
                    fieldbackground=C["surface0"], borderwidth=0,
                    focuscolor=C["accent"], troughcolor=C["surface0"],
                    selectbackground=C["accent"], selectforeground=C["base"],
                    insertcolor=C["accent"])

    # ── TFrame ──────────────────────────────────────────────────────
    style.configure("TFrame", background=C["base"])
    style.configure("Surface0.TFrame", background=C["surface0"])
    style.configure("Surface1.TFrame", background=C["surface1"])

    # ── TLabel ──────────────────────────────────────────────────────
    style.configure("TLabel", background=C["base"], foreground=C["text"])
    style.configure("Dim.TLabel", foreground=C["dim"])
    style.configure("Accent.TLabel", foreground=C["accent"])
    style.configure("Success.TLabel", foreground=C["green"])
    style.configure("Warning.TLabel", foreground=C["yellow"])
    style.configure("Error.TLabel", foreground=C["red"])
    style.configure("Surface0.TLabel", background=C["surface0"])

    # ── TButton ─────────────────────────────────────────────────────
    style.configure("TButton", background=C["surface1"], foreground=C["text"],
                    padding=(12, 6), borderwidth=0, relief="flat")
    style.map("TButton",
              background=[("active", C["surface2"]), ("pressed", C["accent"]),
                          ("disabled", C["surface0"])],
              foreground=[("disabled", C["dim"])])

    # Primary action button (Launch, Start)
    style.configure("Accent.TButton", background=C["accent"],
                    foreground=C["base"], padding=(16, 8),
                    font=("sans-serif", 10, "bold"))
    style.map("Accent.TButton",
              background=[("active", C["blue"]), ("pressed", C["sapphire"]),
                          ("disabled", C["surface0"])],
              foreground=[("disabled", C["dim"])])

    # Danger button (Kill, Stop)
    style.configure("Danger.TButton", background=C["red"],
                    foreground=C["base"], padding=(12, 6))
    style.map("Danger.TButton",
              background=[("active", C["maroon"]), ("pressed", C["surface0"])],
              foreground=[("disabled", C["dim"])])

    # Subtle/ghost button
    style.configure("Ghost.TButton", background=C["base"],
                    foreground=C["subtext"], padding=(8, 4))
    style.map("Ghost.TButton",
              background=[("active", C["surface0"]), ("pressed", C["surface1"])])

    # ── TCheckbutton ────────────────────────────────────────────────
    style.configure("TCheckbutton", background=C["base"], foreground=C["text"])
    style.map("TCheckbutton",
              background=[("active", C["surface0"])],
              foreground=[("active", C["accent"])])

    # ── TCombobox ───────────────────────────────────────────────────
    style.configure("TCombobox", fieldbackground=C["surface0"],
                    background=C["surface1"], foreground=C["text"],
                    selectbackground=C["accent"], selectforeground=C["base"],
                    arrowcolor=C["accent"])
    style.map("TCombobox",
              fieldbackground=[("readonly", C["surface0"])],
              selectbackground=[("readonly", C["accent"])],
              foreground=[("readonly", C["text"])])

    # ── TEntry ──────────────────────────────────────────────────────
    style.configure("TEntry", fieldbackground=C["surface0"],
                    foreground=C["text"], insertcolor=C["accent"])

    # ── Treeview ────────────────────────────────────────────────────
    style.configure("Treeview", background=C["surface0"],
                    foreground=C["text"], fieldbackground=C["surface0"],
                    borderwidth=0, rowheight=28)
    style.configure("Treeview.Heading", background=C["surface1"],
                    foreground=C["accent"], borderwidth=0,
                    font=("sans-serif", 9, "bold"))
    style.map("Treeview",
              background=[("selected", C["accent"])],
              foreground=[("selected", C["base"])])

    # ── TScrollbar ──────────────────────────────────────────────────
    style.configure("TScrollbar", background=C["surface0"],
                    troughcolor=C["base"], borderwidth=0,
                    arrowsize=14)
    style.map("TScrollbar",
              background=[("active", C["surface2"])])

    # ── TSeparator ──────────────────────────────────────────────────
    style.configure("TSeparator", background=C["surface2"])

    # ── TProgressbar ────────────────────────────────────────────────
    style.configure("TProgressbar", background=C["accent"],
                    troughcolor=C["surface0"])

    # ── TMenubutton ─────────────────────────────────────────────────
    style.configure("TMenubutton", background=C["surface1"],
                    foreground=C["text"])
    style.map("TMenubutton",
              background=[("active", C["surface2"])])

    # ── Tooltip style ───────────────────────────────────────────────
    style.configure("Tooltip.TLabel", background=C["surface1"],
                    foreground=C["text"], padding=(6, 3))


# ═══════════════════════════════════════════════════════════════════════
# ThemedToplevel — base class for all X Edition sub-windows
# ═══════════════════════════════════════════════════════════════════════

class ThemedToplevel(tk.Toplevel):
    """A Toplevel window that automatically gets the Catppuccin dark theme.

    Instead of creating raw `tk.Toplevel(parent)` everywhere, sub-windows
    in the X Edition use `ThemedToplevel(parent)`. This ensures every
    popup, dialog, and sub-window matches the dark theme automatically.

    Usage:
        class MyWindow(ThemedToplevel):
            def __init__(self, parent):
                super().__init__(parent)
                self.title("My Window")
                self.geometry("600x400")
                # Now self is a dark-themed window!
    """
    C = CATPPUCCIN  # Shorthand access in subclasses

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(bg=self.C["base"])
        # Apply the theme's ttk styles to this toplevel too
        # (ttk styles are shared globally, but tk widget bg must be set per-window)
        self._apply_sub_styles()

    def _apply_sub_styles(self):
        """Override in subclasses to add specific style tweaks."""
        pass

    def themed_frame(self, parent, style_key="base", **kwargs):
        """Create a Frame with the correct dark background.

        style_key: "base", "surface0", "surface1"
        """
        bg = self.C.get(style_key, self.C["base"])
        return tk.Frame(parent, bg=bg, **kwargs)

    def themed_label(self, parent, text="", style_key="text", size=10,
                     bold=False, **kwargs):
        """Create a Label with themed colors and cross-platform font.

        style_key: "text", "dim", "accent", "success", "warning", "error"
        """
        from gui_x.fonts import FONT_UI
        fg = self.C.get(style_key, self.C["text"])
        bg = kwargs.pop("bg", parent.cget("bg") if hasattr(parent, "cget") else self.C["base"])
        weight = "bold" if bold else "normal"
        return tk.Label(parent, text=text, fg=fg, bg=bg,
                        font=(FONT_UI, size, weight), **kwargs)

    def themed_button(self, parent, text="", command=None, style="normal", **kwargs):
        """Create a Button with themed colors.

        style: "normal", "accent", "danger", "ghost"
        """
        from gui_x.fonts import FONT_UI
        configs = {
            "normal": (self.C["surface1"], self.C["text"], self.C["surface2"]),
            "accent": (self.C["accent"], self.C["base"], self.C["blue"]),
            "danger": (self.C["red"], self.C["base"], self.C["maroon"]),
            "ghost":  (self.C["base"], self.C["subtext"], self.C["surface0"]),
        }
        bg, fg, active_bg = configs.get(style, configs["normal"])
        return tk.Button(parent, text=text, command=command,
                         bg=bg, fg=fg, activebackground=active_bg,
                         activeforeground=self.C["text"],
                         relief="flat", bd=0, padx=12, pady=5,
                         font=(FONT_UI, 9), **kwargs)
