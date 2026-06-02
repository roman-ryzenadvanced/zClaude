"""Gemini and Antigravity adapter — handler delegation."""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import secrets
import string
import socket

# Import from proxy package modules
from proxy.config import *
from proxy.auth_pools import (
    RateLimitError, _google_antigravity_pool, _google_cli_pool,
    _refresh_google_token, _force_refresh_google_token, _get_google_account,
    _classify_antigravity_error, _parse_rate_limit_reset, _sanitize_err_body,
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
    _stream_with_idle_timeout, _refresh_oauth_token, _record_usage_with_tokens,
    _log_resp, _response_store_lock, _response_store,
)
from proxy.adapters.openai import oa_convert_tools
from proxy.compaction import (
    _adaptive_compact, _apply_prompt_enhancer,
)
from proxy.tool_validation import (
    validate_tool_pairs, repair_orphan_tool_outputs,
)
from proxy.adapters.auto_sense import _load_schema, _preprocess_vision_input

# ═══════════════════════════════════════════════════════════════════
# Gemini & Antigravity Constants & Helper functions
# ═══════════════════════════════════════════════════════════════════

_ANTIGRAVITY_MAX_CONTENTS = 20
_ANTIGRAVITY_MAX_TOOL_VERBATIM = 2
_ANTIGRAVITY_MAX_TOOL_CHARS = 2000
_ANTIGRAVITY_MAX_OLD_SUMMARY_CHARS = 1200
_ANTIGRAVITY_SOFT_CHARS = 120000
_ANTIGRAVITY_HARD_CHARS = 250000
_ANTIGRAVITY_EMERGENCY_CHARS = 500000
_ANTIGRAVITY_SIMPLE_WORDS = frozenset({"hi", "hello", "hey", "test", "ping", "thanks", "thank you", "ok", "okay", "yes", "no", "cool", "nice", "good", "great", "done", "go", "stop", "yep", "nope", "sure", "right", "correct", "continue", "cont", "k", "thx", "ty", "np", "lol", "brb", "bye"})
_ANTIGRAVITY_EDIT_WORDS = frozenset(("change", "fix", "update", "redesign", "rewrite", "modify", "improve", "replace", "edit", "make it", "add", "remove", "delete", "rename", "move", "convert", "create", "build", "implement"))
_ANTIGRAVITY_REFERENCE_WORDS = frozenset(("previous", "file", "error", "again", "that", "this", "it", "same", "last", "above", "earlier", "before", "earlier output", "last error", "previous result", "what was", "show me", "give me"))

def _antigravity_is_simple_user(text):
    if not text:
        return True
    stripped = text.strip().lower()
    if stripped in _ANTIGRAVITY_SIMPLE_WORDS:
        return True
    if len(stripped) < 30:
        words = set(stripped.split())
        if not words.intersection(_ANTIGRAVITY_REFERENCE_WORDS) and not words.intersection(_ANTIGRAVITY_EDIT_WORDS):
            return True
    return False

def _antigravity_normalize_context(input_data, model=""):
    if not isinstance(input_data, list) or len(input_data) < 2:
        return input_data
    is_claude_model = "claude" in model.lower()

    latest_user = ""
    latest_user_idx = -1
    for i in range(len(input_data) - 1, -1, -1):
        item = input_data[i]
        if isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "user":
            c = item.get("content", "")
            if isinstance(c, str):
                latest_user = c
            elif isinstance(c, list):
                latest_user = "\n".join(p.get("text", p.get("input_text", "")) for p in c if isinstance(p, dict))
            latest_user_idx = i
            break

    if not latest_user:
        return input_data

    is_simple = _antigravity_is_simple_user(latest_user)

    n_raw = len(input_data)
    n_tool_outputs = sum(1 for it in input_data if isinstance(it, dict) and it.get("type") == "function_call_output")
    n_tool_calls = sum(1 for it in input_data if isinstance(it, dict) and it.get("type") == "function_call")

    auto_reset = (n_raw > 200 or n_tool_outputs > 20) and is_simple
    if os.environ.get("ANTIGRAVITY_AUTO_RESET_POLLUTED_CONTEXT", "1") != "1":
        auto_reset = False

    has_compaction_summary = any(
        isinstance(it, dict) and it.get("type") == "message" and it.get("role") == "user"
        and ("Auto-compacted" in str(it.get("content", "")) or "auto-compacted" in str(it.get("content", "")).lower())
        for it in input_data
    )

    if is_simple and auto_reset and not has_compaction_summary:
        system_items = [it for it in input_data if isinstance(it, dict) and it.get("type") == "message" and it.get("role") in ("developer", "system")]
        user_item = input_data[latest_user_idx]
        result = system_items + [user_item] if system_items else [user_item]
        print(f"[antigravity-context] raw_items={n_raw} compacted_items={n_raw} final_items={len(result)}", file=sys.stderr)
        print(f"[antigravity-context] raw_tool_outputs={n_tool_outputs} kept_tool_outputs=0", file=sys.stderr)
        print(f"[antigravity-context] simple_latest_user=true auto_reset={auto_reset} has_compaction={has_compaction_summary}", file=sys.stderr)
        return result

    dev_messages = []
    recent_items = []
    tool_outputs = []
    tool_calls = []

    for i, item in enumerate(input_data):
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "message" and item.get("role") in ("developer", "system"):
            dev_messages.append(item)
        elif t == "function_call_output":
            tool_outputs.append((i, item))
        elif t == "function_call":
            tool_calls.append((i, item))
        elif t == "message":
            recent_items.append((i, item))

    latest_words = set(latest_user.strip().lower().split())
    has_edit_intent = bool(latest_words.intersection(_ANTIGRAVITY_EDIT_WORDS))
    has_ref_intent = bool(latest_words.intersection(_ANTIGRAVITY_REFERENCE_WORDS))
    if is_claude_model:
        keep_tools = len(tool_outputs)
    else:
        keep_tools = 2 if (has_edit_intent or has_ref_intent) else 1

    if is_claude_model:
        kept_tools = tool_outputs
    else:
        kept_tools = tool_outputs[-keep_tools:] if tool_outputs and (has_edit_intent or has_ref_intent) else []

    for idx_t, t_item in enumerate(kept_tools):
        orig = t_item[1]
        out = orig.get("output", "")
        if isinstance(out, list):
            cleaned = []
            for part in out:
                if isinstance(part, dict) and part.get("type") in ("input_image", "image_url"):
                    url = part.get("image_url", {}).get("url", "") if isinstance(part.get("image_url"), dict) else ""
                    if url.startswith("data:"):
                        cleaned.append({"type": "text", "text": "[image data stripped for compaction]"})
                        continue
                cleaned.append(part)
            if len(json.dumps(cleaned)) > _ANTIGRAVITY_MAX_TOOL_CHARS:
                new_item = dict(orig)
                new_item["output"] = json.dumps(cleaned)[:_ANTIGRAVITY_MAX_TOOL_CHARS] + "\n... [truncated]"
                kept_tools[idx_t] = (t_item[0], new_item)
            elif cleaned != out:
                new_item = dict(orig)
                new_item["output"] = cleaned
                kept_tools[idx_t] = (t_item[0], new_item)
        elif isinstance(out, str) and len(out) > _ANTIGRAVITY_MAX_TOOL_CHARS:
            new_item = dict(orig)
            new_item["output"] = out[:_ANTIGRAVITY_MAX_TOOL_CHARS] + f"\n... [truncated: kept {_ANTIGRAVITY_MAX_TOOL_CHARS} of {len(out)} chars]"
            kept_tools[idx_t] = (t_item[0], new_item)

    n_summarized = len(tool_outputs) - len(kept_tools)

    tail_start = max(0, len(recent_items) - 6)
    recent_tail = recent_items[tail_start:]

    deduped_tail = []
    seen_goal_context = False
    for idx, msg_item in recent_tail:
        content_str = ""
        c = msg_item.get("content", "")
        if isinstance(c, str):
            content_str = c
        elif isinstance(c, list):
            content_str = " ".join(p.get("text", p.get("input_text", "")) for p in c if isinstance(p, dict))
        if "<goal_context>" in content_str:
            if seen_goal_context:
                continue
            seen_goal_context = True
        deduped_tail.append((idx, msg_item))
    recent_tail = deduped_tail if deduped_tail else recent_tail

    # Build call_id -> function_call mapping
    tool_call_map = {}
    for _, call_item in tool_calls:
        cid = call_item.get("call_id", call_item.get("id", ""))
        if cid:
            tool_call_map[cid] = call_item

    # Build result: maintain PAIRED sequence (function_call -> function_call_output)
    result = list(dev_messages)

    compaction_summaries = []
    for idx, msg_item in recent_items:
        if msg_item is input_data[latest_user_idx]:
            continue
        c = msg_item.get("content", "")
        content_str = c if isinstance(c, str) else " ".join(p.get("text", p.get("input_text", "")) for p in c if isinstance(p, dict)) if isinstance(c, list) else ""

        if not is_claude_model and not has_edit_intent and not has_ref_intent and idx < latest_user_idx - 1:
            if content_str and len(content_str) > _ANTIGRAVITY_MAX_OLD_SUMMARY_CHARS:
                compact_msg = dict(msg_item)
                compact_msg["content"] = content_str[:_ANTIGRAVITY_MAX_OLD_SUMMARY_CHARS] + f"\n... [proxy compacted: kept {_ANTIGRAVITY_MAX_OLD_SUMMARY_CHARS} of {len(content_str)} chars]"
                result.append(compact_msg)
                continue

        result.append(msg_item)

    tool_indices = {t_idx: t_orig for t_idx, t_orig in kept_tools}
    for idx, call_item in tool_calls:
        if idx in tool_indices or is_claude_model:
            result.append(call_item)
            cid = call_item.get("call_id", call_item.get("id", ""))
            if cid:
                for t_idx, t_orig in kept_tools:
                    if t_orig.get("call_id") == cid or t_orig.get("id") == cid:
                        result.append(t_orig)
                        break

    if n_summarized > 0 and not is_claude_model:
        summary_msg = f"[Auto-compacted: {n_summarized} older tool interactions were summarized to stay within context window]"
        result.append({"role": "user", "type": "message", "content": summary_msg})

    for idx, msg_item in recent_tail:
        if msg_item is input_data[latest_user_idx]:
            result.append(msg_item)

    print(f"[antigravity-context] raw_items={n_raw} compacted_items={len(result)} (tool_outputs={n_tool_outputs} kept={len(kept_tools)}) model={model}", file=sys.stderr)
    return result

