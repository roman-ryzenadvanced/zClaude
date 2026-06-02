"""Log directory cleanup — rotation, purge, __pycache__ removal.

Called once at startup from:
  - proxy server.py main()
  - lib/bootstrap.py ensure_dirs()
"""
import glob
import os
import shutil
import sys

# ── Rotation config ──────────────────────────────────────────────
_MAX_LOG_BYTES = 1_048_576   # 1 MB
_MAX_LOG_COPIES = 3           # .log.1, .log.2, .log.3
_MAX_DEBUG_DUMPS = 10         # antigravity-v2-request-*.json

_LOG_FILES = (
    "proxy.log",
    "proxy-stderr.log",
    "cc-debug.log",
    "monitoring.log",
)

_PACKAGE_DIRS = ("lib", "gui", "proxy", "antigravity_grpc", "plugins", "locales")


# ── Internal helpers ─────────────────────────────────────────────

def _rotate_log_file(path):
    """Rotate a single log file if it exceeds _MAX_LOG_BYTES.

    Strategy: shift .log.N -> .log.N+1 (drop oldest), then rename
    current .log -> .log.1.  The caller (or next startup) will
    re-open a fresh empty file.
    """
    try:
        if not os.path.isfile(path):
            return
        if os.path.getsize(path) < _MAX_LOG_BYTES:
            return
        # Shift existing rotated copies: .3 -> gone, .2 -> .3, .1 -> .2
        for i in range(_MAX_LOG_COPIES, 0, -1):
            older = "{}.{}".format(path, i)
            if os.path.isfile(older):
                if i == _MAX_LOG_COPIES:
                    os.remove(older)
                else:
                    os.replace(older, "{}.{}".format(path, i + 1))
        # Current -> .1
        os.replace(path, "{}.1".format(path))
    except Exception:
        pass


def _purge_old_files(directory, pattern, max_count):
    """Remove oldest files matching *pattern* beyond *max_count*.

    Reuses the same pattern as config_manager._rotate_backups.
    """
    try:
        files = sorted(
            glob.glob(os.path.join(directory, pattern)),
            key=os.path.getmtime,
            reverse=True,
        )
        for old_file in files[max_count:]:
            try:
                os.remove(old_file)
            except Exception:
                pass
    except Exception:
        pass


def _clean_pycache_in_tree(base_dir):
    """Remove all __pycache__ directories under *base_dir* (one level deep)."""
    for pkg in _PACKAGE_DIRS:
        cache = os.path.join(base_dir, pkg, "__pycache__")
        if os.path.isdir(cache):
            try:
                shutil.rmtree(cache, ignore_errors=True)
            except Exception:
                pass
    # Top-level __pycache__ if present
    top_cache = os.path.join(base_dir, "__pycache__")
    if os.path.isdir(top_cache):
        try:
            shutil.rmtree(top_cache, ignore_errors=True)
        except Exception:
            pass


# ── Public API ───────────────────────────────────────────────────

def cleanup_log_dir(log_dir):
    """Run all cleanup tasks for the proxy config/log directory.

    Tasks:
      1. Rotate log files that exceed 1 MB (keep last 3 copies)
      2. Purge debug dumps beyond 10 files
      3. Clean __pycache__ in all package dirs under the install base
    """
    # 1. Rotate log files
    for log_name in _LOG_FILES:
        _rotate_log_file(os.path.join(log_dir, log_name))

    # 2. Purge debug dumps
    _purge_old_files(log_dir, "antigravity-v2-request-*.json", _MAX_DEBUG_DUMPS)

    # 3. Clean __pycache__ — detect install base from this file's location
    #    this file lives at <install>/src/lib/cleanup.py
    #    packages live at <install>/src/{lib,gui,proxy,...}
    try:
        this_file_dir = os.path.dirname(os.path.abspath(__file__))
        src_dir = os.path.dirname(this_file_dir)  # <install>/src
        _clean_pycache_in_tree(src_dir)
    except Exception:
        pass
