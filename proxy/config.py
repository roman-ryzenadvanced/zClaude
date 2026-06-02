"""Proxy configuration — centralized mutable state and constants."""
import argparse
import collections
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

__all__ = [
    'ADAPTIVE_COMPACT', 'API_KEY', 'AUTO_COMPACT', 'BACKEND', 'BGP_ROUTES',
    'CAVEMAN_MODE', 'CC_VERSION', 'CONFIG', 'CircuitBreaker', 'DEFAULT_MODELS',
    'FORCE_MODEL', 'KIRO_AUTH_SERVICE', 'KIRO_ISSUER_URL', 'KIRO_SCOPES', 'KIRO_SSO_OIDC_ENDPOINT',
    'KIRO_START_URL', 'KIRO_TARGET_HEADER', 'MODELS', 'OAUTH_PROVIDER', 'PORT',
    'PROMPT_ENHANCER', 'PROMPT_ENHANCER_KEY', 'PROMPT_ENHANCER_MODE', 'PROMPT_ENHANCER_MODEL', 'PROMPT_ENHANCER_URL',
    'REASONING_EFFORT', 'REASONING_ENABLED', 'RTK_COMPRESSION', 'SERVER', 'TARGET_URL',
    'TOOL_OUTPUT_TRUNCATION', 'VISION_FALLBACK_KEY', 'VISION_FALLBACK_MODEL', 'VISION_FALLBACK_URL',
    '_CODEBUFF_AGENT_MAP', '_CODEBUFF_API_URL', '_CODEBUFF_AUTH_URL', '_CODEBUFF_CREDS_PATH',
    '_CONFIG_MTIME', '_CONFIG_PATH', '_DEFAULT_MODEL_PROFILE', '_GRPC_FALLBACK_REST_ERRORS',
    '_GRPC_REVERSE_ALIAS', '_HOT_RELOAD_LOCK', '_IS_WINDOWS', '_LOG_DIR', '_LOG_FILE',
    '_LOG_FILE_LOCK', '_MAX_CONCURRENT_REQUESTS', '_MAX_DS_STORED', '_MAX_STORED', '_MODEL_PROFILES',
    '_REQUESTS_DIR', '_RESPONSE_TTL', '_STATS', '_STATS_FLUSH_INTERVAL', '_STREAM_IDLE_TIMEOUT',
    '_active_connections', '_active_connections_lock', '_active_requests', '_active_requests_lock',
    '_antigravity_endpoint_lock', '_antigravity_grpc_available', '_antigravity_grpc_client',
    '_antigravity_preferred_endpoint', '_antigravity_version', '_antigravity_version_checked',
    '_antigravity_version_lock', '_antigravity_version_validated', '_api_key_pool', '_cb_pool',
    '_circuit_breakers', '_circuit_breakers_lock', '_codebuff_session_cache', '_codebuff_token_cache',
    '_codebuff_token_lock', '_conn_pool', '_conn_pool_lock', '_crof_lock', '_deepseek_reasoning_lock',
    '_deepseek_reasoning_store', '_fb_reasoning_store', '_fb_reasoning_store_lock', '_get_grpc_client',
    '_google_antigravity_pool', '_google_cli_pool', '_idle_timeout_for_model', '_last_reasoning_lock',
    '_last_reasoning_store', '_last_user_urls', '_model_profile', '_provider_caps',
    '_provider_caps_lock', '_provider_caps_path', '_request_semaphore', '_response_store',
    '_response_store_lock', '_shutdown_requested', '_stats_flush_timer', '_stats_lock',
    '_stats_path', '_stats_pending', 'get_circuit_breaker', 'load_config',
    '_init_runtime', '_hot_reload_api_key', '_verify_api_key',
    '_auto_detect_vision_fallback', '_VISION_MODEL_KEYWORDS',
]


_IS_WINDOWS = sys.platform == "win32"

# ═══════════════════════════════════════════════════════════════════
# Lazy gRPC import for Antigravity fallback
# ═══════════════════════════════════════════════════════════════════
_antigravity_grpc_client = None
_antigravity_grpc_available = None