def _auto_continue_gemini(handler, flush_event, message_id, model, gen_config, gemini_tools, system_parts, project_id, headers, endpoints, url_suffix, accumulated_text, output_items, message_started):
    max_continuations = 5
    for _cont in range(max_continuations):
        cont_contents = [
            {"role": "model", "parts": [{"text": accumulated_text[-12000:]}]},
            {"role": "user", "parts": [{"text": "Continue exactly where you left off. Do not repeat anything already written."}]},
        ]
        cont_request = {"contents": cont_contents, "generationConfig": dict(gen_config)}
        if system_parts:
            cont_request["systemInstruction"] = {"parts": system_parts}
        if gemini_tools:
            cont_request["tools"] = gemini_tools
        cont_wrapped = {"project": project_id, "model": model, "request": cont_request}
        if OAUTH_PROVIDER == "google-antigravity":
            cont_wrapped["requestType"] = "agent"
            cont_wrapped["userAgent"] = "antigravity"
            cont_wrapped["requestId"] = f"agent-{uuid.uuid4().hex[:12]}"
        cont_body = json.dumps(cont_wrapped).encode()
        upstream = None
        for ep in endpoints:
            target = f"{ep}/{url_suffix}"
            req = urllib.request.Request(target, data=cont_body, headers=headers)
            try:
                upstream = urllib.request.urlopen(req, timeout=180)
                break
            except Exception as e:
                print(f"[auto-continue] {ep} failed: {e}", file=sys.stderr)
                continue
        if not upstream:
            break
        cont_text = ""
        cont_finish = ""
        cont_buf = ""
        for raw_line in _stream_with_idle_timeout(upstream, _idle_timeout_for_model(model)):
            line = raw_line.decode(errors="replace")
            if line.startswith("data: "):
                cont_buf += line[6:]
                continue
            if not line.strip() and cont_buf:
                try:
                    chunk = json.loads(cont_buf)
                except Exception:
                    cont_buf = ""
                    continue
                cont_buf = ""
                candidates = chunk.get("response", chunk).get("candidates", [])
                if not candidates:
                    continue
                cont_finish = candidates[0].get("finishReason", "")
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    if part.get("thought"):
                        continue
                    if "text" in part and not part.get("functionCall"):
                        delta = part["text"]
                        if delta:
                            cont_text += delta
                            flush_event("response.output_text.delta", {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": delta})
                    elif part.get("functionCall"):
                        fc = part["functionCall"]
                        call_id = f"call_{uuid.uuid4().hex[:24]}"
                        args_str = json.dumps(fc.get("args", fc.get("arguments", {})))
                        output_index = len(output_items)
                        flush_event("response.output_item.added", {"type": "response.output_item.added", "output_index": output_index, "item": {"type": "function_call", "id": call_id, "call_id": call_id, "name": fc.get("name", ""), "arguments": ""}})
                        flush_event("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "output_index": output_index, "item_id": call_id, "delta": args_str})
                        flush_event("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "output_index": output_index, "item_id": call_id, "arguments": args_str})
                        output_items.append({"tool": True, "fc": fc, "call_id": call_id})
        accumulated_text += cont_text
        print(f"[auto-continue] chunk {len(cont_text)} chars, finish={cont_finish}, total={len(accumulated_text)}", file=sys.stderr)
        if cont_finish != "MAX_TOKENS":
            break
    return accumulated_text


# ═══════════════════════════════════════════════════════════════════
# Delegated Handlers
# ═══════════════════════════════════════════════════════════════════

def handle_antigravity_v2(handler, body, model, stream, tracker=None):
    _model_alias = {
        "gemini-3.5-flash-high": "gemini-3-flash",
        "gemini-3.5-flash-medium": "gemini-3-flash",
        "gemini-3.5-flash-low": "gemini-3.5-flash-low",
        "gemini-3.5-flash": "gemini-3-flash",
        "gemini-3-flash-preview": "gemini-3-flash",
        "gemini-3-pro-preview": "gemini-3.1-pro-low",
        "gemini-3-pro": "gemini-3.1-pro-low",
        "gemini-3-pro-low": "gemini-3.1-pro-low",
        "gemini-3-pro-high": "gemini-3.1-pro-low",
        "gemini-3.1-pro": "gemini-3.1-pro-low",
        "gemini-3.1-pro-high": "gemini-3.1-pro-low",
        "claude-sonnet-4.6": "claude-sonnet-4-6",
        "claude-sonnet-4.6-thinking": "claude-sonnet-4-6",
        "claude-opus-4.6": "claude-opus-4-6-thinking",
        "claude-opus-4.6-thinking": "claude-opus-4-6-thinking",
    }
    _resolved = _model_alias.get(model, model)
    if _resolved != model:
        print(f"[{getattr(handler, '_session_id', '?')}] [antigravity-v2] model resolved: {model} -> {_resolved}", file=sys.stderr)
        model = _resolved

    input_data = body.get("input", "")
    _schema = _load_schema(model=model)
    if _schema and not _schema.supports_vision:
        input_data = _preprocess_vision_input(input_data, _schema)
        body = dict(body)
        body["input"] = input_data

    if isinstance(input_data, list) and len(input_data) > 30:
        input_data = _antigravity_normalize_context(input_data, model)
        body = dict(body)
        body["input"] = input_data

    access_token = _refresh_oauth_token()
    token_path = os.path.join(_LOG_DIR, "google-antigravity-oauth-token.json")
    project_id = ""
    try:
        with open(token_path) as f:
            project_id = json.load(f).get("project_id", "")
    except Exception:
        pass

    tool_call_names = {}
    contents = []

    if isinstance(input_data, list):
        for item in input_data:
            t = item.get("type")
            if t == "message":
                role = "user" if item.get("role") == "user" else "model"
                content = item.get("content", "")
                parts = []
                if isinstance(content, list):
                    for c in content:
                        ct = c.get("type")
                        if ct in ("input_text", "text"):
                            parts.append({"text": c.get("text", "")})
                        elif ct in ("input_image", "image_url"):
                            iu = c.get("image_url") or c.get("url", {})
                            url = iu.get("url", iu) if isinstance(iu, dict) else iu
                            if isinstance(url, str) and url.startswith("data:"):
                                mime, _, b64 = url.partition(";base64,")
                                mime = mime.replace("data:", "") or "image/png"
                                parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                            else:
                                parts.append({"text": str(url)})
                        elif ct in ("input_file", "file", "document", "inlineData", "inline_data"):
                            b64 = ""
                            mime = "text/plain"
                            filename = c.get("filename") or c.get("name") or "attachment"
                            if ct in ("inlineData", "inline_data"):
                                mime = c.get("mimeType") or c.get("mime_type") or "text/plain"
                                b64 = c.get("data", "")
                            elif ct == "document" and isinstance(c.get("source"), dict):
                                src = c["source"]
                                mime = src.get("media_type") or src.get("mime_type") or "text/plain"
                                b64 = src.get("data", "")
                            else:
                                fu = c.get("file_url") or c.get("document_url") or c.get("url", {})
                                url = fu.get("url", fu) if isinstance(fu, dict) else fu
                                if isinstance(url, str) and url.startswith("data:"):
                                    mime_part, _, b64 = url.partition(";base64,")
                                    mime = mime_part.replace("data:", "") or "text/plain"
                                else:
                                    b64 = c.get("data", "")
                                    mime = c.get("mimeType") or c.get("mime_type") or "text/plain"
                            
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
                                    formatted_text = f"\n[Attached File: {filename}]\n" + "-"*40 + f"\n{decoded_text}\n" + "-"*40 + "\n"
                                    parts.append({"text": formatted_text})
                                except Exception as e:
                                    print(f"[gemini-file-parser] failed to decode: {e}", file=sys.stderr)
                                    parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                            elif b64:
                                parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                elif isinstance(content, str):
                    parts.append({"text": content})
                if parts:
                    contents.append({"role": role, "parts": parts})
            elif t == "function_call":
                call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:24]}"
                fname = item.get("name", "")
                if call_id and fname:
                    tool_call_names[call_id] = fname
                args = item.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                fc_part = {"functionCall": {"name": fname, "args": args, "id": call_id}}
                stored_sig = _gemini_get_sig(f"fc:{call_id}") or _gemini_get_sig(f"fc:{fname}")
                if stored_sig:
                    fc_part["thoughtSignature"] = stored_sig
                    fc_part["thought_signature"] = stored_sig
                else:
                    fc_part["thought_signature"] = "skip_thought_signature_validator"
                contents.append({"role": "model", "parts": [fc_part]})
            elif t == "function_call_output":
                call_id = item.get("call_id", item.get("id", ""))
                output = item.get("output", "")
                fname = item.get("name", "") or tool_call_names.get(call_id, "")
                resp_part = {"functionResponse": {"name": fname or "unknown", "response": {"result": str(output)}}}
                if call_id:
                    resp_part["functionResponse"]["id"] = call_id
                contents.append({"role": "user", "parts": [resp_part]})

    sanitized = []
    last_user_text = None
    last_role = None
    for content in contents:
        role = content.get("role")
        parts = [p for p in content.get("parts", []) if isinstance(p, dict)]
        if not parts:
            continue
        has_function_call = any("functionCall" in p for p in parts)
        has_function_response = any("functionResponse" in p for p in parts)
        text_key = "\n".join([p.get("text", "") for p in parts if "text" in p]).strip()

        if has_function_call or has_function_response:
            sanitized.append({"role": role, "parts": parts})
            last_role = role
            continue

        if role == "user" and text_key and text_key == last_user_text:
            continue

        if role == last_role and role in ("user", "model") and sanitized:
            last_parts = sanitized[-1].get("parts", [])
            last_has_tool = any("functionCall" in p or "functionResponse" in p for p in last_parts)
            if not last_has_tool:
                sanitized[-1].setdefault("parts", []).extend(parts)
                if role == "user" and text_key:
                    last_user_text = text_key
                last_role = role
                continue

        sanitized.append({"role": role, "parts": parts})
        if role == "user" and text_key:
            last_user_text = text_key
        last_role = role

    while sanitized and sanitized[0].get("role") != "user":
        sanitized.pop(0)

    contents = sanitized

    instructions = body.get("instructions", "").strip()
    ag_identity = "You are Antigravity, a powerful agentic AI coding assistant designed by the Google Deepmind team working on Advanced Agentic Coding.\nYou are pair programming with a USER to solve their coding task. The task may require creating a new codebase, modifying or debugging an existing codebase, or simply answering a question.\n**Absolute paths only**\n**Proactiveness**"
    system_parts = [{"text": ag_identity}, {"text": "\n--- [SYSTEM_PROMPT_END] ---"}]
    if instructions:
        system_parts.append({"text": instructions})

    gen_config = {"maxOutputTokens": body.get("max_output_tokens", 64000), "stopSequences": ["\n\nHuman:", "[DONE]"]}
    if body.get("temperature") is not None:
        gen_config["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        gen_config["topP"] = body["top_p"]

    _is_claude_model = "claude" in model.lower()
    _is_claude_thinking = _is_claude_model and "thinking" in model.lower()

    if REASONING_ENABLED and REASONING_EFFORT != "none":
        if _is_claude_thinking:
            budget = {"low": 8192, "medium": 16384, "high": 32768}.get(REASONING_EFFORT, 16384)
            gen_config["thinkingConfig"] = {"include_thoughts": True, "thinking_budget": budget}
            if gen_config.get("maxOutputTokens", 0) <= budget:
                gen_config["maxOutputTokens"] = 64000
        elif not _is_claude_model:
            budget = {"low": 2048, "medium": 8192, "high": 24576}.get(REASONING_EFFORT, 8192)
            gen_config["thinkingConfig"] = {"includeThoughts": True, "thinkingBudget": budget}

    oa_tools = body.get("tools", [])
    gemini_tools = []
    if oa_tools:
        func_decls = []
        for tool in oa_tools:
            ttype = tool.get("type", "function")
            fname = tool.get("name", "")
            if ttype == "function":
                fn = tool.get("function", tool)
                name = fn.get("name", fname)
                desc = fn.get("description", "")
                params = fn.get("parameters", fn.get("input_schema", {}))
                func_decls.append({"name": name, "description": desc, "parameters": params})
            elif fname:
                func_decls.append({"name": fname, "description": tool.get("description", ""), "parameters": tool.get("parameters", {"type": "object", "properties": {}})})
        if func_decls:
            gemini_tools = [{"functionDeclarations": func_decls}]

    contents = _gemini_reattach_sigs(contents)

    ag_key = _antigravity_loop_key(handler._session_id)
    with _ANTIGRAVITY_LOOP_TRACKER_LOCK:
        if ag_key not in _ANTIGRAVITY_LOOP_TRACKER:
            _ANTIGRAVITY_LOOP_TRACKER[ag_key] = {
                "latest_user_hash": None, "nudge_injected": False, "latest_user_appended": False,
                "tool_calls_for_request": 0, "repeated_tool": False, "force_finalize": False,
                "last_tool": None, "last_tool_count": 0,
                "task_retry_count": 0, "total_tool_calls": 0, "first_seen": time.time(),
            }
        ag_state = _ANTIGRAVITY_LOOP_TRACKER[ag_key]

    latest_user = ""
    latest_user_hash = None
    if isinstance(input_data, list):
        for item in reversed(input_data):
            if item.get("type") == "message" and item.get("role") == "user":
                c = item.get("content", "")
                if isinstance(c, str):
                    latest_user = c
                elif isinstance(c, list):
                    latest_user = "\n".join(p.get("text", p.get("input_text", "")) for p in c if isinstance(p, dict))
                break
        if latest_user:
            latest_norm = " ".join(latest_user.strip().split())[:500]
            latest_norm = re.sub(r'<current_date>[^<]*</current_date>', '', latest_norm)
            latest_norm = re.sub(r'</?goal_context>', '', latest_norm)
            latest_norm = re.sub(r'</?environment_context>', '', latest_norm)
            latest_norm = " ".join(latest_norm.strip().split())[:200]
            latest_user_hash = hashlib.sha256(latest_norm.encode()).hexdigest()[:16]
    if latest_user_hash:
        task_key = _antigravity_loop_key(handler._session_id, latest_user_hash)
    else:
        task_key = ag_key
    if task_key != ag_key:
        with _ANTIGRAVITY_LOOP_TRACKER_LOCK:
            if task_key not in _ANTIGRAVITY_LOOP_TRACKER:
                _ANTIGRAVITY_LOOP_TRACKER[task_key] = dict(_ANTIGRAVITY_LOOP_TRACKER.get(ag_key, {
                    "latest_user_hash": None, "nudge_injected": False, "latest_user_appended": False,
                    "tool_calls_for_request": 0, "repeated_tool": False, "force_finalize": False,
                    "last_tool": None, "last_tool_count": 0,
                    "task_retry_count": 0, "total_tool_calls": 0, "first_seen": time.time(),
                }))
            ag_state = _ANTIGRAVITY_LOOP_TRACKER[task_key]
            ag_key = task_key

    with _ANTIGRAVITY_LOOP_TRACKER_LOCK:
        if latest_user_hash and latest_user_hash != ag_state.get("latest_user_hash"):
            ag_state["latest_user_hash"] = latest_user_hash
            ag_state["nudge_injected"] = False
            ag_state["latest_user_appended"] = False
            ag_state["tool_calls_for_request"] = 0
            ag_state["repeated_tool"] = False
            ag_state["last_tool"] = None
            ag_state["last_tool_count"] = 0
            ag_state["task_retry_count"] = 1
            ag_state["total_tool_calls"] = 0
            ag_state["first_seen"] = time.time()
            ag_state["force_finalize"] = False
        else:
            ag_state["task_retry_count"] = ag_state.get("task_retry_count", 0) + 1

    # Cross-session retry cap — only fires when same task retried many times
    if ag_state.get("task_retry_count", 0) >= 15:
        ag_state["task_retry_count"] = 0
        ag_state["force_finalize"] = False
        return handler._send_ag_finalize(
            "Task retry limit reached. Breaking out of loop. "
            "Try a more specific or smaller request if needed.",
            stream=body.get("stream", False))
    if ag_state.get("task_retry_count", 0) >= 8:
        ag_state["force_finalize"] = True

    if isinstance(input_data, list):
        n_tool_calls = sum(1 for it in input_data if isinstance(it, dict) and it.get("type") == "function_call")
        ag_state["tool_calls_for_request"] = n_tool_calls
        cumulative_calls = ag_state.get("total_tool_calls", 0) + n_tool_calls
        ag_state["total_tool_calls"] = cumulative_calls

        _mp = _model_profile(model)
        _mp_max_calls = _mp["max_tool_calls"]
        _mp_warn_calls = _mp["warn_tool_calls"]

        if cumulative_calls > _mp_max_calls:
            print(f"[{getattr(handler, '_session_id', '?')}] [antigravity-budget] HARD CAP: {cumulative_calls}/{_mp_max_calls} calls (model={model}), injecting force-write directive", file=sys.stderr)
            contents.append({"role": "user", "parts": [{"text":
                f"CRITICAL BUDGET LIMIT: {cumulative_calls} tool calls made. "
                f"YOU MUST STOP NOW. Do NOT call any more tools. "
                f"Write your FINAL answer immediately using the information you already have. "
                f"If you have file edits, apply them in this response using exec_command with a write command. "
                f"DO NOT READ ANY MORE FILES."}]})
        elif cumulative_calls > _mp_warn_calls:
            contents.append({"role": "user", "parts": [{"text":
                f"WARNING: {cumulative_calls} tool calls made. "
                f"{_mp_max_calls - cumulative_calls} remaining before forced stop. "
                f"STOP READING FILES AND APPLY YOUR EDITS NOW."}]})

        null_tool_names = {"get_goal", "get_remaining_tokens", "get_completion_budget", "status"}
        consecutive_null = 0
        for item in reversed(input_data):
            if isinstance(item, dict):
                if item.get("type") == "function_call" and item.get("name") in null_tool_names:
                    consecutive_null += 1
                elif item.get("type") == "function_call":
                    break
        if consecutive_null >= 3:
            ag_state["force_finalize"] = True
            print(f"[{getattr(handler, '_session_id', '?')}] [antigravity-loop] NULL-TOOL LOOP: {consecutive_null} consecutive {null_tool_names} calls, forcing finalize", file=sys.stderr)

        last_tool_key = None
        for item in reversed(input_data):
            if isinstance(item, dict) and item.get("type") == "function_call":
                fname = item.get("name", "")
                args_str = json.dumps(item.get("arguments", {}), sort_keys=True)[:100]
                last_tool_key = f"{fname}:{args_str}"
                break
        if last_tool_key:
            if last_tool_key == ag_state.get("last_tool"):
                ag_state["last_tool_count"] = ag_state.get("last_tool_count", 0) + 1
                if ag_state["last_tool_count"] >= 5:
                    ag_state["repeated_tool"] = True
                    ag_state["force_finalize"] = True
            else:
                ag_state["last_tool"] = last_tool_key
                ag_state["last_tool_count"] = 1

    if ag_state.get("force_finalize"):
        return handler._send_ag_finalize(
            "Loop detected. The proxy is forcing a stop because the model repeatedly "
            "called tools without making progress. Try a more specific or smaller request.",
            stream=body.get("stream", False))

    if not _antigravity_is_simple_user(latest_user):
        contents.insert(0, {"role": "user", "parts": [{"text": _GEMINI_AGENT_GUARDRAIL}]})

    request_body = {"contents": contents, "safetySettings": [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
        {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "OFF"},
    ]}
    request_body["systemInstruction"] = {"role": "user", "parts": system_parts}
    if gen_config:
        request_body["generationConfig"] = gen_config
    _budget_exceeded = ag_state.get("total_tool_calls", 0) > _mp.get("max_tool_calls", 150)
    if gemini_tools and not _budget_exceeded and not ag_state.get("force_finalize"):
        request_body["tools"] = gemini_tools
    elif _budget_exceeded or ag_state.get("force_finalize"):
        print(f"[{getattr(handler, '_session_id', '?')}] [antigravity-budget] TOOLS STRIPPED from request (budget exceeded or force_finalize)", file=sys.stderr)
    if _is_claude_model and "tools" in request_body:
        request_body["toolConfig"] = {"functionCallingConfig": {"mode": "VALIDATED"}}

    import platform as _plat
    _os_name = _plat.system().lower()
    _os_arch = _plat.machine().lower().replace("x86_64", "x64").replace("aarch64", "arm64")
    _fetched_ver = _ensure_antigravity_version()
    _ag_ua = f"antigravity/{_fetched_ver} {_os_name}/{_os_arch}"
    
    # Get platform for Client-Metadata header (repo4/opencode-antigravity-auth)
    _client_meta_platform = "WINDOWS" if _os_name == "windows" else "MACOS"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Antigravity/{_fetched_ver} Chrome/138.0.7204.235 Electron/37.3.1 Safari/537.36",
        "X-Client-Name": "antigravity",
        "X-Client-Version": _ensure_antigravity_client_version(),
        "x-goog-api-client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps({
            "ideType": "ANTIGRAVITY",
            "platform": _client_meta_platform,
            "pluginType": "GEMINI"
        }),
    }

    wrapped = {
        "project": project_id,
        "model": model,
        "requestType": "agent",
        "userAgent": _ag_ua,
        "requestId": f"agent-{uuid.uuid4().hex[:12]}",
        "request": request_body,
    }
    wrapped["request"]["sessionId"] = f"{uuid.uuid4().hex}{int(time.time()*1000)}"

    _antigravity_endpoints = [
        "https://cloudcode-pa.googleapis.com",
        "https://daily-cloudcode-pa.sandbox.googleapis.com",
        "https://autopush-cloudcode-pa.sandbox.googleapis.com",
    ]

    body_b = json.dumps(wrapped).encode()
    print(f"[{handler._session_id}] [antigravity-v2] model={model} stream={stream} contents={len(contents)} tools={bool(gemini_tools)} project={project_id} ver={_fetched_ver}", file=sys.stderr)
    if os.environ.get("CODEX_LAUNCHER_DEBUG"):
        try:
            debug_path = os.path.join(_LOG_DIR, f"antigravity-v2-request-{handler._session_id}.json")
            with open(debug_path, "w") as dbg:
                json.dump(wrapped, dbg, indent=2)
        except Exception:
            pass

    upstream = None
    chosen_ep = None
    global _antigravity_preferred_endpoint
    with _antigravity_endpoint_lock:
        _pref = _antigravity_preferred_endpoint
    ordered = ([_pref] + [e for e in _antigravity_endpoints if e != _pref]) if _pref and _pref in _antigravity_endpoints else list(_antigravity_endpoints)

    _all_404 = True
    for ep in ordered:
        action = "streamGenerateContent" if stream else "generateContent"
        url_suffix = f"v1internal:{action}?alt=sse" if stream else f"v1internal:{action}"
        target = f"{ep}/{url_suffix}"
        req = urllib.request.Request(target, data=body_b, headers=headers)
        try:
            upstream = urllib.request.urlopen(req, timeout=_upstream_timeout(body, stream))
            chosen_ep = ep
            _all_404 = False
            with _antigravity_endpoint_lock:
                _antigravity_preferred_endpoint = ep
            break
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            err_class = _classify_antigravity_error(e.code, err_body)
            print(f"[{handler._session_id}] [antigravity-v2] {ep.replace('https://','')} {e.code} class={err_class} body={err_body[:300]}", file=sys.stderr)
            if e.code != 404:
                _all_404 = False
            if e.code in (400, 404):
                if os.environ.get("CODEX_LAUNCHER_DEBUG"):
                    try:
                        debug_path = os.path.join(_LOG_DIR, f"antigravity-v2-{e.code}.json")
                        with open(debug_path, "w") as dbg:
                            json.dump({"endpoint": ep, "url": target, "model": model, "wrapped": wrapped, "error": err_body}, dbg, indent=2)
                    except Exception:
                        pass
                if e.code == 400:
                    return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            if err_class in ("auth_permanent", "forbidden", "account_banned", "validation_required"):
                return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            if err_class == "auth_transient":
                print(f"[{handler._session_id}] [antigravity-v2] 401 transient, force-refreshing token", file=sys.stderr)
                try:
                    _force_refresh_google_token()
                    access_token = _refresh_oauth_token()
                    headers["Authorization"] = f"Bearer {access_token}"
                    new_body_b = json.dumps(wrapped).encode()
                    retry_req = urllib.request.Request(target, data=new_body_b, headers=headers)
                    upstream = urllib.request.urlopen(retry_req, timeout=_upstream_timeout(body, stream))
                    chosen_ep = ep
                    with _antigravity_endpoint_lock:
                        _antigravity_preferred_endpoint = ep
                    print(f"[{handler._session_id}] [antigravity-v2] 401 retry succeeded", file=sys.stderr)
                    break
                except Exception as retry_e:
                    print(f"[{handler._session_id}] [antigravity-v2] 401 retry failed: {retry_e}", file=sys.stderr)
                    return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            if err_class == "service_disabled":
                _is_prod = "cloudcode-pa.googleapis.com" in ep and "sandbox" not in ep
                if _is_prod:
                    return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            if err_class in ("quota_exhausted", "rate_limited"):
                pool = _google_antigravity_pool
                _, acct = _get_google_account(OAUTH_PROVIDER)
                if acct:
                    reset_s = _parse_rate_limit_reset(err_body)
                    cooldown = reset_s if reset_s and reset_s > 10 else 60
                    pool.mark_rate_limited(acct, cooldown)
                return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            if ep == ordered[-1] and not _all_404:
                return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            continue
        except Exception as e:
            _all_404 = False
            print(f"[{handler._session_id}] [antigravity-v2] {ep.replace('https://','')} conn failed: {e}", file=sys.stderr)
            if ep == ordered[-1]:
                return handler.send_json(502, {"error": {"type": "proxy_error", "message": str(e)}})
            continue

    if _all_404 and upstream is None:
        print(f"[{handler._session_id}] [antigravity-v2] all endpoints 404, invalidating version cache and re-fetching", file=sys.stderr)
        global _antigravity_version_validated
        with _antigravity_version_lock:
            _antigravity_version_validated = False
            _antigravity_version_checked = 0
        _new_ver = _ensure_antigravity_version()
        if _new_ver != _fetched_ver:
            print(f"[{handler._session_id}] [antigravity-v2] version changed {_fetched_ver} -> {_new_ver}, retrying", file=sys.stderr)
            _ag_ua_new = f"antigravity/{_new_ver} {_os_name}/{_os_arch}"
            headers["User-Agent"] = _ag_ua_new
            wrapped["userAgent"] = _ag_ua_new
            body_b = json.dumps(wrapped).encode()
            for ep in ordered:
                action = "streamGenerateContent" if stream else "generateContent"
                url_suffix = f"v1internal:{action}?alt=sse" if stream else f"v1internal:{action}"
                target = f"{ep}/{url_suffix}"
                req = urllib.request.Request(target, data=body_b, headers=headers)
                try:
                    upstream = urllib.request.urlopen(req, timeout=_upstream_timeout(body, stream))
                    chosen_ep = ep
                    with _antigravity_endpoint_lock:
                        _antigravity_preferred_endpoint = ep
                    break
                except urllib.error.HTTPError as e:
                    err_body = e.read().decode()
                    print(f"[{handler._session_id}] [antigravity-v2-retry] {ep.replace('https://','')} {e.code}", file=sys.stderr)
                    if e.code == 400:
                        return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
                    if ep == ordered[-1]:
                        return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
                    continue
                except Exception as e:
                    if ep == ordered[-1]:
                        return handler.send_json(502, {"error": {"type": "proxy_error", "message": str(e)}})
                    continue

    if upstream is None:
        if _all_404:
            grpc_result = try_grpc_fallback(handler, wrapped, access_token, stream, tracker)
            if grpc_result is not None:
                return
        return handler.send_json(502, {"error": {"type": "proxy_error", "message": "All endpoints failed"}})

    if stream:
        forward_gemini_sse(handler, upstream, model, body, input_data, tracker)
    else:
        forward_gemini_json(handler, upstream, model, body, input_data)


