"""Endpoint and BGP pool CRUD operations."""
import json
import os
import sys
from lib.constants import ENDPOINTS_FILE, BGP_POOLS_FILE

# ═══════════════════════════════════════════════════════════════════════
# Endpoint CRUD
# ═══════════════════════════════════════════════════════════════════════

def load_endpoints():
    if ENDPOINTS_FILE.exists():
        try:
            return json.loads(ENDPOINTS_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[lib] failed to load endpoints: {exc}", file=sys.stderr)
    return {"default": None, "endpoints": []}


def save_endpoints(data):
    ENDPOINTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ENDPOINTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(str(tmp), str(ENDPOINTS_FILE))


def load_bgp_pools():
    if BGP_POOLS_FILE.exists():
        try:
            return json.loads(BGP_POOLS_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[lib] failed to load bgp pools: {exc}", file=sys.stderr)
    return {"pools": []}


def save_bgp_pools(data):
    BGP_POOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = BGP_POOLS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(str(tmp), str(BGP_POOLS_FILE))


def get_endpoint(name):
    for e in load_endpoints()["endpoints"]:
        if e["name"] == name:
            return e
    return None
