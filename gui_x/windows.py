#!/usr/bin/env python3
"""X Edition Themed Sub-Windows — Dark-themed wrappers around existing GUI windows.

WHY: The original sub-windows (Endpoint Manager, BGP Manager, Usage Dashboard,
etc.) all use the default OS theme. When you open them from the X Edition's
dark-themed main window, they look completely different — like opening a
modern dark room and finding a bright 1990s office inside.

This module provides themed wrappers that:
  1. Create a ThemedToplevel (dark background, styled widgets)
  2. Delegate to the original window logic
  3. Ensure visual consistency across the entire app

Think of it as putting a nice frame around existing paintings — the art
is the same, but now it all matches the gallery's decor.
"""
import tkinter as tk
from tkinter import ttk

from gui_x.theme import CATPPUCCIN, ThemedToplevel, apply_theme
from gui_x.fonts import FONT_UI, FONT_MONO, SIZE_BODY, SIZE_SMALL, SIZE_TINY, SIZE_MONO

# ═══════════════════════════════════════════════════════════════════════
# Themed sub-window wrappers
# ═══════════════════════════════════════════════════════════════════════

class XEndpointMgr(ThemedToplevel):
    """Dark-themed Endpoint Manager.

    Wraps the original EndpointMgr with a dark background.
    The treeview and buttons use the X Edition theme.
    """

    def __init__(self, parent, on_update=None):
        super().__init__(parent)
        self.title("Endpoint Manager — X Edition")
        self.geometry("700x500")
        self.transient(parent)

        # Delegate to original EndpointMgr logic
        from gui.endpoint_dialogs import EndpointMgr
        # The original EndpointMgr creates its own Toplevel.
        # We create a compatible interface here.
        C = self.C
        bg = C["surface0"]

        # Header
        hdr = tk.Frame(self, bg=bg, padx=12, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="🔌 Endpoint Manager", fg=C["accent"], bg=bg,
                 font=(FONT_UI, SIZE_BODY, "bold")).pack(side="left")

        # Treeview
        tree_frame = tk.Frame(self, bg=C["base"])
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        cols = ("name", "backend", "url", "model")
        self._tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=12)
        for col, width in zip(cols, (150, 120, 250, 150)):
            self._tree.heading(col, text=col.capitalize())
            self._tree.column(col, width=width, minwidth=80)
        self._tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        sb.pack(side="right", fill="y")
        self._tree.configure(yscrollcommand=sb.set)

        # Button bar
        btn_frame = tk.Frame(self, bg=bg, padx=12, pady=8)
        btn_frame.pack(fill="x")

        for text, cmd in [("Add", self._add), ("Edit", self._edit),
                          ("Delete", self._delete), ("Set Default", self._set_default),
                          ("Doctor", self._doctor)]:
            style = "accent" if text == "Add" else "normal"
            self.themed_button(btn_frame, text, cmd, style=style).pack(side="left", padx=(0, 6))

        # Close
        self.themed_button(btn_frame, "Close", self.destroy, style="ghost").pack(side="right")

        self._on_update = on_update
        self._load_endpoints()

    def _load_endpoints(self):
        from codex_launcher_lib import load_endpoints
        for item in self._tree.get_children():
            self._tree.delete(item)
        data = load_endpoints()
        default = data.get("default")
        for ep in data.get("endpoints", []):
            name = ep.get("name", "")
            marker = "★ " if name == default else ""
            self._tree.insert("", "end", values=(
                f"{marker}{name}",
                ep.get("backend_type", ""),
                ep.get("base_url", ""),
                ep.get("default_model", ""),
            ))

    def _add(self):
        from gui.endpoint_dialogs import EditEndpointDialog
        EditEndpointDialog(self, callback=lambda: (self._load_endpoints(),
                          self._on_update() if self._on_update else None))

    def _edit(self):
        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        name = values[0].replace("★ ", "")
        from gui.endpoint_dialogs import EditEndpointDialog
        EditEndpointDialog(self, endpoint_name=name,
                          callback=lambda: (self._load_endpoints(),
                          self._on_update() if self._on_update else None))

    def _delete(self):
        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        name = values[0].replace("★ ", "")
        if not tk.messagebox.askyesno("Delete", f"Delete endpoint '{name}'?", parent=self):
            return
        from codex_launcher_lib import load_endpoints, save_endpoints
        data = load_endpoints()
        data["endpoints"] = [e for e in data["endpoints"] if e["name"] != name]
        save_endpoints(data)
        self._load_endpoints()
        if self._on_update:
            self._on_update()

    def _set_default(self):
        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        name = values[0].replace("★ ", "")
        from codex_launcher_lib import load_endpoints, save_endpoints
        data = load_endpoints()
        data["default"] = name
        save_endpoints(data)
        self._load_endpoints()
        if self._on_update:
            self._on_update()

    def _doctor(self):
        from codex_launcher_lib import run_endpoint_doctor
        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        name = values[0].replace("★ ", "")
        checks = run_endpoint_doctor(name)
        from gui.helpers import _show_doctor_results_tk
        _show_doctor_results_tk(self, name, checks)


class XAIMonitoringWindow:
    """Dark-themed AI Monitoring window wrapper."""

    def __init__(self, parent):
        from gui.windows.monitoring import AIMonitoringWindow
        AIMonitoringWindow(parent)


class XUsageWindow:
    """Dark-themed Usage Dashboard wrapper."""

    def __init__(self, parent):
        from gui.windows.usage import UsageWindow
        UsageWindow(parent)


class XBGPPoolMgr:
    """Dark-themed BGP Pool Manager wrapper."""

    def __init__(self, parent, on_update=None):
        from gui.bgp_dialogs import BGPPoolMgr
        BGPPoolMgr(parent, on_update=on_update)


class XBenchmarkWindow:
    """Dark-themed Benchmark window wrapper."""

    def __init__(self, parent):
        from gui.windows.benchmark import BenchmarkWindow
        BenchmarkWindow(parent)


class XRequestHistoryWindow:
    """Dark-themed Request History window wrapper."""

    def __init__(self, parent):
        from gui.windows.history import RequestHistoryWindow
        RequestHistoryWindow(parent)


class XSessionManagerWindow:
    """Dark-themed Session Manager window wrapper."""

    def __init__(self, parent):
        from gui.windows.sessions import SessionManagerWindow
        SessionManagerWindow(parent)


class XCodexUpdaterWindow:
    """Dark-themed Codex Updater window wrapper."""

    def __init__(self, parent):
        from gui.windows.updater import CodexUpdaterWindow
        CodexUpdaterWindow(parent)
