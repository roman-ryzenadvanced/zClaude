"""Anthropic Messages API adapter."""
import json
import time
import urllib.request

from proxy.config import *
from proxy.shared_utils import uid, emit, upstream_target, forwarded_headers, _openrouter_extra


def an_input_to_messages(input_data):
    """Convert Responses API input to Anthropic messages format."""
    msgs = []
    if isinstance(input_data, str):
        msgs.append({"role": "user", "content": input_data})
    elif isinstance(input_data, list):
        for item in input_data:
            t = item.get("type")
            if t == "message":
                role = item.get("role", "user")
                if role == "developer":
                    role = "user"
                text = ""
                thinking_blocks = []
                for part in item.get("content", []):
                    pt = part.get("type", "")
                    if pt in ("input_text", "output_text"):
                        text += part.get("text", "")
                    elif pt in ("reasoning", "thinking"):
                        thinking_text = ""
                        for rp in part.get("content", []):
                            thinking_text += rp.get("text", "")
                        if thinking_text:
                            thinking_blocks.append({"type": "thinking", "thinking": thinking_text, "signature": part.get("signature", "")})
                    elif pt in ("input_file", "file", "document", "inlineData", "inline_data"):
                        b64 = ""
                        mime = "text/plain"
                        filename = part.get("filename") or part.get("name") or "attachment"
                        if pt in ("inlineData", "inline_data"):
                            mime = part.get("mimeType") or part.get("mime_type") or "text/plain"
                            b64 = part.get("data", "")
                        elif pt == "document" and isinstance(part.get("source"), dict):
                            src = part["source"]
                            mime = src.get("media_type") or src.get("mime_type") or "text/plain"
                            b64 = src.get("data", "")
                        else:
                            fu = part.get("file_url") or part.get("document_url") or part.get("url", {})
                            url = fu.get("url", fu) if isinstance(fu, dict) else fu
                            if isinstance(url, str) and url.startswith("data:"):
                                mime_part, _, b64 = url.partition(";base64,")
                                mime = mime_part.replace("data:", "") or "text/plain"
                            else:
                                b64 = part.get("data", "")
                                mime = part.get("mimeType") or part.get("mime_type") or "text/plain"
                        
                        is_text = mime.startswith("text/") or mime in (
                            "application/json", "application/javascript", 
                            "application/x-javascript", "application/xml",
                            "text/plain", "text/html", "text/css", "text/csv", 
                            "text/markdown", "text/x-python", "text/x-sh"
                        ) or filename.endswith(
                            (".txt", ".py", ".js", ".json", ".html", ".css", 
                             ".md", ".sh", ".c", ".cpp", ".h", ".java", ".go", ".ts")
                        )
                        
                        if is_text and b64:
                            try:
                                import base64
                                decoded_text = base64.b64decode(b64).decode("utf-8", errors="replace")
                                text += f"\n[Attached File: {filename}]\n" + "-"*40 + f"\n{decoded_text}\n" + "-"*40 + "\n"
                            except Exception as e:
                                print(f"[anthropic-file-parser] failed to decode text: {e}", file=sys.stderr)
                                text += f"\n[Attached File: {filename} (binary/unsupported type)]\n"
                        else:
                            text += f"\n[Attached File: {filename} (binary/unsupported type)]\n"
                if role == "assistant":
                    content_parts = []
                    if thinking_blocks:
                        content_parts.extend(thinking_blocks)
                    if text:
                        content_parts.append({"type": "text", "text": text})
                    msgs.append({"role": "assistant", "content": content_parts if content_parts else text})
                else:
                    msgs.append({"role": "user", "content": text})
            elif t == "function_call":
                msgs.append({"role": "assistant", "content": [
                    {"type": "tool_use", "id": item.get("call_id", item.get("id", uid("tu"))),
                     "name": item.get("name", ""),
                     "input": json.loads(item.get("arguments", "{}"))}
                ]})
            elif t == "function_call_output":
                msgs.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": item.get("id", ""),
                     "content": item.get("output", "")}
                ]})
    return msgs


