#!/usr/bin/env python3
"""Codex Launcher X Edition — backward-compatible entry point.

Launch the X Edition GUI (modernized dark theme, Warp-inspired layout).
This file lives alongside codex-launcher-gui.py so both GUIs coexist.
"""
import os
import sys
from pathlib import Path

# Ensure src/ is on the path so `from gui_x.xxx` and `from codex_launcher_lib` work
_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from gui_x import main  # noqa: E402

if __name__ == "__main__":
    main()
