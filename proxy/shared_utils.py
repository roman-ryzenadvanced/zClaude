"""Shared utilities — stats, response store, headers, connection pooling."""
import json
import os
import re
import selectors
import threading
import time
import urllib.parse
import http.client
import uuid

from proxy.config import *
from proxy.auth_pools import _get_google_account

_pool = uuid.uuid4().hex[:8]


def _pooled_urlopen(url, data=None, headers=None, timeout=180):
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    pool_key = f"{parsed.scheme}://{host}:{port}"
    with _conn_pool_lock:
        conn = _conn_pool.get(pool_key)
        if conn:
            try:
                sock = conn.sock
                if sock is None or sock._closed if hasattr(sock, '_closed') else False:
                    conn = None
            except Exception:
                conn = None
    if conn is None:
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        with _conn_pool_lock:
            _conn_pool[pool_key] = conn
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    method = "POST" if data else "GET"
    conn.request(method, path, body=data, headers=headers or {})
    return conn.getresponse()


def _response_store_evict():
    with _response_store_lock:
        now = time.time()
        expired = [k for k, v in _response_store.items()
                   if isinstance(v, dict) and now - v.get("ts", 0) > _RESPONSE_TTL]
        for k in expired:
            del _response_store[k]


def _log_dual(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, file=__import__('sys').stderr, flush=True)
    with _LOG_FILE_LOCK:
        if _LOG_FILE:
            try:
                _LOG_FILE.write(line + "\n")
                _LOG_FILE.flush()
            except Exception:
                pass


def _stream_with_idle_timeout(response, timeout_seconds=None):
    if timeout_seconds is None:
        timeout_seconds = _STREAM_IDLE_TIMEOUT
    sel = selectors.DefaultSelector()
    try:
        sock = response if hasattr(response, 'fp') and response.fp else response
        raw_sock = getattr(getattr(sock, 'fp', None), 'raw', None) or getattr(sock, '_sock', None)
        if raw_sock is None:
            for chunk in response:
                yield chunk
            return
        sel.register(raw_sock, selectors.EVENT_READ)
        while True:
            ready = sel.select(timeout=timeout_seconds)
            if not ready:
                raise TimeoutError(f"Stream idle for {timeout_seconds}s")
            chunk = response.readline()
            if not chunk:
                break
            yield chunk
    finally:
        try:
            sel.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# Provider caps
# ═══════════════════════════════════════════════════════════════════

def _provider_cap_key(target_url=None, backend=None, model=None):
    host = urllib.parse.urlparse(target_url or TARGET_URL).netloc.lower()
    return f"{backend or BACKEND}|{host}|{model or '*'}"


def _load_provider_caps():
    global _provider_caps
    with _provider_caps_lock:
        if _provider_caps is not None:
            return _provider_caps
        try:
            with open(_provider_caps_path) as f:
                _provider_caps = json.load(f)
        except Exception:
            _provider_caps = {}
        return _provider_caps


def _save_provider_caps():
    try:
        os.makedirs(os.path.dirname(_provider_caps_path), exist_ok=True)
        with open(_provider_caps_path, "w", encoding="utf-8") as f:
            json.dump(_provider_caps or {}, f, indent=2)
    except Exception as e:
        print(f"[provider-sensor] failed to save caps: {e}", file=__import__('sys').stderr)


def _provider_cap(model, key, default=None):
    caps = _load_provider_caps()
    specific = caps.get(_provider_cap_key(model=model), {})
    generic = caps.get(_provider_cap_key(model="*"), {})
    return specific.get(key, generic.get(key, default))


def _set_provider_cap(model, key, value, reason=""):
    caps = _load_provider_caps()
    cap_key = _provider_cap_key(model=model)
    caps.setdefault(cap_key, {})[key] = value
    caps[cap_key]["reason"] = reason
    caps[cap_key]["updated_at"] = time.time()
    _save_provider_caps()
    print(f"[provider-sensor] learned {cap_key}: {key}={value} reason={reason}", file=__import__('sys').stderr)


