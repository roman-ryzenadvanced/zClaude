"""Codex Launcher GUI — modular split of codex-launcher-gui.py."""
import tkinter as tk
from codex_launcher_lib import ensure_dirs, create_default_endpoints
from gui.launcher import LauncherWin

def main():
    ensure_dirs()
    create_default_endpoints()

    root = tk.Tk()
    root.title("Codex Launcher")
    root.geometry("800x680")
    root.minsize(640, 520)
    app = LauncherWin(root)
    root.mainloop()

__all__ = ["main"]
