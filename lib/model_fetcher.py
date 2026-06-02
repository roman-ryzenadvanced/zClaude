"""Model fetching, latency checking, and endpoint probing."""
import hashlib
import json
import time
import urllib.parse
import urllib.request
from lib.constants import UA, PROXY_CONFIG_DIR
from lib.utils import normalize_base_url, normalize_model_id

# ═══════════════════════════════════════════════════════════════════════
# Model fetching
# ═══════════════════════════════════════════════════════════════════════

def endpoint_models_url(endpoint):
    base = normalize_base_url(endpoint.get("base_url") or "")
    if not base:
        return ""
    return f"{base}/models"


def endpoint_model_headers(endpoint):
    key = (endpoint.get("api_key") or "").strip()
    backend = endpoint.get("backend_type", "openai-compat")
    headers = {"User-Agent": UA}
    if backend == "anthropic":
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def check_provider_latency(endpoint, timeout=5):
    bt = endpoint.get("backend_type", "")
    if bt in ("native", "codex-default", "gemini-oauth-antigravity"):
        return None
    base = endpoint.get("base_url", "").strip()
    if not base:
        return None
    url = base.rstrip("/") + "/models"
    try:
        headers = endpoint_model_headers(endpoint)
        req = urllib.request.Request(url, headers=headers, method="GET")
        t0 = time.time()
        urllib.request.urlopen(req, timeout=timeout)
        return time.time() - t0
    except Exception:
        return None


def _fetch_kiro_models(endpoint, timeout=10):
    """Fetch available models from Kiro's ListAvailableModels API."""
    token_path = str(PROXY_CONFIG_DIR / "kiro-oauth-token.json")
    try:
        with open(token_path, encoding="utf-8") as f:
            td = json.load(f)
    except Exception:
        return None, "No Kiro token found. Run OAuth login first."
    access_token = td.get("access_token", "")
    if not access_token:
        return None, "No access token in Kiro credentials."
    profile_arn = td.get("profileArn", "")
    region = "us-east-1"
    if profile_arn and ":" in profile_arn:
        parts = profile_arn.split(":")
        if len(parts) >= 4 and parts[3]:
            region = parts[3]
    params = "origin=AI_EDITOR"
    if profile_arn:
        params += f"&profileArn={urllib.parse.quote(profile_arn)}"
    url = f"https://q.{region}.amazonaws.com/ListAvailableModels?{params}"
    seed = td.get("client_id", "") or td.get("refresh_token", "") or access_token
    machine_id = hashlib.sha256(seed.encode()).hexdigest()
    ua = (f"aws-sdk-js/1.0.0 ua/2.1 os/windows#10.0.26200 "
          f"lang/js md/nodejs#22.21.1 api/codewhispererruntime#1.0.0 "
          f"m/N,E KiroIDE-0.10.32-{machine_id}")
    try:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": ua,
            "Accept": "application/json",
        })
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        raw_models = data.get("models", []) if isinstance(data, dict) else []
        ids = []
        seen = set()
        for m in raw_models:
            if not isinstance(m, dict):
                continue
            mid = m.get("modelId") or m.get("id", "")
            if mid and mid not in seen:
                seen.add(mid)
                ids.append(mid)
        if not ids:
            return None, "No models returned by Kiro API."
        return ids, None
    except Exception as e:
        return None, f"Kiro model fetch failed: {e}"


def fetch_models_for_endpoint(endpoint, timeout=10):
    bt = endpoint.get("backend_type", "")
    if bt == "gemini-oauth-antigravity":
        return list(ANTIGRAVITY_MODELS), None
    if bt == "kiro-oauth":
        return _fetch_kiro_models(endpoint, timeout)
    url = endpoint_models_url(endpoint)
    if not url:
        return None, "Base URL is empty"
    try:
        req = urllib.request.Request(url, headers=endpoint_model_headers(endpoint))
        raw = urllib.request.urlopen(req, timeout=timeout).read()
        payload = json.loads(raw)
        items = payload.get("data") or payload.get("models") or []
        ids = []
        seen = set()
        for item in items:
            mid = item.get("id") if isinstance(item, dict) else None
            if mid and mid not in seen:
                seen.add(mid)
                ids.append(mid)
        if not ids:
            return None, "No models returned"
        return ids, None
    except Exception as e:
        return None, str(e)


def refresh_endpoint_models(endpoint):
    ids, err = fetch_models_for_endpoint(endpoint)
    if not ids:
        return None, err
    updated = dict(endpoint)
    updated["models"] = ids
    if updated.get("default_model") not in ids:
        updated["default_model"] = ids[0]
    return updated, None


# ═══════════════════════════════════════════════════════════════════════
# Antigravity model list (static — no /v1/models REST endpoint)
# ═══════════════════════════════════════════════════════════════════════

ANTIGRAVITY_MODELS = [
    "Gemini 3.5 Flash (High)", "Gemini 3.5 Flash (Medium)", "Gemini 3.5 Flash (Low)",
    "Gemini 3.1 Pro (High)", "Gemini 3.1 Pro (Low)",
    "Claude Sonnet 4.6 (Thinking)",
    "Claude Opus 4.6 (Thinking)",
    "GPT-OSS 120B (Medium)",
]
