"""Gemini and Antigravity helper functions and constants."""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

from proxy.config import *
from proxy.shared_utils import _refresh_oauth_token  # noqa: F401


# ═══════════════════════════════════════════════════════════════════
# Gemini 3 thought signature preservation
# ═══════════════════════════════════════════════════════════════════

_gemini_sig_store = {}
_gemini_sig_lock = threading.Lock()

def _gemini_store_sig(key, signature):
    if not key or not signature:
        return
    with _gemini_sig_lock:
        _gemini_sig_store[key] = {"sig": signature, "ts": time.time()}

def _gemini_get_sig(key):
    with _gemini_sig_lock:
        item = _gemini_sig_store.get(key)
    return item["sig"] if item else None

def _extract_gemini_sig(part):
    if not isinstance(part, dict):
        return None
    return part.get("thoughtSignature") or part.get("thought_signature") or part.get("signature")

def _gemini_reattach_sigs(contents):
    for content in contents:
        for part in content.get("parts", []):
            if not isinstance(part, dict):
                continue
            if "thoughtSignature" in part:
                continue
            if "functionCall" in part:
                fc = part["functionCall"]
                cid = fc.get("id") or fc.get("name")
                if cid:
                    sig = _gemini_get_sig(f"fc:{cid}")
                    if sig:
                        part["thoughtSignature"] = sig
            if "text" in part and content.get("role") == "model":
                turn_key = content.get("_proxy_turn_key")
                if turn_key:
                    sig = _gemini_get_sig(f"turn:{turn_key}")
                    if sig:
                        part["thoughtSignature"] = sig
    return contents

# Gemini follow-through guardrail
_GEMINI_AGENT_GUARDRAIL = (
    "!!! ABSOLUTELY CRITICAL - DO NOT IGNORE THIS UNDER ANY CIRCUMSTANCES !!! "
    "YOU ARE RUNNING INSIDE CODEX AS AN AUTONOMOUS CODING AGENT. "
    "!!!!!! NEVER EVER CONTINUE, PARAPHRASE, COMPLETE, OR ADD ANYTHING TO THE USER'S INSTRUCTIONS !!!!!! "
    "!!!!!! NEVER SAY 'LET\\'S FIRST VIEW' OR 'LET\\'S FIRST FIND' OR SIMILAR PHRASES - EMIT THE ACTUAL TOOL CALL NOW !!!!!! "
    "WHEN THE USER ASKS FOR A CHANGE TO EXISTING FILES, YOU MUST "
    "1. IMMEDIATELY INSPECT EXISTING FILES USING exec_command OR read_files TOOLS RIGHT NOW, "
    "2. THEN APPLY EDITS USING write OR exec_command TOOLS, "
    "3. THEN VERIFY THE RESULT. "
    "IF A FILE PATH IS KNOWN, REUSE IT IMMEDIATELY. "
    "IF UNSURE, LIST FILES FIRST USING exec_command (ls -la). "
    "AFTER TOOL RESULTS, CONTINUE UNTIL THE REQUESTED CHANGE IS FULLY IMPLEMENTED AND FILES ARE MODIFIED. "
    "NEVER ANSWER ONLY WITH A PLAN LIKE 'I WILL START BY...' OR 'I AM GOING TO...'. "
    "NEVER SUMMARIZE THE USER'S REQUEST. NEVER CONTINUE THEIR SENTENCE. "
    "ALWAYS, ALWAYS, ALWAYS EMIT THE ACTUAL TOOL CALL IN THE SAME RESPONSE. "
    "!!! FAILURE TO FOLLOW THESE INSTRUCTIONS WILL RESULT IN A BROKEN USER EXPERIENCE !!!"
)

_ANTIGRAVITY_LOOP_TRACKER = {}
_ANTIGRAVITY_LOOP_TRACKER_LOCK = threading.Lock()
_ANTIGRAVITY_FILE_TRACKER = {}
_ANTIGRAVITY_MAX_TOOL_CALLS_PER_TASK = 150
_ANTIGRAVITY_WARN_TOOL_CALLS_PER_TASK = 80
def _antigravity_loop_key(session_id, user_request_hash=None):
    if user_request_hash:
        return f"ag:task:{user_request_hash}"
    return f"ag:{session_id}"