def _get_grpc_client():
    """Lazy-load the Antigravity gRPC client. Returns None if grpcio is not installed."""
    global _antigravity_grpc_client, _antigravity_grpc_available
    if _antigravity_grpc_available is False:
        return None
    if _antigravity_grpc_client is not None:
        return _antigravity_grpc_client
    try:
        _src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _src_dir not in sys.path:
            sys.path.insert(0, _src_dir)
        from antigravity_grpc import is_grpc_available, AntigravityGrpcClient, get_client
        if is_grpc_available():
            _antigravity_grpc_client = get_client()
            _antigravity_grpc_available = True
            print("[antigravity-grpc] gRPC fallback module loaded OK", file=sys.stderr)
            return _antigravity_grpc_client
        else:
            _antigravity_grpc_available = False
            print("[antigravity-grpc] grpcio available but stubs failed to load, gRPC fallback disabled", file=sys.stderr)
            return None
    except ImportError as e:
        _antigravity_grpc_available = False
        print(f"[antigravity-grpc] grpcio not installed ({e}), gRPC fallback disabled", file=sys.stderr)
        return None

# Reverse alias map: REST slug → gRPC display name
_GRPC_REVERSE_ALIAS = {
    "gemini-3-flash": "Gemini 3.5 Flash (High)",
    "gemini-3.5-flash-low": "Gemini 3.5 Flash (Low)",
    "gemini-3.1-pro-low": "Gemini 3.1 Pro (High)",
    "claude-sonnet-4-6": "Claude Sonnet 4.6 (Thinking)",
    "claude-opus-4-6-thinking": "Claude Opus 4.6 (Thinking)",
    "gpt-oss-120b-medium": "GPT-OSS 120B (Medium)",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-2.5-pro": "Gemini 2.5 Pro",
    "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite",
}

# Errors from REST that should trigger gRPC fallback
_GRPC_FALLBACK_REST_ERRORS = {404}

# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════

DEFAULT_MODELS = {
    "openai-compat": [
        {"id": "gpt-4o-mini", "object": "model", "created": 1700000000, "owned_by": "custom"},
    ],
    "anthropic": [
        {"id": "claude-sonnet-4-20250514", "object": "model", "created": 1700000000, "owned_by": "anthropic"},
    ],
    "codebuff": [
        {"id": "deepseek/deepseek-v4-pro", "object": "model", "created": 1700000000, "owned_by": "codebuff"},
        {"id": "deepseek/deepseek-v4-flash", "object": "model", "created": 1700000000, "owned_by": "codebuff"},
        {"id": "moonshotai/kimi-k2.6", "object": "model", "created": 1700000000, "owned_by": "codebuff"},
        {"id": "minimax/minimax-m2.7", "object": "model", "created": 1700000000, "owned_by": "codebuff"},
    ],
    "auto": [
        {"id": "default-model", "object": "model", "created": 1700000000, "owned_by": "auto"},
    ],
}

def load_config():
    global _CONFIG_PATH, _CONFIG_MTIME
    p = argparse.ArgumentParser(description="Responses API translation proxy")
    p.add_argument("--config", help="JSON config file path")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--host", default=None, help="Bind address (default: 127.0.0.1, use 0.0.0.0 for Docker)")
    p.add_argument("--backend", default=None)
    p.add_argument("--target-url", default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--models-file", default=None, help="JSON file with model list array")
    _args = p.parse_args()

    cfg = {}
    if _args.config:
        _CONFIG_PATH = os.path.abspath(_args.config)
        with open(_args.config) as f:
            cfg = json.load(f)
        try:
            _CONFIG_MTIME = os.path.getmtime(_CONFIG_PATH)
        except OSError:
            pass

    for ck, ak in [("port", "port"), ("backend_type", "backend"),
                    ("target_url", "target_url"), ("api_key", "api_key")]:
        v = getattr(_args, ak, None)
        if v is not None:
            cfg[ck] = v

    env_map = {
        "port": ("PROXY_PORT", "ZAI_PROXY_PORT", int),
        "backend_type": ("PROXY_BACKEND", None, str),
        "target_url": ("PROXY_TARGET_URL", "ZAI_BASE_URL", str),
        "api_key": ("PROXY_API_KEY", "ZAI_API_KEY", str),
        "vision_fallback_url": ("VISION_FALLBACK_URL", None, str),
        "vision_fallback_model": ("VISION_FALLBACK_MODEL", None, str),
        "vision_fallback_key": ("VISION_FALLBACK_KEY", None, str),
    }
    for ck, (ev1, ev2, conv) in env_map.items():
        if ck not in cfg:
            v = os.environ.get(ev1) or (os.environ.get(ev2) if ev2 else None)
            if v:
                cfg[ck] = conv(v) if conv == int else v

    cfg.setdefault("port", 8080)
    cfg.setdefault("backend_type", "openai-compat")
    cfg.setdefault("target_url", "http://localhost:11434/v1")
    cfg.setdefault("api_key", "")

    models = cfg.get("models", [])
    if not models and _args.models_file:
        with open(_args.models_file) as f:
            models = json.load(f)
    if not models:
        models = DEFAULT_MODELS.get(cfg["backend_type"], [])
    cfg["models"] = models

    return cfg

# ═══════════════════════════════════════════════════════════════════
# Runtime configuration globals (set by _init_runtime)
# ═══════════════════════════════════════════════════════════════════

CONFIG = None
_CONFIG_PATH = None
_CONFIG_MTIME = 0
PORT = 8080
BACKEND = "openai-compat"
TARGET_URL = ""
API_KEY = ""
OAUTH_PROVIDER = ""
MODELS = []
CC_VERSION = ""
REASONING_ENABLED = True
REASONING_EFFORT = "medium"
FORCE_MODEL = ""
BGP_ROUTES = []
CAVEMAN_MODE = False
RTK_COMPRESSION = False
AUTO_COMPACT = False
ADAPTIVE_COMPACT = False
TOOL_OUTPUT_TRUNCATION = True
PROMPT_ENHANCER = False
PROMPT_ENHANCER_MODE = "offline"
PROMPT_ENHANCER_MODEL = ""
PROMPT_ENHANCER_URL = ""
PROMPT_ENHANCER_KEY = ""
VISION_FALLBACK_URL = ""
VISION_FALLBACK_MODEL = ""
VISION_FALLBACK_KEY = ""
SERVER = None

# ═══════════════════════════════════════════════════════════════════
# Log directory setup
# ═══════════════════════════════════════════════════════════════════

if _IS_WINDOWS:
    _LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "codex-proxy")