# ═══════════════════════════════════════════════════════════════════
# OAuth token refresh (wrapper)
# ═══════════════════════════════════════════════════════════════════

def _refresh_oauth_token():
    return _refresh_oauth_token_for(API_KEY, OAUTH_PROVIDER)


def _refresh_oauth_token_for(api_key, oauth_provider):
    oauth_provider = oauth_provider or ""
    if oauth_provider.startswith("google"):
        token, acct = _get_google_account(oauth_provider)
        if token and acct:
            return token
    if not oauth_provider.startswith("google"):
        return api_key
    token_name = "google-antigravity-oauth-token.json" if oauth_provider == "google-antigravity" else "google-cli-oauth-token.json"
    token_path = os.path.join(_LOG_DIR, token_name)
    if not os.path.exists(token_path):
        return api_key
    try:
        with open(token_path) as f:
            tokens = json.load(f)
        if tokens.get("expires_at", 0) > time.time() + 60:
            return tokens.get("access_token", api_key)
        client_id = tokens.get("client_id", "")
        client_secret = tokens.get("client_secret", "")
        refresh_token = tokens.get("refresh_token", "")
        if not all([client_id, client_secret, refresh_token]):
            return tokens.get("access_token", api_key)
        print("[oauth] refreshing Google access token...", file=__import__('sys').stderr)
        import urllib.request
        data = urllib.parse.urlencode({
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = urllib.request.urlopen(req, timeout=30)
        new_tokens = json.loads(resp.read())
        tokens["access_token"] = new_tokens.get("access_token", tokens.get("access_token"))
        tokens["expires_at"] = time.time() + new_tokens.get("expires_in", 3600)
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
        print("[oauth] token refreshed OK", file=__import__('sys').stderr)
        return tokens["access_token"]
    except Exception as e:
        print(f"[oauth] refresh failed: {e}", file=__import__('sys').stderr)
        return API_KEY


# ═══════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════

def _load_stats():
    try:
        with open(_stats_path) as _f:
            return json.load(_f)
    except Exception:
        return {"providers": {}, "updated": None}


def _atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _flush_stats():
    global _stats_flush_timer
    with _stats_lock:
        batch = list(_stats_pending)
        _stats_pending.clear()
        _stats_flush_timer = None
    if not batch:
        return
    stats = _load_stats()
    for entry in batch:
        provider = entry["provider"]
        model = entry["model"]
        p = stats["providers"].setdefault(provider, {
            "total_requests": 0, "successes": 0, "failures": 0,
            "total_tokens_in": 0, "total_tokens_out": 0,
            "total_duration_s": 0.0, "models": {}, "last_used": None, "last_error": None,
        })
        p["total_requests"] += 1
        p["total_tokens_in"] += entry["tokens_in"]
        p["total_tokens_out"] += entry["tokens_out"]
        p["total_duration_s"] += entry["duration_s"]
        p["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(entry["ts"]))
        if entry["success"]:
            p["successes"] += 1
        else:
            p["failures"] += 1
            p["last_error"] = entry.get("error_type") or "unknown"
        m = p["models"].setdefault(model, {"requests": 0, "tokens_in": 0, "tokens_out": 0})
        m["requests"] += 1
        m["tokens_in"] += entry["tokens_in"]
        m["tokens_out"] += entry["tokens_out"]
    stats["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _atomic_write_json(_stats_path, stats)


def _record_usage(provider, model, success, duration_s, tokens_in=0, tokens_out=0, error_type=None):
    global _stats_flush_timer
    entry = {
        "provider": provider or "unknown", "model": model or "unknown",
        "success": bool(success), "duration_s": float(duration_s or 0),
        "tokens_in": int(tokens_in or 0), "tokens_out": int(tokens_out or 0),
        "error_type": error_type, "ts": time.time(),
    }
    with _stats_lock:
        _stats_pending.append(entry)
        if _stats_flush_timer is None:
            _stats_flush_timer = threading.Timer(_STATS_FLUSH_INTERVAL, _flush_stats)
            _stats_flush_timer.daemon = True
            _stats_flush_timer.start()


# ═══════════════════════════════════════════════════════════════════
# Response store
# ═══════════════════════════════════════════════════════════════════

def store_response(resp_id, input_data, output_items):
    if not resp_id:
        return
    _response_store_evict()
    with _response_store_lock:
        _response_store[resp_id] = {"input": input_data, "output": output_items, "ts": time.time()}
        while len(_response_store) > _MAX_STORED:
            _response_store.popitem(last=False)


def resolve_previous_response(body):
    prev_id = body.get("previous_response_id")
    input_data = body.get("input", "")
    if not prev_id:
        return input_data
    with _response_store_lock:
        stored = _response_store.get(prev_id)
    if not stored:
        return input_data
    prev_input = stored["input"]
    prev_output = stored["output"]
    new_input = input_data if isinstance(input_data, list) else []
    if isinstance(prev_input, list):
        combined = list(prev_input) + list(prev_output) + new_input
    else:
        combined = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": str(prev_input)}]}] + list(prev_output) + new_input
    return combined


# ═══════════════════════════════════════════════════════════════════
# Reasoning stores
# ═══════════════════════════════════════════════════════════════════

def _fb_store_reasoning(resp_id, reasoning_text):
    if not resp_id or not reasoning_text:
        return
    with _fb_reasoning_store_lock:
        _fb_reasoning_store[resp_id] = {"reasoning": reasoning_text, "ts": time.time()}
        while len(_fb_reasoning_store) > _MAX_STORED:
            _fb_reasoning_store.popitem(last=False)
        expired = [k for k, v in _fb_reasoning_store.items() if time.time() - v["ts"] > _RESPONSE_TTL]
        for k in expired:
            del _fb_reasoning_store[k]


def _fb_get_reasoning(resp_id):
    if not resp_id:
        return ""
    with _fb_reasoning_store_lock:
        entry = _fb_reasoning_store.get(resp_id)
        return entry["reasoning"] if entry else ""


def _fb_get_any_reasoning():
    with _fb_reasoning_store_lock:
        for k in _fb_reasoning_store:
            return _fb_reasoning_store[k]["reasoning"]
        return ""


def _codebuff_hard_disable_reasoning(messages):
    """Strip all reasoning/thinking fields from every message."""
    result = []
    for msg in messages:
        if not isinstance(msg, dict):
            result.append(msg)
            continue
        result.append({k: v for k, v in msg.items()
                        if k not in ("reasoning_content", "reasoning",
                                     "thinking", "thinking_content", "thoughts")})
    messages[:] = result  # update in-place for backward compat
    return result


def _is_reasoning_content_error(error_text):
    if not error_text:
        return False
    e = error_text.lower()
    return ("reasoning_content" in e or "thinking mode" in e
            or "must be passed back" in e)


def _ds_store_assistant(resp_id, assistant_msg):
    if not resp_id or not isinstance(assistant_msg, dict):
        return
    tool_calls = assistant_msg.get("tool_calls") or []
    reasoning = assistant_msg.get("reasoning_content")
    if not tool_calls or not reasoning:
        return
    with _deepseek_reasoning_lock:
        for tc in tool_calls:
            tc_id = tc.get("id") or tc.get("call_id", "")
            if tc_id:
                _deepseek_reasoning_store[tc_id] = {
                    "resp_id": resp_id,
                    "assistant": dict(assistant_msg),
                    "reasoning_content": reasoning,
                    "ts": time.time(),
                }
        keys = list(_deepseek_reasoning_store.keys())
        if len(keys) > _MAX_DS_STORED:
            for k in keys[:len(keys) - _MAX_DS_STORED]:
                del _deepseek_reasoning_store[k]


def _ds_rebuild_tool_history(messages):
    with _deepseek_reasoning_lock:
        snapshot = dict(_deepseek_reasoning_store)
        expired = [k for k, v in snapshot.items() if time.time() - v["ts"] > 900]
        for k in expired:
            _deepseek_reasoning_store.pop(k, None)
            snapshot.pop(k, None)
    if not snapshot:
        return messages
    rebuilt = []
    inserted_ids = set()
    for msg in messages:
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            stored = snapshot.get(tc_id)
            if stored and tc_id not in inserted_ids:
                am = dict(stored["assistant"])
                if am.get("reasoning_content"):
                    rebuilt.append(am)
                    inserted_ids.add(tc_id)
        rebuilt.append(msg)
    return rebuilt


# ═══════════════════════════════════════════════════════════════════
# Input converters
# ═══════════════════════════════════════════════════════════════════

def _cb_input_to_messages(input_data, instructions=""):
    msgs = []
    tool_name_by_id = {}
    pending_tool_calls = []
    last_flushed_ids = []
    if isinstance(input_data, str):
        msgs.append({"role": "user", "content": input_data})
    elif isinstance(input_data, list):
        for item in input_data:
            t = item.get("type")
            if t == "reasoning":
                continue
            if t == "function_call":
                tcid = item.get("call_id") or item.get("id") or uid("tc")
                pending_tool_calls.append(
                    {"id": tcid, "type": "function",
                     "function": {"name": item.get("name", ""),
                                   "arguments": item.get("arguments", "{}")}})
                tool_name_by_id[tcid] = item.get("name", "")
                continue
            if pending_tool_calls:
                last_flushed_ids = [tc["id"] for tc in pending_tool_calls]
                msg = {"role": "assistant", "content": None, "tool_calls": pending_tool_calls}
                msgs.append(msg)
                pending_tool_calls = []
            if t == "message":
                role = item.get("role", "user")
                if role == "developer":
                    role = "system"
                text = ""
                content = item.get("content", [])
                if isinstance(content, str):
                    text = content
                else:
                    for part in content:
                        if isinstance(part, str):
                            text += part
                            continue
                        pt = part.get("type", "")
                        if pt in ("input_text", "output_text"):
                            text += part.get("text", "")
                if text is not None:
                    am = {"role": role, "content": text}
                    if role == "assistant":
                        am["_fb_orig_id"] = item.get("id", "")
                    msgs.append(am)
            elif t == "function_call_output":
                tcid = item.get("call_id") or item.get("id") or ""
                if not tcid and last_flushed_ids:
                    idx = len([m for m in msgs if m.get("role") == "tool"])
                    if idx < len(last_flushed_ids):
                        tcid = last_flushed_ids[idx]
                msgs.append({"role": "tool", "tool_call_id": tcid,
                             "tool_name": tool_name_by_id.get(tcid, ""),
                             "content": item.get("output", "")})
        if pending_tool_calls:
            msg = {"role": "assistant", "content": None, "tool_calls": pending_tool_calls}
            msgs.append(msg)
    if instructions:
        msgs.insert(0, {"role": "system", "content": instructions})
    return msgs


def _fb_strip_reasoning_from_messages(messages):
    out = []
    for m in messages:
        nm = {k: v for k, v in m.items() if k != "reasoning_content"}
        out.append(nm)
    return out


# ═══════════════════════════════════════════════════════════════════
# Header helpers
# ═══════════════════════════════════════════════════════════════════

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def uid(prefix="id"):
    return f"{prefix}-{_pool}-{uuid.uuid4().hex[:12]}"


def emit(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def upstream_target(base_url, suffix):
    base = base_url.rstrip("/")
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Accept": "application/json, text/event-stream, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="137", "Not/A)Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Linux"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def forwarded_headers(request_headers, extra=None, browser_ua=False):
    headers = {}
    if browser_ua:
        headers.update(_BROWSER_HEADERS)
    for key, value in request_headers.items():
        if key.lower() in _HOP_BY_HOP_HEADERS:
            continue
        if browser_ua and key.lower() == "user-agent":
            continue
        headers[key] = value
    if extra:
        headers.update(extra)
    return headers


def _openrouter_extra():
    if not TARGET_URL:
        return {}
    if "z.ai" in TARGET_URL:
        return {
            "HTTP-Referer": "https://openclaw.ai",
            "X-OpenRouter-Title": "OpenClaw",
            "X-OpenRouter-Categories":
                "cli-agent,cloud-agent,programming-app,creative-writing,"
                "writing-assistant,general-chat,personal-agent",
        }
    if "openrouter.ai" in TARGET_URL:
        return {
            "HTTP-Referer": "https://chats-llm.com",
            "X-OpenRouter-Title": "Chats-LLM",
            "X-OpenRouter-Categories": "general-chat, ide-extension",
            "X-OpenRouter-Cache": "true",
        }
    return {}


def _extract_text_length(items):
    if not items:
        return 0
    if isinstance(items, str):
        return len(items)
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return 0

    total_len = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "message":
            content = item.get("content", "")
            if isinstance(content, str):
                total_len += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        total_len += len(part.get("text", part.get("input_text", "")))
        elif itype == "function_call":
            total_len += len(item.get("name", ""))
            args = item.get("arguments", "")
            if isinstance(args, str):
                total_len += len(args)
            elif isinstance(args, dict):
                total_len += len(json.dumps(args))
        elif itype == "function_call_output":
            out = item.get("output", "")
            if isinstance(out, str):
                total_len += len(out)
            elif isinstance(out, list):
                for part in out:
                    if isinstance(part, dict):
                        total_len += len(part.get("text", ""))
    return total_len


def _estimate_tokens_from_items(items):
    return _extract_text_length(items) // 4


def _record_usage_with_tokens(provider, model, success, duration_s, raw_resp, input_items=None, output_items=None, error_type=None, **kwargs):
    try:
        import sys
        usage = raw_resp.get("usage", {}) if isinstance(raw_resp, dict) else {}
        if not usage and isinstance(raw_resp, dict) and ("input_tokens" in raw_resp or "prompt_tokens" in raw_resp):
            usage = raw_resp
        
        tokens_in = int(usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0) or 0)
        tokens_out = int(usage.get("completion_tokens", 0) or usage.get("output_tokens", 0) or 0)
        
        # Fallback estimation heuristic
        if tokens_in == 0 and input_items:
            tokens_in = _estimate_tokens_from_items(input_items)
        if tokens_out == 0 and output_items:
            tokens_out = _estimate_tokens_from_items(output_items)
            
        _record_usage(provider, model, success, duration_s, tokens_in, tokens_out, error_type)
    except Exception as e:
        import sys
        print(f"[usage] error recording usage: {e}", file=sys.stderr)
        _record_usage(provider, model, success, duration_s, error_type=error_type)


def _log_resp(resp_id, status, output):
    try:
        import datetime as _dt
        _lp = os.path.join(_LOG_DIR, "requests.log")
        with open(_lp, "a", encoding="utf-8") as _f:
            _f.write(f"  RESPONSE id={resp_id} status={status}\n")
            if output:
                for o in output:
                    ot = o.get("type")
                    if ot == "message":
                        _f.write(f"    -> message: {o.get('content',[{}])[0].get('text','')[:200]}\n")
                    elif ot == "function_call":
                        _f.write(f"    -> function_call: {o.get('name')}({o.get('arguments','')[:120]})\n")
                    else:
                        _f.write(f"    -> {ot}\n")
            _f.write(f"{'='*60}\n")
            _f.flush()
            _f.seek(0)
            lines = _f.readlines()
            if len(lines) > _MAX_REQLOG_LINES:
                with open(_lp, "w", encoding="utf-8") as _f2:
                    _f2.writelines(lines[-_MAX_REQLOG_LINES:])
    except Exception:
        pass


def _upstream_timeout(body, stream):
    input_data = body.get("input", "")
    n_items = len(input_data) if isinstance(input_data, list) else 1
    has_tools = bool(body.get("tools"))
    if stream:
        return min((180 if has_tools else 120) + n_items * 2, 300)
    return min(60 + n_items * 2, 120)



