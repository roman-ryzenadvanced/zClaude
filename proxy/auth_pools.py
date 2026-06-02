"""Account pools — multi-account rotation and token refresh."""
import json
import os
import re
import sys
import threading
import time
import urllib.request
import urllib.error

from proxy.config import (
    _LOG_DIR, _IS_WINDOWS, OAUTH_PROVIDER,
    KIRO_AUTH_SERVICE, _CODEBUFF_CREDS_PATH,
    _codebuff_token_cache, _codebuff_session_cache, _codebuff_token_lock,
    _CODEBUFF_API_URL, _cb_pool, _google_antigravity_pool, _google_cli_pool,
)

# ═══════════════════════════════════════════════════════════════════
# Codebuff session helpers
# ═══════════════════════════════════════════════════════════════════

def _get_codebuff_token():
    with _codebuff_token_lock:
        if _codebuff_token_cache["token"] and _codebuff_token_cache["checked"] > time.time() - 300:
            return _codebuff_token_cache["token"]
    try:
        with open(_CODEBUFF_CREDS_PATH) as f:
            creds = json.load(f)
        default_account = creds.get("default", {})
        token = default_account.get("authToken") or creds.get("apiKey") or ""
        with _codebuff_token_lock:
            _codebuff_token_cache["token"] = token
            _codebuff_token_cache["checked"] = time.time()
        return token
    except Exception as e:
        print(f"[codebuff] no credentials at {_CODEBUFF_CREDS_PATH}: {e}", file=sys.stderr)
        return ""

def _codebuff_get_session(token, model):
    with _codebuff_token_lock:
        sc = _codebuff_session_cache
        if sc["instance_id"] and sc["expires"] > time.time() + 60 and sc["model"] == model:
            return sc["instance_id"]
    try:
        url = f"{_CODEBUFF_API_URL}/api/v1/freebuff/session"
        body = json.dumps({}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "ai-sdk/openai-compatible/1.0.25/codebuff",
            "x-codebuff-model": model,
        })
        try:
            resp = urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()[:1000]
            if e.code == 429:
                retry_s = 120
                user_msg = ""
                try:
                    err_data = json.loads(err_body)
                    retry_ms = err_data.get("retryAfterMs", 0)
                    if retry_ms:
                        retry_s = retry_ms / 1000
                    user_msg = err_data.get("message", err_data.get("error", ""))
                    if isinstance(user_msg, dict):
                        user_msg = user_msg.get("message", "")
                except Exception:
                    pass
                if not user_msg:
                    user_msg = _sanitize_err_body(err_body)
                raise RateLimitError(retry_s, user_msg)
            print(f"[codebuff] session HTTP {e.code}: {err_body[:200]}", file=sys.stderr)
            return None
        data = json.loads(resp.read())
        instance_id = data.get("instanceId", data.get("data", {}).get("instance_id", ""))
        expires_at = data.get("remainingMs", 0)
        if instance_id:
            with _codebuff_token_lock:
                _codebuff_session_cache["instance_id"] = instance_id
                _codebuff_session_cache["expires"] = time.time() + min(expires_at / 1000, 3600)
                _codebuff_session_cache["model"] = model
            print(f"[codebuff] session active, instance={instance_id[:8]}...", file=sys.stderr)
            return instance_id
        return None
    except RateLimitError:
        raise
    except Exception as e:
        print(f"[codebuff] session failed: {e}", file=sys.stderr)
        return None

def _codebuff_start_run(token, agent_id):
    url = f"{_CODEBUFF_API_URL}/api/v1/agent-runs"
    body = json.dumps({"action": "START", "agentId": agent_id, "ancestorRunIds": []}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "ai-sdk/openai-compatible/1.0.25/codebuff",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        run_id = data.get("runId")
        print(f"[codebuff] started run {run_id} for agent {agent_id}", file=sys.stderr)
        return run_id, None
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:500]
        print(f"[codebuff] start run failed: HTTP {e.code}: {err}", file=sys.stderr)
        if e.code == 429:
            retry_s = 120
            try:
                err_data = json.loads(err)
                retry_ms = err_data.get("retryAfterMs", 0)
                if retry_ms:
                    retry_s = retry_ms / 1000
            except Exception:
                pass
            return None, ("rate_limit_error", 429, retry_s, _sanitize_err_body(err))
        return None, ("upstream_error", e.code, 0, _sanitize_err_body(err))
    except Exception as e:
        print(f"[codebuff] start run error: {e}", file=sys.stderr)
        return None, ("proxy_error", 502, 0, str(e))

