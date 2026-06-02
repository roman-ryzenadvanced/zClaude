"""Proxy server engine — Handler, ReusableHTTPServer, connection trackers."""
import collections
import contextlib
import datetime
import http.server
import http.client
import json
import os
import re
import selectors
import signal
import socket
import socketserver
import ssl
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Import from proxy package modules
from proxy.config import *
from proxy.auth_pools import (
    RateLimitError, _refresh_google_token, _force_refresh_google_token,
    _refresh_kiro_token, _sanitize_err_body, _get_codebuff_account,
    _get_google_account,
)
from proxy.adapters.kiro import (
    _parse_eventstream_frame, _iter_eventstream,
    _kiro_resolve_model, _kiro_is_thinking_enabled, _kiro_convert_tools,
    _kiro_extract_text_from_content, _kiro_extract_images,
    kiro_input_to_conversation, _kiro_stream_to_sse,
)
from proxy.adapters.gemini_helpers import (
    _gemini_sig_store, _gemini_sig_lock,
    _gemini_store_sig, _gemini_get_sig, _extract_gemini_sig, _gemini_reattach_sigs,
    _GEMINI_AGENT_GUARDRAIL,
    _ANTIGRAVITY_LOOP_TRACKER, _ANTIGRAVITY_LOOP_TRACKER_LOCK,
    _ANTIGRAVITY_FILE_TRACKER, _ANTIGRAVITY_MAX_TOOL_CALLS_PER_TASK,
    _ANTIGRAVITY_WARN_TOOL_CALLS_PER_TASK,
    _antigravity_loop_key, _validate_antigravity_version, _fetch_antigravity_version,
    _ensure_antigravity_version, _antigravity_client_version, _antigravity_client_version_checked,
    _ensure_antigravity_client_version,
)
from proxy.shared_utils import (
    _pooled_urlopen, _response_store_evict, _log_dual, _stream_with_idle_timeout,
    _provider_cap_key, _load_provider_caps, _save_provider_caps,
    _provider_cap, _set_provider_cap,
    _refresh_oauth_token, _refresh_oauth_token_for,
    _load_stats, _atomic_write_json, _flush_stats, _record_usage,
    store_response, resolve_previous_response,
    _fb_store_reasoning, _fb_get_reasoning, _fb_get_any_reasoning,
    _codebuff_hard_disable_reasoning, _is_reasoning_content_error,
    _ds_store_assistant, _ds_rebuild_tool_history,
    _cb_input_to_messages, _fb_strip_reasoning_from_messages,
    _HOP_BY_HOP_HEADERS, uid, emit, upstream_target,
    _BROWSER_HEADERS, forwarded_headers, _openrouter_extra,
    _extract_text_length, _estimate_tokens_from_items, _record_usage_with_tokens,
    _log_resp, _upstream_timeout,
)
from proxy.compaction import (
    _MAX_INPUT_ITEMS, _MAX_TOOL_OUTPUT_CHARS, _COMPACT_KEEP_RECENT,
    _CROF_ADAPTIVE, _model_max_tokens, _model_max_tokens_lock,
    _estimate_item_tokens, _estimate_input_tokens,
    _get_model_max_tokens, _set_model_max_tokens,
    _BGP_STATS_PATH, _bgp_stats_lock, _route_key,
    _load_bgp_stats, _save_bgp_stats, _score_route,
    _update_route_stats, _sorted_bgp_routes,
    _crof_record, _crof_item_limit, _crof_compact_for_retry,
    _item_summary, _extract_files, _rtk_compress_tool_output,
    _compact_input,
    _PROVIDER_POLICIES, _DEFAULT_PROVIDER_POLICY, provider_policy,
    _MODEL_CONTEXT, _context_limit_for_model, _estimate_tokens,
    _adaptive_compact,
    _PROMPT_ENHANCER_SYSTEM, _PROMPT_ENHANCER_OFFLINE,
    _enhance_prompt_llm, _apply_prompt_enhancer,
)
from proxy.tool_validation import (
    validate_tool_pairs, repair_orphan_tool_outputs,
    synthesize_tool_results_for_chat, has_function_call_output,
    _text_looks_like_tool_calls,
)
from proxy.logging_utils import (
    _redact, _redact_json, _init_logging,
    save_request_snapshot, update_snapshot_response, _rotate_snapshots,
    TokenBucket, _bucket_for_route, _rate_buckets, _rate_buckets_lock,
    _SECRET_PATTERNS, _MAX_SNAPSHOTS,
)
from proxy.adapters.openai import (
    _inject_stored_reasoning, _normalize_tool_args,
    _XML_TC_RE, _XML_ARG_VALUE_RE, _PAREN_TC_RE,
    _extract_xml_tool_calls,
    _NON_VISION_MODEL_PATTERNS, _vision_fail_cache, _vision_fail_lock,
    _model_supports_vision, _mark_vision_fail, _strip_images_from_input,
    oa_input_to_messages, oa_convert_tools, oa_resp_to_responses, oa_stream_to_sse,
)
from proxy.adapters.command_code import (
    cc_input_to_messages,
)
from proxy.adapters.anthropic import (
    an_input_to_messages, an_convert_tools, an_resp_to_responses, an_stream_to_sse,
)
from proxy.cc_parser import (
    _DEFAULT_CC_CONFIG, _cc_config, cc_convert_tools, _strip_xmlish_tags,
    _unwrap_cmd, _build_explore_cmd, _parse_commandcode_text_tool_calls,
    _sanitize_tool_calls, _parse_cc_line, _iter_cc_events,
    cc_resp_to_responses, cc_stream_to_sse,
)
from proxy.adapters.auto_sense import (
    ProviderSchema, ErrorAnalyzer, SchemaAdapter,
    _SENTINEL, _schema_cache_key, _load_schema, _save_schema,
    _extract_text,
    _vision_describe_image, _vision_desc_cache, _vision_desc_lock, _VISION_DESC_CACHE_MAX,
    _preprocess_vision, _preprocess_vision_input,
)


_MAX_REQLOG_LINES = 2000

def _log_cache_stats(raw_resp):
    try:
        usage = raw_resp.get("usage", {})
        cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
        total = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        if cached and total:
            pct = (cached / total * 100) if total else 0
            print(f"[cache] stats: cached={cached}/{total} ({pct:.1f}%)", file=sys.stderr)
    except Exception:
        pass


class ConnectionTracker:
    def __enter__(self):
        global _active_connections
        with _active_connections_lock:
            _active_connections += 1
    def __exit__(self, *a):
        global _active_connections
        with _active_connections_lock:
            _active_connections -= 1

class RequestTracker:
    def __init__(self, request_id):
        self.request_id = request_id
        self.cancelled = threading.Event()

    def __enter__(self):
        if self.request_id:
            with _active_requests_lock:
                _active_requests[self.request_id] = self
        return self

    def __exit__(self, *a):
        if self.request_id:
            with _active_requests_lock:
                _active_requests.pop(self.request_id, None)

def _cancel_request(request_id):
    with _active_requests_lock:
        req = _active_requests.get(request_id)
    if not req:
        return False
    req.cancelled.set()
    return True

