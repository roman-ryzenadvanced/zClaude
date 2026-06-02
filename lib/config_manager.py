"""Config management — backup, restore, TOML merge, write."""
import glob as _glob
import json
import os
import re
import shutil
import sys
import time
from lib.constants import CONFIG, CONFIG_BAK, CONFIG_TXN, IS_WINDOWS, PROXY_CONFIG_DIR
from lib.utils import safe_name, _profile_slug
from lib.endpoints import load_endpoints

# ═══════════════════════════════════════════════════════════════════════
# Secure file write
# ═══════════════════════════════════════════════════════════════════════

def write_secure_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if not IS_WINDOWS:
        os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(path))

# ═══════════════════════════════════════════════════════════════════════
# Config management
# ═══════════════════════════════════════════════════════════════════════

def backup_config():
    if not CONFIG.exists():
        return
    tmp = CONFIG_BAK.with_suffix(".tmp")
    shutil.copy2(str(CONFIG), str(tmp))
    os.replace(str(tmp), str(CONFIG_BAK))
    ts = time.strftime("%Y%m%d_%H%M%S")
    rot = CONFIG.parent / f"config.toml.{ts}.bak"
    try:
        shutil.copy2(str(CONFIG), str(rot))
        _rotate_backups(CONFIG.parent, "config.toml.*.bak", max_backups=10)
    except Exception as exc:
        print(f"[lib] backup rotation failed: {exc}", file=sys.stderr)


def _rotate_backups(directory, pattern, max_backups=10):
    files = sorted(_glob.glob(str(directory / pattern)), key=os.path.getmtime, reverse=True)
    for old in files[max_backups:]:
        try:
            os.remove(old)
        except Exception:
            pass


def restore_config():
    if CONFIG_BAK.exists():
        tmp = CONFIG.with_suffix(".tmp")
        shutil.copy2(str(CONFIG_BAK), str(tmp))
        os.replace(str(tmp), str(CONFIG))


def begin_config_transaction(reason):
    txn = {"started_at": time.time(), "reason": reason,
           "config_existed": CONFIG.exists(), "backup_path": str(CONFIG_BAK)}
    if CONFIG.exists():
        backup_config()
    CONFIG_TXN.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_TXN.write_text(json.dumps(txn, indent=2))


def end_config_transaction():
    CONFIG_TXN.unlink(missing_ok=True)


def recover_config_if_needed(logfn=None):
    if not CONFIG_TXN.exists():
        return
    try:
        txn = json.loads(CONFIG_TXN.read_text(encoding="utf-8"))
        if txn.get("config_existed") and CONFIG_BAK.exists():
            restore_config()
            if logfn:
                logfn("Recovered Codex config from interrupted session.")
        elif CONFIG.exists():
            CONFIG.unlink()
            if logfn:
                logfn("Removed generated config from interrupted session.")
    finally:
        CONFIG_TXN.unlink(missing_ok=True)


def _toml_safe(val):
    val = str(val).replace("\\", "/").replace('"', '\\"')
    return val.split('\n', 1)[0].strip()


def _resolve_secret(value):
    value = (value or "").strip()
    m = re.fullmatch(r"\$\{ENV:([A-Z0-9_]+)\}", value)
    if m:
        return os.environ.get(m.group(1), "")
    return value


def _merge_toml(existing_text, new_sections_text):
    """Merge launcher-generated TOML sections into an existing config.toml.

    Preserves all existing sections/keys that are not overwritten by the
    launcher.  This is a simple line-based merge — good enough for the flat
    TOML structure Codex uses.
    """
    if not existing_text:
        return new_sections_text

    new_lines = new_sections_text.rstrip().splitlines()

    root_keys = []
    new_section_blocks = {}
    current_section = None
    current_block_lines = []

    for line in new_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and not stripped.startswith("[["):
            if current_section is not None:
                new_section_blocks[current_section] = current_block_lines
            current_section = stripped
            current_block_lines = []
        elif current_section is None:
            root_keys.append(line)
        else:
            current_block_lines.append(line)
    if current_section is not None:
        new_section_blocks[current_section] = current_block_lines

    existing_lines = existing_text.splitlines()
    existing_sections = {}
    existing_root_lines = []
    existing_section_order = []
    cur_sec = None

    for line in existing_lines:
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            if cur_sec is not None:
                pass
            cur_sec = stripped
            existing_section_order.append(cur_sec)
            existing_sections[cur_sec] = [line]
        elif cur_sec is not None:
            existing_sections[cur_sec].append(line)
        else:
            existing_root_lines.append(line)

    merged_root = []
    root_key_names = set()
    for rk in root_keys:
        key_name = rk.strip().split("=")[0].strip() if "=" in rk else ""
        if key_name:
            root_key_names.add(key_name)

    for line in existing_root_lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            merged_root.append(line)
            continue
        if "=" in stripped:
            key_name = stripped.split("=")[0].strip()
            if key_name in root_key_names:
                continue
        merged_root.append(line)

    merged_root.extend(root_keys)

    all_sections = list(existing_section_order)
    for sec in new_section_blocks:
        if sec not in all_sections:
            all_sections.append(sec)

    merged = list(merged_root)
    if merged and merged[-1] != "":
        merged.append("")
    for sec in all_sections:
        if sec in new_section_blocks:
            merged.append(sec)
            merged.extend(new_section_blocks[sec])
        else:
            merged.extend(existing_sections.get(sec, []))
        merged.append("")

    return "\n".join(merged).strip() + "\n"