else:
    _LOG_DIR = os.path.join(os.path.expanduser("~"), ".cache", "codex-proxy")
os.makedirs(_LOG_DIR, exist_ok=True)
_REQUESTS_DIR = os.path.join(_LOG_DIR, "requests")
os.makedirs(_REQUESTS_DIR, exist_ok=True)
try:
    for _f in os.listdir(_REQUESTS_DIR):
        if _f.endswith(".tmp"):
            os.remove(os.path.join(_REQUESTS_DIR, _f))
except Exception:
    pass

# ═══════════════════════════════════════════════════════════════════
# Kiro (AWS CodeWhisperer) constants
# ═══════════════════════════════════════════════════════════════════

KIRO_SSO_OIDC_ENDPOINT = "https://oidc.us-east-1.amazonaws.com"
KIRO_START_URL = "https://view.awsapps.com/start"
KIRO_AUTH_SERVICE = "https://prod.us-east-1.auth.desktop.kiro.dev"
KIRO_SCOPES = ["codewhisperer:completions", "codewhisperer:analysis", "codewhisperer:conversations"]
KIRO_ISSUER_URL = "https://identitycenter.amazonaws.com/ssoins-722374e8c3c8e6c6"
KIRO_TARGET_HEADER = "AmazonCodeWhispererStreamingService.GenerateAssistantResponse"

# ═══════════════════════════════════════════════════════════════════
# Stats / response store / locks
# ═══════════════════════════════════════════════════════════════════

_stats_path = os.path.join(_LOG_DIR, "usage-stats.json")
_provider_caps_path = os.path.join(_LOG_DIR, "provider-caps.json")
_stats_lock = threading.Lock()
_stats_pending = []
_stats_flush_timer = None
_STATS_FLUSH_INTERVAL = 5.0
_STATS = {}

try:
    _LOG_FILE = open(os.path.join(_LOG_DIR, "proxy.log"), "a", encoding="utf-8")
except Exception:
    _LOG_FILE = None
_LOG_FILE_LOCK = threading.Lock()

_response_store = collections.OrderedDict()
_response_store_lock = threading.Lock()
_MAX_STORED = 50
_RESPONSE_TTL = 600

_fb_reasoning_store = collections.OrderedDict()
_fb_reasoning_store_lock = threading.Lock()

_deepseek_reasoning_store = {}
_deepseek_reasoning_lock = threading.Lock()
_MAX_DS_STORED = 100

_last_reasoning_store = {}
_last_reasoning_lock = threading.Lock()

_crof_lock = threading.Lock()
_provider_caps_lock = threading.Lock()
_provider_caps = None

_shutdown_requested = False
_active_connections = 0
_active_connections_lock = threading.Lock()
_active_requests = {}
_active_requests_lock = threading.Lock()

_antigravity_version = "2.0.1"
_antigravity_version_checked = 0
_antigravity_version_lock = threading.Lock()
_antigravity_version_validated = False

