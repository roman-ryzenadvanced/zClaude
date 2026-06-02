#!/usr/bin/env python3
"""X Edition Card Components — Dark-themed card containers for layout grouping.

WHY: The original GUI was a flat vertical stack of buttons and labels with
no visual grouping. Everything looked the same — a launch button next to
a checkbox next to a status label. No hierarchy, no visual structure.

Warp uses card-based panels: related controls are grouped into distinct
visual sections with subtle borders and backgrounds. It's like the difference
between a messy desk and an organized one with labeled trays.

Cards provide:
  - Visual grouping (related controls live inside a card)
  - Hierarchy (cards have titles, content sections, and footers)
  - Depth (slightly lighter background than the base creates "layers")
  - Status (colored left borders indicate state — green = active, etc.)

Example layout:
  ┌─ Status ──────────────────────┐
  │ ✓ Codex CLI v2.1             │
  │ ✓ Codex Desktop              │
  │ ✓ Auth: logged in            │
  └──────────────────────────────┘
  ┌─ Provider ────────────────────┐
  │ Endpoint: [OpenAI    ▾]      │
  │ Model:    [gpt-4o    ▾]      │
  │ Latency:  0.45s              │
  └──────────────────────────────┘
"""
import tkinter as tk
from tkinter import ttk
from gui_x.theme import CATPPUCCIN
from gui_x.fonts import FONT_UI, SIZE_HEADING, SIZE_SMALL, SIZE_TINY


class Card(tk.Frame):
    """A dark-themed card container with optional title and status border.

    Args:
        parent:   Parent widget
        title:    Optional card heading text (shown top-left)
        accent:   Optional left-border color (e.g., "green" for active card)
        padx:     Horizontal padding inside the card (default 14)
        pady:     Vertical padding inside the card (default 10)
    """

    C = CATPPUCCIN

    def __init__(self, parent, title=None, accent=None, padx=14, pady=10, **kwargs):
        bg = kwargs.pop("bg", self.C["surface0"])
        super().__init__(parent, bg=bg, padx=padx, pady=pady, **kwargs)

        # Accent left border (like Warp's colored sidebar indicators)
        if accent:
            accent_color = self.C.get(accent, accent)
            border = tk.Frame(self, bg=accent_color, width=3)
            border.pack(side="left", fill="y", padx=(0, 10))
            # Prevent the border from shrinking
            border.pack_propagate(False)

        # Title row
        self._title_frame = None
        if title:
            self._title_frame = tk.Frame(self, bg=bg)
            self._title_frame.pack(fill="x", pady=(0, 8))
            tk.Label(self._title_frame, text=title, fg=self.C["accent"], bg=bg,
                     font=(FONT_UI, SIZE_HEADING, "bold")).pack(side="left")
            # Optional right-side content area in title
            self._title_right = tk.Frame(self._title_frame, bg=bg)
            self._title_right.pack(side="right")

        # Content area (where caller adds widgets)
        self.content = tk.Frame(self, bg=bg)
        self.content.pack(fill="both", expand=True)

    def add_title_widget(self, widget_factory):
        """Add a widget to the right side of the title row.

        widget_factory: a callable that takes (self._title_right) and returns a widget
        """
        if self._title_right:
            return widget_factory(self._title_right)


class StatusCard(Card):
    """A Card variant specifically for status display (CLI detection, auth, etc.).

    Adds a built-in status indicator (colored dot + text) at the top.
    """

    def __init__(self, parent, title=None, **kwargs):
        super().__init__(parent, title=title, **kwargs)
        self._status_row = tk.Frame(self.content, bg=self.C["surface0"])
        self._status_row.pack(fill="x")

    def add_status_item(self, icon, text, color="dim"):
        """Add a status line with icon + text.

        icon:  Unicode symbol (✓, ✗, ⚠)
        text:  Description text
        color: "green", "yellow", "red", "dim"
        """
        fg = self.C.get(color, self.C["dim"])
        bg = self.C["surface0"]
        row = tk.Frame(self._status_row, bg=bg)
        row.pack(fill="x", pady=1)
        tk.Label(row, text=icon, fg=fg, bg=bg,
                 font=(FONT_UI, SIZE_SMALL, "bold"), width=2, anchor="w").pack(side="left")
        tk.Label(row, text=text, fg=fg, bg=bg,
                 font=(FONT_UI, SIZE_SMALL), anchor="w").pack(side="left", fill="x")
        return row


class ProviderCard(Card):
    """A Card variant for the endpoint/model selector area.

    Features:
      - Accent border on the left that changes color with latency
      - Endpoint + Model selectors in a clean row layout
      - Latency badge
    """

    def __init__(self, parent, **kwargs):
        accent = kwargs.pop("accent", "accent")
        super().__init__(parent, title="Provider", accent=accent, **kwargs)
        bg = self.C["surface0"]

        # Row 1: Endpoint + Latency
        self.ep_row = tk.Frame(self.content, bg=bg)
        self.ep_row.pack(fill="x", pady=(0, 6))

        # Header row (Endpoint label on left, Latency on right)
        self.ep_header = tk.Frame(self.ep_row, bg=bg)
        self.ep_header.pack(fill="x")

        tk.Label(self.ep_header, text="Endpoint", fg=self.C["dim"], bg=bg,
                 font=(FONT_UI, SIZE_TINY), anchor="w").pack(side="left")
        self.latency_label = tk.Label(self.ep_header, text=" -- ", fg=self.C["dim"], bg=bg,
                                       font=(FONT_UI, SIZE_SMALL, "bold"))
        self.latency_label.pack(side="right")

        self.ep_combo_frame = tk.Frame(self.ep_row, bg=bg)
        self.ep_combo_frame.pack(fill="x", pady=(2, 0))

        # Row 2: Model
        self.model_row = tk.Frame(self.content, bg=bg)
        self.model_row.pack(fill="x", pady=(0, 6))

        tk.Label(self.model_row, text="Model", fg=self.C["dim"], bg=bg,
                 font=(FONT_UI, SIZE_TINY), anchor="w").pack(anchor="w")
        self.model_combo_frame = tk.Frame(self.model_row, bg=bg)
        self.model_combo_frame.pack(fill="x")

        # Row 3: Sandbox + Approval
        self.mode_row = tk.Frame(self.content, bg=bg)
        self.mode_row.pack(fill="x")

        # Sandbox
        self.sandbox_frame = tk.Frame(self.mode_row, bg=bg)
        self.sandbox_frame.pack(side="left", fill="x", expand=True)
        tk.Label(self.sandbox_frame, text="Sandbox", fg=self.C["dim"], bg=bg,
                 font=(FONT_UI, SIZE_TINY)).pack(anchor="w")

        # Approval
        self.approval_frame = tk.Frame(self.mode_row, bg=bg)
        self.approval_frame.pack(side="left", fill="x", expand=True, padx=(16, 0))
        tk.Label(self.approval_frame, text="Approval", fg=self.C["dim"], bg=bg,
                 font=(FONT_UI, SIZE_TINY)).pack(anchor="w")


class LaunchCard(Card):
    """A Card variant for the launch action buttons.

    Features:
      - Large, prominent launch buttons
      - Accent-colored primary actions
      - Secondary default-mode buttons below
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, title=None, **kwargs)
        bg = self.C["surface0"]

        # Primary launch buttons
        self.primary_row = tk.Frame(self.content, bg=bg)
        self.primary_row.pack(fill="x", pady=(0, 6))

        # Secondary launch buttons
        self.secondary_row = tk.Frame(self.content, bg=bg)
        self.secondary_row.pack(fill="x")
