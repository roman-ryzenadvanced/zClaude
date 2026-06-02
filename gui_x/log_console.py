#!/usr/bin/env python3
"""X Edition Log Console — Syntax-colored terminal-style log viewer.

WHY: The original log area was a plain grey text box. All messages looked the
same — info, warnings, errors, success — all in one boring color. You had to
READ every line to know if something went wrong.

Warp's terminal output is rich and colorful:
  - Green for success
  - Yellow for warnings
  - Red for errors
  - Blue for informational messages
  - Gray for timestamps

This module gives the Codex Launcher the same treatment. It auto-detects
log levels from message prefixes and applies the right color automatically.

It's like going from a black-and-white TV to color — you can instantly
see what's important without reading every word.
"""
import tkinter as tk
from tkinter import scrolledtext
import time
from gui_x.theme import CATPPUCCIN
from gui_x.fonts import FONT_MONO, SIZE_MONO


class LogConsole(tk.Frame):
    """A dark-themed, syntax-colored log console widget.

    Features:
      - Auto-colors messages based on prefix (✓, ✗, ⚠, [proxy], etc.)
      - Right-aligned timestamps in dim color
      - Monospace font for terminal feel
      - Log level filter (All / Info / Warnings / Errors)
      - Auto-scrolls to bottom (pauses if user scrolls up)
      - Subtle accent-colored left border (Warp-style)

    Usage:
        console = LogConsole(parent_frame)
        console.pack(fill="both", expand=True)
        console.log("✓ Codex CLI detected")
        console.log("✗ Something failed")
        console.warn("⚠ Warning message")
        console.error("ERROR: something broke")
    """

    C = CATPPUCCIN

    def __init__(self, parent, height=12, **kwargs):
        bg = kwargs.pop("bg", self.C["base"])
        super().__init__(parent, bg=bg, **kwargs)

        # ── Header bar with filter ──────────────────────────────────
        self._header = tk.Frame(self, bg=self.C["surface0"], padx=8, pady=4)
        self._header.pack(fill="x")

        tk.Label(self._header, text="Console", fg=self.C["accent"], bg=self.C["surface0"],
                 font=("sans-serif", 9, "bold")).pack(side="left")

        # Log level filter
        self._filter_var = tk.StringVar(value="All")
        for level in ("All", "Info", "Warnings", "Errors"):
            rb = tk.Radiobutton(self._header, text=level, variable=self._filter_var,
                                value=level, bg=self.C["surface0"], fg=self.C["subtext"],
                                selectcolor=self.C["surface1"],
                                activebackground=self.C["surface0"],
                                activeforeground=self.C["accent"],
                                font=("sans-serif", 8), indicatoron=0,
                                padx=6, pady=1, relief="flat", bd=0)
            rb.pack(side="left", padx=(8, 0))

        # Line count
        self._count_label = tk.Label(self._header, text="0 lines", fg=self.C["dim"],
                                      bg=self.C["surface0"], font=("sans-serif", 8))
        self._count_label.pack(side="right")

        # Clear button
        tk.Button(self._header, text="Clear", bg=self.C["surface1"], fg=self.C["subtext"],
                  activebackground=self.C["surface2"], activeforeground=self.C["text"],
                  relief="flat", bd=0, padx=8, pady=2,
                  font=("sans-serif", 8), command=self.clear).pack(side="right", padx=(4, 0))

        # ── Left accent border ──────────────────────────────────────
        border_frame = tk.Frame(self, bg=self.C["accent"], width=2)
        border_frame.pack(side="left", fill="y")
        border_frame.pack_propagate(False)

        # ── Text area ───────────────────────────────────────────────
        self._text = tk.Text(self, bg=self.C["base"], fg=self.C["text"],
                             font=(FONT_MONO, SIZE_MONO), wrap="word",
                             state="disabled", relief="flat", bd=0,
                             padx=8, pady=6, insertbackground=self.C["accent"],
                             selectbackground=self.C["accent"],
                             selectforeground=self.C["base"])
        self._text.pack(side="left", fill="both", expand=True)

        # Scrollbar
        self._scrollbar = tk.Scrollbar(self, orient="vertical",
                                        command=self._text.yview,
                                        bg=self.C["surface0"],
                                        troughcolor=self.C["base"])
        self._scrollbar.pack(side="right", fill="y")
        self._text.configure(yscrollcommand=self._scrollbar.set)

        # ── Configure color tags ────────────────────────────────────
        self._text.tag_configure("info",    foreground=self.C["subtext"])
        self._text.tag_configure("success", foreground=self.C["green"])
        self._text.tag_configure("warn",    foreground=self.C["yellow"])
        self._text.tag_configure("error",   foreground=self.C["red"])
        self._text.tag_configure("accent",  foreground=self.C["accent"])
        self._text.tag_configure("dim",     foreground=self.C["dim"])
        self._text.tag_configure("proxy",   foreground=self.C["sapphire"])
        self._text.tag_configure("monitor", foreground=self.C["lavender"])

        # ── Auto-scroll tracking ────────────────────────────────────
        self._auto_scroll = True
        self._text.bind("<MouseWheel>", self._on_scroll)
        self._text.bind("<Button-4>", self._on_scroll)  # Linux scroll up
        self._text.bind("<Button-5>", self._on_scroll)  # Linux scroll down

        self._line_count = 0
        self._all_messages = []  # Store for filter re-application

    def _on_scroll(self, event):
        """Detect if user scrolled up — pause auto-scroll."""
        if hasattr(event, 'delta'):
            # Windows/macOS
            if event.delta > 0:
                self._auto_scroll = False
        elif hasattr(event, 'num'):
            # Linux
            if event.num == 4:
                self._auto_scroll = False
            elif event.num == 5:
                # Scrolling down — re-enable auto-scroll at bottom
                self._auto_scroll = True

    def _classify_message(self, msg):
        """Determine the log level tag from message content.

        This is the "magic" that makes messages colorful:
          - "✓" or "OK" → green (success)
          - "✗" or "ERROR" or "Failed" → red (error)
          - "⚠" or "WARN" → yellow (warn)
          - "[proxy]" → cyan (proxy)
          - "[AI Monitor]" → lavender (monitor)
          - Everything else → default text color (info)
        """
        msg_lower = msg.lower()
        if msg_lower.startswith("✓") or msg_lower.startswith("ok"):
            return "success"
        if any(x in msg_lower for x in ("✗", "error", "failed", "failed:", "fail")):
            return "error"
        if any(x in msg_lower for x in ("⚠", "warn", "warning")):
            return "warn"
        if "[proxy]" in msg_lower:
            return "proxy"
        if "[ai monitor]" in msg_lower or "[ai diag]" in msg_lower:
            return "monitor"
        return "info"

    def log(self, msg):
        """Append a message to the console with auto-coloring.

        This is the main method the rest of the app calls.
        It adds a timestamp, detects the log level, and applies color.
        """
        timestamp = time.strftime("%H:%M:%S")
        level = self._classify_message(msg)
        self._all_messages.append((timestamp, msg, level))
        self._line_count += 1

        # Check filter
        current_filter = self._filter_var.get()
        if current_filter != "All":
            filter_map = {"Info": ("info", "success", "proxy", "monitor"),
                          "Warnings": ("warn",),
                          "Errors": ("error",)}
            allowed = filter_map.get(current_filter, ())
            if level not in allowed:
                self._update_count()
                return

        self._append_line(timestamp, msg, level)
        self._update_count()

    def _append_line(self, timestamp, msg, level):
        """Actually write a line to the text widget."""
        self._text.configure(state="normal")
        # Timestamp in dim color
        self._text.insert("end", f"{timestamp} ", "dim")
        # Message in level color
        self._text.insert("end", f"{msg}\n", level)
        self._text.configure(state="disabled")

        if self._auto_scroll:
            self._text.see("end")

    def _update_count(self):
        """Update the line count label."""
        self._count_label.configure(text=f"{len(self._all_messages)} lines")

    def warn(self, msg):
        """Log a warning message (yellow)."""
        self.log(f"⚠ {msg}")

    def error(self, msg):
        """Log an error message (red)."""
        self.log(f"✗ {msg}")

    def success(self, msg):
        """Log a success message (green)."""
        self.log(f"✓ {msg}")

    def clear(self):
        """Clear all log messages."""
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")
        self._all_messages.clear()
        self._line_count = 0
        self._update_count()