# ═══════════════════════════════════════════════════════════════════
# Circuit Breaker
# ═══════════════════════════════════════════════════════════════════

class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.last_state_change = time.time()
        self.lock = threading.Lock()

    def can_execute(self):
        with self.lock:
            now = time.time()
            if self.state == "OPEN":
                if now - self.last_state_change > self.recovery_timeout:
                    self.state = "HALF_OPEN"
                    self.last_state_change = now
                    print(f"[circuit-breaker] Circuit transitioning from OPEN to HALF_OPEN", file=sys.stderr)
                    return True
                return False
            return True

    def record_success(self):
        with self.lock:
            if self.state == "HALF_OPEN":
                print(f"[circuit-breaker] Circuit transitioning from HALF_OPEN to CLOSED (success)", file=sys.stderr)
            self.failure_count = 0
            self.state = "CLOSED"
            self.last_state_change = time.time()

    def record_failure(self):
        with self.lock:
            self.failure_count += 1
            now = time.time()
            if self.state in ("CLOSED", "HALF_OPEN"):
                if self.failure_count >= self.failure_threshold or self.state == "HALF_OPEN":
                    self.state = "OPEN"
                    self.last_state_change = now
                    print(f"[circuit-breaker] Circuit tripped! Transitioning to OPEN. Failure count: {self.failure_count}", file=sys.stderr)

_circuit_breakers = {}
_circuit_breakers_lock = threading.Lock()

def get_circuit_breaker(backend_name):
    with _circuit_breakers_lock:
        if backend_name not in _circuit_breakers:
            _circuit_breakers[backend_name] = CircuitBreaker()
        return _circuit_breakers[backend_name]

# ═══════════════════════════════════════════════════════════════════
# Connection pool / stream timeout
# ═══════════════════════════════════════════════════════════════════

_last_user_urls = collections.deque(maxlen=20)

_conn_pool_lock = threading.Lock()
_conn_pool = {}

_STREAM_IDLE_TIMEOUT = 300

def _idle_timeout_for_model(model, default=300):
    return _model_profile(model).get("idle_timeout", default)

# ═══════════════════════════════════════════════════════════════════
# Model profiles
# ═══════════════════════════════════════════════════════════════════

_MODEL_PROFILES = {
    "flash": {
        "idle_timeout": 120, "max_tool_calls": 100, "warn_tool_calls": 60,
        "max_reads_no_write": 10, "warn_reads_no_write": 6,
        "max_input_items": 120, "tool_output_limit": 8000, "compaction": "balanced",
        "reasoning_budget": 8192, "max_tokens": 65536,
    },
    "gemini-3.5-flash": {
        "idle_timeout": 120, "max_tool_calls": 100, "warn_tool_calls": 60,
        "max_reads_no_write": 10, "warn_reads_no_write": 6,
        "max_input_items": 120, "tool_output_limit": 8000, "compaction": "balanced",
        "reasoning_budget": 8192, "max_tokens": 65536,
    },
    "gemini-3.1-pro": {
        "idle_timeout": 300, "max_tool_calls": 150, "warn_tool_calls": 80,
        "max_reads_no_write": 12, "warn_reads_no_write": 8,
        "max_input_items": 200, "tool_output_limit": 8000, "compaction": "conservative",
        "reasoning_budget": 24576, "max_tokens": 65536,
    },
    "pro": {
        "idle_timeout": 300, "max_tool_calls": 150, "warn_tool_calls": 80,
        "max_reads_no_write": 12, "warn_reads_no_write": 8,
        "max_input_items": 200, "tool_output_limit": 8000, "compaction": "conservative",
        "reasoning_budget": 24576, "max_tokens": 65536,
    },
    "sonnet": {
        "idle_timeout": 300, "max_tool_calls": 150, "warn_tool_calls": 80,
        "max_reads_no_write": 10, "warn_reads_no_write": 7,
        "max_input_items": 180, "tool_output_limit": 8000, "compaction": "balanced",
        "reasoning_budget": 16384, "max_tokens": 65536,
    },
    "opus": {
        "idle_timeout": 600, "max_tool_calls": 200, "warn_tool_calls": 100,
        "max_reads_no_write": 8, "warn_reads_no_write": 5,
        "max_input_items": 250, "tool_output_limit": 10000, "compaction": "conservative",
        "reasoning_budget": 32768, "max_tokens": 131072,
    },
    "deepseek": {
        "idle_timeout": 300, "max_tool_calls": 120, "warn_tool_calls": 70,
        "max_reads_no_write": 10, "warn_reads_no_write": 7,
        "max_input_items": 150, "tool_output_limit": 6000, "compaction": "balanced",
        "reasoning_budget": 16384, "max_tokens": 65536,
    },
    "qwen": {
        "idle_timeout": 300, "max_tool_calls": 120, "warn_tool_calls": 70,
        "max_reads_no_write": 10, "warn_reads_no_write": 7,
        "max_input_items": 150, "tool_output_limit": 6000, "compaction": "balanced",
        "reasoning_budget": 16384, "max_tokens": 65536,
    },
    "gpt-oss": {
        "idle_timeout": 300, "max_tool_calls": 100, "warn_tool_calls": 60,
        "max_reads_no_write": 10, "warn_reads_no_write": 6,
        "max_input_items": 120, "tool_output_limit": 6000, "compaction": "balanced",
        "reasoning_budget": 8192, "max_tokens": 32768,
    },
}