def an_convert_tools(tools):
    """Convert OpenAI-style tools to Anthropic format."""
    if not tools:
        return None
    out = []
    for t in tools:
        if t.get("type") != "function":
            continue
        fn = t.get("function", {})
        if fn:
            out.append({"name": fn.get("name"), "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters", {"type": "object", "properties": {}})})
        else:
            out.append({"name": t.get("name"), "description": t.get("description", ""),
                        "input_schema": t.get("parameters", {"type": "object", "properties": {}})})
    return out or None


def an_resp_to_responses(anthro_resp, model, resp_id=None):
    """Convert Anthropic response to Responses API format."""
    blocks = anthro_resp.get("content", [])
    sr = anthro_resp.get("stop_reason", "end_turn")
    sm = {"end_turn": "completed", "max_tokens": "incomplete", "stop_sequence": "completed", "tool_use": "completed"}
    status = sm.get(sr, "incomplete")
    outputs = []
    for b in blocks:
        bt = b.get("type", "")
        if bt == "text":
            outputs.append({"type": "message", "id": uid("msg"), "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": b.get("text", ""), "annotations": []}]})
        elif bt == "tool_use":
            outputs.append({"type": "function_call", "id": uid("fc"), "call_id": b.get("id", ""),
                            "name": b.get("name", ""), "arguments": json.dumps(b.get("input", {})),
                            "status": "completed"})
        elif bt == "thinking":
            outputs.append({"type": "reasoning", "id": uid("rsn"), "status": "completed",
                            "content": [{"type": "text", "text": b.get("thinking", "")}]})
    usage = anthro_resp.get("usage", {})
    return {"id": resp_id or uid("resp"), "object": "response", "created": int(time.time()),
            "model": model, "status": status, "output": outputs,
            "usage": {"input_tokens": usage.get("input_tokens", 0),
                      "output_tokens": usage.get("output_tokens", 0),
                      "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                      "input_tokens_details": {"cached_tokens": 0}}}


def an_stream_to_sse(stream, model, req_id):
    """Convert Anthropic SSE stream to Responses API SSE events."""
    resp_id = req_id or uid("resp")
    completed = []
    msg_id = uid("msg")
    text_buf = ""
    tc_id = None
    tc_call_id = None
    tc_name = ""
    tc_args = ""
    block_type = None
    stop_reason = "end_turn"
    an_input_tokens = 0
    an_output_tokens = 0

    yield emit("response.created", {"type": "response.created",
        "response": {"id": resp_id, "object": "response", "model": model,
                     "status": "in_progress", "created": int(time.time()), "output": []}})
    yield emit("response.in_progress", {"type": "response.in_progress", "response": {"id": resp_id}})

    for raw in stream:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("event: "):
            evt_type = line[7:]
            continue
        if not line.startswith("data: "):
            continue
        try:
            data = json.loads(line[6:])
        except json.JSONDecodeError:
            continue

        et = data.get("type", "")

        if et == "message_start":
            msg_usage = data.get("message", {}).get("usage", {})
            if msg_usage:
                an_input_tokens = msg_usage.get("input_tokens", 0)

        elif et == "content_block_start":
            cb_type = data.get("content_block", {}).get("type", "")
            block_type = cb_type
            if cb_type == "text":
                msg_id = uid("msg")
                yield emit("response.output_item.added", {"type": "response.output_item.added",
                    "item": {"type": "message", "id": msg_id, "role": "assistant",
                             "status": "in_progress", "content": []}})
                yield emit("response.content_part.added", {"type": "response.content_part.added",
                    "part": {"type": "output_text", "text": "", "annotations": []}, "item_id": msg_id})
            elif cb_type == "tool_use":
                cb = data.get("content_block", {})
                tc_id = uid("fc")
                tc_call_id = cb.get("id", tc_id)
                tc_name = cb.get("name", "")
                yield emit("response.output_item.added", {"type": "response.output_item.added",
                    "item": {"type": "function_call", "id": tc_id, "call_id": tc_call_id,
                             "name": tc_name, "arguments": "", "status": "in_progress"}})
            elif cb_type == "thinking":
                pass

        elif et == "content_block_delta":
            dd = data.get("delta", {})
            dt = dd.get("type", "")
            if dt == "text_delta":
                txt = dd.get("text", "")
                text_buf += txt
                yield emit("response.output_text.delta", {"type": "response.output_text.delta",
                            "delta": txt, "item_id": msg_id, "content_index": 0})
            elif dt == "input_json_delta":
                pj = dd.get("partial_json", "")
                tc_args += pj
                yield emit("response.output_text.delta", {"type": "response.function_call_arguments.delta",
                            "delta": pj, "item_id": tc_id})
            elif dt == "thinking_delta":
                tk = dd.get("thinking", "")
                yield emit("response.reasoning.delta", {"type": "response.reasoning.delta", "delta": tk})

        elif et == "content_block_stop":
            if block_type == "text":
                yield emit("response.output_text.done", {"type": "response.output_text.done",
                            "text": text_buf, "item_id": msg_id, "content_index": 0})
                yield emit("response.content_part.done", {"type": "response.content_part.done",
                    "part": {"type": "output_text", "text": text_buf, "annotations": []}, "item_id": msg_id})
                yield emit("response.output_item.done", {"type": "response.output_item.done",
                    "item": {"type": "message", "id": msg_id, "role": "assistant", "status": "completed",
                             "content": [{"type": "output_text", "text": text_buf, "annotations": []}]}})
                completed.append({"type": "message", "id": msg_id, "role": "assistant", "status": "completed",
                                  "content": [{"type": "output_text", "text": text_buf, "annotations": []}]})
                text_buf = ""
            elif block_type == "tool_use":
                yield emit("response.function_call_arguments.done", {"type": "response.function_call_arguments.done",
                            "item_id": tc_id, "name": tc_name, "arguments": tc_args})
                yield emit("response.output_item.done", {"type": "response.output_item.done",
                    "item": {"type": "function_call", "id": tc_id, "call_id": tc_call_id,
                             "name": tc_name, "arguments": tc_args, "status": "completed"}})
                completed.append({"type": "function_call", "id": tc_id, "call_id": tc_call_id,
                                  "name": tc_name, "arguments": tc_args, "status": "completed"})
                tc_id = None
                tc_args = ""
            block_type = None

        elif et == "message_delta":
            stop_reason = data.get("delta", {}).get("stop_reason", "end_turn")
            delta_usage = data.get("usage", {})
            if delta_usage:
                an_output_tokens = delta_usage.get("output_tokens", 0)

        elif et == "message_stop":
            sm = {"end_turn": "completed", "max_tokens": "incomplete",
                  "stop_sequence": "completed", "tool_use": "completed"}
            status = sm.get(stop_reason, "incomplete")
            _final_usage = {
                "input_tokens": an_input_tokens,
                "output_tokens": an_output_tokens,
                "total_tokens": an_input_tokens + an_output_tokens
            }
            yield emit("response.completed", {"type": "response.completed",
                "response": {"id": resp_id, "object": "response", "model": model,
                             "status": status, "created": int(time.time()), "output": completed,
                             "usage": _final_usage}})


def handle(handler, body, model, stream, tracker=None):
    """Handle Anthropic Messages API request."""
    input_data = body.get("input", "")
    an_body = {"model": model, "messages": an_input_to_messages(input_data),
               "max_tokens": body.get("max_output_tokens", 8192)}
    instructions = body.get("instructions", "").strip()
    if instructions:
        an_body["system"] = [{"type": "text", "text": instructions,
                               "cache_control": {"type": "ephemeral"}}]
    for k in ("temperature", "top_p"):
        if k in body:
            an_body[k] = body[k]
    tools = an_convert_tools(body.get("tools"))
    if tools:
        an_body["tools"] = tools
    if body.get("tool_choice"):
        tc = body["tool_choice"]
        if isinstance(tc, str):
            an_body["tool_choice"] = {"type": tc}
        elif isinstance(tc, dict):
            an_body["tool_choice"] = tc
    an_body["stream"] = stream

    target = upstream_target(TARGET_URL, "/messages")
    req = urllib.request.Request(
        target,
        data=json.dumps(an_body).encode(),
        headers=forwarded_headers(handler.headers, {
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            **_openrouter_extra(),
        }),
    )
    handler._forward(req, stream, model,
        lambda r: an_resp_to_responses(json.loads(r.read()), model),
        lambda s: an_stream_to_sse(s, model, body.get("request_id") or body.get("id")),
        input_data=body.get("input", ""), tracker=tracker)