def _handle_shutdown_signal(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    print("[proxy] shutdown requested; draining connections", file=sys.stderr)
    def _drain():
        deadline = time.time() + 5
        while time.time() < deadline:
            with _active_connections_lock:
                if _active_connections == 0:
                    break
            time.sleep(0.1)
        if SERVER is not None:
            SERVER.shutdown()
    threading.Thread(target=_drain, daemon=True).start()



class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    _request_failed = False

    def do_GET(self):
         if self.path in ("/v1/models", "/models"):
             self.send_json(200, {"object": "list", "data": MODELS})
         elif self.path in ("/v1/accounts", "/accounts"):
             info = {"provider": BACKEND, "oauth_provider": OAUTH_PROVIDER}
             if BACKEND in ("codebuff", "freebuff"):
                 info["accounts"] = _cb_pool.status()
                 info["total"] = len(_cb_pool._accounts)
             elif OAUTH_PROVIDER and OAUTH_PROVIDER.startswith("google"):
                 pool = _google_antigravity_pool if OAUTH_PROVIDER == "google-antigravity" else _google_cli_pool
                 info["accounts"] = pool.status()
                 info["total"] = len(pool._accounts)
             elif _api_key_pool:
                 info["accounts"] = _api_key_pool.status()
                 info["total"] = len(_api_key_pool._accounts)
             else:
                 info["accounts"] = []
                 info["total"] = 0
             self.send_json(200, info)
         elif self.path in ("/health", "/v1/health"):
            _mem_mb = 0
            try:
                if _IS_WINDOWS:
                    import ctypes
                    class _PMI(ctypes.Structure):
                        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
                    _pmi = _PMI()
                    _pmi.cb = ctypes.sizeof(_PMI)
                    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
                    ctypes.windll.psapi.GetProcessMemoryInfo.restype = ctypes.c_int
                    ctypes.windll.psapi.GetProcessMemoryInfo(
                        ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(_pmi), _pmi.cb)
                    _mem_mb = _pmi.PeakWorkingSetSize / (1024 * 1024)
                else:
                    import resource as _res
                    _mem_mb = _res.getrusage(_res.RUSAGE_SELF).ru_maxrss / 1024
            except Exception:
                pass
            _uptime = time.time() - _START_TIME if '_START_TIME' in dir() else 0
            self.send_json(200, {"ok": True, "backend": BACKEND,
                                  "target_url": TARGET_URL,
                                  "models": [m.get("id") for m in MODELS],
                                  "bgp_routes": len(BGP_ROUTES),
                                  "uptime_s": round(_uptime, 1),
                                  "memory_mb": round(_mem_mb, 1),
                                   "requests_total": _STATS.get("requests", 0)})
         elif self.path == "/admin/reload":
            reloaded = _hot_reload_api_key()
            key_preview = API_KEY[:8] + "..." if len(API_KEY) > 8 else "(empty)"
            self.send_json(200, {"ok": True, "reloaded": reloaded,
                                 "api_key_preview": key_preview,
                                 "config_path": _CONFIG_PATH or "none"})
         elif self.path == "/admin/verify-key":
            result = _verify_api_key(API_KEY, TARGET_URL)
            key_preview = API_KEY[:8] + "..." if len(API_KEY) > 8 else "(empty)"
            result["api_key_preview"] = key_preview
            self.send_json(200, result)
         else:
             self.send_error(404)

    def do_POST(self):
        if _shutdown_requested:
            return self.send_json(503, {"error": {"type": "proxy_shutting_down",
                                                   "message": "Proxy is shutting down"}})
        if self.path.startswith("/admin/cancel/"):
            request_id = self.path.rsplit("/", 1)[-1]
            if _cancel_request(request_id):
                return self.send_json(200, {"ok": True, "cancelled": request_id})
            return self.send_json(404, {"ok": False, "error": "request_not_found"})
        if self.path in ("/v1/responses", "/responses"):
            with ConnectionTracker():
                self._handle()
        else:
            self.send_error(404)

    _logf = None

    def _handle(self):
        _hot_reload_api_key()
        try:
            clen = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(clen))
        except Exception as e:
            return self.send_json(400, {"error": {"message": f"Bad request: {e}"}})

        if CAVEMAN_MODE and isinstance(body, dict):
            instructions = body.get("instructions", "").strip()
            caveman_prompt = "You are in Caveman Mode. Answer concisely, using technical terms. Strip away all pleasantries, greetings, introductory filler, and concluding remarks. Go straight to the answer/code."
            if instructions:
                instructions = f"{instructions}\n\n{caveman_prompt}"
            else:
                instructions = caveman_prompt
            body["instructions"] = instructions

        self._session_id = uuid.uuid4().hex[:8]
        _sid = self._session_id

        # Check Circuit Breaker
        cb = get_circuit_breaker(BACKEND)
        if not cb.can_execute():
            print(f"[{_sid}] Circuit breaker is OPEN for backend {BACKEND}. Rejecting request.", file=sys.stderr)
            return self.send_json(503, {"error": {"type": "circuit_breaker_open", "message": f"Circuit breaker is open for backend {BACKEND}"}})

        import datetime as _dt
        _log_path = os.path.join(_LOG_DIR, "requests.log")
        _ts = _dt.datetime.now().isoformat()

        prev_id = body.get("previous_response_id")
        raw_input = body.get("input", "")
        input_data = resolve_previous_response(body)
        input_data = _compact_input(input_data)
        body["input"] = input_data

        raw_types = [i.get("type") for i in raw_input] if isinstance(raw_input, list) else "str"
        resolved_types = [i.get("type") for i in input_data] if isinstance(input_data, list) else "str"

        with open(_log_path, "a", encoding="utf-8") as _lf:
            _lf.write(f"\n{'='*60}\n{_ts} [session={_sid}] REQUEST {self.path}\n")
            _lf.write(f"  prev_id={prev_id}\n")
            _lf.write(f"  raw_input_types={raw_types}\n")
            _lf.write(f"  resolved_input_types={resolved_types}\n")
            _lf.write(f"  stream={body.get('stream')} model={body.get('model')} force_model={FORCE_MODEL}\n")
            _lf.write(f"  store_keys={list(_response_store.keys())}\n")
            if isinstance(input_data, list):
                for i, item in enumerate(input_data):
                    t = item.get("type")
                    if t == "message":
                        _lf.write(f"  [{i}] message role={item.get('role')} text={str(item.get('content',''))[:120]}\n")
                    elif t == "function_call":
                        _lf.write(f"  [{i}] function_call call_id={item.get('call_id')} id={item.get('id')} name={item.get('name')} args={item.get('arguments','')[:120]}\n")
                    elif t == "function_call_output":
                        _lf.write(f"  [{i}] function_call_output id={item.get('id')} output={str(item.get('output',''))[:120]}\n")
                    else:
                        _lf.write(f"  [{i}] {t}\n")
            _lf.flush()

        model = body.get("model", MODELS[0]["id"] if MODELS else "unknown")
        if FORCE_MODEL:
            # Only remap if the incoming model matches the normalized FORCE_MODEL.
            # Strip prefix (e.g. "remote/") before comparing, same as Codex does.
            _norm = lambda m: re.sub(r'[^a-z0-9]', '', m.rsplit('/', 1)[-1].lower())
            if _norm(model) == _norm(FORCE_MODEL):
                model = FORCE_MODEL
                body["model"] = FORCE_MODEL
        stream = body.get("stream", False)
        _desktop_forced_models = {"gpt-5.4-mini", "gpt-5.4", "gpt-5.5", "gpt-5-codex", "gpt-5.3-codex"}
        _launcher_model = os.environ.get("CODEX_LAUNCHER_MODEL", "") or FORCE_MODEL
        if _launcher_model and model in _desktop_forced_models:
            print(f"[{_sid}] remap desktop model {model} -> {_launcher_model}", file=sys.stderr)
            model = _launcher_model
            body["model"] = model
        request_id = body.get("request_id") or body.get("id") or uid("req")
        if isinstance(input_data, list):
            for item in input_data:
                if isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "user":
                    content = str(item.get("content", ""))
                    for url_m in re.finditer(r"https?://[^\s\]'\"<>]+", content):
                        _last_user_urls.append(url_m.group(0))
        save_request_snapshot(request_id, body)
        _req_t0 = time.time()
        wait_start = time.monotonic()
        _request_semaphore.acquire()
        wait_ms = (time.monotonic() - wait_start) * 1000
        if wait_ms > 100:
            print(f"[{_sid}] waited {wait_ms:.0f}ms for upstream slot (concurrency gate)", file=sys.stderr)
        self._last_status = 200
        self._request_failed = False
        cb = get_circuit_breaker(BACKEND)
        try:
            with RequestTracker(request_id) as tracker:
                if BACKEND == "auto":
                    self._handle_auto(body, model, stream, tracker)
                elif BACKEND == "anthropic":
                    self._handle_anthropic(body, model, stream, tracker)
                elif BACKEND == "command-code":
                    self._handle_command_code(body, model, stream, tracker)
                elif BACKEND in ("codebuff", "freebuff"):
                    self._handle_codebuff(body, model, stream, tracker)
                elif (BACKEND or "").startswith("gemini-oauth"):
                    if OAUTH_PROVIDER == "google-antigravity":
                        self._handle_antigravity_v2(body, model, stream, tracker)
                    else:
                        self._handle_gemini_oauth(body, model, stream, tracker)
                elif BACKEND == "kiro-oauth":
                    self._handle_kiro(body, model, stream, tracker)
                else:
                    self._handle_openai_compat(body, model, stream, tracker)
            
            # Record success or failure
            is_4xx = (400 <= getattr(self, "_last_status", 200) < 500)
            if self._request_failed and not is_4xx:
                cb.record_failure()
            else:
                cb.record_success()
                
            update_snapshot_response(request_id, "completed", time.time() - _req_t0)
        except Exception as _snap_err:
            is_4xx = (400 <= getattr(self, "_last_status", 200) < 500)
            if not is_4xx:
                cb.record_failure()
            update_snapshot_response(request_id, "error", time.time() - _req_t0, _snap_err)
            raise
        finally:
            _request_semaphore.release()

    def _handle_openai_compat(self, body, model, stream, tracker=None):
        input_data = body.get("input", "")
        policy = provider_policy()

        pair_errors = validate_tool_pairs(input_data)
        if pair_errors:
            print(f"[tool-validator] repairing {len(pair_errors)} orphan tool outputs", file=sys.stderr)
            input_data = repair_orphan_tool_outputs(input_data, pair_errors)
            body = dict(body)
            body["input"] = input_data

        # synthetic tool-results disabled: causes deepseek-v4-pro truncation on opencode.ai
        if False and (policy.get("synthetic_tool_results") or _provider_cap(model, "synthetic_tool_results", False)) and isinstance(input_data, list):
            input_data, synthesized = synthesize_tool_results_for_chat(input_data)
            if synthesized:
                print("[provider-adapter] using synthetic tool-result continuation", file=sys.stderr)
                body = dict(body)
                body["input"] = input_data

        compacted = False
        if ADAPTIVE_COMPACT and policy.get("compaction") and isinstance(input_data, list) and "claude" not in model.lower():
            input_data, compacted = _adaptive_compact(input_data, model, policy)
            if compacted:
                body = dict(body)
                body["input"] = input_data

        if PROMPT_ENHANCER and isinstance(input_data, list):
            input_data = _apply_prompt_enhancer(input_data)
            body = dict(body)
            body["input"] = input_data

        crof_limit = _crof_item_limit(model)
        _crof_eligible = "crof.ai" in TARGET_URL
        if _crof_eligible and not compacted and isinstance(input_data, list):
            _needs_compact = len(input_data) > crof_limit
            max_tok = _get_model_max_tokens(model)
            est_tok = _estimate_input_tokens(input_data) if max_tok else 0
            if not _needs_compact and max_tok and est_tok > max_tok * 0.8:
                _needs_compact = True
            if _needs_compact:
                _agg = 0
                if max_tok and est_tok > max_tok:
                    _agg = 1
                print(f"[crof-adaptive] proactive compact: {len(input_data)} items, est={est_tok}tok max={max_tok}tok agg={_agg}", file=sys.stderr)
                input_data = _crof_compact_for_retry(input_data, model, aggression=_agg)
                body = dict(body)
                body["input"] = input_data

        # Vision preprocessing for non-vision models
        _schema = _load_schema(model=model)
        _needs_vision_preprocess = False
        if _schema and not _schema.supports_vision:
            _needs_vision_preprocess = True
        elif not _model_supports_vision(model):
            print(f"[vision] model {model} detected as non-vision via name pattern, preprocessing images", file=sys.stderr)
            if _schema:
                _schema.supports_vision = False
                _save_schema(_schema, model=model)
            _needs_vision_preprocess = True
        if _needs_vision_preprocess:
            input_data = _preprocess_vision_input(input_data, _schema)
            body["input"] = input_data

        messages = oa_input_to_messages(input_data)
        messages = _inject_stored_reasoning(messages)
        instructions = body.get("instructions", "").strip()
        if instructions:
            messages.insert(0, {"role": "system", "content": instructions})

        if BGP_ROUTES:
            self._handle_bgp(body, model, stream, messages, input_data)
        else:
            chat_body = self._build_chat_body(model, messages, body, stream)
            target = upstream_target(TARGET_URL, "/chat/completions")
            if _api_key_pool:
                pool_acct = _api_key_pool.get()
                effective_key = pool_acct["token"] if pool_acct else API_KEY
            else:
                effective_key = _refresh_oauth_token()
            fwd = forwarded_headers(self.headers, {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {effective_key}",
                **_openrouter_extra(),
            }, browser_ua=True)
            print(f"[{self._session_id}] POST {target} model={model} stream={stream} items={len(input_data) if isinstance(input_data,list) else 1}", file=sys.stderr)
            chat_body_b = json.dumps(chat_body).encode()
            max_retries = 3
            for attempt in range(max_retries + 1):
                req = urllib.request.Request(target, data=chat_body_b, headers=fwd)
                try:
                    upstream = urllib.request.urlopen(req, timeout=_upstream_timeout(body, stream))
                except urllib.error.HTTPError as e:
                    err_body = e.read().decode()
                    if re.search(r"unknown variant\b.*image_url", err_body.lower()) or \
                       re.search(r"unexpected.*image_url", err_body.lower()) or \
                       re.search(r"does not support.*image", err_body.lower()):
                        _schema = _load_schema(model=model)
                        if _schema:
                            _schema.supports_vision = False
                        if attempt < max_retries:
                            print(f"[{self._session_id}] vision not supported, retrying with image preprocessing", file=sys.stderr)
                            messages = _preprocess_vision(messages, _schema) if _schema else messages
                            chat_body = self._build_chat_body(model, messages, body, stream)
                            chat_body_b = json.dumps(chat_body).encode()
                            continue
                    if "context_length_exceeded" in err_body and attempt < max_retries:
                        import re as _re
                        _tok_m = _re.search(r'~?(\d+)\s*tokens', err_body)
                        if _tok_m:
                            _set_model_max_tokens(model, int(_tok_m.group(1)))
                        print(f"[{self._session_id}] context_length_exceeded (attempt {attempt+1}/{max_retries}), retrying with compaction (agg={attempt})!", file=sys.stderr)
                        policy = provider_policy()
                        if isinstance(input_data, list):
                            est = _estimate_input_tokens(input_data)
                            print(f"[{self._session_id}] applying compaction to {len(input_data)} items ~{est}tok", file=sys.stderr)
                            input_data = _crof_compact_for_retry(input_data, model, aggression=attempt)
                            body = dict(body)
                            body["input"] = input_data
                            messages = oa_input_to_messages(_preprocess_vision_input(input_data, _schema) if _schema and not _schema.supports_vision else input_data)
                            messages = _inject_stored_reasoning(messages)
                            instructions = body.get("instructions", "").strip()
                            if instructions:
                                messages.insert(0, {"role": "system", "content": instructions})
                            chat_body = self._build_chat_body(model, messages, body, stream)
                            chat_body_b = json.dumps(chat_body).encode()
                            continue
                    if e.code in (429, 502, 503) and attempt < max_retries:
                        if e.code == 429 and _api_key_pool:
                            pool_acct = _api_key_pool.get()
                            if pool_acct:
                                _api_key_pool.mark_rate_limited(pool_acct, 60)
                                next_acct = _api_key_pool.get()
                                if next_acct:
                                    effective_key = next_acct["token"]
                                    fwd["Authorization"] = f"Bearer {effective_key}"
                                    print(f"[multi-account] rotating to key {next_acct['id']}", file=sys.stderr)
                        retry_after = e.headers.get("Retry-After")
                        if retry_after:
                            try:
                                wait = min(int(retry_after), 60)
                            except ValueError:
                                wait = min(2 ** (attempt + 1), 15)
                        else:
                            wait = min(2 ** (attempt + 1), 15)
                        print(f"[{self._session_id}] HTTP {e.code} (attempt {attempt+1}/{max_retries}), retrying in {wait}s: {err_body[:150]}", file=sys.stderr)
                        time.sleep(wait)
                        continue
                    return self.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as e:
                    if attempt < max_retries:
                        wait = min(2 ** (attempt + 1), 10)
                        print(f"[{self._session_id}] connection error (attempt {attempt+1}/{max_retries}), retrying in {wait}s: {e}", file=sys.stderr)
                        time.sleep(wait)
                        continue
                    return self.send_json(502, {"error": {"type": "proxy_error", "message": str(e)}})
                except Exception as e:
                    return self.send_json(500, {"error": {"type": "proxy_error", "message": str(e)}})
                break
            self._forward_oa_compat(upstream, stream, model, chat_body, body, input_data, fwd, target, tracker)

    @staticmethod
    def _is_mimo_provider():
        return "xiaomimimo.com" in TARGET_URL

    @staticmethod
    def _make_cache_key(model, instructions):
        import hashlib
        raw = f"{model}|{instructions}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _build_chat_body(self, model, messages, body, stream):
        chat_body = {"model": model, "messages": messages}
        is_mimo = self._is_mimo_provider()
        for k in ("temperature", "top_p"):
            if k in body:
                chat_body[k] = body[k]

        # Clamp max output tokens dynamically to fit the remaining context budget
        policy = provider_policy()
        context_size = int(policy.get("context_size", policy.get("max_tokens", _context_limit_for_model(model))))
        prompt_tokens = _estimate_tokens(messages)
        remaining = context_size - prompt_tokens
        allowed_max_output = max(1024, remaining - 500)

        max_tok = max(body.get("max_output_tokens", 0), 64000)
        max_tok = min(max_tok, allowed_max_output)

        if is_mimo:
            chat_body["max_completion_tokens"] = max_tok
        else:
            chat_body["max_tokens"] = max_tok
        tools = oa_convert_tools(body.get("tools"))
        if tools:
            chat_body["tools"] = tools
        if body.get("tool_choice"):
            chat_body["tool_choice"] = body["tool_choice"]
        chat_body["stream"] = stream
        _pc_policy = provider_policy().get("prompt_caching", "auto")
        if _pc_policy != "none" and not is_mimo:
            _instr = body.get("instructions", "").strip()
            if _instr:
                chat_body["prompt_cache_key"] = self._make_cache_key(model, _instr)
                chat_body["prompt_cache_retention"] = "24h"
        if is_mimo:
            if not REASONING_ENABLED or REASONING_EFFORT == "none":
                chat_body["thinking"] = {"type": "disabled"}
            else:
                mimo_effort = {"minimal": "low", "max": "high"}.get(REASONING_EFFORT, REASONING_EFFORT)
                chat_body["thinking"] = {"type": "enabled"}
                chat_body["reasoning_effort"] = mimo_effort
        else:
            if not REASONING_ENABLED or REASONING_EFFORT == "none":
                chat_body["enable_thinking"] = False
                chat_body["reasoning_effort"] = "none"
            else:
                chat_body["reasoning_effort"] = REASONING_EFFORT
        return chat_body

    def _handle_antigravity_v2(self, body, model, stream, tracker=None):
        from proxy.adapters.gemini import handle_antigravity_v2
        return handle_antigravity_v2(self, body, model, stream, tracker)

    def _try_grpc_fallback(self, wrapped_dict, access_token, stream, tracker=None):
        from proxy.adapters.gemini import try_grpc_fallback
        return try_grpc_fallback(self, wrapped_dict, access_token, stream, tracker)

    def _forward_grpc_sse(self, grpc_result, model):
        from proxy.adapters.gemini import forward_grpc_sse
        return forward_grpc_sse(self, grpc_result, model)

    def _forward_grpc_json(self, grpc_result, model):
        from proxy.adapters.gemini import forward_grpc_json
        return forward_grpc_json(self, grpc_result, model)

    def _handle_gemini_oauth(self, body, model, stream, tracker=None):
        from proxy.adapters.gemini import handle_gemini_oauth
        return handle_gemini_oauth(self, body, model, stream, tracker)

    def _forward_gemini_sse(self, upstream, model, body, input_data, tracker=None):
        from proxy.adapters.gemini import forward_gemini_sse
        return forward_gemini_sse(self, upstream, model, body, input_data, tracker)

    def _forward_gemini_json(self, upstream, model, body, input_data):
        from proxy.adapters.gemini import forward_gemini_json
        return forward_gemini_json(self, upstream, model, body, input_data)

    def _handle_bgp(self, body, model, stream, messages, input_data):
        routes = _sorted_bgp_routes()
        routes = [r for r in routes if _bucket_for_route(r).allow()]
        if not routes:
            return self.send_json(503, {"error": {"type": "bgp_rate_limited", "message": "All routes rate-limited"}})
        errors = []
        for route in routes:
            r_model = route.get("model", model)
            r_url = route["target_url"].rstrip("/")
            r_key = route.get("api_key", "")
            r_reasoning = route.get("reasoning_enabled", True)
            r_effort = route.get("reasoning_effort", "medium")
            r_oauth = route.get("oauth_provider", "")

            chat_body = dict(messages=list(messages))
            chat_body["model"] = r_model
            for k in ("temperature", "top_p"):
                if k in body:
                    chat_body[k] = body[k]
            chat_body["max_tokens"] = max(body.get("max_output_tokens", 0), 64000)
            tools = oa_convert_tools(body.get("tools"))
            if tools:
                chat_body["tools"] = tools
            if body.get("tool_choice"):
                chat_body["tool_choice"] = body["tool_choice"]
            chat_body["stream"] = stream
            if not r_reasoning or r_effort == "none":
                chat_body["enable_thinking"] = False
                chat_body["reasoning_effort"] = "none"
            else:
                chat_body["reasoning_effort"] = r_effort

            target = upstream_target(r_url, "/chat/completions")
            if r_oauth == "google":
                r_key = _refresh_oauth_token_for(r_key, r_oauth)
            fwd = forwarded_headers(self.headers, {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {r_key}",
                **_openrouter_extra(),
            }, browser_ua=True)
            print(f"[{self._session_id}] trying route '{route.get('name', r_url)}' model={r_model}", file=sys.stderr)
            req = urllib.request.Request(target, data=json.dumps(chat_body).encode(), headers=fwd)
            t0_route = time.time()
            route_ok = False
            for attempt in range(3):
                try:
                    upstream = urllib.request.urlopen(req, timeout=_upstream_timeout(body, stream))
                    print(f"[{self._session_id}] route '{route.get('name', r_url)}' connected OK", file=sys.stderr)
                    _update_route_stats(route, True, time.time() - t0_route)
                    self._forward_oa_compat(upstream, stream, r_model, chat_body, body, input_data, fwd, target)
                    return
                except urllib.error.HTTPError as e:
                    err = e.read().decode()
                    if e.code in (429, 502, 503) and attempt < 2:
                        retry_after = e.headers.get("Retry-After")
                        wait = min(int(retry_after), 60) if retry_after and retry_after.isdigit() else min(2 ** (attempt + 1), 10)
                        print(f"[{self._session_id}] route '{route.get('name', r_url)}' HTTP {e.code}, retry {attempt+1}/2 in {wait}s", file=sys.stderr)
                        time.sleep(wait)
                        req = urllib.request.Request(target, data=json.dumps(chat_body).encode(), headers=fwd)
                        continue
                    print(f"[{self._session_id}] route '{route.get('name', r_url)}' FAILED: HTTP {e.code}: {err[:200]}", file=sys.stderr)
                    _update_route_stats(route, False, time.time() - t0_route, http_code=e.code)
                    errors.append(f"{route.get('name','?')}: HTTP {e.code}")
                    break
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as e:
                    if attempt < 2:
                        wait = min(2 ** (attempt + 1), 8)
                        print(f"[{self._session_id}] route '{route.get('name', r_url)}' conn error, retry {attempt+1}/2 in {wait}s: {e}", file=sys.stderr)
                        time.sleep(wait)
                        req = urllib.request.Request(target, data=json.dumps(chat_body).encode(), headers=fwd)
                        continue
                    _update_route_stats(route, False, time.time() - t0_route, error_type=str(e))
                    errors.append(f"{route.get('name','?')}: {e}")
                    break
                except Exception as e:
                    print(f"[{self._session_id}] route '{route.get('name', r_url)}' FAILED: {e}", file=sys.stderr)
                    _update_route_stats(route, False, time.time() - t0_route, error_type=str(e))
                    errors.append(f"{route.get('name','?')}: {e}")
                    break

        print(f"[{self._session_id}] ALL ROUTES FAILED: {errors}", file=sys.stderr)
        self.send_json(502, {"error": {"type": "bgp_all_routes_failed", "message": f"All BGP routes failed: {'; '.join(errors)}"}})

    def _forward_oa_compat(self, upstream, stream, model, chat_body, body, input_data, fwd, target, tracker=None):
        n_items = len(input_data) if isinstance(input_data, list) else 1
        t0 = time.time()
        provider = TARGET_URL.split("//")[-1].split("/")[0]
        if BGP_ROUTES:
            provider = "bgp:" + (BGP_ROUTES[0].get("name", "pool") if BGP_ROUTES else "unknown")

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            if hasattr(self, 'connection') and self.connection:
                try:
                    self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass

            collected_events = []
            last_resp_id = None
            last_output = None
            last_status = None
            finish_reason = None
            has_content = False
            has_message = False
            has_tool_call = False
            _last_stream_usage = {}

            def _observe_event(event):
                nonlocal last_resp_id, last_output, last_status, finish_reason, has_content, has_message, has_tool_call, _last_stream_usage
                for line in event.strip().split("\n"):
                    if line.startswith("data: "):
                        try:
                            d = json.loads(line[6:])
                            if d.get("type") == "response.completed":
                                last_resp_id = d.get("response", {}).get("id")
                                last_output = d.get("response", {}).get("output", [])
                                last_status = d.get("response", {}).get("status")
                                finish_reason = "length" if last_status == "incomplete" else "stop"
                                has_tool_call = any(o.get("type") == "function_call" for o in (last_output or []))
                                has_message = any(o.get("type") == "message" for o in (last_output or []))
                                has_content = has_message or has_tool_call
                                resp_usage = d.get("response", {}).get("usage")
                                if resp_usage:
                                    _last_stream_usage = resp_usage
                        except Exception:
                            pass

            try:
                reasoning_out = {}
                for event in oa_stream_to_sse(upstream, model, body.get("request_id") or body.get("id"), _reasoning_out=reasoning_out):
                    if tracker and tracker.cancelled.is_set():
                        print("[translate-proxy] stream cancelled", file=sys.stderr)
                        break
                    collected_events.append(event)
                    _observe_event(event)
                print(f"[{self._session_id}] stream ended: events={len(collected_events)} finish={finish_reason} has_content={has_content} has_message={has_message} has_tool_call={has_tool_call} elapsed={time.time()-t0:.1f}s", file=sys.stderr)
                pass
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                print("[translate-proxy] client disconnected during stream", file=sys.stderr)
                _crof_record(model, n_items, False)
                _log_resp(last_resp_id, "client_disconnect", last_output)
                return
            except (TimeoutError, OSError, urllib.error.URLError) as e:
                print(f"[translate-proxy upstream error during stream: {type(e).__name__}: {e}", file=sys.stderr)
                self._request_failed = True
                err_resp_id = body.get("request_id") or body.get("id") or uid("resp")
                try:
                    self.wfile.write(emit("response.failed", {"type": "response.failed",
                        "response": {"id": err_resp_id, "error": {"type": "upstream_error",
                        "code": "stream_interrupted", "message": str(e)[:200]}}}).encode())
                    self.wfile.flush()
                except Exception:
                    pass
                _crof_record(model, n_items, False)
                _log_resp(last_resp_id, "upstream_error", last_output)
                return

            # Record outcome
            success = (finish_reason != "length")
            _crof_record(model, n_items, success)
            _log_resp(last_resp_id, last_status, last_output)
            if last_resp_id and input_data is not None:
                store_response(last_resp_id, input_data, last_output)
            if reasoning_out.get("text"):
                with _last_reasoning_lock:
                    _last_reasoning_store[last_resp_id or ""] = {
                        "reasoning": reasoning_out["text"],
                        "tool_calls": reasoning_out.get("tool_calls", []),
                        "ts": time.time(),
                    }
                    while len(_last_reasoning_store) > _MAX_STORED:
                        oldest = next(iter(_last_reasoning_store))
                        del _last_reasoning_store[oldest]
            _record_usage_with_tokens(provider, model, success, time.time() - t0, _last_stream_usage, error_type="length" if not success else None, input_items=input_data, output_items=last_output)

            # Auto-learn provider quirks before flushing the bad response to Codex.
            if finish_reason == "length" and not has_content and has_function_call_output(input_data):
                _set_provider_cap(model, "synthetic_tool_results", True, "incomplete empty response after tool output")
                new_input, synthesized = synthesize_tool_results_for_chat(input_data)
                if synthesized:
                    print("[provider-sensor] retrying turn with synthetic tool results", file=sys.stderr)
                    new_messages = oa_input_to_messages(new_input)
                    instructions = body.get("instructions", "").strip()
                    if instructions:
                        new_messages.insert(0, {"role": "system", "content": instructions})
                    new_chat_body = self._build_chat_body(model, new_messages, body, stream)
                    new_req = urllib.request.Request(target, data=json.dumps(new_chat_body).encode(), headers=fwd)
                    try:
                        retry_upstream = urllib.request.urlopen(new_req, timeout=_upstream_timeout(body, True))
                        collected_events = []
                        last_resp_id = last_output = last_status = None
                        finish_reason = None
                        has_content = False
                        has_message = False
                        has_tool_call = False
                        for event in oa_stream_to_sse(retry_upstream, model, body.get("request_id") or body.get("id")):
                            collected_events.append(event)
                            _observe_event(event)
                        input_data = new_input
                    except Exception as e:
                        print(f"[provider-sensor] synthetic retry failed: {e}", file=sys.stderr)

            # Auto-retry on finish_reason=length with no content due to too much context.
            if finish_reason == "length" and not has_content and isinstance(input_data, list) and len(input_data) > 5:
                print(f"[crof-adaptive] RETRY: finish_reason=length with no content, compacting {n_items} items", file=sys.stderr)
                new_input = _crof_compact_for_retry(input_data, model)
                if len(new_input) < len(input_data):
                    new_body = dict(body)
                    new_body["input"] = new_input
                    new_messages = oa_input_to_messages(new_input)
                    instructions = body.get("instructions", "").strip()
                    if instructions:
                        new_messages.insert(0, {"role": "system", "content": instructions})
                    new_chat_body = dict(chat_body)
                    new_chat_body["messages"] = new_messages
                    new_req = urllib.request.Request(
                        target,
                        data=json.dumps(new_chat_body).encode(),
                        headers=fwd,
                    )
                    try:
                        retry_upstream = urllib.request.urlopen(new_req, timeout=_upstream_timeout(body, True))
                        collected_events = []
                        last_resp_id = last_output = last_status = None
                        finish_reason = None
                        has_content = False
                        has_message = False
                        has_tool_call = False
                        for event in oa_stream_to_sse(retry_upstream, model, body.get("request_id") or body.get("id")):
                            collected_events.append(event)
                            _observe_event(event)
                        input_data = new_input
                    except Exception as e:
                        print(f"[crof-adaptive] retry failed: {e}", file=sys.stderr)

            # ── Auto-continue for truncated responses ── (cobra PR)
            _ac_did_run = False
            if stream and collected_events:
                _ac_text = ""
                _ac_msg_id = _ac_resp_id = None
                for _ev in collected_events:
                    for _ln in _ev.strip().split("\n"):
                        if not _ln.startswith("data: "):
                            continue
                        try:
                            _d = json.loads(_ln[6:])
                            _t = _d.get("type")
                            if _t == "response.output_text.done":
                                _ac_text = _d.get("text", "")
                            elif _t == "response.output_item.added" and _d.get("item",{}).get("type") == "message":
                                _ac_msg_id = _d.get("item",{}).get("id")
                            elif _t == "response.completed":
                                _ac_resp_id = _d.get("response",{}).get("id")
                        except Exception:
                            pass

                _ac_tc = reasoning_out.get("tool_calls", [])
                _ac_truncated = False
                if not _ac_tc and _ac_text:
                    _ac_stripped = _ac_text.rstrip()
                    if finish_reason == "length":
                        _ac_truncated = True
                    elif len(_ac_stripped) > 10 and _ac_stripped[-1] in "(:,;…":
                        _ac_truncated = True

                if _ac_truncated and _ac_text:
                    print(f"[{self._session_id}] auto-continue: truncated (finish={finish_reason}, ends '{_ac_text.rstrip()[-10:]}')", file=sys.stderr)
                    _ac_did_run = True
                    _ac_cut = len(collected_events)
                    for _i, _ev2 in enumerate(collected_events):
                        if "response.output_text.done" in _ev2:
                            _ac_cut = _i
                            break
                    collected_events = collected_events[:_ac_cut]

                    _ac_accumulated = _ac_text
                    _ac_max = 3
                    for _ac_attempt in range(_ac_max):
                        try:
                            _ac_cont_msgs = list(chat_body.get("messages", []))
                            _ac_cont_msgs.append({"role": "assistant", "content": _ac_accumulated})
                            _ac_cont_msgs.append({"role": "user", "content": "Continue exactly where you left off. Do not repeat anything already written."})
                            _ac_cont_body = dict(chat_body)
                            _ac_cont_body["messages"] = _ac_cont_msgs
                            _ac_cont_body["stream"] = False
                            _ac_cont_req = urllib.request.Request(target, data=json.dumps(_ac_cont_body).encode(), headers=fwd)
                            _ac_cont_resp = json.loads(urllib.request.urlopen(_ac_cont_req, timeout=120).read())
                            _ac_choices = _ac_cont_resp.get("choices", [])
                            if _ac_choices:
                                _ac_chunk = _ac_choices[0].get("message",{}).get("content","")
                                if not _ac_chunk:
                                    _ac_chunk = _ac_choices[0].get("delta",{}).get("content","")
                                _ac_finish = _ac_choices[0].get("finish_reason")
                                if _ac_chunk:
                                    _ac_accumulated += _ac_chunk
                                    collected_events.append(emit("response.output_text.delta", {
                                        "type": "response.output_text.delta",
                                        "delta": _ac_chunk, "item_id": _ac_msg_id, "content_index": 0}))
                                if _ac_finish != "length":
                                    break
                                _ac_text = _ac_accumulated
                        except Exception as _ac_e:
                            print(f"[{self._session_id}] auto-continue attempt {_ac_attempt+1} failed: {_ac_e}", file=sys.stderr)
                            break

                    if _ac_msg_id:
                        collected_events.append(emit("response.output_text.done", {
                            "type": "response.output_text.done",
                            "text": _ac_accumulated, "item_id": _ac_msg_id, "content_index": 0}))
                        collected_events.append(emit("response.content_part.done", {
                            "type": "response.content_part.done",
                            "part": {"type": "output_text", "text": _ac_accumulated, "annotations": []}, "item_id": _ac_msg_id}))
                        collected_events.append(emit("response.output_item.done", {
                            "type": "response.output_item.done",
                            "item": {"type": "message", "id": _ac_msg_id, "role": "assistant", "status": "completed",
                                     "content": [{"type": "output_text", "text": _ac_accumulated, "annotations": []}]}}))
                    if _ac_resp_id:
                        collected_events.append(emit("response.completed", {
                            "type": "response.completed",
                            "response": {"id": _ac_resp_id, "object": "response", "model": model,
                                         "status": "completed", "created": int(time.time()),
                                         "output": [{"type": "message", "id": _ac_msg_id, "role": "assistant",
                                                     "status": "completed",
                                                     "content": [{"type": "output_text", "text": _ac_accumulated, "annotations": []}]}]}}))
                    has_content = True
                    finish_reason = "stop"
                    print(f"[{self._session_id}] auto-continue done: {len(_ac_text)} -> {len(_ac_accumulated)} chars", file=sys.stderr)

            # Smart continuation: loop with escalating nudges when model stops text-only mid-task.
            # Skip if auto-continue already handled the response.
            if not _ac_did_run:
                _smart_max = 2
                _smart_attempt = 0
                while _smart_attempt < _smart_max:
                    _has_tool_calls_in_output = any(o.get("type") == "function_call" for o in (last_output or []))
                    last_text = ""
                    for o in (last_output or []):
                        if o.get("type") == "message":
                            for c in (o.get("content") or []):
                                if isinstance(c, dict) and c.get("type") == "output_text":
                                    last_text += c.get("text", "")
                    _looks_like_tools = _text_looks_like_tool_calls(last_text)
                    _has_prior_tool_ctx = has_function_call_output(input_data)
                    if not (finish_reason == "stop" and has_content and not _has_tool_calls_in_output
                            and isinstance(input_data, list) and len(input_data) >= 3
                            and (_has_prior_tool_ctx or _looks_like_tools)):
                        break
                    _smart_attempt += 1
                    _nudges = [
                        "Continue with the task using tool calls. Do NOT describe what to do — call the appropriate functions.",
                        "You MUST use tool calls to complete the task. Read files, run commands, and make changes using tools. Do NOT output XML tool calls as text.",
                    ]
                    nudge_text = _nudges[min(_smart_attempt - 1, len(_nudges) - 1)]
                    # Try extracting XML tool calls from text as fallback before nudging
                    xml_fc = _extract_xml_tool_calls(last_text)
                    if xml_fc:
                        print(f"[{self._session_id}] [smart-continue] extracted {len(xml_fc)} XML tool calls from text, injecting and retrying", file=sys.stderr)
                        fake_input = list(input_data)
                        for xfc in xml_fc:
                            fake_input.append({"type": "function_call", "id": uid("fcx"), "call_id": uid("fcx"),
                                               "name": xfc["name"], "arguments": xfc["args"], "status": "completed"})
                        fake_messages = oa_input_to_messages(fake_input)
                        instructions = body.get("instructions", "").strip()
                        if instructions:
                            fake_messages.insert(0, {"role": "system", "content": instructions})
                        fake_chat_body = self._build_chat_body(model, fake_messages, body, stream)
                        fake_req = urllib.request.Request(target, data=json.dumps(fake_chat_body).encode(), headers=fwd)
                        try:
                            retry_upstream = urllib.request.urlopen(fake_req, timeout=_upstream_timeout(body, True))
                            collected_events = []
                            last_resp_id = last_output = last_status = None
                            finish_reason = None
                            has_content = False
                            has_message = False
                            has_tool_call = False
                            for event in oa_stream_to_sse(retry_upstream, model, body.get("request_id") or body.get("id")):
                                collected_events.append(event)
                                _observe_event(event)
                            input_data = fake_input
                            continue
                        except Exception as e:
                            print(f"[{self._session_id}] [smart-continue] XML injection retry failed: {e}", file=sys.stderr)
                            break
                    _nudge_msg = {"role": "user", "content": nudge_text}
                    _nudge_schema = _load_schema(model=model)
                    nudge_messages = oa_input_to_messages(_preprocess_vision_input(input_data, _nudge_schema) if _nudge_schema and not _nudge_schema.supports_vision else input_data) + [_nudge_msg]
                    instructions = body.get("instructions", "").strip()
                    if instructions:
                        nudge_messages.insert(0, {"role": "system", "content": instructions})
                    nudge_chat_body = self._build_chat_body(model, nudge_messages, body, stream)
                    nudge_req = urllib.request.Request(target, data=json.dumps(nudge_chat_body).encode(), headers=fwd)
                    print(f"[{self._session_id}] [smart-continue] attempt {_smart_attempt}/{_smart_max}: model stopped mid-task (prior_ctx={_has_prior_tool_ctx} text_tools={_looks_like_tools}), nudging", file=sys.stderr)
                    try:
                        retry_upstream = urllib.request.urlopen(nudge_req, timeout=_upstream_timeout(body, True))
                        collected_events = []
                        last_resp_id = last_output = last_status = None
                        finish_reason = None
                        has_content = False
                        has_message = False
                        has_tool_call = False
                        for event in oa_stream_to_sse(retry_upstream, model, body.get("request_id") or body.get("id")):
                            collected_events.append(event)
                            _observe_event(event)
                    except Exception as e:
                        print(f"[{self._session_id}] [smart-continue] nudge attempt {_smart_attempt} failed: {e}", file=sys.stderr)
                        break

            self.stream_buffered_events(collected_events)
        else:
            raw_resp = json.loads(upstream.read())
            result = oa_resp_to_responses(raw_resp, model)
            success = result.get("status") != "incomplete"
            _log_cache_stats(raw_resp)
            _crof_record(model, n_items, success)
            _record_usage_with_tokens(provider, model, success, time.time() - t0, raw_resp, input_items=input_data, output_items=result.get("output", []))
            self.send_json(200, result)
            rid = result.get("id")
            _log_resp(rid, result.get("status"), result.get("output", []))
            if rid and input_data is not None:
                store_response(rid, input_data, result.get("output", []))

    def _forward_oa_compat_retry(self, req, model, chat_body, body, input_data, tracker=None):
        try:
            upstream = urllib.request.urlopen(req, timeout=_upstream_timeout(body, True))
        except Exception as e:
            print(f"[crof-adaptive] retry failed: {e}", file=sys.stderr)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        if hasattr(self, 'connection') and self.connection:
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass

        last_resp_id = None
        last_output = None
        last_status = None
        try:
            def on_event(event):
                nonlocal last_resp_id, last_output, last_status
                if tracker and tracker.cancelled.is_set():
                    print("[translate-proxy] retry stream cancelled", file=sys.stderr)
                    return False
                for line in event.strip().split("\n"):
                    if line.startswith("data: "):
                        try:
                            d = json.loads(line[6:])
                            if d.get("type") == "response.completed":
                                 last_resp_id = d.get("response", {}).get("id")
                                 last_output = d.get("response", {}).get("output", [])
                                 last_status = d.get("response", {}).get("status")
                        except (json.JSONDecodeError, KeyError, TypeError): pass
                return True
            self.stream_buffered_events(oa_stream_to_sse(upstream, model, body.get("request_id") or body.get("id")), on_event=on_event)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            print("[translate-proxy] client disconnected during retry stream", file=sys.stderr)

        n_items = len(input_data) if isinstance(input_data, list) else 1
        _crof_record(model, n_items, last_status == "completed")
        _log_resp(last_resp_id, last_status or "retry_disconnect", last_output)
        if last_resp_id and input_data is not None:
            store_response(last_resp_id, input_data, last_output)

    def _handle_anthropic(self, body, model, stream, tracker=None):
        from proxy.adapters.anthropic import handle
        return handle(self, body, model, stream, tracker)

    def _handle_kiro(self, body, model, stream, tracker=None):
        """Kiro (AWS CodeWhisperer) backend handler."""
        from proxy.adapters.kiro import handle
        return handle(self, body, model, stream, tracker)

    def _handle_command_code(self, body, model, stream, tracker=None):
        from proxy.adapters.command_code import handle
        return handle(self, body, model, stream, tracker)

    def _handle_codebuff(self, body, model, stream, tracker=None):
        from proxy.adapters.codebuff import handle_codebuff
        return handle_codebuff(self, body, model, stream, tracker)

    def _cb_retry_thinking_disabled(self, body, model, token, agent_id, stream, tracker, input_data, instructions, original_error, acct=None):
        from proxy.adapters.codebuff import cb_retry_thinking_disabled
        return cb_retry_thinking_disabled(self, body, model, token, agent_id, stream, tracker, input_data, instructions, original_error, acct)

    def _handle_auto(self, body, model, stream, tracker=None):
        """Auto-sensing backend: probe schema, adapt, retry on errors.
        Uses hostname heuristics as initial guess, then learns from errors
        and caches the learned schema for subsequent requests.
        """
        input_data = body.get("input", "")
        instructions = body.get("instructions", "").strip()

        policy = provider_policy()
        compacted = False
        if ADAPTIVE_COMPACT and policy.get("compaction") and isinstance(input_data, list) and "claude" not in model.lower():
            input_data, compacted = _adaptive_compact(input_data, model, policy)
            if compacted:
                body = dict(body)
                body["input"] = input_data

        schema = _load_schema(model=model)
        fresh = not schema.hints().get("_updated")
        host = urllib.parse.urlparse(TARGET_URL).netloc.lower()

        def _detect_style():
            cc = schema.cc_body_wrap or "commandcode" in host or "command-code" in host
            anth = schema.tool_call_style == "anthropic_tool_use" or any(h in host for h in ("anthropic", "claude"))
            return cc, anth

        is_cc, is_anthropic = _detect_style()

        def _endpoint():
            ep = schema.field_names.get("endpoint_path", "")
            if ep:
                return ep
            if is_cc:
                return "/alpha/generate"
            if is_anthropic:
                return "/messages"
            return "/chat/completions"

        _FALLBACK_ENDPOINTS = ["/v1/chat/completions", "/chat/completions",
                                "/v1/messages", "/messages",
                                "/alpha/generate", "/complete", "/v1/complete"]
        target = upstream_target(TARGET_URL, _endpoint())
        tried_endpoints = {target}  # track tried endpoints to avoid loops

        max_retries = 3
        prev_content_type = None  # for oscillation detection
        for attempt in range(max_retries + 1):
            # Preprocess images for text-only providers BEFORE conversion
            processed_input = _preprocess_vision_input(input_data, schema) if not schema.supports_vision else input_data
            adapter = SchemaAdapter(schema)
            processed_input = _preprocess_vision_input(input_data, schema) if not schema.supports_vision else input_data
            messages = adapter.convert(processed_input, instructions)
            use_cc_wrap = schema.cc_body_wrap or is_cc

            # Build auth header from schema
            auth_val = f"{schema.auth_scheme}{API_KEY}" if schema.auth_scheme else API_KEY
            headers_extra = {"Content-Type": "application/json"}
            if schema.auth_header:
                headers_extra[schema.auth_header] = auth_val

            pm = schema.param_names  # short alias

            # Clamp max output tokens dynamically to fit the remaining context budget
            policy = provider_policy()
            context_size = int(policy.get("context_size", policy.get("max_tokens", _context_limit_for_model(model))))
            prompt_tokens = _estimate_tokens(messages)
            remaining = context_size - prompt_tokens
            allowed_max_output = max(1024, remaining - 500)

            if use_cc_wrap:
                thread_id = body.get("request_id") or body.get("id") or str(uuid.uuid4())
                try:
                    uuid.UUID(thread_id)
                except (ValueError, AttributeError):
                    thread_id = str(uuid.uuid4())
                max_tok_cc = min(max(body.get("max_output_tokens", 0), 64000), allowed_max_output)
                params_body = {
                    "stream": True,
                    pm.get("max_tokens", "max_tokens"): max_tok_cc,
                    pm.get("temperature", "temperature"): body.get("temperature", 0.3),
                    "messages": messages,
                    "model": model,
                }
                tp = schema.field_names.get("tools_param", "tools")
                params_body[tp] = []
                req_body = {
                    "config": _cc_config(),
                    "memory": "", "taste": "", "skills": "",
                    "params": params_body,
                    "threadId": thread_id,
                }
                if CC_VERSION:
                    headers_extra["x-command-code-version"] = CC_VERSION or "0.26.8"
            elif is_anthropic:
                max_tok_anth = min(body.get("max_output_tokens", 8192), allowed_max_output)
                req_body = {
                    "model": model,
                    "messages": messages,
                    pm.get("max_tokens", "max_tokens"): max_tok_anth,
                    "stream": stream,
                }
                if instructions:
                    req_body["system"] = [{"type": "text", "text": instructions}]
                tools = an_convert_tools(body.get("tools"))
                if tools:
                    req_body["tools"] = tools
                headers_extra.setdefault("anthropic-version", "2023-06-01")
            else:
                max_tok_oa = min(max(body.get("max_output_tokens", 0), 64000), allowed_max_output)
                req_body = {
                    "model": model,
                    "messages": messages,
                    pm.get("max_tokens", "max_tokens"): max_tok_oa,
                    "stream": stream,
                }
                for k in ("temperature", "top_p"):
                    pk = pm.get(k, k)
                    if k in body:
                        req_body[pk] = body[k]
                if schema.tool_decl_format == "anthropic":
                    tools = an_convert_tools(body.get("tools"))
                else:
                    tools = oa_convert_tools(body.get("tools"))
                if tools:
                    req_body["tools"] = tools
                    req_body["tool_choice"] = body.get("tool_choice", "auto")
                if not REASONING_ENABLED or REASONING_EFFORT == "none":
                    req_body["enable_thinking"] = False
                    req_body["reasoning_effort"] = "none"
                else:
                    req_body["reasoning_effort"] = REASONING_EFFORT

            req_body_b = json.dumps(req_body).encode()
            fwd = forwarded_headers(self.headers, {**headers_extra, **_openrouter_extra()}, browser_ua=True)
            print(f"[auto-sense] POST {target} model={model} attempt={attempt} schema={schema.hints()}", file=sys.stderr)

            req = urllib.request.Request(target, data=req_body_b, headers=fwd)
            try:
                upstream = urllib.request.urlopen(req, timeout=_upstream_timeout(body, stream))
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()
                # ── 404 endpoint fallback ──
                if e.code == 404 and attempt < max_retries:
                    for ep in _FALLBACK_ENDPOINTS:
                        ep_full = upstream_target(TARGET_URL, ep)
                        if ep_full not in tried_endpoints:
                            tried_endpoints.add(ep_full)
                            target = ep_full
                            # Try the new endpoint without schema change
                            print(f"[auto-sense] 404 -> trying endpoint {ep_full}", file=sys.stderr)
                            break
                    else:
                        # All endpoints tried -> real 404
                        return self.send_json(404, {"error": {"type": "not_found", "message": f"No working endpoint found (tried {len(tried_endpoints)} paths)"}})
                    continue
                # ── Non-404 error handling ──
                if attempt < max_retries:
                    hints = ErrorAnalyzer.analyze(err_body, schema)
                    oscillation_retry = False
                    if hints:
                        # Content-type oscillation detection
                        if "content_type" in hints:
                            if prev_content_type is not None and hints["content_type"] != prev_content_type:
                                print(f"[auto-sense] content_type oscillation: {prev_content_type} -> {hints['content_type']}, freezing", file=sys.stderr)
                                hints.pop("content_type")
                                schema.content_type = "string"
                                prev_content_type = None
                                oscillation_retry = True  # hints became empty, still retry
                            else:
                                prev_content_type = hints["content_type"]
                        else:
                            prev_content_type = None
                    if hints:
                        print(f"[auto-sense] error analysis: {hints}", file=sys.stderr)
                        ErrorAnalyzer.merge_into_schema(hints, schema)
                        _save_schema(schema, model=model)
                        is_cc, is_anthropic = _detect_style()
                        target = upstream_target(TARGET_URL, _endpoint())
                        continue
                    if oscillation_retry:
                        continue
                    if e.code in (429, 502, 503):
                        wait = min(2 ** (attempt + 1), 15)
                        time.sleep(wait)
                        continue
                return self.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            except Exception as e:
                if attempt < max_retries:
                    continue
                return self.send_json(500, {"error": {"type": "proxy_error", "message": str(e)}})

            if fresh:
                _save_schema(schema, model=model)
                fresh = False

            # Auto-detect stream/response format from Content-Type if still "auto"
            ct = (upstream.headers.get("Content-Type", "") if hasattr(upstream, "headers") else "").lower()
            if schema.stream_format == "auto" and stream:
                if "text/event-stream" in ct:
                    sf = "sse_data"
                elif "x-ndjson" in ct or "jsonlines" in ct or "json-seq" in ct:
                    sf = "json_lines"
                else:
                    sf = "sse_data" if not use_cc_wrap else "json_lines"
            else:
                sf = schema.stream_format
            if schema.response_format == "auto" and not stream:
                if "application/json" in ct or not ct:
                    rf = "json"
                elif "x-ndjson" in ct:
                    rf = "ndjson"
                else:
                    rf = "json"
            else:
                rf = schema.response_format

            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                if sf == "json_lines" or use_cc_wrap:
                    events = cc_stream_to_sse(upstream, model,
                                              body.get("request_id") or body.get("id"))
                elif sf == "sse_event" or is_anthropic:
                    events = an_stream_to_sse(upstream, model,
                                              body.get("request_id") or body.get("id"))
                else:
                    events = oa_stream_to_sse(upstream, model,
                                              body.get("request_id") or body.get("id"))
                self.stream_buffered_events(events)
            else:
                raw = upstream.read().decode().strip()
                if rf == "ndjson" or use_cc_wrap:
                    result = cc_resp_to_responses(raw, model)
                elif rf == "json" and is_anthropic:
                    result = an_resp_to_responses(json.loads(raw), model)
                else:
                    result = oa_resp_to_responses(json.loads(raw), model)
                self.send_json(200, result)
            return

    def _forward(self, req, stream, model, nonstream_fn, stream_fn, input_data=None, tracker=None):
        t0 = time.time()
        provider = TARGET_URL.split("//")[-1].split("/")[0]
        try:
            upstream = urllib.request.urlopen(req, timeout=_upstream_timeout({}, stream))
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            self._request_failed = True
            _record_usage(provider, model, False, time.time() - t0, error_type=f"HTTP_{e.code}", tokens_in=_estimate_tokens_from_items(input_data))
            return self.send_json(e.code, {"error": {"type": "upstream_error", "message": err}})
        except Exception as e:
            self._request_failed = True
            _record_usage(provider, model, False, time.time() - t0, error_type=type(e).__name__, tokens_in=_estimate_tokens_from_items(input_data))
            return self.send_json(500, {"error": {"type": "proxy_error", "message": str(e)}})

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            if hasattr(self, 'connection') and self.connection:
                try:
                    self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass
            last_resp_id = None
            last_output = None
            last_status = None
            _last_stream_usage = {}
            try:
                def on_event(event):
                    nonlocal last_resp_id, last_output, last_status, _last_stream_usage
                    if tracker and tracker.cancelled.is_set():
                        print("[translate-proxy] stream cancelled", file=sys.stderr)
                        return False
                    for line in event.strip().split("\n"):
                        if line.startswith("data: "):
                            try:
                                d = json.loads(line[6:])
                                if d.get("type") == "response.completed":
                                     last_resp_id = d.get("response", {}).get("id")
                                     last_output = d.get("response", {}).get("output", [])
                                     last_status = d.get("response", {}).get("status")
                                     resp_usage = d.get("response", {}).get("usage")
                                     if resp_usage:
                                         _last_stream_usage = resp_usage
                            except (json.JSONDecodeError, KeyError, TypeError): pass
                    return True
                self.stream_buffered_events(stream_fn(upstream), on_event=on_event)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                print("[translate-proxy] client disconnected during stream", file=sys.stderr)
            except Exception as e:
                print(f"[translate-proxy] stream error: {e}", file=sys.stderr)
                self._request_failed = True
            
            success = (last_status == "completed")
            _record_usage_with_tokens(provider, model, success, time.time() - t0, _last_stream_usage, input_items=input_data, output_items=last_output)
            _log_resp(last_resp_id, last_status or "client_disconnect", last_output)
            if last_resp_id and input_data is not None:
                store_response(last_resp_id, input_data, last_output)
        else:
            result = nonstream_fn(upstream)
            self.send_json(200, result)
            rid = result.get("id")
            success = result.get("status") != "incomplete"
            _record_usage_with_tokens(provider, model, success, time.time() - t0, result.get("usage", {}), input_items=input_data, output_items=result.get("output", []))
            _log_resp(rid, result.get("status"), result.get("output", []))
            if rid and input_data is not None:
                store_response(rid, input_data, result.get("output", []))

    def send_json(self, status, data):
        self._last_status = status
        if status >= 500:
            self._request_failed = True
        try:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _send_ag_finalize(self, text, stream=False, is_responses_api=True):
        sid = getattr(self, '_session_id', 'fin')
        print(f"[{sid}] [antigravity-finalize] Sending finalize-as-failed: {text[:80]}...", file=sys.stderr)
        _log_resp(f"finalize-{sid}", "failed", [{"type": "error", "code": "rate_limit_error", "message": text}])
        resp_id = f"resp_{uuid.uuid4().hex[:12]}"
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        error_output = [{"type": "error", "code": "rate_limit_error", "message": text}]
        text_output = [{"type": "message", "id": msg_id, "role": "assistant",
                        "content": [{"type": "output_text", "text": text}]}]
        if stream:
            events = [
                f"event: response.created\ndata: {json.dumps({'type':'response.created','response':{'id':resp_id,'object':'response','status':'in_progress'}})}\n\n",
                f"event: response.output_item.added\ndata: {json.dumps({'type':'response.output_item.added','output_index':0,'item':{'type':'message','id':msg_id,'role':'assistant','content':[]}})}\n\n",
                f"event: response.content_part.added\ndata: {json.dumps({'type':'response.content_part.added','output_index':0,'content_index':0,'part':{'type':'output_text','text':''}})}\n\n",
                f"event: response.output_text.delta\ndata: {json.dumps({'type':'response.output_text.delta','output_index':0,'content_index':0,'delta':text})}\n\n",
                f"event: response.output_text.done\ndata: {json.dumps({'type':'response.output_text.done','output_index':0,'content_index':0,'text':text})}\n\n",
                f"event: response.content_part.done\ndata: {json.dumps({'type':'response.content_part.done','output_index':0,'content_index':0,'part':{'type':'output_text','text':text}})}\n\n",
                f"event: response.output_item.done\ndata: {json.dumps({'type':'response.output_item.done','output_index':0,'item':{'type':'message','id':msg_id,'role':'assistant','content':[{'type':'output_text','text':text}]}})}\n\n",
                f"event: response.failed\ndata: {json.dumps({'type':'response.failed','response':{'id':resp_id,'object':'response','status':'failed','output':error_output}})}\n\n",
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            for evt in events:
                self.wfile.write(evt.encode())
                self.wfile.flush()
        else:
            self.send_json(200, {"id": resp_id, "object": "response", "status": "failed",
                                 "output": error_output + text_output, "model": "gemini-3-flash",
                                 "error": {"type": "rate_limit_error", "message": text}})
        return None

    def stream_buffered_events(self, event_iter, flush_interval=0.03, max_bytes=4096, on_event=None):
        buf = bytearray()
        last_flush = time.monotonic()
        _MAX_BUF = 8 * 1024 * 1024
        def _flush():
            nonlocal buf, last_flush
            if buf:
                self.wfile.write(buf)
                self.wfile.flush()
                buf.clear()
                last_flush = time.monotonic()
        for event in event_iter:
            if on_event is not None and on_event(event) is False:
                break
            encoded = event.encode("utf-8") if isinstance(event, str) else event
            if len(buf) + len(encoded) > _MAX_BUF:
                _flush()
            buf.extend(encoded)
            urgent = ("response.completed" in event or "response.output_text.done" in event
                      or "response.output_item.done" in event
                      or "function_call_arguments.done" in event
                      or "response.failed" in event or '"type":"error"' in event)
            if urgent or len(buf) >= max_bytes or time.monotonic() - last_flush >= flush_interval:
                _flush()
        _flush()

    def log_message(self, fmt, *args):
        msg = fmt % args if args else fmt
        _sid = getattr(self, '_session_id', None) or 'proxy'
        print(f"[{_sid}] {BACKEND} {msg}", file=sys.stderr)

def _anti_stall_cleanup():
    my_pid = os.getpid()
    killed = []
    try:
        # Scan log directory for proxy-*.pid files
        for f in os.listdir(_LOG_DIR):
            if f.startswith("proxy-") and f.endswith(".pid"):
                pid_path = os.path.join(_LOG_DIR, f)
                try:
                    with open(pid_path, "r", encoding="utf-8") as pf:
                        pid_str = pf.read().strip()
                    if pid_str.isdigit():
                        pid = int(pid_str)
                        if pid == my_pid:
                            continue
                        
                        # Verify if process is active and terminate it
                        is_active = False
                        if sys.platform == "win32":
                            try:
                                import ctypes
                                handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
                                if handle:
                                    ctypes.windll.kernel32.CloseHandle(handle)
                                    is_active = True
                            except Exception:
                                is_active = True
                        else:
                            try:
                                os.kill(pid, 0)
                                is_active = True
                            except OSError:
                                is_active = False

                        if is_active:
                            if sys.platform == "win32":
                                import subprocess as _sp
                                _sp.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=5)
                            else:
                                os.kill(pid, signal.SIGTERM)
                            killed.append(pid)
                    
                    # Remove the stale PID file
                    if os.path.exists(pid_path):
                        os.remove(pid_path)
                except Exception:
                    pass
    except Exception:
        pass
    # __pycache__ + log rotation now handled by cleanup_log_dir()
    if killed:
        print(f"[anti-stall] killed {len(killed)} stale proxy process(es): {killed}", flush=True)
        time.sleep(1)


