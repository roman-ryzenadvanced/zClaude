"""Platform detection, paths, and runtime constants."""
import json
import os
import sys
from pathlib import Path

UA = "codex-launcher/1.0"
_MODULE_DIR = Path(__file__).resolve().parent

# ═══════════════════════════════════════════════════════════════════════
# Platform detection
# ═══════════════════════════════════════════════════════════════════════

IS_WINDOWS = sys.platform == "win32"
HOME = Path.home()

if IS_WINDOWS:
    _LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
    PROXY_CONFIG_DIR = _LOCAL_APPDATA / "codex-proxy"
    CONFIG_DIR = HOME / ".codex"
    BIN_DIR = _LOCAL_APPDATA / "Programs" / "Codex-Launcher"
    LOG_DIR = _LOCAL_APPDATA / "codex-proxy"
    PID_REGISTRY = _LOCAL_APPDATA / "codex-proxy" / "pids.json"
    _USAGE_STATS_FILE = _LOCAL_APPDATA / "codex-proxy" / "usage-stats.json"
    MONITORING_FILE = _LOCAL_APPDATA / "codex-proxy" / "monitoring-config.json"
    INCIDENT_STORE_FILE = _LOCAL_APPDATA / "codex-proxy" / "incident-store.json"
    MONITORING_LOG = _LOCAL_APPDATA / "codex-proxy" / "monitoring.log"
    REQUEST_SNAP_DIR = _LOCAL_APPDATA / "codex-proxy" / "requests"
else:
    PROXY_CONFIG_DIR = HOME / ".cache/codex-proxy"
    CONFIG_DIR = HOME / ".codex"
    BIN_DIR = HOME / ".local/bin"
    LOG_DIR = HOME / ".cache/codex-proxy"
    PID_REGISTRY = HOME / ".cache/codex-proxy" / "pids.json"
    _USAGE_STATS_FILE = HOME / ".cache/codex-proxy/usage-stats.json"
    MONITORING_FILE = HOME / ".cache/codex-proxy/monitoring-config.json"
    INCIDENT_STORE_FILE = HOME / ".cache/codex-proxy/incident-store.json"
    MONITORING_LOG = HOME / ".cache/codex-proxy/monitoring.log"
    REQUEST_SNAP_DIR = HOME / ".cache/codex-proxy/requests"

CONFIG = CONFIG_DIR / "config.toml"
CONFIG_BAK = CONFIG_DIR / "config.toml.launcher-bak"
CONFIG_TXN = CONFIG_DIR / "config.toml.launcher-txn.json"
ENDPOINTS_FILE = CONFIG_DIR / "endpoints.json"
BGP_POOLS_FILE = CONFIG_DIR / "bgp-pools.json"
LAUNCH_LOG = LOG_DIR / "launcher.log"
OAUTH_SECRETS_PATH = HOME / ".config" / "codex-launcher" / "oauth-secrets.json"

GEMINI_OAUTH_CLIENT_ID = os.environ.get(
    "ZCLAUDE_GEMINI_CLIENT_ID",
    "",  # Set via env var or oauth-secrets.json at runtime
)
GEMINI_OAUTH_CLIENT_SECRET = os.environ.get(
    "ZCLAUDE_GEMINI_CLIENT_SECRET",
    "",  # Set via env var or oauth-secrets.json at runtime
)

# ── Kiro (AWS CodeWhisperer) constants ──
KIRO_SSO_OIDC_ENDPOINT = "https://oidc.us-east-1.amazonaws.com"
KIRO_START_URL = "https://view.awsapps.com/start"
KIRO_AUTH_SERVICE = "https://prod.us-east-1.auth.desktop.kiro.dev"
KIRO_SCOPES = ["codewhisperer:completions", "codewhisperer:analysis", "codewhisperer:conversations"]
KIRO_ISSUER_URL = "https://identitycenter.amazonaws.com/ssoins-722374e8c3c8e6c6"

_LOCAL_PROXY = _MODULE_DIR.parent / "translate-proxy.py"
_LOCAL_CLEANUP_PY = _MODULE_DIR.parent.parent / "tools" / "cleanup-codex-stale.py"
_LOCAL_CLEANUP_SH = _MODULE_DIR.parent.parent / "tools" / "cleanup-codex-stale.sh"

if IS_WINDOWS:
    PROXY = _LOCAL_PROXY if _LOCAL_PROXY.exists() else BIN_DIR / "translate-proxy.py"
    CLEANUP = _LOCAL_CLEANUP_PY if _LOCAL_CLEANUP_PY.exists() else BIN_DIR / "cleanup-codex-stale.py"
    START_SH = None
else:
    PROXY = _LOCAL_PROXY if _LOCAL_PROXY.exists() else BIN_DIR / "translate-proxy.py"
    CLEANUP = _LOCAL_CLEANUP_SH if _LOCAL_CLEANUP_SH.exists() else BIN_DIR / "cleanup-codex-stale.sh"
    START_SH = Path("/opt/codex-desktop/start.sh")

DEFAULT_CONFIG = """model = ""
model_provider = ""
model_catalog_json = ""
"""

if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))
try:
    import universal_runtime as _universal_runtime
except (ImportError, ModuleNotFoundError):
    _universal_runtime = None


def detect_runtime_environment():
    if _universal_runtime is None:
        return {"profile": "unknown", "fallback_mode": "builtin"}
    return _universal_runtime.detect_environment()


def build_cross_platform_profile(mode="basic", overrides=None):
    if _universal_runtime is None:
        return {"profile": "legacy", "mode": mode, "overrides": overrides or {}}
    return _universal_runtime.build_runtime_profile(mode=mode, overrides=overrides)


def run_doctor_plus():
    if _universal_runtime is None:
        return {"health": "unknown", "checks": []}
    deps = ["python3" if not IS_WINDOWS else "python", "curl"]
    return _universal_runtime.doctor_plus(deps, [CONFIG, ENDPOINTS_FILE, PROXY_CONFIG_DIR / "probe"])


def choose_policy_route(routes, policy=None):
    if _universal_runtime is None:
        return routes[0] if routes else {}
    return _universal_runtime.select_policy_route(routes, policy=policy)


def create_session_portability_pack(destination, metadata=None, files=None):
    if _universal_runtime is None:
        raise RuntimeError("universal runtime unavailable")
    return _universal_runtime.export_session_pack(Path(destination), metadata or {}, [Path(p) for p in (files or [])])


def restore_session_portability_pack(bundle_path, destination_dir):
    if _universal_runtime is None:
        raise RuntimeError("universal runtime unavailable")
    return _universal_runtime.import_session_pack(Path(bundle_path), Path(destination_dir))

