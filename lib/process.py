"""Cross-platform process management primitives."""
import json
import os
import signal
import subprocess
import sys
import time
from lib.constants import IS_WINDOWS, PID_REGISTRY

def _load_pid_registry():
    if PID_REGISTRY.exists():
        try:
            return json.loads(PID_REGISTRY.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[lib] failed to load pid registry: {exc}", file=sys.stderr)
    return {}


def _save_pid_registry(data):
    PID_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = PID_REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(str(tmp), str(PID_REGISTRY))
# ═══════════════════════════════════════════════════════════════════════
# Cross-platform process management
# ═══════════════════════════════════════════════════════════════════════

def _subprocess_new_group_flag():
    if IS_WINDOWS:
        return subprocess.CREATE_NEW_PROCESS_GROUP
    return None


def _subprocess_preexec_fn():
    if IS_WINDOWS:
        return None
    return os.setsid


def _kill_process_group(pid):
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
    else:
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
            time.sleep(0.5)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        except (ProcessLookupError, PermissionError):
            pass


def _kill_process_group_soft(pid):
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
    else:
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


def _register_pgid_entry(kind, pid):
    data = _load_pid_registry()
    if IS_WINDOWS:
        data[kind] = {"pid": pid, "pgid": pid, "ts": time.time()}
    else:
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        data[kind] = {"pid": pid, "pgid": pgid, "ts": time.time()}
    _save_pid_registry(data)


def safe_cleanup_owned(logfn=None):
    """Kill all registered processes from the PID registry."""
    data = _load_pid_registry()
    for kind, info in list(data.items()):
        pid = info.get("pid") if isinstance(info, dict) else info
        if pid:
            try:
                _kill_process_group(pid)
                if logfn:
                    logfn(f"[cleanup] killed {kind} (pid {pid})")
            except Exception as exc:
                if logfn:
                    logfn(f"[cleanup] failed to kill {kind} (pid {pid}): {exc}")
    _save_pid_registry({})