def main():
    global SERVER, _START_TIME
    _START_TIME = time.time()
    _anti_stall_cleanup()
    try:
        from lib.cleanup import cleanup_log_dir
        cleanup_log_dir(_LOG_DIR)
    except Exception:
        pass
    print("[main] calling _init_runtime...", file=sys.stderr, flush=True)
    _init_runtime()
    print(f"[main] _init_runtime done. PORT={PORT} BACKEND={BACKEND}", file=sys.stderr, flush=True)
    try:
        _pid_file = os.path.join(_LOG_DIR, f"proxy-{PORT}.pid")
        with open(_pid_file, "w", encoding="utf-8") as _pf:
            _pf.write(str(os.getpid()))
        import atexit
        atexit.register(lambda: os.remove(_pid_file) if os.path.exists(_pid_file) else None)
    except Exception:
        pass
    try:
        _current_cfg = os.path.basename(_CONFIG_PATH) if _CONFIG_PATH else ""
        for _f in os.listdir(_LOG_DIR):
            if _f.startswith("proxy-") and _f.endswith(".json") and _f != _current_cfg:
                os.remove(os.path.join(_LOG_DIR, _f))
            if _f.startswith("models-") and _f.endswith(".json"):
                os.remove(os.path.join(_LOG_DIR, _f))
    except Exception:
        pass
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    if _IS_WINDOWS:
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _handle_shutdown_signal)
        import atexit
        atexit.register(lambda: setattr(sys.modules[__name__], '_shutdown_requested', True))
    else:
        signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    try:
        from http.server import ThreadingHTTPServer as _BaseSrv
    except ImportError:
        class _BaseSrv(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
    class ReusableHTTPServer(_BaseSrv):
        allow_reuse_address = True
        daemon_threads = True
        request_queue_size = 64
    BIND_HOST = os.environ.get("CODEX_HOST", "127.0.0.1")
    print(f"[main] starting HTTP server on {BIND_HOST}:{PORT}...", file=sys.stderr, flush=True)
    SERVER = ReusableHTTPServer((BIND_HOST, PORT), Handler)
    print(f"translate-proxy ({BACKEND}) listening on http://{BIND_HOST}:{PORT}", flush=True)
    print(f"Target: {TARGET_URL}", flush=True)
    print(f"Models: {[m['id'] for m in MODELS]}", flush=True)
    if BACKEND in ("codebuff", "freebuff"):
        _cb_pool.load_accounts(force=True)
        fb_status = _cb_pool.status()
        print(f"[multi-account] codebuff: {len(fb_status)} accounts loaded {[a['id'] for a in fb_status]}", flush=True)
    if OAUTH_PROVIDER and OAUTH_PROVIDER.startswith("google"):
        pool = _google_antigravity_pool if OAUTH_PROVIDER == "google-antigravity" else _google_cli_pool
        pool.load_accounts(force=True)
        g_status = pool.status()
        print(f"[multi-account] {OAUTH_PROVIDER}: {len(g_status)} accounts loaded {[a['id'] for a in g_status]}", flush=True)
    if _api_key_pool:
        print(f"[multi-account] API keys: {len(_api_key_pool._accounts)} keys loaded", flush=True)
    if BGP_ROUTES:
        print(f"BGP routes: {len(BGP_ROUTES)} ({[r.get('name','?') for r in BGP_ROUTES]})", flush=True)
    try:
        SERVER.serve_forever()
    finally:
        _flush_stats()