def _codebuff_finish_run(token, run_id, status="completed"):
    url = f"{_CODEBUFF_API_URL}/api/v1/agent-runs"
    body = json.dumps({"action": "FINISH", "runId": run_id, "status": status,
                       "totalSteps": 1, "directCredits": 0, "totalCredits": 0}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "ai-sdk/openai-compatible/1.0.25/codebuff",
    })
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[codebuff] finish run {run_id} error: {e}", file=sys.stderr)

# ═══════════════════════════════════════════════════════════════════
# Multi-account rotation system
# ═══════════════════════════════════════════════════════════════════

class RateLimitError(Exception):
    def __init__(self, retry_seconds, message=""):
        self.retry_seconds = retry_seconds
        self.message = message
        super().__init__(f"rate-limited for {retry_seconds:.0f}s: {message}")

class AccountPool:
    """Manages multiple accounts for a provider. Rotates on rate-limit (429/426)."""

    def __init__(self, provider_name):
        self.provider_name = provider_name
        self._lock = threading.Lock()
        self._accounts = []
        self._rate_limited = {}
        self._current_idx = 0
        self._loaded_at = 0

    def load_accounts(self, force=False):
        with self._lock:
            if not force and self._accounts and time.time() - self._loaded_at < 60:
                return len(self._accounts)
        accounts = self._do_load()
        with self._lock:
            if accounts:
                self._accounts = accounts
                self._loaded_at = time.time()
                for a in accounts:
                    key = a.get("id", a.get("email", ""))
                    if key not in self._rate_limited:
                        self._rate_limited[key] = 0
        return len(self._accounts) if accounts else 0

    def _do_load(self):
        return []

    def get(self):
        """Return the best available account dict, or None."""
        self.load_accounts()
        with self._lock:
            if not self._accounts:
                return None
            now = time.time()
            n = len(self._accounts)
            for attempt in range(n):
                idx = (self._current_idx + attempt) % n
                acct = self._accounts[idx]
                key = acct.get("id", acct.get("email", ""))
                if self._rate_limited.get(key, 0) < now:
                    self._current_idx = idx
                    return acct
            best_key = min(self._rate_limited, key=self._rate_limited.get)
            wait = self._rate_limited[best_key] - now
            print(f"[{self.provider_name}] all accounts rate-limited, earliest free in {wait:.0f}s", file=sys.stderr)
            return self._accounts[self._current_idx]

    def mark_rate_limited(self, account, duration=120):
        key = account.get("id", account.get("email", ""))
        with self._lock:
            self._rate_limited[key] = time.time() + duration
            idx = None
            for i, a in enumerate(self._accounts):
                if a.get("id", a.get("email", "")) == key:
                    idx = i
                    break
            if idx is not None:
                self._current_idx = (idx + 1) % len(self._accounts)
        print(f"[{self.provider_name}] account {key} rate-limited for {duration}s, rotating to next", file=sys.stderr)

    def advance(self):
        with self._lock:
            if self._accounts:
                self._current_idx = (self._current_idx + 1) % len(self._accounts)

    def status(self):
        with self._lock:
            now = time.time()
            result = []
            for a in self._accounts:
                key = a.get("id", a.get("email", ""))
                rl_until = self._rate_limited.get(key, 0)
                info = {"id": key, "email": a.get("email", ""), "rate_limited": rl_until > now}
                if rl_until > now:
                    info["rate_limited_until"] = rl_until
                    info["resets_in"] = int(rl_until - now)
                result.append(info)
            return result

class CodebuffAccountPool(AccountPool):
    def _do_load(self):
        if not os.path.exists(_CODEBUFF_CREDS_PATH):
            return None
        try:
            with open(_CODEBUFF_CREDS_PATH) as f:
                creds = json.load(f)
        except Exception:
            return None
        accounts = []
        if "accounts" in creds and isinstance(creds["accounts"], list):
            for i, ac in enumerate(creds["accounts"]):
                token = ac.get("authToken") or ac.get("apiKey") or ""
                if token:
                    acct = {"id": ac.get("email") or ac.get("id") or f"account-{i}", "token": token, "email": ac.get("email", "")}
                    accounts.append(acct)
        default = creds.get("default", {})
        default_token = default.get("authToken") or creds.get("apiKey") or ""
        if default_token:
            default_id = default.get("email") or default.get("id") or "default"
            if not any(a["id"] == default_id for a in accounts):
                accounts.insert(0, {"id": default_id, "token": default_token, "email": default.get("email", "")})
        return accounts if accounts else None