_DEFAULT_MODEL_PROFILE = {
    "idle_timeout": 300, "max_tool_calls": 150, "warn_tool_calls": 80,
    "max_reads_no_write": 12, "warn_reads_no_write": 8,
    "max_input_items": 150, "tool_output_limit": 6000, "compaction": "balanced",
    "reasoning_budget": 16384, "max_tokens": 65536,
}

def _model_profile(model):
    if not model:
        return dict(_DEFAULT_MODEL_PROFILE)
    m = model.lower().replace("-", "").replace("_", "").replace(" ", "")
    for key, profile in _MODEL_PROFILES.items():
        key_norm = key.replace("-", "").replace("_", "").replace(" ", "")
        if key_norm in m:
            return dict(profile)
    if "flash" in m or "mini" in m or "haiku" in m or "tiny" in m:
        return dict(_MODEL_PROFILES["flash"])
    if "opus" in m or "ultra" in m:
        return dict(_MODEL_PROFILES["opus"])
    if "sonnet" in m:
        return dict(_MODEL_PROFILES["sonnet"])
    if "pro" in m and "flash" not in m:
        return dict(_MODEL_PROFILES["pro"])
    return dict(_DEFAULT_MODEL_PROFILE)

_MAX_CONCURRENT_REQUESTS = 3
_request_semaphore = threading.Semaphore(_MAX_CONCURRENT_REQUESTS)

# ═══════════════════════════════════════════════════════════════════
# Codebuff constants
# ═══════════════════════════════════════════════════════════════════

_CODEBUFF_AUTH_URL = "https://www.codebuff.com"
_CODEBUFF_API_URL = "https://www.codebuff.com"
_CODEBUFF_AGENT_MAP = {
    "deepseek/deepseek-v4-pro": "base2-free-deepseek",
    "deepseek/deepseek-v4-flash": "base2-free-deepseek-flash",
    "moonshotai/kimi-k2.6": "base2-free-kimi",
    "minimax/minimax-m2.7": "base2-free",
}
if _IS_WINDOWS:
    _CODEBUFF_CREDS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "manicode", "credentials.json")
else:
    _CODEBUFF_CREDS_PATH = os.path.join(os.path.expanduser("~"), ".config", "manicode", "credentials.json")
_codebuff_token_cache = {"token": None, "checked": 0}
_codebuff_session_cache = {"instance_id": None, "expires": 0, "model": None}
_codebuff_token_lock = threading.Lock()

# Account pool instances
_cb_pool = None  # initialized in _init_runtime
_google_antigravity_pool = None  # initialized later
_google_cli_pool = None  # initialized later
_api_key_pool = None  # initialized in _init_runtime
_antigravity_preferred_endpoint = None
_antigravity_endpoint_lock = threading.Lock()
_HOT_RELOAD_LOCK = threading.Lock()

# ── Runtime initialization (moved from translate-proxy.py) ────────────

_VISION_MODEL_KEYWORDS = ("vl", "vision", "gpt-4o", "gpt-5", "claude-3", "claude-4", "gemini", "qwen-vl", "kimi-vl", "pixtral", "llava")


def _auto_detect_vision_fallback(target_url, api_key, models):
    """Auto-detect a vision-capable model from the current provider for image description."""
    base = target_url.rstrip("/")
    if "/v1" in base:
        chat_url = base.split("/v1")[0] + "/v1/chat/completions"
    else:
        chat_url = base + "/v1/chat/completions"
    vision_model = ""
    for m in (models or []):
        if isinstance(m, dict):
            m = m.get("name", m.get("id", str(m)))
        if not isinstance(m, str):
            continue
        ml = m.lower()
        if any(kw in ml for kw in _VISION_MODEL_KEYWORDS):
            vision_model = m
            break
    if not vision_model:
        return "", "", ""
    return chat_url, vision_model, api_key


