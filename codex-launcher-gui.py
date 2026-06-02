#!/usr/bin/env python3
"""Backward-compatible shim for codex-launcher-gui.py.
All functionality has been moved to the src/gui/ package.
"""
import sys
import os

# Ensure src/ is in sys.path
_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from gui import main

if __name__ == "__main__":
    main()