class GoogleAccountPool(AccountPool):
    def __init__(self, variant):
        super().__init__(f"google-{variant}")
        self.variant = variant

    def _do_load(self):
        cache_dir = _LOG_DIR
        accounts = []
        primary = f"google-{self.variant}-oauth-token.json"
        primary_path = os.path.join(cache_dir, primary)
        if os.path.exists(primary_path):
            try:
                with open(primary_path) as f:
                    tok = json.load(f)
                token = tok.get("access_token", "")
                if token:
                    accounts.append({"id": f"google-{self.variant}-primary", "token": token, "email": tok.get("email", ""), "_token_data": tok, "_path": primary_path})
            except Exception:
                pass
        idx = 1
        while True:
            extra = f"google-{self.variant}-oauth-token-{idx}.json"
            extra_path = os.path.join(cache_dir, extra)
            if not os.path.exists(extra_path):
                break
            try:
                with open(extra_path) as f:
                    tok = json.load(f)
                token = tok.get("access_token", "")
                if token:
                    accounts.append({"id": f"google-{self.variant}-{idx}", "token": token, "email": tok.get("email", ""), "_token_data": tok, "_path": extra_path})
            except Exception:
                pass
            idx += 1
        return accounts if accounts else None

class APIKeyPool(AccountPool):
    """Rotates through comma-separated API keys."""

    def __init__(self, provider_name, keys_str):
        super().__init__(provider_name)
        self._raw_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        self._accounts = [{"id": f"key-{i}", "token": k, "email": f"key-{i}"} for i, k in enumerate(self._raw_keys)]
        for a in self._accounts:
            self._rate_limited[a["id"]] = 0
        self._loaded_at = time.time()

    def load_accounts(self, force=False):
        return len(self._accounts)

# ═══════════════════════════════════════════════════════════════════
# Error classification
# ═══════════════════════════════════════════════════════════════════

def _classify_antigravity_error(status_code, body):
    lower = body.lower()
    if status_code == 400:
        return "bad_request"
    if status_code == 401:
        if any(x in lower for x in ["invalid_grant", "token revoked", "token_revoked", "invalid_client"]):
            return "auth_permanent"
        return "auth_transient"
    if status_code == 403:
        if "validation_required" in lower or "account_disabled" in lower:
            return "validation_required"
        if "has been disabled" in lower and "violation of terms of service" in lower:
            return "account_banned"
        if "service_disabled" in lower:
            return "service_disabled"
        return "forbidden"
    if status_code in (429, 503, 529):
        if any(x in lower for x in ["model_capacity_exhausted", "capacity_exhausted", "model is currently overloaded", "service temporarily unavailable"]):
            return "capacity_exhausted"
        if any(x in lower for x in ["quota_exhausted", "resource_exhausted", "daily limit", "quota exceeded", "quotaresetdelay"]):
            return "quota_exhausted"
        return "rate_limited"
    if status_code >= 500:
        return "server_error"
    return "unknown"