def _init_runtime():
    global CONFIG, PORT, BACKEND, TARGET_URL, API_KEY, OAUTH_PROVIDER, _antigravity_version
    global MODELS, CC_VERSION, REASONING_ENABLED, REASONING_EFFORT, BGP_ROUTES
    global _api_key_pool, PROMPT_ENHANCER, FORCE_MODEL, PROMPT_ENHANCER_MODE
    global PROMPT_ENHANCER_MODEL, PROMPT_ENHANCER_URL, PROMPT_ENHANCER_KEY
    global VISION_FALLBACK_URL, VISION_FALLBACK_MODEL, VISION_FALLBACK_KEY
    global CAVEMAN_MODE, RTK_COMPRESSION, AUTO_COMPACT, ADAPTIVE_COMPACT, TOOL_OUTPUT_TRUNCATION
    global _cb_pool, _google_antigravity_pool, _google_cli_pool

    from proxy.auth_pools import CodebuffAccountPool, GoogleAccountPool
    if _cb_pool is None:
        _cb_pool = CodebuffAccountPool("codebuff")
    if _google_antigravity_pool is None:
        _google_antigravity_pool = GoogleAccountPool("antigravity")
    if _google_cli_pool is None:
        _google_cli_pool = GoogleAccountPool("cli")

    CONFIG = load_config()
    from proxy.logging_utils import _init_logging
    _init_logging(_REQUESTS_DIR)
    PORT = CONFIG["port"]
    BACKEND = CONFIG["backend_type"]
    TARGET_URL = CONFIG["target_url"].rstrip("/")
    API_KEY = CONFIG["api_key"]
    OAUTH_PROVIDER = CONFIG.get("oauth_provider") or ""
    if not OAUTH_PROVIDER and BACKEND == "gemini-oauth-antigravity":
        OAUTH_PROVIDER = "google-antigravity"
    if not OAUTH_PROVIDER and BACKEND == "gemini-oauth":
        OAUTH_PROVIDER = "google-cli"
    if not OAUTH_PROVIDER and BACKEND == "kiro-oauth":
        OAUTH_PROVIDER = "kiro"
    MODELS = CONFIG["models"]
    CC_VERSION = CONFIG.get("cc_version", "")
    REASONING_ENABLED = CONFIG.get("reasoning_enabled", True)
    REASONING_EFFORT = CONFIG.get("reasoning_effort", "medium")
    FORCE_MODEL = (CONFIG.get("force_model") or "").strip()
    PROMPT_ENHANCER = CONFIG.get("prompt_enhancer", False)
    PROMPT_ENHANCER_MODE = CONFIG.get("prompt_enhancer_mode", "offline")
    PROMPT_ENHANCER_MODEL = CONFIG.get("prompt_enhancer_model", "")
    PROMPT_ENHANCER_URL = CONFIG.get("prompt_enhancer_url", "")
    PROMPT_ENHANCER_KEY = CONFIG.get("prompt_enhancer_key", "")
    VISION_FALLBACK_URL = CONFIG.get("vision_fallback_url") or ""
    VISION_FALLBACK_MODEL = CONFIG.get("vision_fallback_model") or ""
    VISION_FALLBACK_KEY = CONFIG.get("vision_fallback_key") or ""
    if not VISION_FALLBACK_URL or not VISION_FALLBACK_MODEL:
        _vision_url, _vision_model, _vision_key = _auto_detect_vision_fallback(TARGET_URL, API_KEY, MODELS)
        if not VISION_FALLBACK_URL:
            VISION_FALLBACK_URL = _vision_url
        if not VISION_FALLBACK_MODEL:
            VISION_FALLBACK_MODEL = _vision_model
        if not VISION_FALLBACK_KEY:
            VISION_FALLBACK_KEY = _vision_key
    BGP_ROUTES = CONFIG.get("bgp_routes", [])
    CAVEMAN_MODE = CONFIG.get("caveman_mode", False) or os.environ.get("CAVEMAN_MODE") == "1"
    RTK_COMPRESSION = CONFIG.get("rtk_compression", False) or os.environ.get("RTK_COMPRESSION") == "1"
    AUTO_COMPACT = CONFIG.get("auto_compact", False) or os.environ.get("AUTO_COMPACT") == "1"
    ADAPTIVE_COMPACT = CONFIG.get("adaptive_compact", False) or os.environ.get("ADAPTIVE_COMPACT") == "1"
    TOOL_OUTPUT_TRUNCATION = CONFIG.get("tool_output_truncation", True)
    if os.environ.get("TOOL_OUTPUT_TRUNCATION") == "0":
        TOOL_OUTPUT_TRUNCATION = False
    _api_key_pool = None
    if API_KEY and "," in API_KEY and not OAUTH_PROVIDER.startswith("google") and BACKEND not in ("codebuff", "freebuff"):
        from proxy.auth_pools import APIKeyPool
        _api_key_pool = APIKeyPool(BACKEND, API_KEY)
        print(f"[multi-account] API key pool: {len(_api_key_pool._accounts)} keys for {BACKEND}", file=sys.stderr)
    if OAUTH_PROVIDER == "google-antigravity":
        from proxy.adapters.gemini_helpers import _ensure_antigravity_version
        _antigravity_version = _ensure_antigravity_version()
        print(f"[antigravity] version={_antigravity_version}", file=sys.stderr)

    # ── BGP route model synthesis ──
    bgp_models = []
    for _r in BGP_ROUTES:
        for _m in _r.get("models", [{"id": _r.get("model", "unknown")}]):
            mid = _m.get("id", _m) if isinstance(_m, dict) else _m
            if mid not in bgp_models:
                bgp_models.append(mid)
    if BGP_ROUTES and not MODELS:
        MODELS = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "bgp"} for m in bgp_models]
        CONFIG["models"] = MODELS

    # ── Gemini OAuth: discovered models from token file ──
    if (BACKEND or "").startswith("gemini-oauth") and (OAUTH_PROVIDER or "").startswith("google"):
        token_name = "google-antigravity-oauth-token.json" if OAUTH_PROVIDER == "google-antigravity" else "google-cli-oauth-token.json"
        token_path = os.path.join(_LOG_DIR, token_name)
        try:
            with open(token_path) as _tf:
                _td = json.load(_tf)
            _discovered = [] if OAUTH_PROVIDER == "google-antigravity" else _td.get("available_models", [])
            if _discovered:
                _seen = []
                for _m in _discovered:
                    if _m not in _seen:
                        _seen.append(_m)
                MODELS = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "gemini-oauth"} for m in _seen]
                CONFIG["models"] = MODELS
                print(f"[gemini-oauth] loaded {len(_seen)} discovered models: {_seen}", file=sys.stderr)
            # Preemptive token refresh check
            expires_at = _td.get("expires_at", 0)
            if expires_at and time.time() > expires_at - 300:
                print(f"[oauth] preemptive refresh: token expires in {int(expires_at - time.time())}s", file=sys.stderr)
        except Exception:
            pass

    # ── Kiro: fetch models dynamically via ListAvailableModels ──
    if BACKEND == "kiro-oauth" or OAUTH_PROVIDER == "kiro":
        _kiro_token_path = os.path.join(_LOG_DIR, "kiro-oauth-token.json")
        try:
            with open(_kiro_token_path, encoding="utf-8") as _ktf:
                _ktd = json.load(_ktf)
            _kaccess = _ktd.get("access_token", "")
            if _kaccess:
                _kprofile = _ktd.get("profileArn", "")
                _kregion = "us-east-1"
                if _kprofile and ":" in _kprofile:
                    _kparts = _kprofile.split(":")
                    if len(_kparts) >= 4 and _kparts[3]:
                        _kregion = _kparts[3]
                # Fetch model catalog from Kiro API
                _kparams = f"origin=AI_EDITOR"
                if _kprofile:
                    _kparams += f"&profileArn={urllib.parse.quote(_kprofile)}"
                _kurl = f"https://q.{_kregion}.amazonaws.com/ListAvailableModels?{_kparams}"
                _kseed = _ktd.get("client_id", "") or _ktd.get("refresh_token", "") or _kaccess
                _kmachine_id = hashlib.sha256(_kseed.encode()).hexdigest()
                _kua = (f"aws-sdk-js/1.0.0 ua/2.1 os/windows#10.0.26200 "
                        f"lang/js md/nodejs#22.21.1 api/codewhispererruntime#1.0.0 "
                        f"m/N,E KiroIDE-0.10.32-{_kmachine_id}")
                _kreq = urllib.request.Request(_kurl, headers={
                    "Authorization": f"Bearer {_kaccess}",
                    "User-Agent": _kua,
                    "Accept": "application/json",
                })
                _kresp = urllib.request.urlopen(_kreq, timeout=30)
                _kdata = json.loads(_kresp.read())
                _kraw_models = _kdata.get("models", []) if isinstance(_kdata, dict) else []
                if _kraw_models:
                    _kseen = []
                    for _km in _kraw_models:
                        if not isinstance(_km, dict):
                            continue
                        _mid = _km.get("modelId") or _km.get("id", "")
                        if _mid and _mid not in _kseen:
                            _kseen.append(_mid)
                    if _kseen:
                        MODELS = [{"id": m, "object": "model", "created": 1700000000, "owned_by": "kiro"} for m in _kseen]
                        CONFIG["models"] = MODELS
                        print(f"[kiro] loaded {len(_kseen)} models from ListAvailableModels: {_kseen}", file=sys.stderr)
        except Exception as _ke:
            print(f"[kiro] model fetch failed: {_ke}", file=sys.stderr)

    # ── Propagate mutable globals to star-importing modules ──
    # from X import * creates copies of references; reassigning a global
    # here does NOT update the copies in other modules.  This block
    # synchronises the key mutable names so that proxy.compaction,
    # proxy.shared_utils, etc. see the current runtime values.
    _mutable_names = (
        'RTK_COMPRESSION', 'CAVEMAN_MODE', 'AUTO_COMPACT', 'ADAPTIVE_COMPACT',
        'TOOL_OUTPUT_TRUNCATION', 'PROMPT_ENHANCER', 'REASONING_ENABLED',
        'REASONING_EFFORT', 'FORCE_MODEL', 'MODELS', 'CONFIG', 'PORT',
        'BACKEND', 'TARGET_URL', 'API_KEY', 'OAUTH_PROVIDER',
        'VISION_FALLBACK_URL', 'VISION_FALLBACK_MODEL', 'VISION_FALLBACK_KEY',
        'BGP_ROUTES', 'SERVER', 'CC_VERSION',
    )
    for _mod_name, _mod in list(sys.modules.items()):
        if (_mod_name.startswith("proxy.") or _mod_name == "proxy") and _mod is not None:
            for _name in _mutable_names:
                try:
                    if hasattr(_mod, _name):
                        setattr(_mod, _name, globals()[_name])
                except Exception:
                    pass


