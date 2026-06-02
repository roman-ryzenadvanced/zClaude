"""OpenAI-compatible backend adapter."""
import json
import re
import sys
import threading
import time

from proxy.config import *
from proxy.shared_utils import uid, emit, _stream_with_idle_timeout, _last_reasoning_lock, _last_reasoning_store, _RESPONSE_TTL

def _inject_stored_reasoning(messages):
    with _last_reasoning_lock:
        snapshot = dict(_last_reasoning_store)
    if not snapshot:
        return messages
    expired = [k for k, v in snapshot.items() if time.time() - v["ts"] > _RESPONSE_TTL]
    for k in expired:
        with _last_reasoning_lock:
            _last_reasoning_store.pop(k, None)
        snapshot.pop(k, None)
    if not snapshot:
        return messages
    latest = max(snapshot.values(), key=lambda v: v["ts"])
    reasoning = latest.get("reasoning", "")
    if not reasoning:
        return messages
    for msg in messages:
        if msg.get("role") == "assistant" and "reasoning_content" not in msg and msg.get("tool_calls"):
            msg["reasoning_content"] = reasoning
    return messages

def _normalize_tool_args(raw_args):
    if not raw_args or raw_args == "{}":
        return raw_args
    try:
        parsed = json.loads(raw_args)
        if isinstance(parsed, dict):
            if "Arguments" in parsed and "arguments" not in parsed:
                inner = parsed["Arguments"]
                if isinstance(inner, str):
                    inner = inner.strip()
                    for pfx in ("```json", "```"):
                        if inner.startswith(pfx):
                            inner = inner[len(pfx):].strip()
                    if inner.endswith("```"):
                        inner = inner[:-3].strip()
                    try:
                        inner_parsed = json.loads(inner)
                        if isinstance(inner_parsed, dict):
                            return json.dumps(inner_parsed)
                    except json.JSONDecodeError:
                        pass
            if "cmd" not in parsed and "Arguments" in parsed:
                inner = parsed["Arguments"]
                if isinstance(inner, str):
                    inner = inner.strip()
                    for pfx in ("```json", "```"):
                        if inner.startswith(pfx):
                            inner = inner[len(pfx):].strip()
                    if inner.endswith("```"):
                        inner = inner[:-3].strip()
                    try:
                        inner_parsed = json.loads(inner)
                        if isinstance(inner_parsed, dict):
                            return json.dumps(inner_parsed)
                    except json.JSONDecodeError:
                        pass
        return raw_args
    except json.JSONDecodeError:
        return raw_args

_XML_TC_RE = re.compile(r'<invoke><(\w+)(?:_command)?>(.*?)</\1(?:_command)?></invoke>', re.DOTALL)
_XML_ARG_VALUE_RE = re.compile(r'</?arg_value>\s*')

_PAREN_TC_RE = re.compile(
    r'(?:^|[\n•\-\*]\s*)\(\s*(exec_command|write_to_file|exec_bash|bash|run_command|shell|edit_file|read_file|search_files|list_files)\b\s*(.*?)\)',
    re.DOTALL | re.I
)

def _extract_xml_tool_calls(text):
    if not text:
        return []
    results = []
    for m in _XML_TC_RE.finditer(text):
        name = m.group(1)
        rest = _XML_ARG_VALUE_RE.sub("", m.group(2)).strip()
        args_str = "{}"
        try:
            for pfx in ("```json", "```"):
                if rest.startswith(pfx):
                    rest = rest[len(pfx):].strip()
            if rest.endswith("```"):
                rest = rest[:-3].strip()
            if rest.startswith("{"):
                json.loads(rest)
                args_str = rest
            else:
                json.loads(rest)
                args_str = rest
        except Exception:
            if rest.startswith("{"):
                args_str = rest
        results.append({"name": name, "args": args_str, "call_id": f"xml_{len(results)}"})
    return results