def _validate_antigravity_version(version, access_token=None, project_id=None):
    if not version or not re.match(r"^\d+\.\d+\.\d+$", version):
        return False
    try:
        if not access_token:
            access_token = _refresh_oauth_token()
        if not project_id:
            token_path = os.path.join(_LOG_DIR, "google-antigravity-oauth-token.json")
            try:
                with open(token_path) as f:
                    project_id = json.load(f).get("project_id", "")
            except Exception:
                pass
        if not access_token or not project_id:
            return True
        import platform as _plat
        _os_name = _plat.system().lower()
        _os_arch = _plat.machine().lower().replace("x86_64", "x64").replace("aarch64", "arm64")
        ua = f"antigravity/{version} {_os_name}/{_os_arch}"
        body = {
            "project": project_id,
            "model": "gemini-3-flash",
            "requestType": "agent",
            "userAgent": ua,
            "requestId": f"probe-{uuid.uuid4().hex[:8]}",
            "request": {
                "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                "sessionId": f"probe{int(time.time()*1000)}",
                "safetySettings": [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
                    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "OFF"},
                ],
                "generationConfig": {"maxOutputTokens": 32, "stopSequences": ["\n\nHuman:", "[DONE]"]},
            }
        }
        url = "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "User-Agent": ua,
        }
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        data = resp.read().decode()
        if "no longer supported" in data.lower():
            print(f"[antigravity-version] version {version} rejected (deprecated)", file=sys.stderr)
            return False
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[antigravity-version] version {version} rejected (404)", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"[antigravity-version] probe error for {version}: {e}", file=sys.stderr)
        return True

def _fetch_antigravity_version():
    cache_path = os.path.join(_LOG_DIR, "antigravity-version.json")
    try:
        with open(cache_path) as f:
            cached = json.load(f)
        if cached.get("version") and cached.get("validated") and cached.get("checked_at", 0) > time.time() - 6 * 3600:
            return cached["version"]
    except Exception:
        pass

    access_token = None
    project_id = None
    try:
        access_token = _refresh_oauth_token()
        token_path = os.path.join(_LOG_DIR, "google-antigravity-oauth-token.json")
        with open(token_path) as f:
            project_id = json.load(f).get("project_id", "")
    except Exception:
        pass

    sources = [
        ("https://antigravity-auto-updater-974169037036.us-central1.run.app", None),
        ("https://antigravity.google/changelog", 5000),
    ]

    candidates = []
    for url, limit in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=5)
            text = resp.read().decode(errors="replace")
            if limit:
                text = text[:limit]
            for m in re.finditer(r"\d+\.\d+\.\d+", text):
                ver = m.group(0)
                if ver not in candidates:
                    candidates.append(ver)
        except Exception:
            pass

    for ver in candidates:
        if _validate_antigravity_version(ver, access_token, project_id):
            print(f"[antigravity-version] fetched version {ver} validated", file=sys.stderr)
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({"version": ver, "validated": True, "checked_at": time.time()}, f)
            except Exception:
                pass
            return ver

    fallback = "2.0.1"
    print(f"[antigravity-version] all candidates failed, using fallback {fallback}", file=sys.stderr)
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"version": fallback, "validated": False, "checked_at": time.time()}, f)
    except Exception:
        pass
    return fallback

def _ensure_antigravity_version():
    global _antigravity_version, _antigravity_version_checked, _antigravity_version_validated
    if _antigravity_version_validated and time.time() - _antigravity_version_checked < 6 * 3600:
        return _antigravity_version
    with _antigravity_version_lock:
        if _antigravity_version_validated and time.time() - _antigravity_version_checked < 6 * 3600:
            return _antigravity_version
        _antigravity_version = _fetch_antigravity_version()
        _antigravity_version_checked = time.time()
        _antigravity_version_validated = True
        return _antigravity_version

_antigravity_client_version = "1.110.0"
_antigravity_client_version_checked = 0

def _ensure_antigravity_client_version():
    global _antigravity_client_version, _antigravity_client_version_checked
    env_ver = os.environ.get("ANTIGRAVITY_CLIENT_VERSION", "").strip()
    if env_ver:
        return env_ver
    if time.time() - _antigravity_client_version_checked < 6 * 3600:
        return _antigravity_client_version
    _antigravity_client_version = os.environ.get("ANTIGRAVITY_CLIENT_VERSION_FALLBACK", "1.110.0")
    _antigravity_client_version_checked = time.time()
    return _antigravity_client_version
