#!/usr/bin/env python3
"""X Edition Title Bar — Custom window chrome with integrated status.

WHY: On Linux, the default window title bar (especially under X11) looks
dated and takes up space. Modern apps like Warp, VS Code, and Discord
use custom title bars that:
  - Match the app's dark theme
  - Integrate status information (connection state, version)
  - Provide window controls (minimize, maximize, close) that match the theme
  - Look consistent across different Linux desktop environments

This module creates a custom title bar ONLY on Linux. On Windows, the
native title bar is kept because it integrates properly with the OS.

Think of it like replacing a generic hotel room door with a custom-built
door that matches your house's design — it looks better and fits the theme.
"""
import tkinter as tk
from gui_x.theme import CATPPUCCIN
from gui_x.fonts import FONT_UI, SIZE_BODY, SIZE_SMALL
import sys


class TitleBar(tk.Frame):
    """Custom dark-themed title bar for the X Edition GUI.

    Features:
      - App name + version on the left
      - Proxy status indicator in the center
      - Window control buttons on the right (min/max/close)
      - Draggable — click and drag to move the window

    Only used on Linux. On Windows, the native title bar is kept.

    Args:
        root:     The root tk.Tk() window
        version:  Version string to display
    """

    C = CATPPUCCIN

    def __init__(self, root, version="", **kwargs):
        bg = kwargs.pop("bg", self.C["mantle"])
        super().__init__(root, bg=bg, height=36, **kwargs)
        self.pack_propagate(False)
        self._root = root
        self._start_x = 0
        self._start_y = 0

        # ── Left: App identity ──────────────────────────────────────
        left = tk.Frame(self, bg=bg)
        left.pack(side="left", padx=(12, 0), fill="y")

        tk.Label(left, text="⬡", fg=self.C["accent"], bg=bg,
                 font=(FONT_UI, 12)).pack(side="left", pady=6)
        tk.Label(left, text="Codex Launcher", fg=self.C["text"], bg=bg,
                 font=(FONT_UI, SIZE_BODY, "bold")).pack(side="left", padx=(4, 0))
        if version:
            tk.Label(left, text=f"X Edition v{version}", fg=self.C["dim"], bg=bg,
                     font=(FONT_UI, SIZE_SMALL)).pack(side="left", padx=(8, 0))

        # ── Center: Proxy status ────────────────────────────────────
        center = tk.Frame(self, bg=bg)
        center.pack(side="left", expand=True, fill="x")

        self._status_dot = tk.Label(center, text="●", fg=self.C["red"], bg=bg,
                                     font=(FONT_UI, 8))
        self._status_dot.pack(side="left", pady=6)
        self._status_text = tk.Label(center, text="Proxy Off", fg=self.C["dim"], bg=bg,
                                      font=(FONT_UI, SIZE_SMALL))
        self._status_text.pack(side="left", padx=(4, 0), pady=6)

        # ── Right: Window controls ──────────────────────────────────
        right = tk.Frame(self, bg=bg)
        right.pack(side="right", padx=(0, 4), fill="y")

        # Minimize
        self._make_control_btn(right, "─", self._minimize)
        # Maximize
        self._make_control_btn(right, "□", self._maximize)
        # Close
        close_btn = tk.Button(right, text="✕", bg=bg, fg=self.C["dim"],
                              activebackground=self.C["red"], activeforeground=self.C["text"],
                              relief="flat", bd=0, font=(FONT_UI, SIZE_SMALL),
                              command=self._close, width=3, cursor="hand2")
        close_btn.pack(side="right", pady=4, padx=1)
        close_btn.bind("<Enter>", lambda e: close_btn.configure(bg=self.C["red"], fg=self.C["text"]))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(bg=bg, fg=self.C["dim"]))

        # ── Drag bindings ───────────────────────────────────────────
        self.bind("<ButtonPress-1>", self._start_drag)
        self.bind("<B1-Motion>", self._on_drag)
        # Also bind to child labels so dragging works everywhere
        for child in self.winfo_children():
            for widget in child.winfo_children():
                widget.bind("<ButtonPress-1>", self._start_drag)
                widget.bind("<B1-Motion>", self._on_drag)

    def _make_control_btn(self, parent, text, command):
        """Create a window control button (min/max) with hover effect."""
        bg = self.C["mantle"]
        btn = tk.Button(parent, text=text, bg=bg, fg=self.C["dim"],
                        activebackground=self.C["surface1"], activeforeground=self.C["text"],
                        relief="flat", bd=0, font=(FONT_UI, SIZE_SMALL),
                        command=command, width=3, cursor="hand2")
        btn.pack(side="right", pady=4, padx=1)
        btn.bind("<Enter>", lambda e: btn.configure(bg=self.C["surface1"], fg=self.C["text"]))
        btn.bind("<Leave>", lambda e: btn.configure(bg=bg, fg=self.C["dim"]))

    def _start_drag(self, event):
        """Record the starting position for window dragging."""
        self._start_x = event.x
        self._start_y = event.y

    def _on_drag(self, event):
        """Move the window while dragging."""
        x = self._root.winfo_x() + (event.x - self._start_x)
        y = self._root.winfo_y() + (event.y - self._start_y)
        self._root.geometry(f"+{x}+{y}")

    def _minimize(self):
        self._root.iconify()

    def _maximize(self):
        if self._root.attributes("-zoomed"):
            self._root.attributes("-zoomed", False)
        else:
            self._root.attributes("-zoomed", True)

    def _close(self):
        self._root.destroy()

    def set_proxy_status(self, running, detail=""):
        """Update the proxy status indicator.

        Args:
            running: True if proxy is running, False otherwise
            detail:  Optional text (e.g., "port 61255")
        """
        if running:
            self._status_dot.configure(fg=self.C["green"])
            self._status_text.configure(text=f"Proxy On {detail}", fg=self.C["green"])
        else:
            self._status_dot.configure(fg=self.C["red"])
            self._status_text.configure(text="Proxy Off", fg=self.C["dim"])

    def set_proxy_starting(self):
        """Show the 'starting' state (yellow indicator)."""
        self._status_dot.configure(fg=self.C["yellow"])
        self._status_text.configure(text="Proxy Starting...", fg=self.C["yellow"])