def try_grpc_fallback(handler, wrapped_dict, access_token, stream, tracker=None):
    grpc_client = _get_grpc_client()
    if grpc_client is None:
        print(f"[{handler._session_id}] [antigravity-grpc] gRPC fallback not available (grpcio not installed), skipping", file=sys.stderr)
        return None

    grpc_wrapped = dict(wrapped_dict)
    rest_model = grpc_wrapped.get("model", "")
    grpc_model = _GRPC_REVERSE_ALIAS.get(rest_model, rest_model)
    grpc_wrapped["model"] = grpc_model
    if grpc_model != rest_model:
        print(f"[{handler._session_id}] [antigravity-grpc] model remapped for gRPC: REST={rest_model} -> gRPC={grpc_model}", file=sys.stderr)

    print(f"[{handler._session_id}] [antigravity-grpc] REST 404, trying gRPC fallback with model={grpc_model} stream={stream}", file=sys.stderr)

    try:
        result = grpc_client.try_generate(
            grpc_wrapped,
            stream=stream,
            access_token=access_token,
            timeout_s=180,
        )
    except Exception as e:
        print(f"[{handler._session_id}] [antigravity-grpc] gRPC call exception: {e}", file=sys.stderr)
        return None

    if not result.ok:
        print(f"[{handler._session_id}] [antigravity-grpc] gRPC fallback also failed: {result.error_message}", file=sys.stderr)
        return None

    print(f"[{handler._session_id}] [antigravity-grpc] gRPC fallback OK! endpoint={result.endpoint_used} model={result.model_used} elapsed={result.elapsed_s:.1f}s", file=sys.stderr)

    if stream and result.stream_chunks is not None:
        forward_grpc_sse(handler, result, grpc_model)
    elif not stream and result.response_data is not None:
        forward_grpc_json(handler, result, grpc_model)
    else:
        print(f"[{handler._session_id}] [antigravity-grpc] unexpected result shape, no data to forward", file=sys.stderr)
        return None

    return True


