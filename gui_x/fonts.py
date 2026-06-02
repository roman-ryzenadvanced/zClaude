#!/usr/bin/env python3
"""X Edition Font System — Cross-platform font detection and configuration.

WHY: The original GUI hard-coded "Segoe UI" everywhere. That's a Windows font
that doesn't exist on Linux. When tkinter can't find it, it falls back to a
generic sans-serif that looks ugly and inconsistent — like wearing sneakers
with a tuxedo.

This module FIXES that by:
  1. Detecting which fonts are actually installed on YOUR system
  2. Picking the best available font for each role (UI, mono, headings)
  3. Providing constants that the rest of the X Edition GUI uses

Think of it as a font wardrobe: instead of assuming everyone has a suit
(Segoe UI), we check what's in your closet and pick the best outfit.

On Linux, the typical best options are:
  - Noto Sans (modern, clean, great Unicode coverage)
  - DejaVu Sans (classic Linux default, always available)
  - JetBrains Mono / Noto Sans Mono (for monospace/terminal areas)
"""
import tkinter as tk
from tkinter import font as tkfont
import sys

# ═══════════════════════════════════════════════════════════════════════
# Platform-specific font candidates (in priority order)
# ═══════════════════════════════════════════════════════════════════════

_UI_CANDIDATES = {
    "linux":   ["Noto Sans", "DejaVu Sans", "Ubuntu", "Liberation Sans", "Cantarell", "FreeSans"],
    "darwin":  ["SF Pro Text", "Helvetica Neue", "Helvetica", "Arial"],
    "win32":   ["Segoe UI", "Tahoma", "Verdana", "Arial"],
}

_MONO_CANDIDATES = {
    "linux":   ["JetBrains Mono", "Noto Sans Mono", "DejaVu Sans Mono",
                "Liberation Mono", "Sarasa Mono SC", "FreeMono"],
    "darwin":  ["SF Mono", "Menlo", "Monaco", "Courier New"],
    "win32":   ["Consolas", "Cascadia Code", "Courier New"],
}

_HEADING_CANDIDATES = {
    "linux":   ["Noto Sans", "DejaVu Sans", "Ubuntu", "Liberation Sans"],
    "darwin":  ["SF Pro Display", "Helvetica Neue", "Helvetica"],
    "win32":   ["Segoe UI", "Calibri"],
}


# ═══════════════════════════════════════════════════════════════════════
# Font detection
# ═══════════════════════════════════════════════════════════════════════

def _detect_best_font(candidates_list):
    """Try each candidate font and return the first one that exists.

    How it works:
      1. Create a temporary tk root (invisible)
      2. Get the list of all font families tkinter knows about
      3. Try each candidate in order — return the first match

    If NO candidate is found, returns "TkDefaultFont" as the ultimate fallback.
    """
    try:
        # Try to get font families without creating a root
        # (if a root already exists, use it)
        root_exists = True
        try:
            _ = tk._default_root  # type: ignore
        except AttributeError:
            root_exists = False

        if root_exists:
            available = set(tkfont.families())
        else:
            # Create a temporary root just for font detection
            tmp_root = tk.Tk()
            tmp_root.withdraw()
            available = set(tkfont.families())
            tmp_root.destroy()

        for candidate in candidates_list:
            if candidate in available:
                return candidate
    except Exception:
        pass

    return "TkDefaultFont"


def _get_platform_key():
    """Get the platform key for font candidate lookup."""
    if sys.platform == "darwin":
        return "darwin"
    elif sys.platform == "win32":
        return "win32"
    else:
        return "linux"


# ═══════════════════════════════════════════════════════════════════════
# Resolved font constants — the rest of the X Edition uses these
# ═══════════════════════════════════════════════════════════════════════

_platform = _get_platform_key()

# Primary UI font (buttons, labels, body text)
FONT_UI = _detect_best_font(_UI_CANDIDATES.get(_platform, _UI_CANDIDATES["linux"]))

# Monospace font (log console, code viewers, request details)
FONT_MONO = _detect_best_font(_MONO_CANDIDATES.get(_platform, _MONO_CANDIDATES["linux"]))

# Heading font (titles, section headers)
FONT_HEADING = _detect_best_font(_HEADING_CANDIDATES.get(_platform, _HEADING_CANDIDATES["linux"]))

# ═══════════════════════════════════════════════════════════════════════
# Font size presets (in pixels)
# ═══════════════════════════════════════════════════════════════════════

SIZE_TITLE   = 16   # Window title
SIZE_HEADING = 13   # Section headings
SIZE_BODY    = 10   # Normal text
SIZE_SMALL   = 9    # Captions, hints
SIZE_TINY    = 8    # Timestamps, secondary info
SIZE_MONO    = 9    # Log console, code blocks
SIZE_ICON    = 14   # Status icons


def font_ui(size=SIZE_BODY, weight="normal"):
    """Return a font tuple for UI text: (FONT_UI, size, weight)."""
    return (FONT_UI, size, weight)


def font_mono(size=SIZE_MONO, weight="normal"):
    """Return a font tuple for monospace text: (FONT_MONO, size, weight)."""
    return (FONT_MONO, size, weight)


def font_heading(size=SIZE_HEADING, weight="bold"):
    """Return a font tuple for heading text: (FONT_HEADING, size, weight)."""
    return (FONT_HEADING, size, weight)