def _gen_model_catalog(endpoint, selected_model=None):
    default_model = selected_model or endpoint.get("default_model")
    models = []
    for mid in endpoint.get("models", []):
        models.append({
            "slug": mid, "model": mid, "display_name": mid,
            "description": f"{endpoint['name']} {mid}",
            "hidden": False, "isDefault": mid == default_model,
            "shell_type": "shell_command", "visibility": "list",
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": [
                {"effort": "low", "description": "Fast"},
                {"effort": "medium", "description": "Balanced"},
                {"effort": "high", "description": "Deep"},
                {"effort": "xhigh", "description": "Extra deep"},
            ],
            "supportedReasoningEfforts": [
                {"reasoningEffort": "low", "description": "Fast"},
                {"reasoningEffort": "medium", "description": "Balanced"},
                {"reasoningEffort": "high", "description": "Deep"},
                {"reasoningEffort": "xhigh", "description": "Extra deep"},
            ],
            "priority": 30, "context_size": 128000,
            "additional_speed_tiers": [], "service_tiers": [],
            "supports_reasoning_summaries": True, "support_verbosity": True,
            "reasoning": True, "tool_call": True,
            "supports_parallel_tool_calls": True,
            "experimental_supported_tools": [], "supported_in_api": True,
            "truncation_policy": {"mode": "tokens", "limit": 128000},
            "base_instructions": "You are Codex, a coding agent.",
        })
    return {"models": models}


def write_config_for_native(endpoint, selected_model):
    backup_config()
    model_catalog = _gen_model_catalog(endpoint, selected_model)
    mc_path = PROXY_CONFIG_DIR / f"models-{safe_name(endpoint['name'])}.json"
    mc_path.parent.mkdir(parents=True, exist_ok=True)
    mc_path.write_text(json.dumps(model_catalog, indent=2))
    mc_str = str(mc_path).replace("\\", "/")

    main_config = [
        f'model = "{_toml_safe(selected_model)}"\n',
        f'model_provider = "{_toml_safe(endpoint["name"])}"\n',
        f'model_catalog_json = "{mc_str}"\n',
        f'\n[model_providers."{endpoint["name"]}"]\n',
        f'name = "{_toml_safe(endpoint["name"])}"\n',
        f'base_url = "{_toml_safe(endpoint["base_url"])}"\n',
        f'experimental_bearer_token = "{_toml_safe(_resolve_secret(endpoint["api_key"]))}"\n',
    ]
    existing = CONFIG.read_text(encoding="utf-8") if CONFIG.exists() else ""
    merged = _merge_toml(existing, "".join(main_config))
    write_secure_text(CONFIG, merged)

    profile_slug = _profile_slug(endpoint["name"])
    profile_path = CONFIG.parent / f"{profile_slug}.config.toml"
    profile_lines = [
        f'model = "{_toml_safe(selected_model)}"\n',
        f'model_provider = "{_toml_safe(endpoint["name"])}"\n',
        f'model_catalog_json = "{mc_str}"\n',
        f'service_tier = "default"\n',
        f'approvals_reviewer = "user"\n',
    ]
    write_secure_text(profile_path, "".join(profile_lines))


def write_config_for_translated(endpoint, selected_model, proxy_port=61255):
    backup_config()
    model_catalog = _gen_model_catalog(endpoint, selected_model)
    mc_path = PROXY_CONFIG_DIR / f"models-{safe_name(endpoint['name'])}.json"
    mc_path.parent.mkdir(parents=True, exist_ok=True)
    mc_path.write_text(json.dumps(model_catalog, indent=2))
    mc_str = str(mc_path).replace("\\", "/")

    main_config = [
        f'model = "{_toml_safe(selected_model)}"\n',
        f'model_provider = "{_toml_safe(endpoint["name"])}"\n',
        f'model_catalog_json = "{mc_str}"\n',
        f'\n[model_providers."{endpoint["name"]}"]\n',
        f'name = "{_toml_safe(endpoint["name"])}"\n',
        f'base_url = "http://127.0.0.1:{proxy_port}"\n',
        f'experimental_bearer_token = "codex-launcher-local"\n',
    ]
    existing = CONFIG.read_text(encoding="utf-8") if CONFIG.exists() else ""
    merged = _merge_toml(existing, "".join(main_config))
    write_secure_text(CONFIG, merged)

    profile_slug = _profile_slug(endpoint["name"])
    profile_path = CONFIG.parent / f"{profile_slug}.config.toml"
    profile_lines = [
        f'model = "{_toml_safe(selected_model)}"\n',
        f'model_provider = "{_toml_safe(endpoint["name"])}"\n',
        f'model_catalog_json = "{mc_str}"\n',
        f'service_tier = "fast"\n',
        f'approvals_reviewer = "user"\n',
    ]
    write_secure_text(profile_path, "".join(profile_lines))