def forward_grpc_sse(handler, grpc_result, model):
    resp_id = f"resp-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()

    full_text = ""
    output_items = []
    current_tool_calls = {}
    message_started = False
    message_id = f"msg-{uuid.uuid4().hex[:24]}"

    def flush_event(event_type, data):
        handler.wfile.write(f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode())
        handler.wfile.flush()

    flush_event("response.created", {"type": "response.created", "response": {"id": resp_id, "object": "response", "model": model, "status": "in_progress", "created": created, "output": []}})
    flush_event("response.in_progress", {"type": "response.in_progress", "response": {"id": resp_id}})

    for chunk in grpc_result.stream_chunks:
        candidates = chunk.get("response", chunk).get("candidates", [])
        if not candidates:
            continue
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            sig = _extract_gemini_sig(part)
            if sig:
                if part.get("functionCall"):
                    fc_id = part["functionCall"].get("id") or part["functionCall"].get("name")
                    fc_name = part["functionCall"].get("name")
                    if fc_id:
                        _gemini_store_sig(f"fc:{fc_id}", sig)
                    if fc_name:
                        _gemini_store_sig(f"fc:{fc_name}", sig)
                _gemini_store_sig(f"turn:{resp_id}", sig)
            if part.get("thought"):
                sig_from_thought = _extract_gemini_sig(part)
                if sig_from_thought:
                    _gemini_store_sig(f"turn:{resp_id}", sig_from_thought)
                continue
            if "text" in part and not part.get("functionCall"):
                text_delta = part["text"]
                if not text_delta:
                    continue
                full_text += text_delta
                if not message_started:
                    flush_event("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": message_id, "role": "assistant", "content": []}})
                    flush_event("response.content_part.added", {"type": "response.content_part.added", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": ""}})
                    output_items.append({"text": True})
                    message_started = True
                flush_event("response.output_text.delta", {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": text_delta})
            elif part.get("functionCall"):
                fc = part["functionCall"]
                call_id = f"call_{uuid.uuid4().hex[:24]}"
                args_str = json.dumps(fc.get("args", fc.get("arguments", {})))
                output_index = len(output_items)
                flush_event("response.output_item.added", {"type": "response.output_item.added", "output_index": output_index, "item": {"type": "function_call", "id": call_id, "call_id": call_id, "name": fc.get("name", ""), "arguments": ""}})
                flush_event("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "output_index": output_index, "item_id": call_id, "delta": args_str})
                flush_event("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "output_index": output_index, "item_id": call_id, "arguments": args_str})
                current_tool_calls[call_id] = fc
                output_items.append({"tool": True})

    out = []
    if full_text:
        out.append({"type": "message", "id": message_id, "role": "assistant", "content": [{"type": "output_text", "text": full_text}]})
    tool_outputs = []
    for cid, fc in current_tool_calls.items():
        tool_outputs.append({"type": "function_call", "id": cid, "call_id": cid, "name": fc.get("name", ""), "arguments": json.dumps(fc.get("args", fc.get("arguments", {})))})
    out.extend(tool_outputs)

    final_resp = {"id": resp_id, "object": "response", "model": model, "status": "completed", "created": created, "output": out}
    if full_text:
        flush_event("response.output_text.done", {"type": "response.output_text.done", "output_index": 0, "content_index": 0, "text": full_text})
        flush_event("response.content_part.done", {"type": "response.content_part.done", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": full_text}})
        flush_event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": out[0]})
    for idx, item in enumerate(tool_outputs, start=(1 if full_text else 0)):
        flush_event("response.output_item.done", {"type": "response.output_item.done", "output_index": idx, "item": item})
    flush_event("response.completed", {"type": "response.completed", "response": final_resp})
    handler.close_connection = True

    from proxy.server import _response_store_lock, _response_store, _MAX_STORED
    with _response_store_lock:
        _response_store[resp_id] = final_resp
        while len(_response_store) > _MAX_STORED:
            _response_store.popitem(last=False)


def forward_grpc_json(handler, grpc_result, model):
    resp_id = f"resp-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    out = []
    full_text = ""
    data = grpc_result.response_data
    candidates = data.get("response", data).get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = []
        for part in parts:
            if part.get("thought"):
                continue
            if "text" in part and not part.get("functionCall"):
                text_parts.append(part["text"])
            elif part.get("functionCall"):
                fc = part["functionCall"]
                call_id = f"call_{uuid.uuid4().hex[:24]}"
                out.append({"type": "function_call", "id": call_id, "call_id": call_id, "name": fc.get("name", ""), "arguments": json.dumps(fc.get("args", fc.get("arguments", {})))})
        if text_parts:
            full_text = "".join(text_parts)
            out.insert(0, {"type": "message", "id": f"msg-{uuid.uuid4().hex[:24]}", "role": "assistant", "content": [{"type": "output_text", "text": full_text}]})
    resp = {"id": resp_id, "object": "response", "model": model, "status": "completed", "created": created, "output": out}
    
    from proxy.server import _response_store_lock, _response_store, _MAX_STORED
    with _response_store_lock:
        _response_store[resp_id] = resp
        while len(_response_store) > _MAX_STORED:
            _response_store.popitem(last=False)
    handler.send_json(200, resp)


def handle_gemini_oauth(handler, body, model, stream, tracker=None):
    ag_state = {}
    _mp_oa = {"max_tool_calls": 150, "warn_tool_calls": 100}
    input_data = body.get("input", "")
    policy = provider_policy()
    original_model = model

    _GEMINI_KEEP_RECENT = 6
    _GEMINI_OLD_LIMIT = 3000
    _GEMINI_RECENT_LIMIT = 20000

    if isinstance(input_data, list) and len(input_data) > 8:
        n_tool_outputs = sum(1 for it in input_data if isinstance(it, dict) and it.get("type") == "function_call_output")
        if n_tool_outputs > 2:
            tool_indexes = [i for i, it in enumerate(input_data) if isinstance(it, dict) and it.get("type") == "function_call_output"]
            recent_set = set(tool_indexes[-_GEMINI_KEEP_RECENT:])
            compacted_data = []
            for i, item in enumerate(input_data):
                if isinstance(item, dict) and item.get("type") == "function_call_output":
                    o = item.get("output", "")
                    limit = _GEMINI_RECENT_LIMIT if i in recent_set else _GEMINI_OLD_LIMIT
                    if len(o) > limit:
                        item = dict(item)
                        item["output"] = o[:limit] + f"\n... [proxy compacted: kept {limit} of {len(o)} chars]"
                compacted_data.append(item)
            input_data = compacted_data
            body = dict(body)
            body["input"] = input_data
            print(f"[gemini-compact] {n_tool_outputs} tool outputs, recent={_GEMINI_RECENT_LIMIT} old={_GEMINI_OLD_LIMIT}", file=sys.stderr)

    if OAUTH_PROVIDER == "google-antigravity":
        alias_map = {
            "Gemini 3.5 Flash (High)": "gemini-3-flash",
            "Gemini 3.5 Flash (Medium)": "gemini-3-flash",
            "Gemini 3.5 Flash (Low)": "gemini-3.5-flash-low",
            "gemini-3.5-flash-high": "gemini-3-flash",
            "gemini-3.5-flash-medium": "gemini-3-flash",
            "gemini-3.5-flash-low": "gemini-3.5-flash-low",
            "gemini-3-flash-preview": "gemini-3-flash",
            "gemini-3-flash": "gemini-3-flash",
            "antigravity-gemini-3-flash": "gemini-3-flash",
            "Gemini 3.1 Pro (High)": "gemini-3.1-pro-low",
            "Gemini 3.1 Pro (Low)": "gemini-3.1-pro-low",
            "gemini-3.1-pro-high": "gemini-3.1-pro-low",
            "gemini-3.1-pro-low": "gemini-3.1-pro-low",
            "gemini-3.1-pro-preview": "gemini-3.1-pro-low",
            "gemini-3.1-pro": "gemini-3.1-pro-low",
            "gemini-3-pro-preview": "gemini-3.1-pro-low",
            "gemini-3-pro": "gemini-3.1-pro-low",
            "gemini-3-pro-low": "gemini-3.1-pro-low",
            "gemini-3-pro-high": "gemini-3.1-pro-low",
            "antigravity-gemini-3-pro": "gemini-3.1-pro-low",
            "antigravity-gemini-3.1-pro": "gemini-3.1-pro-low",
            "Claude Sonnet 4.6 (Thinking)": "claude-sonnet-4-6",
            "Claude Sonnet 4.6 Thinking": "claude-sonnet-4-6",
            "claude-sonnet-4.6-thinking": "claude-sonnet-4-6",
            "antigravity-claude-sonnet-4-6": "claude-sonnet-4-6",
            "Claude Opus 4.6 (Thinking)": "claude-opus-4-6-thinking",
            "Claude Opus 4.6 Thinking": "claude-opus-4-6-thinking",
            "claude-opus-4.6-thinking": "claude-opus-4-6-thinking",
            "antigravity-claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
            "GPT-OSS 120B (Medium)": "gpt-oss-120b-medium",
            "GPT-OSS 120B Medium": "gpt-oss-120b-medium",
            "gpt-oss-120b": "gpt-oss-120b-medium",
            "gemini-2.5-flash": "gemini-2.5-flash",
            "gemini-2.5-pro": "gemini-2.5-pro",
            "gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
        }
        model = alias_map.get(model, model)
        if model != original_model:
            print(f"[antigravity] model mapped user={original_model} upstream={model}", file=sys.stderr)

    pair_errors = validate_tool_pairs(input_data)
    if pair_errors:
        input_data = repair_orphan_tool_outputs(input_data, pair_errors)
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

    if OAUTH_PROVIDER == "google-antigravity" and isinstance(input_data, list) and "claude" not in model.lower():
        input_data = _antigravity_normalize_context(input_data, model)
        body = dict(body)
        body["input"] = input_data

    access_token = _refresh_oauth_token()
    token_name = "google-antigravity-oauth-token.json" if OAUTH_PROVIDER == "google-antigravity" else "google-cli-oauth-token.json"
    token_path = os.path.join(_LOG_DIR, token_name)
    project_id = ""
    try:
        with open(token_path) as f:
            project_id = json.load(f).get("project_id", "")
    except Exception:
        pass

    contents = []
    system_parts = []
    instructions = body.get("instructions", "").strip()
    tool_call_names = {}

    if isinstance(input_data, list):
        for item in input_data:
            t = item.get("type")
            if t == "message":
                role = "user" if item.get("role") == "user" else "model"
                content = item.get("content", "")
                if isinstance(content, list):
                    parts = []
                    for c in content:
                        ct = c.get("type")
                        if ct in ("input_text", "text"):
                            parts.append({"text": c.get("text", "")})
                        elif ct in ("input_image", "image_url"):
                            iu = c.get("image_url") or c.get("url", {})
                            url = iu.get("url", iu) if isinstance(iu, dict) else iu
                            if isinstance(url, str) and url.startswith("data:"):
                                mime, _, b64 = url.partition(";base64,")
                                mime = mime.replace("data:", "") or "image/png"
                                parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                            else:
                                parts.append({"text": str(url)})
                        elif ct in ("input_file", "file", "document", "inlineData", "inline_data"):
                            b64 = ""
                            mime = "text/plain"
                            filename = c.get("filename") or c.get("name") or "attachment"
                            if ct in ("inlineData", "inline_data"):
                                mime = c.get("mimeType") or c.get("mime_type") or "text/plain"
                                b64 = c.get("data", "")
                            elif ct == "document" and isinstance(c.get("source"), dict):
                                src = c["source"]
                                mime = src.get("media_type") or src.get("mime_type") or "text/plain"
                                b64 = src.get("data", "")
                            else:
                                fu = c.get("file_url") or c.get("document_url") or c.get("url", {})
                                url = fu.get("url", fu) if isinstance(fu, dict) else fu
                                if isinstance(url, str) and url.startswith("data:"):
                                    mime_part, _, b64 = url.partition(";base64,")
                                    mime = mime_part.replace("data:", "") or "text/plain"
                                else:
                                    b64 = c.get("data", "")
                                    mime = c.get("mimeType") or c.get("mime_type") or "text/plain"
                            
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
                                    formatted_text = f"\n[Attached File: {filename}]\n" + "-"*40 + f"\n{decoded_text}\n" + "-"*40 + "\n"
                                    parts.append({"text": formatted_text})
                                except Exception as e:
                                    print(f"[gemini-file-parser] failed to decode: {e}", file=sys.stderr)
                                    parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                            elif b64:
                                parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                    if parts:
                        contents.append({"role": role, "parts": parts})
                elif isinstance(content, str):
                    contents.append({"role": role, "parts": [{"text": content}]})
            elif t == "function_call":
                call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:24]}"
                fname = item.get("name", "")
                if call_id and fname:
                    tool_call_names[call_id] = fname
                args = item.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                fc_part = {"functionCall": {"name": fname, "args": args, "id": call_id}}
                stored_sig = _gemini_get_sig(f"fc:{call_id}") or _gemini_get_sig(f"fc:{fname}")
                if stored_sig:
                    fc_part["thoughtSignature"] = stored_sig
                    fc_part["thought_signature"] = stored_sig
                else:
                    fc_part["thought_signature"] = "skip_thought_signature_validator"
                contents.append({"role": "model", "parts": [fc_part]})
            elif t == "function_call_output":
                call_id = item.get("call_id", item.get("id", ""))
                output = item.get("output", "")
                fname = item.get("name", "") or tool_call_names.get(call_id, "")
                try:
                    output_parsed = json.loads(output) if isinstance(output, str) else output
                except Exception:
                    output_parsed = output
                resp_part = {"functionResponse": {"name": fname or "unknown", "response": {"result": output_parsed if isinstance(output_parsed, (dict, list)) else output}}}
                if call_id:
                    resp_part["functionResponse"]["id"] = call_id
                contents.append({"role": "user", "parts": [resp_part]})

    if OAUTH_PROVIDER.startswith("google") and "claude" not in model.lower():
        sanitized = []
        last_user_text = None
        last_role = None
        for content in contents:
            role = content.get("role")
            parts = [p for p in content.get("parts", []) if isinstance(p, dict)]
            if not parts:
                continue
            has_function_call = any("functionCall" in p for p in parts)
            has_function_response = any("functionResponse" in p for p in parts)
            text_key = "\n".join([p.get("text", "") for p in parts if "text" in p]).strip()
            
            if has_function_call or has_function_response:
                sanitized.append({"role": role, "parts": parts})
                continue
            
            if role == "user" and text_key and text_key == last_user_text:
                continue
            
            if role == last_role and role in ("user", "model") and sanitized:
                last_parts = sanitized[-1].get("parts", [])
                last_has_tool = any("functionCall" in p or "functionResponse" in p for p in last_parts)
                if not last_has_tool:
                    sanitized[-1].setdefault("parts", []).extend(parts)
                    if role == "user" and text_key:
                        last_user_text = text_key
                    continue
            
            sanitized.append({"role": role, "parts": parts})
            if role == "user" and text_key:
                last_user_text = text_key
            last_role = role
        
        while sanitized and sanitized[0].get("role") != "user":
            sanitized.pop(0)
        while sanitized and sanitized[-1].get("role") != "user":
            sanitized.pop()
        contents = sanitized

    if instructions:
        system_parts.append({"text": instructions})
    if OAUTH_PROVIDER == "google-antigravity":
        system_parts.append({"text": (
            "You are connected through a Responses API translation proxy. "
            "If tools are available and the user's request requires changing files, call the appropriate tool immediately. "
            "Do not announce plans, do not say you will list files, browse, fetch, inspect, or start by exploring unless you are emitting the actual tool call in the same response. "
            "For file creation requests, use tools to create or modify the file instead of only printing code in chat. "
            "If no suitable tool is available, answer directly with the complete result. "
            "Never answer only with a plan such as 'I will start by...' or 'I am going to...'."
        )})

    gen_config = {}
    mot = body.get("max_output_tokens", 0)
    if mot:
        gen_config["maxOutputTokens"] = mot
    if body.get("temperature") is not None:
        gen_config["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        gen_config["topP"] = body["top_p"]

    _is_claude_model = "claude" in model.lower()
    _is_claude_thinking = _is_claude_model and "thinking" in model.lower()

    if OAUTH_PROVIDER == "google-antigravity" and _is_claude_thinking:
        if REASONING_ENABLED and REASONING_EFFORT != "none":
            budget = {"low": 8192, "medium": 16384, "high": 32768}.get(REASONING_EFFORT, 16384)
        else:
            budget = 16384
        gen_config["thinkingConfig"] = {
            "include_thoughts": True,
            "thinking_budget": budget,
        }
        current_max = gen_config.get("maxOutputTokens", 0)
        if not current_max or current_max <= budget:
            gen_config["maxOutputTokens"] = 64000
        print(f"[antigravity-claude] thinking model={model} budget={budget} maxOutputTokens={gen_config.get('maxOutputTokens')}", file=sys.stderr)
    elif OAUTH_PROVIDER == "google-antigravity" and _is_claude_model:
        if "thinkingConfig" in gen_config:
            del gen_config["thinkingConfig"]
    elif REASONING_ENABLED and REASONING_EFFORT != "none":
        budget = {"low": 2048, "medium": 8192, "high": 24576}.get(REASONING_EFFORT, 8192)
        gen_config["thinkingConfig"] = {"includeThoughts": True, "thinkingBudget": budget}

    oa_tools = body.get("tools", [])
    gemini_tools = []
    if oa_tools:
        func_decls = []
        for tool in oa_tools:
            ttype = tool.get("type", "function")
            fname = tool.get("name", "")
            if ttype == "function":
                fn = tool.get("function", tool)
                name = fn.get("name", fname)
                desc = fn.get("description", "")
                params = fn.get("parameters", fn.get("input_schema", {}))
                func_decls.append({"name": name, "description": desc, "parameters": params})
            elif fname:
                func_decls.append({"name": fname, "description": tool.get("description", ""), "parameters": tool.get("parameters", {"type": "object", "properties": {}})})
        if func_decls:
            gemini_tools = [{"functionDeclarations": func_decls}]

    if OAUTH_PROVIDER == "google-antigravity":
        contents = _gemini_reattach_sigs(contents)

    if OAUTH_PROVIDER == "google-antigravity":
        latest_user = ""
        if isinstance(input_data, list):
            for item in reversed(input_data):
                if item.get("type") == "message" and item.get("role") == "user":
                    c = item.get("content", "")
                    if isinstance(c, str):
                        latest_user = c
                    elif isinstance(c, list):
                        latest_user = "\n".join(p.get("text", p.get("input_text", "")) for p in c if isinstance(p, dict))
                    break
        is_latest_simple = _antigravity_is_simple_user(latest_user)
        if not is_latest_simple:
            contents.insert(0, {"role": "user", "parts": [{"text": _GEMINI_AGENT_GUARDRAIL}]})

        if OAUTH_PROVIDER == "google-antigravity":
            ag_key = _antigravity_loop_key(handler._session_id)
            with _ANTIGRAVITY_LOOP_TRACKER_LOCK:
                if ag_key not in _ANTIGRAVITY_LOOP_TRACKER:
                    _ANTIGRAVITY_LOOP_TRACKER[ag_key] = {
                        "latest_user_hash": None,
                        "nudge_injected": False,
                        "latest_user_appended": False,
                        "tool_calls_for_request": 0,
                        "repeated_tool": False,
                        "force_finalize": False,
                        "last_tool": None,
                        "last_tool_count": 0,
                        "task_retry_count": 0,
                        "total_tool_calls": 0,
                        "first_seen": time.time(),
                    }
                ag_state = _ANTIGRAVITY_LOOP_TRACKER[ag_key]

            latest_user = ""
            latest_user_hash = None
            if isinstance(input_data, list):
                for item in reversed(input_data):
                    if item.get("type") == "message" and item.get("role") == "user":
                        c = item.get("content", "")
                        if isinstance(c, str):
                            latest_user = c
                        elif isinstance(c, list):
                            latest_user = "\n".join(p.get("text", p.get("input_text", "")) for p in c if isinstance(p, dict))
                        break
                if latest_user:
                    latest_norm = " ".join(latest_user.strip().split())[:500]
                    latest_norm = re.sub(r'<current_date>[^<]*</current_date>', '', latest_norm)
                    latest_norm = re.sub(r'</?goal_context>', '', latest_norm)
                    latest_norm = re.sub(r'</?environment_context>', '', latest_norm)
                    latest_norm = " ".join(latest_norm.strip().split())[:200]
                    latest_user_hash = hashlib.sha256(latest_norm.encode()).hexdigest()[:16]

            if latest_user_hash:
                task_key = _antigravity_loop_key(handler._session_id, latest_user_hash)
            else:
                task_key = ag_key
            if task_key != ag_key:
                with _ANTIGRAVITY_LOOP_TRACKER_LOCK:
                    if task_key not in _ANTIGRAVITY_LOOP_TRACKER:
                        _ANTIGRAVITY_LOOP_TRACKER[task_key] = dict(_ANTIGRAVITY_LOOP_TRACKER.get(ag_key, {
                            "latest_user_hash": None, "nudge_injected": False,
                            "latest_user_appended": False, "tool_calls_for_request": 0,
                            "repeated_tool": False, "force_finalize": False,
                            "last_tool": None, "last_tool_count": 0,
                            "task_retry_count": 0, "total_tool_calls": 0, "first_seen": time.time(),
                        }))
                    ag_state = _ANTIGRAVITY_LOOP_TRACKER[task_key]
                    ag_key = task_key

            with _ANTIGRAVITY_LOOP_TRACKER_LOCK:
                if latest_user_hash and latest_user_hash != ag_state.get("latest_user_hash"):
                    ag_state["latest_user_hash"] = latest_user_hash
                    ag_state["nudge_injected"] = False
                    ag_state["latest_user_appended"] = False
                    ag_state["tool_calls_for_request"] = 0
                    ag_state["repeated_tool"] = False
                    ag_state["last_tool"] = None
                    ag_state["last_tool_count"] = 0
                    ag_state["task_retry_count"] = 1
                    ag_state["total_tool_calls"] = 0
                    ag_state["first_seen"] = time.time()
                    ag_state["force_finalize"] = False
                else:
                    ag_state["task_retry_count"] = ag_state.get("task_retry_count", 0) + 1

            if ag_state.get("task_retry_count", 0) >= 15:
                ag_state["task_retry_count"] = 0
                ag_state["force_finalize"] = False
                handler._send_ag_finalize("Task retry limit reached. Breaking loop.",
                                       stream=body.get("stream", False) if isinstance(body, dict) else False)
                return
            if ag_state.get("task_retry_count", 0) >= 8:
                ag_state["force_finalize"] = True

            if isinstance(input_data, list):
                n_tool_calls = sum(1 for it in input_data if isinstance(it, dict) and it.get("type") == "function_call")
                ag_state["tool_calls_for_request"] = n_tool_calls
                cumulative_calls = ag_state.get("total_tool_calls", 0) + n_tool_calls
                ag_state["total_tool_calls"] = cumulative_calls

                _mp_oa = _model_profile(model)
                _mp_max = _mp_oa["max_tool_calls"]
                _mp_warn = _mp_oa["warn_tool_calls"]

                if cumulative_calls > _mp_max:
                    print(f"[antigravity-budget] HARD CAP: {cumulative_calls}/{_mp_max} calls (model={model}), injecting force-write", file=sys.stderr)
                    contents.append({"role": "user", "parts": [{"text":
                        f"CRITICAL BUDGET LIMIT: {cumulative_calls} tool calls. "
                        f"STOP ALL TOOL CALLS. Write your FINAL answer now. "
                        f"Apply any edits using exec_command with a write command in this response."}]})
                elif cumulative_calls > _mp_warn:
                    contents.append({"role": "user", "parts": [{"text":
                        f"WARNING: {cumulative_calls} tool calls. "
                        f"{_mp_max - cumulative_calls} remaining. "
                        f"STOP READING AND WRITE NOW."}]})

        null_tool_names = {"get_goal", "get_remaining_tokens", "get_completion_budget", "status"}
        consecutive_null = 0
        for item in reversed(input_data):
            if isinstance(item, dict):
                if item.get("type") == "function_call" and item.get("name") in null_tool_names:
                    consecutive_null += 1
                elif item.get("type") == "function_call":
                    break
        if consecutive_null >= 3:
            ag_state["force_finalize"] = True
            print(f"[{getattr(handler, '_session_id', '?')}] [antigravity-loop] NULL-TOOL LOOP: {consecutive_null} consecutive {null_tool_names} calls, forcing finalize", file=sys.stderr)

        last_tool_key = None
        for item in reversed(input_data):
            if isinstance(item, dict) and item.get("type") == "function_call":
                fname = item.get("name", "")
                args_str = json.dumps(item.get("arguments", {}), sort_keys=True)[:100]
                last_tool_key = f"{fname}:{args_str}"
                break
        if last_tool_key:
            if last_tool_key == ag_state["last_tool"]:
                ag_state["last_tool_count"] += 1
                if ag_state["last_tool_count"] >= 5:
                    ag_state["repeated_tool"] = True
                    ag_state["force_finalize"] = True
            else:
                ag_state["last_tool"] = last_tool_key
                ag_state["last_tool_count"] = 1

        _EDIT_WORDS = ("change", "fix", "update", "redesign", "rewrite", "modify", "improve", "replace", "edit", "make it", "add", "remove", "delete", "rename", "move", "convert")
        latest_lower = ""
        if isinstance(input_data, list):
            for item in reversed(input_data):
                if item.get("type") == "message" and item.get("role") == "user":
                    c = item.get("content", "")
                    if isinstance(c, str): latest_lower = c.lower()
                    elif isinstance(c, list): latest_lower = " ".join(p.get("text", p.get("input_text", "")) for p in c if isinstance(p, dict)).lower()
                    break

        if ag_state["force_finalize"]:
            return handler._send_ag_finalize(
                "Loop detected. The proxy is forcing a stop because the model repeatedly "
                "called tools without making progress. Try a more specific or smaller request.",
                stream=body.get("stream", False) if isinstance(body, dict) else False)
        elif latest_lower and any(w in latest_lower for w in _EDIT_WORDS) and not ag_state["nudge_injected"]:
            contents.append({"role": "user", "parts": [{"text": "!!! ABSOLUTELY NO PLANNING - EMIT THE TOOL CALL NOW !!! IMPORTANT: The user is requesting a modification to existing files. You MUST use tools (exec_command, read_files, write, etc.) to make the changes RIGHT NOW. Do NOT just describe what to do — actually CALL THE TOOLS IN THIS RESPONSE. IMMEDIATELY INSPECT THE FILE OR LIST FILES USING exec_command TOOL CALL."}]})
            ag_state["nudge_injected"] = True
            print(f"[antigravity] edit-intent detected; injected tool-use nudge (first time for this request)", file=sys.stderr)
        else:
            if ag_state["nudge_injected"]:
                print(f"[antigravity] edit-intent nudge already injected, skipping", file=sys.stderr)

        if latest_user and not ag_state["latest_user_appended"] and not ag_state["force_finalize"]:
            latest_norm = " ".join(latest_user.strip().split())[:160]
            final_text = ""
            if contents:
                last = contents[-1]
                if last.get("role") == "user":
                    final_text = " ".join(json.dumps(last.get("parts", []), ensure_ascii=False).split())
            if latest_norm[:120] not in final_text:
                print(f"[antigravity] latest user instruction was not final turn; appending (first time for this request)", file=sys.stderr)
                contents.append({"role": "user", "parts": [{"text": latest_user}]})
                ag_state["latest_user_appended"] = True
            else:
                print(f"[antigravity] latest user instruction is final turn", file=sys.stderr)
        else:
            if ag_state["latest_user_appended"]:
                print(f"[antigravity] latest user instruction already appended, skipping", file=sys.stderr)

        print(f"[antigravity-loop] latest_user_hash={latest_user_hash}", file=sys.stderr)
        print(f"[antigravity-loop] tool_calls_for_request={ag_state['tool_calls_for_request']}", file=sys.stderr)
        print(f"[antigravity-loop] repeated_tool={ag_state['repeated_tool']}", file=sys.stderr)
        print(f"[antigravity-loop] nudge_injected={ag_state['nudge_injected']}", file=sys.stderr)
        print(f"[antigravity-loop] force_finalize={ag_state['force_finalize']}", file=sys.stderr)
        print(f"[{handler._session_id}] [antigravity-debug] input_items={len(input_data) if isinstance(input_data, list) else 1} contents={len(contents)} latest={latest_user[:80]!r}", file=sys.stderr)
        if contents:
            last_c = contents[-1]
            print(f"[{handler._session_id}] [antigravity-debug] final_role={last_c.get('role')} preview={json.dumps(last_c.get('parts', []), ensure_ascii=False)[:200]}", file=sys.stderr)

    request_body = {"contents": contents}
    if system_parts:
        request_body["systemInstruction"] = {"parts": system_parts}
    if gen_config:
        request_body["generationConfig"] = gen_config
    _budget_exceeded_oa = ag_state.get("total_tool_calls", 0) > _mp_oa.get("max_tool_calls", 150)
    if gemini_tools and not _budget_exceeded_oa and not ag_state.get("force_finalize"):
        request_body["tools"] = gemini_tools
    elif _budget_exceeded_oa or ag_state.get("force_finalize"):
        print(f"[antigravity-budget] TOOLS STRIPPED from OA request (budget exceeded or force_finalize)", file=sys.stderr)

    if OAUTH_PROVIDER == "google-antigravity" and _is_claude_model and "tools" in request_body:
        request_body["toolConfig"] = {"functionCallingConfig": {"mode": "VALIDATED"}}
        if _is_claude_thinking:
            print(f"[antigravity-claude] applied VALIDATED toolConfig for thinking model", file=sys.stderr)

    wrapped = {
        "project": project_id,
        "model": model,
        "request": request_body,
    }
    if OAUTH_PROVIDER == "google-antigravity":
        wrapped["requestType"] = "agent"
        wrapped["userAgent"] = "antigravity"
        wrapped["requestId"] = f"agent-{uuid.uuid4().hex[:12]}"
        wrapped["request"]["sessionId"] = f"{uuid.uuid4().hex}{int(time.time()*1000)}"

    _allow_staging = os.environ.get("ALLOW_ANTIGRAVITY_STAGING", "0") == "1"
    if OAUTH_PROVIDER == "google-antigravity":
        _antigravity_endpoints = [
            "https://cloudcode-pa.googleapis.com",
            "https://daily-cloudcode-pa.googleapis.com",
        ]
        if _allow_staging:
            _antigravity_endpoints.extend([
                "https://daily-cloudcode-pa.sandbox.googleapis.com",
                "https://autopush-cloudcode-pa.sandbox.googleapis.com",
            ])
        endpoints = _antigravity_endpoints
    else:
        endpoints = ["https://cloudcode-pa.googleapis.com"]
    action = "streamGenerateContent" if stream else "generateContent"
    url_suffix = f"v1internal:{action}?alt=sse" if stream else f"v1internal:{action}"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    if OAUTH_PROVIDER == "google-antigravity":
        version = _ensure_antigravity_version()
        import platform as _plat
        _os_name = _plat.system().lower()
        _os_arch = _plat.machine().lower().replace("x86_64", "x64").replace("aarch64", "arm64")
        headers["User-Agent"] = f"antigravity/{version} {_os_name}/{_os_arch}"
        headers["X-Client-Name"] = "antigravity"
        headers["X-Client-Version"] = _ensure_antigravity_client_version()
        headers["x-goog-api-client"] = "gl-node/18.18.2 fire/0.8.6 grpc/1.10.x"
        if "request" in wrapped and "sessionId" in wrapped["request"]:
            headers["X-Machine-Session-Id"] = wrapped["request"]["sessionId"]
    else:
        headers["User-Agent"] = "google-api-nodejs-client/9.15.1"
        headers["X-Goog-Api-Client"] = "gl-node/22.17.0"
        headers["Client-Metadata"] = "ideType=IDE_UNSPECIFIED,platform=PLATFORM_UNSPECIFIED,pluginType=GEMINI"
    body_b = json.dumps(wrapped).encode()
    n_contents = len(contents)
    has_tools = bool(gemini_tools)
    print(f"[{handler._session_id}] model={model} stream={stream} items={len(input_data) if isinstance(input_data, list) else 1} project={project_id} contents={n_contents} tools={has_tools}", file=sys.stderr)
    if n_contents > 10 and os.environ.get("CODEX_LAUNCHER_DEBUG"):
        debug_path = os.path.join(_LOG_DIR, f"gemini-long-ctx-{handler._session_id}.json")
        try:
            with open(debug_path, "w", encoding="utf-8") as dbg:
                json.dump({"contents_count": n_contents, "contents_roles": [c.get("role") for c in contents], "has_tools": has_tools, "model": model, "wrapped_size": len(body_b)}, dbg, indent=2)
        except Exception:
            pass

    if OAUTH_PROVIDER == "google-antigravity":
        print(f"[antigravity-endpoint] endpoints={[e.replace('https://','') for e in endpoints]} project={project_id}", file=sys.stderr)

    upstream = None
    chosen_ep = None
    global _antigravity_preferred_endpoint

    with _antigravity_endpoint_lock:
        _pref = _antigravity_preferred_endpoint

    if _pref and _pref in endpoints:
        ordered = [_pref] + [e for e in endpoints if e != _pref]
    else:
        ordered = list(endpoints)

    for ep in ordered:
        target = f"{ep}/{url_suffix}"
        req = urllib.request.Request(target, data=body_b, headers=headers)
        try:
            upstream = urllib.request.urlopen(req, timeout=_upstream_timeout(body, stream))
            chosen_ep = ep
            with _antigravity_endpoint_lock:
                _antigravity_preferred_endpoint = ep
            if ep != _pref:
                print(f"[{handler._session_id}] fallback OK: {ep.replace('https://','')}", file=sys.stderr)
            break
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            err_class = _classify_antigravity_error(e.code, err_body)
            print(f"[{handler._session_id}] {ep.replace('https://','')} {e.code} class={err_class}", file=sys.stderr)
            if e.code == 400 and OAUTH_PROVIDER.startswith("google"):
                if os.environ.get("CODEX_LAUNCHER_DEBUG"):
                    try:
                        debug_path = os.path.join(_LOG_DIR, "gemini-last-400-request.json")
                        with open(debug_path, "w", encoding="utf-8") as dbg:
                            json.dump({"endpoint": ep, "model": model, "wrapped": wrapped, "error": err_body}, dbg, indent=2)
                        print(f"[{handler._session_id}] saved 400 debug request to {debug_path}", file=sys.stderr)
                    except Exception:
                        pass
                return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            if err_class == "auth_permanent":
                return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            if err_class == "auth_transient":
                print(f"[{handler._session_id}] {ep.replace('https://','')} 401 transient, force-refreshing token and retrying", file=sys.stderr)
                try:
                    _force_refresh_google_token()
                    access_token = _refresh_oauth_token()
                    headers["Authorization"] = f"Bearer {access_token}"
                    new_body_b = json.dumps(wrapped).encode()
                    retry_req = urllib.request.Request(target, data=new_body_b, headers=headers)
                    upstream = urllib.request.urlopen(retry_req, timeout=_upstream_timeout(body, stream))
                    chosen_ep = ep
                    with _antigravity_endpoint_lock:
                        _antigravity_preferred_endpoint = ep
                    print(f"[{handler._session_id}] 401 retry succeeded after token refresh", file=sys.stderr)
                    break
                except Exception as retry_e:
                    print(f"[{handler._session_id}] 401 retry also failed: {retry_e}", file=sys.stderr)
                    return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            if err_class in ("quota_exhausted", "rate_limited"):
                reset_s = _parse_rate_limit_reset(err_body)
                if ep == ordered[-1]:
                    pool = _google_antigravity_pool if OAUTH_PROVIDER == "google-antigravity" else _google_cli_pool
                    _, acct = _get_google_account(OAUTH_PROVIDER)
                    if acct:
                        cooldown = reset_s if reset_s and reset_s > 10 else 60
                        pool.mark_rate_limited(acct, cooldown)
                        print(f"[{handler._session_id}] quota reset in ~{reset_s}s, cooldown={cooldown}s", file=sys.stderr)
                    return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
                print(f"[{handler._session_id}] {ep.replace('https://','')} 429, trying next", file=sys.stderr)
                with _antigravity_endpoint_lock:
                    _antigravity_preferred_endpoint = None
                continue
            if err_class in ("service_disabled", "forbidden", "account_banned", "validation_required"):
                if ep == ordered[-1]:
                    return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
                continue
            if ep == ordered[-1]:
                return handler.send_json(e.code, {"error": {"type": "upstream_error", "message": _sanitize_err_body(err_body)}})
            continue
        except Exception as e:
            print(f"[{handler._session_id}] {ep.replace('https://','')} conn failed: {e}", file=sys.stderr)
            if ep == ordered[-1]:
                return handler.send_json(502, {"error": {"type": "proxy_error", "message": str(e)}})
            continue

    if upstream is None:
        return handler.send_json(502, {"error": {"type": "proxy_error", "message": "All endpoints failed"}})

    if stream:
        forward_gemini_sse(handler, upstream, model, body, input_data, tracker)
    else:
        forward_gemini_json(handler, upstream, model, body, input_data)


def forward_gemini_sse(handler, upstream, model, body, input_data, tracker=None):
    resp_id = f"resp-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()

    full_text = ""
    output_items = []
    current_tool_calls = {}
    message_started = False
    message_id = f"msg-{uuid.uuid4().hex[:24]}"

    def flush_event(event_type, data):
        handler.wfile.write(f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode())
        handler.wfile.flush()

    flush_event("response.created", {"type": "response.created", "response": {"id": resp_id, "object": "response", "model": model, "status": "in_progress", "created": created, "output": []}})
    flush_event("response.in_progress", {"type": "response.in_progress", "response": {"id": resp_id}})

    buf = ""
    stream_finished = False
    last_finish = ""
    _last_stream_usage = {}
    try:
        for raw_line in _stream_with_idle_timeout(upstream, _idle_timeout_for_model(model)):
            if tracker and tracker.cancelled.is_set():
                print("[gemini-oauth] stream cancelled", file=sys.stderr)
                break
            if stream_finished:
                break
            line = raw_line.decode(errors="replace")
            if line.startswith("data: "):
                buf += line[6:]
                continue
            if not line.strip() and buf:
                try:
                    chunk = json.loads(buf)
                    usage = chunk.get("response", chunk).get("usageMetadata", {})
                    if usage:
                        _last_stream_usage = {
                            "prompt_tokens": usage.get("promptTokenCount", 0),
                            "completion_tokens": usage.get("candidatesTokenCount", 0),
                            "total_tokens": usage.get("totalTokenCount", 0)
                        }
                except Exception:
                    buf = ""
                    continue
                buf = ""

                candidates = chunk.get("response", chunk).get("candidates", [])
                if not candidates:
                    if chunk.get("error"):
                        print(f"[{handler._session_id}] stream error chunk: {str(chunk.get('error'))[:300]}", file=sys.stderr)
                    continue
                if candidates[0].get("finishReason") and not candidates[0].get("content", {}).get("parts"):
                    print(f"[{handler._session_id}] finish without parts: {candidates[0].get('finishReason')}", file=sys.stderr)
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    sig = _extract_gemini_sig(part)
                    if sig:
                        if part.get("functionCall"):
                            fc_id = part["functionCall"].get("id") or part["functionCall"].get("name")
                            fc_name = part["functionCall"].get("name")
                            if fc_id:
                                _gemini_store_sig(f"fc:{fc_id}", sig)
                            if fc_name:
                                _gemini_store_sig(f"fc:{fc_name}", sig)
                        _gemini_store_sig(f"turn:{resp_id}", sig)
                    if part.get("thought"):
                        sig_from_thought = _extract_gemini_sig(part)
                        if sig_from_thought:
                            _gemini_store_sig(f"turn:{resp_id}", sig_from_thought)
                        continue
                    if "text" in part and not part.get("functionCall"):
                        text_delta = part["text"]
                        if not text_delta:
                            continue
                        full_text += text_delta
                        if not message_started:
                            flush_event("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": message_id, "role": "assistant", "content": []}})
                            flush_event("response.content_part.added", {"type": "response.content_part.added", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": ""}})
                            output_items.append({"text": True})
                            message_started = True
                        flush_event("response.output_text.delta", {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": text_delta})
                    elif part.get("functionCall"):
                        fc = part["functionCall"]
                        call_id = f"call_{uuid.uuid4().hex[:24]}"
                        args_str = json.dumps(fc.get("args", fc.get("arguments", {})))
                        output_index = len(output_items)
                        flush_event("response.output_item.added", {"type": "response.output_item.added", "output_index": output_index, "item": {"type": "function_call", "id": call_id, "call_id": call_id, "name": fc.get("name", ""), "arguments": ""}})
                        flush_event("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "output_index": output_index, "item_id": call_id, "delta": args_str})
                        flush_event("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "output_index": output_index, "item_id": call_id, "arguments": args_str})
                        current_tool_calls[call_id] = fc
                        output_items.append({"tool": True})
                last_finish = candidates[0].get("finishReason", "")
                if last_finish:
                    part_kinds = []
                    for p in parts:
                        if "text" in p: part_kinds.append("text")
                        if "functionCall" in p: part_kinds.append("functionCall")
                        if _extract_gemini_sig(p): part_kinds.append("thoughtSignature")
                    print(f"[{handler._session_id}] [antigravity] finish={last_finish} parts={part_kinds} tool_calls={len(current_tool_calls)}", file=sys.stderr)
                    if OAUTH_PROVIDER == "google-antigravity" and last_finish == "MAX_TOKENS" and full_text and not current_tool_calls:
                        print(f"[{handler._session_id}] MAX_TOKENS hit ({len(full_text)} chars), auto-continuing...", file=sys.stderr)
                        break
                    stream_finished = True
                    break
            else:
                if line.strip():
                    buf += line
    except TimeoutError as te:
        print(f"[{handler._session_id}] [antigravity-v2] STREAM TIMEOUT: {te}", file=sys.stderr)
        handler._request_failed = True
        _log_resp(resp_id, "stream_timeout", [{"type": "error", "code": "stream_timeout", "message": str(te)}])
        try:
            flush_event("response.failed", {"type": "response.failed", "response": {"id": resp_id, "object": "response", "status": "failed", "error": {"type": "stream_timeout", "message": str(te)[:200]}}})
        except Exception:
            pass
        handler.close_connection = True
        return
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        print(f"[{handler._session_id}] [antigravity-v2] client disconnected during stream", file=sys.stderr)
        _log_resp(resp_id, "client_disconnect", [])
        return
    except Exception as e:
        print(f"[{handler._session_id}] [antigravity-v2] stream error: {e}", file=sys.stderr)
        handler._request_failed = True
        handler.close_connection = True
        return

    if OAUTH_PROVIDER.startswith("google") and full_text and not current_tool_calls and last_finish == "MAX_TOKENS" and not stream_finished:
        # Get parent context variables
        from proxy.server import _auto_continue_gemini as _server_auto_continue
        # We try to use the defined method
        result = _auto_continue_gemini(handler, flush_event, message_id, model, gen_config, gemini_tools, system_parts, project_id, headers, endpoints, url_suffix, full_text, output_items, message_started)
        if result:
            full_text = result
            for item in output_items:
                if isinstance(item, dict) and item.get("tool") and "fc" in item and "call_id" in item:
                    current_tool_calls[item["call_id"]] = item["fc"]

    out = []
    if not full_text and not current_tool_calls:
        print("[gemini-oauth] WARNING: completed with empty output", file=sys.stderr)
    if full_text:
        out.append({"type": "message", "id": message_id, "role": "assistant", "content": [{"type": "output_text", "text": full_text}]})
    tool_outputs = []
    for cid, fc in current_tool_calls.items():
        tool_outputs.append({"type": "function_call", "id": cid, "call_id": cid, "name": fc.get("name", ""), "arguments": json.dumps(fc.get("args", fc.get("arguments", {})))})
    out.extend(tool_outputs)

    final_resp = {"id": resp_id, "object": "response", "model": model, "status": "completed", "created": created, "output": out}
    if full_text:
        flush_event("response.output_text.done", {"type": "response.output_text.done", "output_index": 0, "content_index": 0, "text": full_text})
        flush_event("response.content_part.done", {"type": "response.content_part.done", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": full_text}})
        flush_event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": out[0]})
    for idx, item in enumerate(tool_outputs, start=(1 if full_text else 0)):
        flush_event("response.output_item.done", {"type": "response.output_item.done", "output_index": idx, "item": item})
    flush_event("response.completed", {"type": "response.completed", "response": final_resp})
    handler.close_connection = True

    provider = TARGET_URL.split("//")[-1].split("/")[0]
    success = not handler._request_failed
    _record_usage_with_tokens(provider, model, success, time.time() - created, _last_stream_usage, input_items=input_data, output_items=out)

    from proxy.server import _response_store_lock, _response_store, _MAX_STORED
    with _response_store_lock:
        _response_store[resp_id] = final_resp
        while len(_response_store) > _MAX_STORED:
            _response_store.popitem(last=False)


def forward_gemini_json(handler, upstream, model, body, input_data):
    data = json.loads(upstream.read().decode())
    resp_id = f"resp-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    out = []
    full_text = ""
    candidates = data.get("response", data).get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = []
        for part in parts:
            if part.get("thought"):
                continue
            if "text" in part and not part.get("functionCall"):
                text_parts.append(part["text"])
            elif part.get("functionCall"):
                fc = part["functionCall"]
                call_id = f"call_{uuid.uuid4().hex[:24]}"
                out.append({"type": "function_call", "id": call_id, "call_id": call_id, "name": fc.get("name", ""), "arguments": json.dumps(fc.get("args", fc.get("arguments", {})))})
        if text_parts:
            full_text = "".join(text_parts)
            out.insert(0, {"type": "message", "id": f"msg-{uuid.uuid4().hex[:24]}", "role": "assistant", "content": [{"type": "output_text", "text": full_text}]})
    resp = {"id": resp_id, "object": "response", "model": model, "status": "completed", "created": created, "output": out}
    
    from proxy.server import _response_store_lock, _response_store, _MAX_STORED
    with _response_store_lock:
        _response_store[resp_id] = resp
        while len(_response_store) > _MAX_STORED:
            _response_store.popitem(last=False)
    handler.send_json(200, resp)

    provider = TARGET_URL.split("//")[-1].split("/")[0]
    usage = data.get("response", data).get("usageMetadata", {})
    _usage_dict = {}
    if usage:
        _usage_dict = {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0)
        }
    _record_usage_with_tokens(provider, model, True, time.time() - created, _usage_dict, input_items=input_data, output_items=out)