_NON_VISION_MODEL_PATTERNS = re.compile(
    r'\b(deepseek|glm|mixtral|llama\b(?!.*vision)|command|dbrx|qwen\b(?!.*vl)|phi-?3(?!.*vision))',
    re.I
)

_vision_fail_cache = set()
_vision_fail_lock = threading.Lock()

def _model_supports_vision(model):
    if not model:
        return True
    with _vision_fail_lock:
        if model in _vision_fail_cache:
            return False
    if _NON_VISION_MODEL_PATTERNS.search(model):
        return False
    return True

def _mark_vision_fail(model):
    if model:
        with _vision_fail_lock:
            _vision_fail_cache.add(model)

def _strip_images_from_input(input_data, model):
    if not isinstance(input_data, list) or _model_supports_vision(model):
        return input_data
    modified = False
    result = []
    for item in input_data:
        if item.get("type") != "message":
            result.append(item)
            continue
        content = item.get("content", [])
        if isinstance(content, str):
            result.append(item)
            continue
        new_content = []
        has_img = False
        for part in content:
            if isinstance(part, str):
                new_content.append(part)
                continue
            pt = part.get("type", "")
            if pt in ("input_image", "image_url"):
                if not has_img:
                    fname = part.get("image_url", {}).get("url", part.get("url", "image.png"))
                    if fname.startswith("data:"):
                        fname = "screenshot.png"
                    new_content.append({"type": "output_text", "text": f"[User attached image: {fname} — this model does not support vision]"})
                    has_img = True
                    modified = True
            else:
                new_content.append(part)
        if modified:
            result.append({**item, "content": new_content})
        else:
            result.append(item)
    if modified:
        print(f"[vision-filter] stripped {sum(1 for i in input_data if i.get('type')=='message' and any(c.get('type') in ('input_image','image_url') for c in (i.get('content') or []) if isinstance(c,dict)))} images for model={model}", file=sys.stderr)
        return result
    return input_data

def oa_input_to_messages(input_data):
    msgs = []
    tool_name_by_id = {}
    if isinstance(input_data, str):
        msgs.append({"role": "user", "content": input_data})
    elif isinstance(input_data, list):
        pending_tool_calls = []
        last_flushed_ids = []
        for item in input_data:
            t = item.get("type")
            if t == "function_call":
                tcid = item.get("call_id") or item.get("id") or uid("tc")
                raw_args = item.get("arguments", "{}")
                normalized_args = _normalize_tool_args(raw_args)
                pending_tool_calls.append(
                    {"id": tcid,
                     "type": "function",
                     "function": {"name": item.get("name", ""),
                                   "arguments": normalized_args}})
                tool_name_by_id[tcid] = item.get("name", "")
                continue
            if pending_tool_calls:
                last_flushed_ids = [tc["id"] for tc in pending_tool_calls]
                msgs.append({"role": "assistant", "content": None, "tool_calls": pending_tool_calls})
                pending_tool_calls = []
            if t == "message":
                role = item.get("role", "user")
                if role == "developer":
                    role = "system"
                text = ""
                reasoning_text = ""
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
                        elif pt in ("reasoning",):
                            for rp in part.get("content", []):
                                reasoning_text += rp.get("text", "")
                        elif pt == "input_image":
                            img = part.get("image_url", part)
                            msgs.append({"role": role, "content": [{"type": "text", "text": text},
                                        {"type": "image_url", "image_url": img}]})
                            text = None
                            break
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
                                    print(f"[openai-file-parser] failed to decode text: {e}", file=sys.stderr)
                                    text += f"\n[Attached File: {filename} (binary/unsupported type)]\n"
                            else:
                                text += f"\n[Attached File: {filename} (binary/unsupported type)]\n"
                if text is not None:
                    msg = {"role": role, "content": text}
                    if reasoning_text and role == "assistant":
                        msg["reasoning_content"] = reasoning_text
                    msgs.append(msg)
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
            msgs.append({"role": "assistant", "content": None, "tool_calls": pending_tool_calls})
    return msgs
