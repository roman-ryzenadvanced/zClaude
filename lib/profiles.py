"""Profile bundle import/export."""
import json
import shutil
from pathlib import Path
from lib.constants import CONFIG, CONFIG_BAK, ENDPOINTS_FILE
from lib.utils import now_utc_iso
from lib.endpoints import save_endpoints

# ═══════════════════════════════════════════════════════════════════════
# Profile bundle import/export
# ═══════════════════════════════════════════════════════════════════════

def build_profile_bundle():
    return {
        "version": 1,
        "exported_at": now_utc_iso(),
        "endpoints": load_endpoints(),
        "codex_config_toml": CONFIG.read_text(encoding="utf-8") if CONFIG.exists() else "",
    }


def save_profile_bundle(path):
    bundle = build_profile_bundle()
    Path(path).write_text(json.dumps(bundle, indent=2), encoding="utf-8")


def import_profile_bundle(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid profile bundle")

    endpoints = data.get("endpoints")
    if not isinstance(endpoints, dict) or "endpoints" not in endpoints:
        raise ValueError("Profile bundle missing endpoints")

    if CONFIG.exists():
        shutil.copy2(str(CONFIG), str(CONFIG_BAK))
    if ENDPOINTS_FILE.exists():
        shutil.copy2(str(ENDPOINTS_FILE), str(ENDPOINTS_FILE.with_suffix(".json.import-bak")))

    save_endpoints(endpoints)

    cfg = data.get("codex_config_toml", "")
    if isinstance(cfg, str) and cfg.strip():
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(cfg, encoding="utf-8")
    return endpoints