def _verify_api_key(key, target_url):
    if not key or not target_url:
        return {"valid": False, "error": "missing key or url"}
    from proxy.shared_utils import upstream_target
    test_url = upstream_target(target_url, "/models")
    if not test_url:
        return {"valid": False, "error": "invalid target url"}
    try:
        req = urllib.request.Request(test_url, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })
        resp = urllib.request.urlopen(req, timeout=10)
        body = resp.read().decode()
        model_count = 0
        try:
            data = json.loads(body)
            model_count = len(data.get("data", []))
        except Exception:
            pass
        return {"valid": True, "status": resp.status, "models": model_count}
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:200]
        return {"valid": False, "status": e.code, "error": err}
    except Exception as e:
        return {"valid": False, "error": str(e)[:200]}


def _hot_reload_api_key():
    global API_KEY, _api_key_pool, _CONFIG_MTIME
    if not _CONFIG_PATH:
        return False
    try:
        cur_mtime = os.path.getmtime(_CONFIG_PATH)
    except OSError:
        return False
    if cur_mtime <= _CONFIG_MTIME:
        return False
    with _HOT_RELOAD_LOCK:
        try:
            cur_mtime2 = os.path.getmtime(_CONFIG_PATH)
            if cur_mtime2 <= _CONFIG_MTIME:
                return False
            with open(_CONFIG_PATH) as f:
                new_cfg = json.load(f)
            new_key = (new_cfg.get("api_key") or "").strip()
            if not new_key or new_key == API_KEY:
                _CONFIG_MTIME = cur_mtime2
                return False
            old_preview = API_KEY[:8] + "..." if len(API_KEY) > 8 else "(empty)"
            new_preview = new_key[:8] + "..." if len(new_key) > 8 else "(empty)"
            API_KEY = new_key
            _CONFIG_MTIME = cur_mtime2
            if API_KEY and "," in API_KEY and not OAUTH_PROVIDER.startswith("google") and BACKEND not in ("codebuff", "freebuff"):
                from proxy.auth_pools import APIKeyPool
                _api_key_pool = APIKeyPool(BACKEND, API_KEY)
                print(f"[hot-reload] API key pool refreshed: {len(_api_key_pool._accounts)} keys", file=sys.stderr)
            print(f"[hot-reload] API key updated: {old_preview} -> {new_preview}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"[hot-reload] error: {e}", file=sys.stderr)