def oa_convert_tools(tools, strict=False):
    if not tools:
        return None
    out = []
    for t in tools:
        if t.get("type") != "function":
            continue
        fn = t.get("function", {})
        name = ""
        if fn:
            name = (fn.get("name") or "").strip()
        else:
            name = (t.get("name") or "").strip()
        if not name or name == "null":
            continue
        if fn:
            entry = dict(t)
            if strict and "strict" not in fn:
                entry["function"] = dict(fn, strict=True)
            out.append(entry)
        else:
            entry = {
                "type": "function",
                "function": {"name": name, "description": t.get("description", ""),
                             "parameters": t.get("parameters", {})}
            }
            if strict:
                entry["function"]["strict"] = True
            out.append(entry)
    return out or None

def oa_resp_to_responses(chat_resp, model, resp_id=None):
    choice = chat_resp["choices"][0]
    msg = choice["message"]
    content = msg.get("content") or ""
    finish = choice.get("finish_reason", "stop")
    fm = {"stop": "completed", "length": "incomplete", "tool_calls": "completed", "content_filter": "incomplete"}
    status = fm.get(finish, "incomplete")
    outputs = []
    if content:
        outputs.append({"type": "message", "id": uid("msg"), "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": content, "annotations": []}]})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        outputs.append({"type": "function_call", "id": uid("fc"), "call_id": tc.get("id"),
                        "name": fn.get("name"), "arguments": fn.get("arguments", "{}"), "status": "completed"})
    usage = chat_resp.get("usage", {})
    return {"id": resp_id or uid("resp"), "object": "response", "created": int(time.time()),
            "model": model, "status": status, "output": outputs,
            "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                      "output_tokens": usage.get("completion_tokens", 0),
                      "total_tokens": usage.get("total_tokens", 0),
                      "input_tokens_details": {"cached_tokens": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)}}}