def _parse_rate_limit_reset(body):
    m = re.search(r'quotaResetDelay[:"\s]+(\d+(?:\.\d+)?)(ms|s)', body, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        return val / 1000 if m.group(2) == 'ms' else val
    m = re.search(r'(\d+)h(\d+)m(\d+)s', body, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    m = re.search(r'Resets in ~(\d+)h(\d+)m', body, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60
    m = re.search(r'retry[-_]?after[:\s]+(\d+)\s*(?:sec|s\b)', body, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None

# ═══════════════════════════════════════════════════════════════════
# Account getters
# ═══════════════════════════════════════════════════════════════════

def _get_codebuff_account():
    """Return (token, account_dict) for best available codebuff account."""
    _cb_pool.load_accounts()
    acct = _cb_pool.get()
    if not acct:
        return "", None
    return acct["token"], acct

def _get_google_account(oauth_provider):
    """Return (access_token, account_dict) for best available Google account."""
    pool = _google_antigravity_pool if oauth_provider == "google-antigravity" else _google_cli_pool
    pool.load_accounts()
    acct = pool.get()
    if not acct:
        return None, None
    token_data = acct.get("_token_data", {})
    token_path = acct.get("_path", "")
    if token_data and token_path:
        refreshed = _refresh_google_token(token_data, token_path)
        return refreshed, acct
    return acct.get("token", ""), acct

# ═══════════════════════════════════════════════════════════════════
# Token refresh
# ═══════════════════════════════════════════════════════════════════

def _refresh_google_token(token_data, token_path):
    if token_data.get("expires_at", 0) > time.time() + 60:
        return token_data.get("access_token", "")
    client_id = token_data.get("client_id", "")
    client_secret = token_data.get("client_secret", "")
    refresh_token = token_data.get("refresh_token", "")
    if not all([client_id, client_secret, refresh_token]):
        return token_data.get("access_token", "")
    print("[oauth] refreshing Google access token...", file=sys.stderr)
    try:
        import urllib.parse
        data = urllib.parse.urlencode({
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
                                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = urllib.request.urlopen(req, timeout=30)
        new_tokens = json.loads(resp.read())
        token_data["access_token"] = new_tokens.get("access_token", token_data.get("access_token"))
        token_data["expires_at"] = time.time() + new_tokens.get("expires_in", 3600)
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)
        print("[oauth] token refreshed OK", file=sys.stderr)
        return token_data["access_token"]
    except Exception as e:
        print(f"[oauth] refresh failed: {e}", file=sys.stderr)
        return token_data.get("access_token", "")

def _force_refresh_google_token():
    token_path = os.path.join(_LOG_DIR,
                              "google-antigravity-oauth-token.json" if OAUTH_PROVIDER == "google-antigravity"
                              else "google-oauth-token.json")
    try:
        with open(token_path) as f:
            token_data = json.load(f)
        token_data["expires_at"] = 0
        new_token = _refresh_google_token(token_data, token_path)
        return bool(new_token)
    except Exception as e:
        print(f"[oauth] force refresh failed: {e}", file=sys.stderr)
        return False

def _refresh_kiro_token():
    """Refresh Kiro (AWS CodeWhisperer) OAuth token. Returns access_token or empty string."""
    token_path = os.path.join(_LOG_DIR, "kiro-oauth-token.json")
    try:
        with open(token_path, encoding="utf-8") as f:
            td = json.load(f)
    except Exception:
        print("[kiro] no token file found", file=sys.stderr)
        return ""
    if td.get("expires_at", 0) > time.time() + 60:
        return td.get("access_token", "")
    refresh_token = td.get("refresh_token", "")
    if not refresh_token:
        return td.get("access_token", "")
    client_id = td.get("client_id", "")
    client_secret = td.get("client_secret", "")
    region = td.get("region", "us-east-1")
    print("[kiro] refreshing access token...", file=sys.stderr)
    try:
        if client_id and client_secret:
            data = json.dumps({
                "clientId": client_id, "clientSecret": client_secret,
                "refreshToken": refresh_token, "grantType": "refresh_token",
            }).encode()
            req = urllib.request.Request(
                f"https://oidc.{region}.amazonaws.com/token", data=data,
                headers={"Content-Type": "application/json"})
        else:
            data = json.dumps({"refreshToken": refresh_token}).encode()
            req = urllib.request.Request(
                f"{KIRO_AUTH_SERVICE}/refreshToken", data=data,
                headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=30)
        new_tokens = json.loads(resp.read())
        td["access_token"] = new_tokens.get("accessToken", new_tokens.get("access_token", td.get("access_token", "")))
        td["refresh_token"] = new_tokens.get("refreshToken", new_tokens.get("refresh_token", refresh_token))
        td["expires_at"] = time.time() + new_tokens.get("expiresIn", new_tokens.get("expires_in", 3600))
        if new_tokens.get("profileArn"):
            td["profileArn"] = new_tokens["profileArn"]
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(td, f, indent=2)
        print("[kiro] token refreshed OK", file=sys.stderr)
        return td["access_token"]
    except Exception as e:
        print(f"[kiro] token refresh failed: {e}", file=sys.stderr)
        return td.get("access_token", "")


def _sanitize_err_body(body):
    """Sanitize upstream error body: strip HTML, truncate, remove control chars."""
    if not body:
        return ""
    s = re.sub(r'<[^>]+>', '', body)
    s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)
    s = s.strip()[:1000]
    return s
