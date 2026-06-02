"""Codex Launcher X Edition — Modernized Linux GUI with Warp-inspired dark theme.

This is the 'X Edition' GUI — a visual overhaul of the original tkinter GUI.
It lives alongside the original gui/ package so both can coexist.

Launch with:  python src/codex-launcher-gui-x.py
Or installed: codex-launcher-gui-x
"""

import tkinter as tk
from codex_launcher_lib import ensure_dirs, create_default_endpoints
from gui_x.launcher import LauncherWinX


def main():
    ensure_dirs()
    create_default_endpoints()

    root = tk.Tk()
    root.title("Codex Launcher X")
    root.geometry("750x1010")
    root.minsize(750, 600)

    # Apply the X Edition dark theme before creating widgets
    from gui_x.theme import apply_theme

    apply_theme(root)

    LauncherWinX(root)
    root.mainloop()


__all__ = ["main"]
