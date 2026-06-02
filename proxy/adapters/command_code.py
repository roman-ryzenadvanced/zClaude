"""Command Code adapter — input conversion and handler."""
import json
import sys
import time
import socket
import uuid
import urllib.request
import urllib.error

from proxy.config import *
from proxy.shared_utils import uid
from proxy.cc_parser import cc_resp_to_responses, cc_stream_to_sse, _cc_config
from proxy.adapters.auto_sense import _load_schema, _save_schema, ErrorAnalyzer
from proxy.auth_pools import _sanitize_err_body
from proxy.shared_utils import (
    forwarded_headers, _openrouter_extra, upstream_target,
    _record_usage_with_tokens, store_response, _upstream_timeout
)

def cc_input_to_messages(input_data, instructions="", schema=None):
    """Convert Responses API input into CommandCode /alpha/generate messages.

    [FIX 1] All messages use STRING content (not content blocks).
    CC API rejects params.messages[i].content when it's an array.
    Tool results are role="user" with plain text content.
    Tool calls: inline JSON text in assistant messages (e.g. {"type":"tool-call","id":"..."}).
    
    The model echoes this format back in its response text-delta events.
    _parse_commandcode_text_tool_calls extracts them via _extract_raw_json_tool_calls.
    
    Schema parameter is accepted but not used for format decisions —
    the conservative string-content format is always used regardless of schema hints.
    """
    msgs = []
    pending_tool_calls = []
    last_flushed_ids = []

    def text_from_content(content):
        if isinstance(content, str):
            return content
        text = ""
        for part in content or []:
            if isinstance(part, str):
                text += part
                continue
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("input_text", "output_text", "text"):
                text += part.get("text", "")
        return text

    def flush_tool_calls():
        nonlocal pending_tool_calls, last_flushed_ids
        if not pending_tool_calls:
            return
        last_flushed_ids = [tc["id"] for tc in pending_tool_calls]
        # Tool calls as plain text in assistant message
        tc_text = "\n".join(
            json.dumps(tc, ensure_ascii=False) for tc in pending_tool_calls
        )
        msgs.append({"role": "assistant", "content": tc_text})
        pending_tool_calls = []

    if instructions:
        msgs.append({"role": "user", "content": instructions})

    if isinstance(input_data, str):
        msgs.append({"role": "user", "content": input_data})
        return msgs
    if not isinstance(input_data, list):
        return msgs

    for item in input_data:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "function_call":
            tcid = item.get("call_id") or item.get("id") or uid("call")
            name = item.get("name") or "exec_command"
            pending_tool_calls.append({
                "type": "tool-call",
                "id": tcid,
                "name": name,
                "arguments": item.get("arguments") or "{}",
            })
            continue
        flush_tool_calls()
        if t == "message":
            role = item.get("role", "user")
            if role not in ("user", "assistant"):
                role = "user"
            text = text_from_content(item.get("content", []))
            msgs.append({"role": role, "content": text})
        elif t == "function_call_output":
            output = item.get("output", "")
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False)
            # /alpha/generate expects string content for ALL messages
            msgs.append({"role": "user", "content": output[:8000]})
    flush_tool_calls()
    return msgs


