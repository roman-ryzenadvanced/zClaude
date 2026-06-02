"""Cross-platform shell/OS utilities."""
import os
import shutil
import subprocess
import sys
from lib.constants import IS_WINDOWS

# ═══════════════════════════════════════════════════════════════════════
# Cross-platform terminal detection
# ═══════════════════════════════════════════════════════════════════════

def detect_terminal():
    if IS_WINDOWS:
        for term in ["wt.exe", "cmd.exe", "powershell.exe"]:
            path = shutil.which(term)
            if path:
                return (term, [], path)
        return None
    terms = [
        ("x-terminal-emulator", ["-e"]),
        ("kgx", ["--"]),
        ("gnome-terminal", ["--"]),
        ("konsole", ["-e"]),
        ("xterm", ["-e"]),
    ]
    for t in terms:
        if shutil.which(t[0]):
            return (t[0], t[1], shutil.which(t[0]))
    return None

# ═══════════════════════════════════════════════════════════════════════
# Cross-platform URL/file opening
# ═══════════════════════════════════════════════════════════════════════

def open_url(url):
    if IS_WINDOWS:
        os.startfile(url)
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_file(path):
    open_url(str(path))
