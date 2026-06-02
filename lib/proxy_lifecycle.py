"""Proxy lifecycle management — start, stop, BGP proxy, PID tracking."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from lib.constants import (
    IS_WINDOWS, PROXY, PROXY_CONFIG_DIR, PID_REGISTRY,
    BIN_DIR, CONFIG, ENDPOINTS_FILE, BGP_POOLS_FILE,
)
from lib.process import (
    _subprocess_new_group_flag, _subprocess_preexec_fn,
    _kill_process_group, _register_pgid_entry,
)
from lib.utils import safe_name, normalize_base_url, now_utc_iso
from lib.endpoints import load_endpoints, get_endpoint, load_bgp_pools
from lib.config_manager import write_config_for_translated

# ═══════════════════════════════════════════════════════════════════════
# Proxy lifecycle
# ═══════════════════════════════════════════════════════════════════════

_proxy_proc = None
_proxy_port = None
_PROXY_PORT_FILE = PROXY_CONFIG_DIR / ".last-proxy-port"
PROXY_PORT = 61255


def _get_proxy_port():
    """Return the fixed proxy port. Raises RuntimeError if port is taken."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", PROXY_PORT))
            return PROXY_PORT
    except OSError:
        raise RuntimeError(
            f"Port {PROXY_PORT} is already in use. "
            f"Stop the conflicting process or change PROXY_PORT in proxy_lifecycle.py."
        )


def get_proxy_state():
    return _proxy_proc, _proxy_port


def set_proxy_state(proc, port):
    global _proxy_proc, _proxy_port
    _proxy_proc = proc
    _proxy_port = port


def stop_proxy():
    global _proxy_proc
    if _proxy_proc and _proxy_proc.poll() is None:
        _kill_process_group(_proxy_proc.pid)
        _proxy_proc = None


def start_proxy_for(endpoint, logfn):
    """Start the translation proxy for an endpoint. Returns the port.
    logfn(msg) is used for status messages (may be called from any thread).
    """
    global _proxy_proc, _proxy_port
    stop_proxy()
    port = _get_proxy_port()
    _proxy_port = port
    _PROXY_PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROXY_PORT_FILE.write_text(str(port))

    model_list = endpoint.get("models", [])
    if (endpoint.get("backend_type") or "").startswith("gemini-oauth") and (endpoint.get("oauth_provider") or "").startswith("google"):
        token_name = "google-antigravity-oauth-token.json" if endpoint.get("oauth_provider") == "google-antigravity" else "google-cli-oauth-token.json"
        token_path = PROXY_CONFIG_DIR / token_name
        try:
            with open(token_path) as tf:
                td = json.load(tf)
            discovered = [] if endpoint.get("oauth_provider") == "google-antigravity" else td.get("available_models", [])
            if discovered:
                model_list = discovered
        except Exception as exc:
            print(f"[lib] oauth token discovery: {exc}", file=sys.stderr)

    pcfg = {
        "port": port,
        "backend_type": endpoint["backend_type"],
        "target_url": normalize_base_url(endpoint["base_url"]),
        "api_key": endpoint["api_key"],
        "cc_version": endpoint.get("cc_version", ""),
        "oauth_provider": endpoint.get("oauth_provider", ""),
        "reasoning_enabled": endpoint.get("reasoning_enabled", True),
        "reasoning_effort": endpoint.get("reasoning_effort", "medium"),
        "force_model": endpoint.get("default_model") or "",
        "caveman_mode": endpoint.get("caveman_mode", False),
        "rtk_compression": endpoint.get("rtk_compression", False),
        "auto_compact": endpoint.get("auto_compact", False),
        "adaptive_compact": endpoint.get("adaptive_compact", False),
        "tool_output_truncation": endpoint.get("tool_output_truncation", True),
        "models": [{"id": m, "object": "model", "created": 1700000000, "owned_by": endpoint["name"]}
                   for m in model_list],
    }
    pcfg_path = PROXY_CONFIG_DIR / f"proxy-{safe_name(endpoint['name'])}-{port}.json"
    pcfg_path.parent.mkdir(parents=True, exist_ok=True)
    pcfg_path.write_text(json.dumps(pcfg, indent=2))
    _start_proxy_with_config(pcfg_path, port, logfn)
    return port


def _start_proxy_with_config(pcfg_path, port, logfn):
    global _proxy_proc
    python_bin = sys.executable
    proxy_script = str(PROXY)

    popen_kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["preexec_fn"] = os.setsid

    _proxy_proc = subprocess.Popen(
        [python_bin, proxy_script, "--config", str(pcfg_path)],
        **popen_kwargs,
    )
    _register_pgid_entry("proxy", _proxy_proc.pid)

    _proxy_log_path = PROXY_CONFIG_DIR / "proxy-stderr.log"
    _proxy_log_file = open(_proxy_log_path, "a", encoding="utf-8")

    def _pipe_stderr():
        if not _proxy_proc.stderr:
            return
        for line in _proxy_proc.stderr:
            logfn(f"[proxy] {line.rstrip()}")
            try:
                _proxy_log_file.write(line)
                _proxy_log_file.flush()
            except Exception:
                pass

    threading.Thread(target=_pipe_stderr, daemon=True).start()

    deadline = time.time() + 15
    last_err = None
    while time.time() < deadline:
        if _proxy_proc.poll() is not None:
            raise RuntimeError(f"Proxy exited early with code {_proxy_proc.returncode}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2)
            logfn(f"Proxy ready on port {port}")
            return
        except Exception as e:
            last_err = e
            time.sleep(0.3)

    _kill_process_group(_proxy_proc.pid)
    raise RuntimeError(f"Proxy failed health check on port {port}: {last_err}")


def start_bgp_proxy(pool, model, logfn, endpoint=None):
    """Start a BGP proxy for a pool. Returns (port, bgp_endpoint, pcfg_path)."""
    global _proxy_proc, _proxy_port
    stop_proxy()
    port = _get_proxy_port()
    _proxy_port = port
    _PROXY_PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROXY_PORT_FILE.write_text(str(port))

    bgp_ep = {
        "name": pool["name"],
        "backend_type": "openai-compat",
        "base_url": "http://bgp.placeholder",
        "api_key": "",
        "default_model": model,
        "models": list(dict.fromkeys(r.get("model", model) for r in pool.get("routes", []))),
    }
    pcfg = {
        "port": port,
        "backend_type": "openai-compat",
        "target_url": "http://bgp.placeholder",
        "api_key": "",
        "bgp_routes": pool.get("routes", []),
        "caveman_mode": (endpoint or {}).get("caveman_mode", False),
        "rtk_compression": (endpoint or {}).get("rtk_compression", False),
        "auto_compact": (endpoint or {}).get("auto_compact", False),
        "adaptive_compact": (endpoint or {}).get("adaptive_compact", False),
        "tool_output_truncation": (endpoint or {}).get("tool_output_truncation", True),
        "models": [{"id": m, "object": "model", "created": 1700000000, "owned_by": "bgp"} for m in bgp_ep["models"]],
    }
    pcfg_path = PROXY_CONFIG_DIR / f"proxy-{safe_name(pool['name'])}-{port}.json"
    pcfg_path.parent.mkdir(parents=True, exist_ok=True)
    pcfg_path.write_text(json.dumps(pcfg, indent=2))
    _start_proxy_with_config(pcfg_path, port, logfn)
    return port, bgp_ep

