"""Codex CLI/Desktop detection, launch, and auth."""
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from lib.constants import IS_WINDOWS, BIN_DIR, LAUNCH_LOG, START_SH, PROXY_CONFIG_DIR
from lib.process import _kill_process_group, _kill_process_group_soft

def detect_codex_cli():
    try:
        path = shutil.which("codex")
        if not path:
            return None
        out = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=5)
        ver = (out.stdout or "").strip() or (out.stderr or "").strip() or "unknown"
        return (path, ver)
    except Exception:
        return None


def detect_codex_desktop():
    """Detect Codex Desktop installation.

    Returns (path_or_aumid, is_msix) tuple on Windows, path string on Linux.
    For MSIX installs, returns the AppUserModelId since the exe cannot be
    launched directly via subprocess from WindowsApps.
    """
    if IS_WINDOWS:
        la = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("PROGRAMFILES", "")
        pf86 = os.environ.get("PROGRAMFILES(X86)", "")
        desktop_paths = [
            Path(la) / "Programs" / "Codex Desktop" / "Codex Desktop.exe",
            Path(pf) / "Codex Desktop" / "Codex Desktop.exe",
            Path(pf86) / "Codex Desktop" / "Codex Desktop.exe",
            Path(la) / "OpenAI" / "Codex Desktop" / "Codex Desktop.exe",
        ]
        for p in desktop_paths:
            if p.exists():
                return str(p), False
        # MSIX / Microsoft Store install
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-AppxPackage *OpenAI.Codex*).InstallLocation"],
                capture_output=True, text=True, timeout=10,
            )
            loc = r.stdout.strip() if r.returncode == 0 else ""
            if loc:
                msix_exe = Path(loc) / "app" / "Codex.exe"
                if msix_exe.exists():
                    r2 = subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         "(Get-AppxPackage *OpenAI.Codex*).PackageFamilyName"],
                        capture_output=True, text=True, timeout=10,
                    )
                    family = r2.stdout.strip() if r2.returncode == 0 else ""
                    if family:
                        return f"{family}!App", True
        except Exception:
            pass
        return None, False
    if START_SH and START_SH.exists():
        return str(START_SH), False
    return None, False


def launch_codex_desktop(desktop_info):
    """Launch Codex Desktop process.

    Args:
        desktop_info: (path_or_aumid, is_msix) tuple from detect_codex_desktop()

    Returns:
        subprocess.Popen object or None
    """
    path, is_msix = desktop_info
    if IS_WINDOWS:
        if is_msix:
            return subprocess.Popen(
                ["cmd", "/c", "start", "", f"shell:AppsFolder\\{path}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        return subprocess.Popen(
            [path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        return subprocess.Popen(
            [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid)


def is_codex_desktop_running():
    """Check if Codex Desktop (or MSIX Codex) is currently running."""
    if IS_WINDOWS:
        try:
            for name in ("Codex Desktop.exe", "Codex.exe"):
                out = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in out.stdout.strip().splitlines():
                    parts = line.split(",")
                    if len(parts) >= 2 and parts[1].strip('"').isdigit():
                        return True
        except Exception:
            pass
        return False
    else:
        try:
            out = subprocess.run(["pgrep", "-f", "/opt/codex-desktop/electron"], capture_output=True, text=True, timeout=5)
            return bool(out.stdout.strip())
        except Exception:
            return False


def check_codex_auth():
    try:
        out = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True, text=True, timeout=10,
        )
        text = (out.stdout or "").strip()
        if not text:
            text = (out.stderr or "").strip()
        if out.returncode == 0 and text:
            return ("logged_in", text)
        if text:
            return ("error", text)
        return ("unknown", "No output from codex login status")
    except FileNotFoundError:
        return ("not_installed", "codex not found")
    except OSError as e:
        if e.errno == 2:
            return ("not_configured", "Config not found — launch Codex once to create it")
        return ("error", str(e))
    except Exception as e:
        return ("error", str(e))

# ═══════════════════════════════════════════════════════════════════════
# Log helpers
# ═══════════════════════════════════════════════════════════════════════

def last_log_lines(n=15):
    try:
        t = LAUNCH_LOG.read_text(encoding="utf-8")
        return "\n".join(t.splitlines()[-n:])
    except Exception:
        return "(no log file)"

# ═══════════════════════════════════════════════════════════════════════
# Process helpers (desktop kill etc.)
# ═══════════════════════════════════════════════════════════════════════

def kill_existing_desktop(logfn=None):
    if IS_WINDOWS:
        for img in ("Codex Desktop.exe", "Codex.exe"):
            try:
                out = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {img}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in out.stdout.strip().splitlines():
                    parts = line.split(",")
                    if len(parts) >= 2:
                        pid_str = parts[1].strip('"')
                        if pid_str.isdigit():
                            pid = int(pid_str)
                            _kill_process_group(pid)
                            if logfn:
                                logfn(f"Killed existing Codex Desktop (pid {pid})")
                            time.sleep(2)
            except Exception as e:
                if logfn:
                    logfn(f"Note: could not kill existing Desktop: {e}")
    else:
        try:
            out = subprocess.run(["pgrep", "-f", "/opt/codex-desktop/electron"], capture_output=True, text=True, timeout=5)
            pids = [p for p in out.stdout.strip().splitlines() if p.strip().isdigit()]
            if not pids:
                return
            main_pid = int(pids[0])
            pgid = os.getpgid(main_pid)
            if pgid > 0:
                os.killpg(pgid, signal.SIGTERM)
                if logfn:
                    logfn(f"Killed existing Codex Desktop (pid {main_pid}, pgid {pgid})")
                time.sleep(2)
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        except Exception as e:
            if logfn:
                logfn(f"Note: could not kill existing Desktop: {e}")

