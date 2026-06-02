"""Kiro (AWS CodeWhisperer) backend adapter."""
import json
import os
import socket
import struct
import time
import uuid

import urllib.request
import urllib.error

from proxy.config import *
from proxy.shared_utils import uid, emit, upstream_target, _record_usage
from proxy.auth_pools import _refresh_kiro_token


def _kiro_upstream_timeout(stream):
    """Timeout for Kiro upstream requests."""
    return 120 if stream else 60


def _parse_eventstream_frame(data):
    """Parse a single AWS EventStream binary frame.

    Wire format:
      [4B total_length][4B headers_length][4B prelude_crc][headers...][payload...][4B message_crc]

    Returns dict with 'headers' and 'payload', or None if data too short.
    """
    if len(data) < 16:
        return None
    total_length = struct.unpack(">I", data[0:4])[0]
    if total_length < 16 or total_length > len(data):
        return None
    headers_length = struct.unpack(">I", data[4:8])[0]
    headers = {}
    offset = 12
    header_end = 12 + headers_length
    while offset < header_end and offset < len(data):
        name_len = data[offset]
        offset += 1
        if offset + name_len > len(data):
            break
        name = data[offset:offset + name_len].decode("utf-8", errors="replace")
        offset += name_len
        if offset >= len(data):
            break
        header_type = data[offset]
        offset += 1
        if header_type == 7:
            if offset + 2 > len(data):
                break
            value_len = struct.unpack(">H", data[offset:offset + 2])[0]
            offset += 2
            if offset + value_len > len(data):
                break
            value = data[offset:offset + value_len].decode("utf-8", errors="replace")
            offset += value_len
            headers[name] = value
        else:
            break
    payload_start = 12 + headers_length
    payload_end = total_length - 4
    payload = None
    if payload_end > payload_start:
        payload_str = data[payload_start:payload_end].decode("utf-8", errors="replace").strip()
        if payload_str:
            try:
                payload = json.loads(payload_str)
            except Exception:
                payload = {"_raw": payload_str}
    return {"headers": headers, "payload": payload}


def _iter_eventstream(response):
    """Generator that reads binary chunks from HTTP response and yields parsed EventStream frames."""
    buf = bytearray()
    for chunk in iter(lambda: response.read(4096), b""):
        buf.extend(chunk)
        while len(buf) >= 16:
            total_length = struct.unpack(">I", bytes(buf[0:4]))[0]
            if total_length < 16 or total_length > len(buf):
                break
            frame_data = bytes(buf[:total_length])
            buf = buf[total_length:]
            parsed = _parse_eventstream_frame(frame_data)
            if parsed:
                yield parsed


def _kiro_resolve_model(model):
    """Strip Kiro suffixes (-thinking, -agentic) and return resolved info."""
    thinking = False
    agentic = False
    m = model
    if m.endswith("-agentic"):
        agentic = True
        m = m[:-8]
    if m.endswith("-thinking"):
        thinking = True
        m = m[:-9]
    elif m.endswith("-reasoning"):
        thinking = True
        m = m[:-10]
    return {"upstream": m, "thinking": thinking, "agentic": agentic}


def _kiro_is_thinking_enabled(body, model_info):
    """Detect if thinking/reasoning should be enabled."""
    if model_info.get("thinking"):
        return True
    thinking_cfg = body.get("thinking", {})
    if isinstance(thinking_cfg, dict) and thinking_cfg.get("type") == "enabled":
        return True
    if body.get("reasoning_effort") in ("low", "medium", "high", "auto"):
        return True
    reasoning = body.get("reasoning", {})
    if isinstance(reasoning, dict) and reasoning.get("effort") in ("low", "medium", "high", "auto"):
        return True
    return False


def _kiro_convert_tools(tools):
    """Convert OpenAI Responses API tools to Kiro tool specifications."""
    if not tools:
        return []
    kiro_tools = []
    for tool in tools:
        if tool.get("type") == "function":
            func = tool.get("function", {})
            spec = {
                "name": func.get("name", ""),
                "description": func.get("description", ""),
            }
            params = func.get("parameters", {})
            if params:
                spec["inputSchema"] = params
            kiro_tools.append(spec)
        elif tool.get("type") == "computer_preview" or tool.get("type") == "computer_20241022":
            continue
    return kiro_tools


