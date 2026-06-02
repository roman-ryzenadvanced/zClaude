"""OAuth secrets management — load/save."""
import json
import os
from lib.constants import OAUTH_SECRETS_PATH, GEMINI_OAUTH_CLIENT_ID, GEMINI_OAUTH_CLIENT_SECRET

# ═══════════════════════════════════════════════════════════════════════
# OAuth secrets (local, never in repo)
# ═══════════════════════════════════════════════════════════════════════

def load_oauth_secrets():
    try:
        with open(OAUTH_SECRETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    for key in ("antigravity", "gemini_cli"):
        sec = data.get(key, {})
        if not sec.get("client_id"):
            data.setdefault(key, {})["client_id"] = GEMINI_OAUTH_CLIENT_ID
        if not sec.get("client_secret"):
            data.setdefault(key, {})["client_secret"] = GEMINI_OAUTH_CLIENT_SECRET
    return data


def save_oauth_secrets(data):
    os.makedirs(os.path.dirname(OAUTH_SECRETS_PATH), exist_ok=True)
    tmp = str(OAUTH_SECRETS_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, OAUTH_SECRETS_PATH)
