"""Codebuff/Freebuff adapter — handler delegation."""
import json
import secrets
import string
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid

# Import from proxy package modules
from proxy.config import *
from proxy.auth_pools import (
    RateLimitError, _cb_pool, _get_codebuff_account, _codebuff_start_run,
    _codebuff_get_session, _codebuff_finish_run, _sanitize_err_body,
)
from proxy.shared_utils import (
    _cb_input_to_messages, _ds_rebuild_tool_history, oa_convert_tools,
    _is_reasoning_content_error, _record_usage, store_response,
    _ds_store_assistant, oa_resp_to_responses, _codebuff_hard_disable_reasoning,
)
from proxy.adapters.openai import oa_stream_to_sse

def handle_codebuff(handler, body, model, stream, tracker=None):
    from proxy.server import _upstream_timeout
    agent_id = _CODEBUFF_AGENT_MAP.get(model)
    if not agent_id:
        matched = None
        for m in _CODEBUFF_AGENT_MAP:
            if model.lower().replace("/", "").replace("-", "") in m.lower().replace("/", "").replace("-", ""):
                matched = m
                break
        if matched:
            agent_id = _CODEBUFF_AGENT_MAP[matched]
            model = matched
        else:
            fallback_model = "deepseek/deepseek-v4-flash"
            agent_id = _CODEBUFF_AGENT_MAP.get(fallback_model, "base2-free-deepseek-flash")
            print(f"[codebuff] unknown model '{model}', falling back to {fallback_model}", file=sys.stderr)
            model = fallback_model

    _cb_pool.load_accounts()
    pool_status = _cb_pool.status()
    n_accounts = len(pool_status)
    if n_accounts == 0:
        return handler.send_json(401, {"error": {"type": "auth_error",
            "message": "No codebuff credentials found. Add accounts to ~/.config/manicode/credentials.json"}})

    last_err = None
    for attempt in range(n_accounts):
        token, acct = _get_codebuff_account()
        if not token:
            return handler.send_json(401, {"error": {"type": "auth_error",
                "message": "No codebuff credentials found. All accounts exhausted."}})

        acct_id = acct.get("id", "?") if acct else "?"
        if attempt > 0:
            print(f"[codebuff] rotation attempt {attempt+1}/{n_accounts}, trying account {acct_id}", file=sys.stderr)

        run_id, run_err = _codebuff_start_run(token, agent_id)
        if not run_id:
            if run_err and run_err[0] == "rate_limit_error":
                retry_s = run_err[2]
                _cb_pool.mark_rate_limited(acct, retry_s)
                last_err = ("rate_limit_error", run_err[1], f"Account {acct_id} rate-limited by Codebuff: {run_err[3]}")
            else:
                _cb_pool.mark_rate_limited(acct, 60)
                last_err = ("upstream_error", run_err[1] if run_err else 502,
                            f"Failed to start agent run for {acct_id}: {run_err[3] if run_err else 'unknown error'}")
            continue

        try:
            instance_id = _codebuff_get_session(token, model)
        except RateLimitError as rle:
            retry_s = rle.retry_seconds
            fb_msg = rle.message
            mins = int(retry_s // 60)
            user_msg = fb_msg if fb_msg else f"Daily session limit reached. Resets in {mins}m."
            print(f"[codebuff] session 429 for {acct_id}, retry after {retry_s:.0f}s", file=sys.stderr)
            _cb_pool.mark_rate_limited(acct, retry_s)
            _codebuff_finish_run(token, run_id, "completed")
            last_err = ("rate_limit_error", 429, user_msg)
            continue

        input_data = body.get("input", "")
        instructions = body.get("instructions", "").strip()
        messages = _cb_input_to_messages(input_data, instructions)
        messages = _ds_rebuild_tool_history(messages)

        metadata = {
            "run_id": run_id,
            "cost_mode": "free",
            "client_id": "".join(secrets.choice(string.digits + string.ascii_lowercase) for _ in range(13)),
        }
        if instance_id:
            metadata["freebuff_instance_id"] = instance_id

        chat_body = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "max_tokens": max(body.get("max_output_tokens", 0), 64000),
            "codebuff_metadata": metadata,
        }
        for k in ("temperature", "top_p"):
            if k in body:
                chat_body[k] = body[k]
        tools = oa_convert_tools(body.get("tools"))
        if tools:
            chat_body["tools"] = tools
        if body.get("tool_choice"):
            chat_body["tool_choice"] = body["tool_choice"]

        target = f"{_CODEBUFF_API_URL}/api/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "ai-sdk/openai-compatible/1.0.25/codebuff",
            "x-codebuff-model": model,
        }
        if instance_id:
            headers["x-codebuff-instance-id"] = instance_id

        print(f"[{handler._session_id}] [codebuff] POST {target} model={model} stream={stream} run={run_id} acct={acct_id}", file=sys.stderr)
        chat_body_b = json.dumps(chat_body).encode()

        try:
            req = urllib.request.Request(target, data=chat_body_b, headers=headers)
            upstream = urllib.request.urlopen(req, timeout=_upstream_timeout(body, stream))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()[:1000]
            _codebuff_finish_run(token, run_id, "failed")
            if e.code in (429, 426):
                reset_ms = 0
                fb_msg = ""
                try:
                    err_json = json.loads(err_body)
                    reset_ms = err_json.get("retryAfterMs", 0)
                    fb_msg = err_json.get("message", err_json.get("error", ""))
                    if isinstance(fb_msg, dict):
                        fb_msg = fb_msg.get("message", "")
                except Exception:
                    pass
                duration = max(reset_ms / 1000, 120) if reset_ms else 120
                mins = int(duration // 60)
                if not fb_msg:
                    fb_msg = _sanitize_err_body(err_body)
                user_msg = f"{fb_msg} (resets in {mins}m)" if fb_msg else f"Rate limited. Resets in {mins}m."
                _cb_pool.mark_rate_limited(acct, duration)
                last_err = ("rate_limit_error", e.code, user_msg)
                print(f"[codebuff] account {acct_id} got HTTP {e.code}, rotating", file=sys.stderr)
                continue
            if _is_reasoning_content_error(err_body):
                print(f"[codebuff] reasoning_content error, retrying with thinking disabled", file=sys.stderr)
                from proxy.adapters.codebuff import cb_retry_thinking_disabled
                result = cb_retry_thinking_disabled(handler, body, model, token, agent_id, stream, tracker, input_data, instructions, err_body, acct)
                return result
            print(f"[codebuff] HTTP {e.code}: {err_body[:300]}", file=sys.stderr)
            return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
        except Exception as e:
            _codebuff_finish_run(token, run_id, "failed")
            return handler.send_json(502, {"error": {"type": "proxy_error", "message": str(e)}})

        t0 = time.time()
        try:
            if stream:
                handler.send_response(200)
                handler.send_header("Content-Type", "text/event-stream")
                handler.send_header("Cache-Control", "no-cache")
                handler.send_header("Connection", "keep-alive")
                handler.end_headers()
                if hasattr(handler, 'connection') and handler.connection:
                    try:
                        handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    except Exception:
                        pass

                last_resp_id = [None]
                last_output = [None]
                last_status = [None]
                finish_reason = [None]
                reasoning_out = {}

                def _on_fb_event(event):
                    if tracker and tracker.cancelled.is_set():
                        return False
                    for line in event.strip().split("\n"):
                        if line.startswith("data: "):
                            try:
                                d = json.loads(line[6:])
                                if d.get("type") == "response.completed":
                                    last_resp_id[0] = d.get("response", {}).get("id")
                                    last_output[0] = d.get("response", {}).get("output", [])
                                    last_status[0] = d.get("response", {}).get("status")
                                    finish_reason[0] = "length" if last_status[0] == "incomplete" else "stop"
                            except Exception:
                                pass
                    return None

                try:
                    handler.stream_buffered_events(
                        oa_stream_to_sse(upstream, model, body.get("request_id") or body.get("id"),
                                         _reasoning_out=reasoning_out),
                        on_event=_on_fb_event)
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                    print(f"[{handler._session_id}] [codebuff] client disconnected", file=sys.stderr)
                    return

                success = finish_reason[0] != "length"
                _record_usage("codebuff", model, success, time.time() - t0)
                if last_resp_id[0] and input_data is not None:
                    store_response(last_resp_id[0], input_data, last_output[0])
                if last_resp_id[0] and reasoning_out.get("text") or reasoning_out.get("tool_calls"):
                    asm = {"role": "assistant", "content": reasoning_out.get("text", "") or ""}
                    if reasoning_out.get("tool_calls"):
                        asm["tool_calls"] = reasoning_out["tool_calls"]
                    if reasoning_out.get("text"):
                        asm["reasoning_content"] = reasoning_out["text"]
                    _ds_store_assistant(last_resp_id[0], asm)
                print(f"[{handler._session_id}] [codebuff] stream done status={last_status[0]} in {time.time()-t0:.1f}s acct={acct_id}", file=sys.stderr)
            else:
                raw = upstream.read().decode()
                chat_resp = json.loads(raw)
                result = oa_resp_to_responses(chat_resp, model)
                handler.send_json(200, result)
                rid = result.get("id")
                if rid:
                    store_response(rid, input_data, result.get("output", []))
                print(f"[{handler._session_id}] [codebuff] non-stream done in {time.time()-t0:.1f}s acct={acct_id}", file=sys.stderr)
        finally:
            _codebuff_finish_run(token, run_id, "completed")
        return

    if last_err:
        msg = last_err[2]
        resp_id = f"resp_{uuid.uuid4().hex[:24]}"
        result = {
            "id": resp_id,
            "object": "response",
            "created_at": int(time.time()),
            "model": model,
            "status": "completed",
            "output": [{
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": msg,
                    "annotations": [],
                }],
                "status": "completed",
            }],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }
        return handler.send_json(200, result)