def oa_stream_to_sse(chat_stream, model, req_id, _reasoning_out=None):
    resp_id = req_id or uid("resp")
    msg_id = uid("msg")
    text_buf = ""
    reasoning_buf = ""
    reasoning_opened = False
    tc_buf = {}
    fr = None
    msg_opened = False
    _last_chunk = {}

    yield emit("response.created", {"type": "response.created",
        "response": {"id": resp_id, "object": "response", "model": model,
                     "status": "in_progress", "created": int(time.time()), "output": []}})
    yield emit("response.in_progress", {"type": "response.in_progress", "response": {"id": resp_id}})

    _last_stream_usage = {}
    for line in _stream_with_idle_timeout(chat_stream):
        line = line.decode("utf-8", errors="replace").strip()
        if not line or line.startswith(":") or line == "data: [DONE]":
            continue
        if not line.startswith("data: "):
            continue
        try:
            chunk = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices", [])
        if not choices:
            continue
        usage = chunk.get("usage")
        if usage:
            _last_stream_usage = usage
        fr = choices[0].get("finish_reason")
        delta = choices[0].get("delta", {})
        fr = choices[0].get("finish_reason")

        rc = delta.get("reasoning_content") or delta.get("reasoning")
        if rc:
            if not reasoning_opened:
                reasoning_opened = True
            reasoning_buf += rc
            yield emit("response.reasoning.delta", {"type": "response.reasoning.delta", "delta": rc})

        content = delta.get("content")
        if content:
            if not msg_opened:
                msg_id = uid("msg")
                yield emit("response.output_item.added", {"type": "response.output_item.added",
                    "item": {"type": "message", "id": msg_id, "role": "assistant", "status": "in_progress", "content": []}})
                yield emit("response.content_part.added", {"type": "response.content_part.added",
                    "part": {"type": "output_text", "text": "", "annotations": []}, "item_id": msg_id})
                msg_opened = True
            text_buf += content
            yield emit("response.output_text.delta", {"type": "response.output_text.delta",
                        "delta": content, "item_id": msg_id, "content_index": 0})

        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            if idx not in tc_buf:
                fid = uid("fc")
                tc_buf[idx] = {"id": fid, "call_id": tc.get("id", fid), "name": "", "args": ""}
                yield emit("response.output_item.added", {"type": "response.output_item.added",
                    "item": {"type": "function_call", "id": fid, "call_id": tc_buf[idx]["call_id"],
                             "name": "", "arguments": "", "status": "in_progress"}})
            fn = tc.get("function", {})
            if "name" in fn and fn["name"]:
                tc_buf[idx]["name"] = fn["name"]
            if "arguments" in fn and fn["arguments"]:
                tc_buf[idx]["args"] += fn["arguments"]
                yield emit("response.output_text.delta", {"type": "response.function_call_arguments.delta",
                            "delta": fn["arguments"], "item_id": tc_buf[idx]["id"]})

    reasoning_rsn_id = uid("rsn") if reasoning_buf else None
    if reasoning_opened:
        yield emit("response.reasoning.done", {"type": "response.reasoning.done",
                    "item_id": reasoning_rsn_id, "text": reasoning_buf})

    if msg_opened:
        yield emit("response.output_text.done", {"type": "response.output_text.done",
                    "text": text_buf, "item_id": msg_id, "content_index": 0})
        yield emit("response.content_part.done", {"type": "response.content_part.done",
                    "part": {"type": "output_text", "text": text_buf, "annotations": []}, "item_id": msg_id})
        yield emit("response.output_item.done", {"type": "response.output_item.done",
            "item": {"type": "message", "id": msg_id, "role": "assistant", "status": "completed",
                     "content": [{"type": "output_text", "text": text_buf, "annotations": []}]}})

    for idx in sorted(tc_buf):
        t = tc_buf[idx]
        yield emit("response.function_call_arguments.done", {"type": "response.function_call_arguments.done",
                    "item_id": t["id"], "name": t["name"], "arguments": t["args"]})
        yield emit("response.output_item.done", {"type": "response.output_item.done",
            "item": {"type": "function_call", "id": t["id"], "call_id": t["call_id"],
                     "name": t["name"], "arguments": t["args"], "status": "completed"}})

    fm = {"stop": "completed", "length": "incomplete", "tool_calls": "completed", "content_filter": "incomplete"}
    status = fm.get(fr, "incomplete")
    final_out = []
    if reasoning_buf:
        final_out.append({"type": "reasoning", "id": reasoning_rsn_id, "status": "completed",
                          "content": [{"type": "text", "text": reasoning_buf}]})
    if msg_opened:
        msg_content = []
        if reasoning_buf:
            msg_content.append({"type": "output_text", "text": text_buf, "annotations": []})
        else:
            msg_content.append({"type": "output_text", "text": text_buf, "annotations": []})
        final_out.append({"type": "message", "id": msg_id, "role": "assistant", "status": "completed",
                          "content": msg_content})
    for idx in sorted(tc_buf):
        t = tc_buf[idx]
        final_out.append({"type": "function_call", "id": t["id"], "call_id": t["call_id"],
                          "name": t["name"], "arguments": t["args"], "status": "completed"})
    _usage = {"input_tokens": _last_stream_usage.get("prompt_tokens", 0),
              "output_tokens": _last_stream_usage.get("completion_tokens", 0),
              "total_tokens": _last_stream_usage.get("total_tokens", 0)}
    yield emit("response.completed", {"type": "response.completed",
        "response": {"id": resp_id, "object": "response", "model": model,
                     "status": status, "created": int(time.time()), "output": final_out,
                     "usage": _usage}})
    if _reasoning_out is not None:
        _reasoning_out["text"] = reasoning_buf
        _reasoning_out["tool_calls"] = [tc_buf[i] for i in sorted(tc_buf)] if tc_buf else []
