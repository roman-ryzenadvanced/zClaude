"""Bootstrap — directory creation, default endpoints, usage stats."""
import json
import os
import sys
from lib.constants import (
    CONFIG_DIR, ENDPOINTS_FILE, LOG_DIR, PROXY_CONFIG_DIR,
    _USAGE_STATS_FILE, UA,
)
from lib.endpoints import save_endpoints

# ═══════════════════════════════════════════════════════════════════════
# Usage stats
# ═══════════════════════════════════════════════════════════════════════

def load_usage_stats():
    try:
        if _USAGE_STATS_FILE.exists():
            return json.loads(_USAGE_STATS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[lib] failed to load usage stats: {exc}", file=sys.stderr)
    return {"providers": {}, "updated": None}

# ═══════════════════════════════════════════════════════════════════════
# Default endpoints creation
# ═══════════════════════════════════════════════════════════════════════

def create_default_endpoints():
    if not ENDPOINTS_FILE.exists():
        save_endpoints({
            "default": "OpenAI",
            "endpoints": [
                {"name": "OpenAI", "backend_type": "native", "base_url": "https://api.openai.com/v1",
                 "api_key": "", "default_model": "gpt-4o", "models": ["gpt-4o", "gpt-4o-mini"],
                 "provider_preset": "OpenAI"},
                {"name": "Z.AI", "backend_type": "openai-compat",
                 "base_url": "https://api.z.ai/api/coding/paas/v4",
                 "api_key": "", "default_model": "glm-5.1",
                 "models": ["glm-4.5", "glm-4.5-air", "glm-4.6", "glm-4.7", "glm-5", "glm-5-turbo", "glm-5.1"],
                 "provider_preset": "Custom"},
            ],
        })


def ensure_dirs():
    for d in [LOG_DIR, PROXY_CONFIG_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    try:
        from lib.cleanup import cleanup_log_dir
        cleanup_log_dir(str(LOG_DIR))
    except Exception:
        pass