def _kiro_extract_text_from_content(content):
    """Extract text from various content formats."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict):
                ct = c.get("type", "")
                if ct in ("input_text", "text"):
                    parts.append(c.get("text", ""))
                elif ct == "input_image" or ct == "image_url":
                    pass
        return "\n".join(parts)
    return str(content) if content else ""


def _kiro_extract_images(content):
    """Extract base64 images from content blocks."""
    images = []
    if not isinstance(content, list):
        return images
    for c in content:
        ct = c.get("type", "") if isinstance(c, dict) else ""
        if ct == "input_image":
            url = c.get("image_url", c.get("url", ""))
            if url and url.startswith("data:"):
                parts = url.split(",", 1)
                if len(parts) == 2:
                    mime = parts[0].split(":")[1].split(";")[0] if ":" in parts[0] else "image/png"
                    images.append({"format": mime.split("/")[-1], "source": {"bytes": parts[1]}})
        elif ct == "image_url":
            url = c.get("url", "") if isinstance(c, dict) else ""
            if url and url.startswith("data:"):
                parts = url.split(",", 1)
                if len(parts) == 2:
                    mime = parts[0].split(":")[1].split(";")[0] if ":" in parts[0] else "image/png"
                    images.append({"format": mime.split("/")[-1], "source": {"bytes": parts[1]}})
            elif isinstance(c, dict) and c.get("source", {}).get("type") == "base64":
                src = c["source"]
                images.append({
                    "format": src.get("media_type", "image/png").split("/")[-1],
                    "source": {"bytes": src.get("data", "")}
                })
    return images


def kiro_input_to_conversation(input_data, instructions="", tools=None, model="", body=None):
    """Convert Responses API input[] to Kiro conversationState format."""
    model_info = _kiro_resolve_model(model)
    thinking_enabled = _kiro_is_thinking_enabled(body or {}, model_info)

    kiro_tools = _kiro_convert_tools(tools)
    history = []
    pending_role = None
    pending_text = []
    pending_images = []
    pending_tool_uses = []
    pending_tool_results = []

    def _flush_pending():
        nonlocal pending_role, pending_text, pending_images, pending_tool_uses, pending_tool_results
        if not pending_role:
            return
        text = "\n".join(pending_text).strip()
        if not text and not pending_images and not pending_tool_uses and not pending_tool_results:
            pending_role = None
            pending_text = []
            pending_images = []
            pending_tool_uses = []
            pending_tool_results = []
            return
        msg = {}
        if pending_role == "user":
            msg["role"] = "user"
            content = text
            if pending_tool_results:
                tr_parts = []
                for tr in pending_tool_results:
                    tr_parts.append(f"Tool result ({tr.get('name', '')}):\n{tr.get('output', '')}")
                content = (content + "\n" if content else "") + "\n".join(tr_parts)
            msg["content"] = content
            user_msg = {"userInputMessage": {"content": content}}
            if pending_images:
                user_msg["userInputMessage"]["images"] = pending_images
            msg = user_msg
        else:
            msg["role"] = "assistant"
            content = text
            msg["content"] = content
            assistant_msg = {"assistantResponseMessage": {"content": content}}
            if pending_tool_uses:
                assistant_msg["assistantResponseMessage"]["toolUses"] = pending_tool_uses
            msg = assistant_msg
        history.append(msg)
        pending_role = None
        pending_text = []
        pending_images = []
        pending_tool_uses = []
        pending_tool_results = []

    items = input_data if isinstance(input_data, list) else []
    if isinstance(input_data, str) and input_data:
        items = [{"type": "message", "role": "user", "content": input_data}]

    for item in items:
        t = item.get("type", "")
        if t == "message":
            role = "user" if item.get("role") == "user" else "assistant"
            content = item.get("content", "")
            text = _kiro_extract_text_from_content(content)
            images = _kiro_extract_images(content if isinstance(content, list) else [])
            if item.get("role") in ("system", "tool"):
                role = "user"
            if pending_role != role:
                _flush_pending()
            pending_role = role
            if text:
                pending_text.append(text)
            pending_images.extend(images)
        elif t == "function_call":
            if pending_role != "assistant":
                _flush_pending()
            pending_role = "assistant"
            pending_text.append("")
            pending_tool_uses.append({
                "toolUseId": item.get("call_id", item.get("id", "")),
                "name": item.get("name", ""),
                "arguments": item.get("arguments", "{}"),
            })
        elif t == "function_call_output":
            if pending_role != "user":
                _flush_pending()
            pending_role = "user"
            output = item.get("output", "")
            try:
                parsed = json.loads(output)
                output = json.dumps(parsed, indent=2)
            except Exception:
                pass
            pending_tool_results.append({
                "name": item.get("name", ""),
                "output": output,
            })

    _flush_pending()

    system_prefix = ""
    if thinking_enabled:
        system_prefix += "<thinking_mode>enabled</thinking_mode>\n<max_thinking_length>16000</max_thinking_length>\n"
    if instructions:
        if thinking_enabled:
            system_prefix += instructions + "\n"

    if system_prefix and history:
        first = history[0]
        if "userInputMessage" in first:
            existing = first["userInputMessage"].get("content", "")
            first["userInputMessage"]["content"] = system_prefix + existing
        elif "assistantResponseMessage" in first and len(history) > 1:
            history.insert(0, {"userInputMessage": {"content": system_prefix.strip()}})

    merged = []
    for msg in history:
        if merged:
            prev_role = "user" if "userInputMessage" in merged[-1] else "assistant"
            cur_role = "user" if "userInputMessage" in msg else "assistant"
            if prev_role == cur_role:
                if "userInputMessage" in msg:
                    prev_content = merged[-1].get("userInputMessage", {}).get("content", "")
                    cur_content = msg.get("userInputMessage", {}).get("content", "")
                    merged[-1]["userInputMessage"]["content"] = prev_content + "\n" + cur_content
                elif "assistantResponseMessage" in msg:
                    prev_content = merged[-1].get("assistantResponseMessage", {}).get("content", "")
                    cur_content = msg.get("assistantResponseMessage", {}).get("content", "")
                    merged[-1]["assistantResponseMessage"]["content"] = prev_content + "\n" + cur_content
                continue
        merged.append(msg)
    history = merged

    if history and "assistantResponseMessage" in history[0]:
        history.insert(0, {"userInputMessage": {"content": "(start)"}})

    current_message = None
    if history:
        last = history[-1]
        if "userInputMessage" in last:
            current_message = history.pop()

    if not current_message:
        current_message = {"userInputMessage": {"content": ""}}

    if kiro_tools:
        ctx = current_message.get("userInputMessage", {}).get("userInputMessageContext", {})
        ctx["tools"] = kiro_tools
        current_message.setdefault("userInputMessage", {})["userInputMessageContext"] = ctx

    for msg in history:
        if "userInputMessage" in msg:
            msg.get("userInputMessage", {}).get("userInputMessageContext", {}).pop("tools", None)
            if not msg["userInputMessage"].get("userInputMessageContext"):
                msg["userInputMessage"].pop("userInputMessageContext", None)

    conversation_state = {
        "chatTriggerType": "MANUAL",
        "conversationId": str(uuid.uuid4()),
        "currentMessage": current_message,
    }
    if history:
        conversation_state["history"] = history

    inference_config = {}
    if body:
        max_tokens = body.get("max_output_tokens")
        if max_tokens:
            inference_config["maxTokens"] = max_tokens
        temp = body.get("temperature")
        if temp is not None:
            inference_config["temperature"] = temp
        top_p = body.get("top_p")
        if top_p is not None:
            inference_config["topP"] = top_p

    return {
        "conversationState": conversation_state,
        "origin": "AI_EDITOR",
        "inferenceConfig": inference_config if inference_config else None,
    }


def _kiro_stream_to_sse(response, model, req_id):
    """Generator: AWS EventStream binary → OpenAI Responses API SSE events."""
    resp_id = req_id or uid("resp")
    created = int(time.time())
    message_id = uid("msg")
    text_id = uid("txt")
    fc_idx = 0
    seen_tool_ids = {}
    full_text = ""
    tool_calls = {}
    usage_info = {}

    yield emit("response.created", {"type": "response.created",
        "response": {"id": resp_id, "object": "response", "model": model,
                     "status": "in_progress", "created": created, "output": []}})
    yield emit("response.in_progress", {"type": "response.in_progress",
        "response": {"id": resp_id, "status": "in_progress"}})
    yield emit("response.output_item.added", {"type": "response.output_item.added",
        "output_index": 0, "item": {"type": "message", "id": message_id, "role": "assistant",
                                     "content": []}})
    yield emit("response.content_part.added", {"type": "response.content_part.added",
        "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": ""}})

    for frame in _iter_eventstream(response):
        headers = frame.get("headers", {})
        payload = frame.get("payload", {})
        event_type = headers.get(":event-type", "")

        if not payload:
            continue

        if event_type == "assistantResponseEvent":
            text = payload.get("assistantResponseEvent", {}).get("content", "")
            if not text:
                text = payload.get("content", "")
            if text:
                full_text += text
                yield emit("response.output_text.delta", {"type": "response.output_text.delta",
                    "output_index": 0, "content_index": 0, "delta": text})

        elif event_type == "reasoningContentEvent":
            text = payload.get("reasoningContentEvent", {}).get("content", "")
            if not text:
                text = payload.get("content", "")
            if text:
                yield emit("response.reasoning.delta", {"type": "response.reasoning.delta",
                    "output_index": 0, "delta": text})

        elif event_type == "codeEvent":
            text = payload.get("codeEvent", {}).get("content", "")
            if not text:
                text = payload.get("content", "")
            if text:
                full_text += text
                yield emit("response.output_text.delta", {"type": "response.output_text.delta",
                    "output_index": 0, "content_index": 0, "delta": text})

        elif event_type == "toolUseEvent":
            tool_event = payload.get("toolUseEvent", payload)
            tool_use_id = tool_event.get("toolUseId", tool_event.get("toolUseEventId", ""))
            tool_name = tool_event.get("name", "")
            tool_args = tool_event.get("arguments", "")

            if tool_use_id not in seen_tool_ids:
                seen_tool_ids[tool_use_id] = fc_idx
                tool_calls[tool_use_id] = {"name": tool_name, "arguments": tool_args}
                fc_idx += 1
                yield emit("response.output_item.added", {"type": "response.output_item.added",
                    "output_index": fc_idx, "item": {"type": "function_call", "id": tool_use_id,
                                                      "call_id": tool_use_id, "name": tool_name}})
            else:
                tool_calls[tool_use_id]["arguments"] += tool_args

            yield emit("response.function_call_arguments.delta", {
                "type": "response.function_call_arguments.delta",
                "output_index": seen_tool_ids[tool_use_id],
                "delta": tool_args})

        elif event_type == "metricsEvent":
            metrics = payload.get("metricsEvent", payload)
            usage_info["input_tokens"] = metrics.get("inputTokens", metrics.get("inputTokenCount", 0))
            usage_info["output_tokens"] = metrics.get("outputTokens", metrics.get("outputTokenCount", 0))

        elif event_type in ("messageStopEvent", "contextUsageEvent", "meteringEvent"):
            pass

        elif event_type == "exception":
            error_msg = payload.get("message", payload.get("exceptionMessage", "Unknown error"))
            yield emit("response.failed", {"type": "response.failed",
                "response": {"id": resp_id, "status": "failed"},
                "error": {"type": "upstream_error", "message": error_msg}})
            return

    yield emit("response.output_text.done", {"type": "response.output_text.done",
        "output_index": 0, "content_index": 0, "text": full_text})
    yield emit("response.content_part.done", {"type": "response.content_part.done",
        "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": full_text}})
    yield emit("response.output_item.done", {"type": "response.output_item.done",
        "output_index": 0, "item": {"type": "message", "id": message_id, "role": "assistant",
                                     "content": [{"type": "output_text", "text": full_text}]}})

    out = [{"type": "message", "id": message_id, "role": "assistant",
            "content": [{"type": "output_text", "text": full_text}]}]
    for tid, tc in tool_calls.items():
        out.append({"type": "function_call", "id": tid, "call_id": tid,
                     "name": tc["name"], "arguments": tc["arguments"]})

    for tid, idx in seen_tool_ids.items():
        tc = tool_calls.get(tid, {})
        yield emit("response.function_call_arguments.done", {
            "type": "response.function_call_arguments.done",
            "output_index": idx, "arguments": tc.get("arguments", "")})
        yield emit("response.output_item.done", {"type": "response.output_item.done",
            "output_index": idx, "item": {"type": "function_call", "id": tid, "call_id": tid,
                                           "name": tc.get("name", ""), "arguments": tc.get("arguments", "")}})

    final_resp = {"id": resp_id, "object": "response", "model": model, "status": "completed",
                  "created": created, "output": out,
                  "usage": {"input_tokens": usage_info.get("input_tokens", 0),
                            "output_tokens": usage_info.get("output_tokens", 0),
                            "total_tokens": usage_info.get("input_tokens", 0) + usage_info.get("output_tokens", 0)}}
    yield emit("response.completed", {"type": "response.completed", "response": final_resp})


def handle(handler, body, model, stream, tracker=None):
    """Handle Kiro (AWS CodeWhisperer) backend request."""
    input_data = body.get("input", "")
    instructions = body.get("instructions", "").strip()
    tools = body.get("tools", [])

    model_info = _kiro_resolve_model(model)
    upstream_model = model_info["upstream"]

    access_token = _refresh_kiro_token()
    if not access_token:
        return handler.send_json(401, {"error": {"type": "auth_error",
            "message": "Kiro: No access token. Run OAuth login first."}})

    token_path = os.path.join(_LOG_DIR, "kiro-oauth-token.json")
    profile_arn = ""
    try:
        with open(token_path, encoding="utf-8") as f:
            td = json.load(f)
        profile_arn = td.get("profileArn", "")
    except Exception:
        pass

    kiro_body = kiro_input_to_conversation(input_data, instructions, tools, upstream_model, body)
    kiro_body["model"] = upstream_model

    if profile_arn:
        kiro_body["profileArn"] = profile_arn

    kiro_body = {k: v for k, v in kiro_body.items() if v is not None}

    target = upstream_target(TARGET_URL, "/generateAssistantResponse")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/vnd.amazon.eventstream",
        "X-Amz-Target": KIRO_TARGET_HEADER,
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "AWS-SDK-JS/3.0.0 kiro-ide/1.0.0",
    }

    req = urllib.request.Request(
        target,
        data=json.dumps(kiro_body).encode(),
        headers=headers)

    t0 = time.time()
    provider = "kiro"

    try:
        upstream = urllib.request.urlopen(req, timeout=_kiro_upstream_timeout(stream))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        handler._request_failed = True
        _record_usage(provider, upstream_model, False, time.time() - t0)
        if e.code == 401:
            new_token = _refresh_kiro_token()
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                req2 = urllib.request.Request(target, data=json.dumps(kiro_body).encode(), headers=headers)
                try:
                    upstream = urllib.request.urlopen(req2, timeout=_kiro_upstream_timeout(stream))
                except Exception:
                    return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": err_body}})
            else:
                return handler.send_json(401, {"error": {"type": "auth_error", "message": "Kiro: Token refresh failed"}})
        else:
            return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": err_body}})
    except Exception as e:
        handler._request_failed = True
        _record_usage(provider, upstream_model, False, time.time() - t0)
        return handler.send_json(500, {"error": {"type": "proxy_error", "message": str(e)}})

    if stream:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()
        if hasattr(handler, "connection") and handler.connection:
            try:
                handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass

        req_id = body.get("request_id") or body.get("id")
        handler.stream_buffered_events(
            _kiro_stream_to_sse(upstream, upstream_model, req_id))
        _record_usage(provider, upstream_model, True, time.time() - t0)
    else:
        full_text = ""
        tool_calls = {}
        usage_info = {}

        for frame in _iter_eventstream(upstream):
            hdrs = frame.get("headers", {})
            payload = frame.get("payload", {})
            event_type = hdrs.get(":event-type", "")
            if not payload:
                continue
            if event_type == "assistantResponseEvent":
                full_text += payload.get("assistantResponseEvent", payload).get("content", "")
            elif event_type == "codeEvent":
                full_text += payload.get("codeEvent", payload).get("content", "")
            elif event_type == "toolUseEvent":
                te = payload.get("toolUseEvent", payload)
                tid = te.get("toolUseId", "")
                if tid not in tool_calls:
                    tool_calls[tid] = {"name": te.get("name", ""), "arguments": te.get("arguments", "")}
                else:
                    tool_calls[tid]["arguments"] += te.get("arguments", "")
            elif event_type == "metricsEvent":
                m = payload.get("metricsEvent", payload)
                usage_info["input_tokens"] = m.get("inputTokens", 0)
                usage_info["output_tokens"] = m.get("outputTokens", 0)

        resp_id = body.get("request_id") or uid("resp")
        out = [{"type": "message", "id": uid("msg"), "role": "assistant",
                 "content": [{"type": "output_text", "text": full_text}]}]
        for tid, tc in tool_calls.items():
            out.append({"type": "function_call", "id": tid, "call_id": tid,
                         "name": tc["name"], "arguments": tc["arguments"]})

        result = {"id": resp_id, "object": "response", "model": upstream_model,
                  "status": "completed", "created": int(time.time()), "output": out,
                  "usage": {"input_tokens": usage_info.get("input_tokens", 0),
                            "output_tokens": usage_info.get("output_tokens", 0),
                            "total_tokens": usage_info.get("input_tokens", 0) + usage_info.get("output_tokens", 0)}}
        handler.send_json(200, result)
        _record_usage(provider, upstream_model, True, time.time() - t0)