def cb_retry_thinking_disabled(handler, body, model, token, agent_id, stream, tracker, input_data, instructions, original_error, acct=None):
    from proxy.server import _upstream_timeout
    run_id, run_err = _codebuff_start_run(token, agent_id)
    if not run_id:
        msg = run_err[3] if run_err else "unknown error"
        return handler.send_json(run_err[1] if run_err else 502, {"error": {"type": run_err[0] if run_err else "upstream_error",
            "message": f"Failed to start agent run for retry: {msg}"}})
    instance_id = _codebuff_get_session(token, model)
    messages = _cb_input_to_messages(input_data, instructions)
    _codebuff_hard_disable_reasoning(messages)
    metadata = {"run_id": run_id, "cost_mode": "free", "client_id": secrets.token_hex(7)[:13]}
    if instance_id:
        metadata["freebuff_instance_id"] = instance_id
    chat_body = {
        "model": model, "messages": messages, "stream": stream,
        "max_tokens": max(body.get("max_output_tokens", 0), 64000),
        "thinking": {"type": "disabled"},
        "codebuff_metadata": metadata,
    }
    for k in ("temperature", "top_p"):
        if k in body:
            chat_body[k] = body[k]
    tools = oa_convert_tools(body.get("tools"))
    if tools:
        chat_body["tools"] = tools
    if body.get("tool_choice"):
        chat_body["tool_choice"] = body["tool_choice"]
    target = f"{_CODEBUFF_API_URL}/api/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}", "User-Agent": "ai-sdk/openai-compatible/1.0.25/codebuff", "x-codebuff-model": model}
    if instance_id:
        headers["x-codebuff-instance-id"] = instance_id
    print(f"[codebuff] retry POST {target} model={model} stream={stream} run={run_id} (thinking disabled via DeepSeek native)", file=sys.stderr)
    try:
        req = urllib.request.Request(target, data=json.dumps(chat_body).encode(), headers=headers)
        upstream = urllib.request.urlopen(req, timeout=_upstream_timeout(body, stream))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:500]
        _codebuff_finish_run(token, run_id, "failed")
        print(f"[codebuff] thinking-disabled retry failed: HTTP {e.code}: {err_body[:300]}", file=sys.stderr)
        return handler.send_json(e.code, {"error": {"type": "codebuff_deepseek_thinking_error",
            "message": "Codebuff/DeepSeek V4 requires reasoning_content round-trip for tool-call sessions. Use Command Code provider for this model instead.", "upstream_error": _sanitize_err_body(err_body)}})
    except Exception as e:
        _codebuff_finish_run(token, run_id, "failed")
        return handler.send_json(502, {"error": {"type": "proxy_error", "message": str(e)}})
    t0 = time.time()
    try:
        if stream:
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "keep-alive")
            handler.end_headers()
            if hasattr(handler, 'connection') and handler.connection:
                try:
                    handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass
            last_resp_id = [None]
            last_output = [None]
            last_status = [None]
            finish_reason = [None]
            reasoning_out = {}
            def _on_fb_retry_event(event):
                if tracker and tracker.cancelled.is_set():
                    return False
                for line in event.strip().split("\n"):
                    if line.startswith("data: "):
                        try:
                            d = json.loads(line[6:])
                            if d.get("type") == "response.completed":
                                last_resp_id[0] = d.get("response", {}).get("id")
                                last_output[0] = d.get("response", {}).get("output", [])
                                last_status[0] = d.get("response", {}).get("status")
                                finish_reason[0] = "length" if last_status[0] == "incomplete" else "stop"
                        except Exception:
                            pass
                return None
            try:
                handler.stream_buffered_events(
                    oa_stream_to_sse(upstream, model, body.get("request_id") or body.get("id"),
                                     _reasoning_out=reasoning_out),
                    on_event=_on_fb_retry_event)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                return
            success = finish_reason[0] != "length"
            _record_usage("codebuff", model, success, time.time() - t0)
            if last_resp_id[0] and input_data is not None:
                store_response(last_resp_id[0], input_data, last_output[0])
            if last_resp_id[0] and reasoning_out.get("text") or reasoning_out.get("tool_calls"):
                asm = {"role": "assistant", "content": reasoning_out.get("text", "") or ""}
                if reasoning_out.get("tool_calls"):
                    asm["tool_calls"] = reasoning_out["tool_calls"]
                if reasoning_out.get("text"):
                    asm["reasoning_content"] = reasoning_out["text"]
                _ds_store_assistant(last_resp_id[0], asm)
            print(f"[{handler._session_id}] [codebuff] retry stream done status={last_status[0]} in {time.time()-t0:.1f}s", file=sys.stderr)
        else:
            raw = upstream.read().decode()
            chat_resp = json.loads(raw)
            result = oa_resp_to_responses(chat_resp, model)
            handler.send_json(200, result)
            rid = result.get("id")
            if rid:
                store_response(rid, input_data, result.get("output", []))
            print(f"[{handler._session_id}] [codebuff] retry non-stream done in {time.time()-t0:.1f}s", file=sys.stderr)
    finally:
        _codebuff_finish_run(token, run_id, "completed")