def handle(handler, body, model, stream, tracker=None):
    """Handle CommandCode /alpha/generate adapter request."""
    input_data = body.get("input", "")
    instructions = body.get("instructions", "").strip()

    schema = _load_schema(model=model)

    thread_id = body.get("request_id") or body.get("id") or ""
    try:
        uuid.UUID(thread_id)
    except (ValueError, AttributeError):
        thread_id = str(uuid.uuid4())

    # Build auth headers
    auth_val = f"{schema.auth_scheme}{API_KEY}" if schema.auth_scheme else API_KEY
    headers_extra = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }
    if schema.auth_header:
        headers_extra[schema.auth_header] = auth_val
    else:
        headers_extra["Authorization"] = f"Bearer {API_KEY}"
    headers_extra["x-command-code-version"] = CC_VERSION or "0.26.8"

    pm = schema.param_names
    tp = schema.field_names.get("tools_param", "tools")
    target = upstream_target(TARGET_URL, "/alpha/generate")

    # ── MAIN REQUEST WITH RETRY ──
    max_retries = 2
    for attempt in range(max_retries + 1):
        cc_msgs = cc_input_to_messages(input_data, instructions, schema)
        cc_body = {
            "config": _cc_config(),
            "memory": "", "taste": "", "skills": "",
            "params": {
                "stream": True,
                pm.get("max_tokens", "max_tokens"): body.get("max_output_tokens", 64000),
                pm.get("temperature", "temperature"): body.get("temperature", 0.3),
                "messages": cc_msgs,
                "model": model,
                tp: [],
            },
            "threadId": thread_id,
        }

        fwd = forwarded_headers(handler.headers, {**headers_extra, **_openrouter_extra()}, browser_ua=True)
        print(f"[{handler._session_id}] POST {target} model={model} stream={stream} attempt={attempt} [command-code]", file=sys.stderr)
        req = urllib.request.Request(
            target,
            data=json.dumps(cc_body).encode(),
            headers=fwd,
        )

        try:
            upstream = urllib.request.urlopen(req, timeout=_upstream_timeout(body, True))
            break
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            if attempt < max_retries:
                hints = ErrorAnalyzer.analyze(err, schema)
                if hints:
                    print(f"[{handler._session_id}] error analysis: {hints}", file=sys.stderr)
                    ErrorAnalyzer.merge_into_schema(hints, schema)
                    _save_schema(schema, model=model)
                    continue
                if e.code in (429, 502, 503):
                    time.sleep(min(2 ** (attempt + 1), 10))
                    continue
            return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err)}})
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1)
                continue
            return handler.send_json(500, {"error": {"type": "proxy_error", "message": str(e)}})

    _save_schema(schema, model=model)

    t0 = time.time()
    provider = TARGET_URL.split("//")[-1].split("/")[0]

    if stream:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "close")
        handler.end_headers()
        if hasattr(handler, 'connection') and handler.connection:
            try:
                handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
        last_resp_id = None
        last_output = None
        last_usage = {}
        def on_event(event):
            nonlocal last_resp_id, last_output, last_usage
            if tracker and tracker.cancelled.is_set():
                print("[command-code] stream cancelled", file=sys.stderr)
                return False
            for line in event.strip().split("\n"):
                if line.startswith("data: "):
                    try:
                        d = json.loads(line[6:])
                        if d.get("type") == "response.completed":
                            last_resp_id = d.get("response", {}).get("id")
                            last_output = d.get("response", {}).get("output", [])
                            last_usage = d.get("response", {}).get("usage", {})
                    except (json.JSONDecodeError, KeyError, TypeError): pass
            return True
        try:
            handler.stream_buffered_events(cc_stream_to_sse(upstream, model, body.get("request_id") or body.get("id")), on_event=on_event)
        except Exception as e:
            print(f"[{handler._session_id}] stream error: {e}", file=sys.stderr)
            handler._request_failed = True
            try:
                err_event = 'data: ' + json.dumps({"type": "response.completed",
                    "response": {"id": body.get("request_id") or body.get("id") or uid("resp"),
                                 "object": "response", "model": model, "status": "failed",
                                 "created": int(time.time()), "output": [],
                                 "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                                           "input_tokens_details": {"cached_tokens": 0}}}})
                handler.wfile.write(err_event.encode())
                handler.wfile.flush()
            except Exception:
                pass
        
        success = not handler._request_failed
        _record_usage_with_tokens(provider, model, success, time.time() - t0, last_usage, input_items=body.get("input", ""), output_items=last_output)
        if last_resp_id:
            store_response(last_resp_id, body.get("input", ""), last_output)
    else:
        raw = upstream.read().decode()
        result = cc_resp_to_responses(raw, model)
        handler.send_json(200, result)
        rid = result.get("id")
        success = result.get("status") != "incomplete"
        _record_usage_with_tokens(provider, model, success, time.time() - t0, result.get("usage", {}), input_items=body.get("input", ""), output_items=result.get("output", []))
        if rid:
            store_response(rid, body.get("input", ""), result.get("output", []))
