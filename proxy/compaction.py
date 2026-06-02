"""Context compaction, truncation, and prompt enhancement."""
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

from proxy.config import *
from proxy.shared_utils import uid, emit


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


_MAX_INPUT_ITEMS = 30
_MAX_TOOL_OUTPUT_CHARS = 8000
_COMPACT_KEEP_RECENT = 10

_CROF_ADAPTIVE = {
    "fail_history": [],
    "model_limits": {},
    "global_item_limit": 80,
    "min_keep_recent": 6,
}
_crof_adaptive_lock = threading.Lock()

_model_max_tokens = {}
_model_max_tokens_lock = threading.Lock()

def _estimate_item_tokens(item):
    if not isinstance(item, dict):
        return 4
    t = item.get("type", "")
    if t == "message":
        content = item.get("content", "")
        if isinstance(content, str):
            return max(4, len(content) // 4)
        elif isinstance(content, list):
            total = 4
            for part in content:
                pt = part.get("type", "")
                if pt in ("input_text", "output_text"):
                    total += max(4, len(part.get("text", "")) // 4)
                elif pt == "input_image":
                    total += 800
                elif pt in ("function_call",):
                    total += max(20, len(part.get("arguments", "{}")) // 2)
                elif pt == "function_call_output":
                    total += max(8, len(part.get("output", "")) // 4)
            return total
    elif t in ("function_call_output",):
        return max(8, len(item.get("output", "")) // 4)
    elif t == "function_call":
        return max(20, len(item.get("arguments", "{}")) // 2)
    return 4

def _estimate_input_tokens(input_data):
    if not isinstance(input_data, list):
        return 0
    return sum(_estimate_item_tokens(i) for i in input_data)

def _get_model_max_tokens(model):
    with _model_max_tokens_lock:
        return _model_max_tokens.get(model)

def _set_model_max_tokens(model, tokens):
    if model and tokens:
        with _model_max_tokens_lock:
            existing = _model_max_tokens.get(model)
            if existing is None or tokens < existing:
                _model_max_tokens[model] = tokens
                print(f"[ctx-limit] learned {model} max ~{tokens} tokens", file=sys.stderr)

_BGP_STATS_PATH = os.path.join(_LOG_DIR, "bgp-route-stats.json")
_bgp_stats_lock = threading.Lock()

def _route_key(route):
    return f"{route.get('name', '')}::{route.get('target_url', '')}::{route.get('model', '')}"

def _load_bgp_stats():
    try:
        with open(_BGP_STATS_PATH) as _f:
            return json.load(_f)
    except Exception:
        return {}

def _save_bgp_stats(stats):
    tmp = _BGP_STATS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    os.replace(tmp, _BGP_STATS_PATH)

def _score_route(route, stats):
    key = _route_key(route)
    rs = stats.get(key, {})
    now = time.time()
    if float(rs.get("open_until_ts", 0)) > now:
        return 1_000_000
    priority = int(route.get("priority", 99))
    ewma = float(rs.get("ewma_latency_s", 0))
    failures = int(rs.get("consecutive_failures", 0))
    score = priority + min(ewma * 5, 50) + failures * 20
    if float(rs.get("rate_limited_until", 0)) > now:
        score += 500
    return score

def _update_route_stats(route, success, duration_s, http_code=None, error_type=None):
    with _bgp_stats_lock:
        stats = _load_bgp_stats()
        key = _route_key(route)
        rs = stats.setdefault(key, {
            "ewma_latency_s": duration_s, "consecutive_failures": 0,
            "last_success": None, "last_failure": None,
            "open_until_ts": 0, "rate_limited_until": 0, "last_error": None,
        })
        alpha = 0.25
        rs["ewma_latency_s"] = alpha * duration_s + (1 - alpha) * float(rs.get("ewma_latency_s", duration_s))
        if success:
            rs["consecutive_failures"] = 0
            rs["last_success"] = time.time()
        else:
            rs["consecutive_failures"] = int(rs.get("consecutive_failures", 0)) + 1
            rs["last_failure"] = time.time()
            rs["last_error"] = error_type or (f"http_{http_code}" if http_code else "unknown")
            if http_code == 429:
                rs["rate_limited_until"] = time.time() + 120
            if rs["consecutive_failures"] >= 3:
                rs["open_until_ts"] = time.time() + 60
                rs["consecutive_failures"] = 0
        _save_bgp_stats(stats)

def _sorted_bgp_routes():
    with _bgp_stats_lock:
        stats = _load_bgp_stats()
    return sorted(BGP_ROUTES, key=lambda r: _score_route(r, stats))

def _crof_record(model, n_items, success):
    if "crof.ai" not in TARGET_URL:
        return
    if not isinstance(n_items, int) or n_items < 1:
        return
    entry = {"model": model, "items": n_items, "ok": success}
    with _crof_adaptive_lock:
        hist = _CROF_ADAPTIVE["fail_history"]
        hist.append(entry)
        if len(hist) > 200:
            _CROF_ADAPTIVE["fail_history"] = hist[-100:]

        ml = _CROF_ADAPTIVE["model_limits"].setdefault(model, {"ok_max": 30, "fail_min": 0, "limit": 30})
        if success and n_items > ml["ok_max"]:
            ml["ok_max"] = n_items
        if not success and (ml["fail_min"] == 0 or n_items < ml["fail_min"]):
            ml["fail_min"] = n_items

        if ml["fail_min"] > 0 and ml["ok_max"] >= ml["fail_min"]:
            ml["limit"] = ml["fail_min"] - 1
        elif ml["fail_min"] > 0:
            ml["limit"] = max(ml["fail_min"] - 2, _CROF_ADAPTIVE["min_keep_recent"] + 2)

        global_limit = 30
        for m, v in _CROF_ADAPTIVE["model_limits"].items():
            if v.get("limit", 30) < global_limit:
                global_limit = v["limit"]
        _CROF_ADAPTIVE["global_item_limit"] = global_limit

    print(f"[crof-adaptive] model={model} items={n_items} {'OK' if success else 'FAIL'} -> limit={ml.get('limit',30)} global={global_limit}", file=sys.stderr)

def _crof_item_limit(model):
    with _crof_adaptive_lock:
        ml = _CROF_ADAPTIVE["model_limits"].get(model, {})
        per_model = ml.get("limit", 30)
        return min(per_model, _CROF_ADAPTIVE["global_item_limit"])

def _crof_compact_for_retry(input_data, model, aggression=0):
    policy = provider_policy()
    if "crof.ai" not in TARGET_URL:
        if not policy.get("compaction"):
            return input_data
    
    limit = _crof_item_limit(model)
    if policy.get("max_input_items"):
        limit = min(limit, policy["max_input_items"])

    if not isinstance(input_data, list) or len(input_data) < 2:
        return input_data

    max_tok = _get_model_max_tokens(model)
    if not max_tok:
        max_tok = int(policy.get("context_size", policy.get("max_tokens", _context_limit_for_model(model))))

    est = _estimate_input_tokens(input_data)
    over_item_limit = len(input_data) > limit
    over_token_limit = max_tok and est >= max_tok * 0.9
    if aggression >= 1:
        over_token_limit = True

    if not over_item_limit and not over_token_limit:
        return input_data

    keep = max(_CROF_ADAPTIVE["min_keep_recent"], limit // 3)
    if over_token_limit:
        ratio = est / max_tok
        if aggression >= 1 or ratio > 1.5:
            keep = max(2, _CROF_ADAPTIVE["min_keep_recent"] // 2)
        elif ratio > 1.2:
            keep = max(3, keep // 2)
        print(f"[ctx-limit] model={model} est={est}tok max={max_tok}tok ratio={ratio:.2f} -> keep={keep}", file=sys.stderr)
    elif over_item_limit:
        keep = max(keep, 6)
    head_end = 0
    for i, item in enumerate(input_data):
        t = item.get("type")
        if t == "message" and item.get("role") in ("developer", "system"):
            head_end = i + 1
        elif t == "message" and item.get("role") == "user" and head_end == i:
            head_end = i + 1
        else:
            break

    head = input_data[:head_end]
    tail_start = max(head_end, len(input_data) - keep)
    while tail_start > head_end:
        t = input_data[tail_start].get("type")
        r = input_data[tail_start].get("role", "")
        if t in ("function_call_output", "function_call"):
            tail_start -= 1
        elif t == "message" and r == "assistant":
            tail_start -= 1
        else:
            break
    tail = input_data[tail_start:]
    body = input_data[head_end:tail_start]

    if not body:
        return head + tail

    summary_lines = [f"[Auto-compacted: {len(body)} turns removed (adaptive limit={limit})]"]
    for item in body[-3:]:
        summary_lines.append(_item_summary(item, max_len=120))

    summary_msg = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "\n".join(summary_lines)}]}
    print(f"[crof-adaptive] RETRY compact: {len(input_data)} -> {len(head)+1+len(tail)} (limit={limit}, keep={len(tail)}, agg={aggression})", file=sys.stderr)
    return head + [summary_msg] + tail

def _item_summary(item, max_len=200):
    t = item.get("type")
    if t == "message":
        role = item.get("role", "?")
        text = ""
        content = item.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for p in content:
                if isinstance(p, dict):
                    if p.get("type") in ("input_text", "output_text"):
                        text += p.get("text", "")
                elif isinstance(p, str):
                    text += p
        return f"[{role}] {text[:max_len]}"
    elif t == "function_call":
        name = item.get("name", "?")
        args = item.get("arguments", "{}")
        try:
            a = json.loads(args)
            cmd = a.get("cmd", a.get("command", ""))
            if cmd:
                return f"[tool call] {name}: {cmd[:max_len]}"
        except Exception:
            pass
        return f"[tool call] {name}({args[:max_len]})"
    elif t == "function_call_output":
        output = item.get("output", "")
        out_len = len(output) if isinstance(output, str) else len(str(output))
        exit_match = re.search(r'Process exited with code (\d+)', output if isinstance(output, str) else str(output))
        exit_code = exit_match.group(1) if exit_match else "?"
        tok_match = re.search(r'Original token count: (\d+)', output if isinstance(output, str) else str(output))
        tok_count = tok_match.group(1) if tok_match else ""
        tok_info = f" ({tok_count} tokens)" if tok_count else ""
        return f"[tool result] exit={exit_code}{tok_info} {out_len} chars"
    return f"[{t}]"

def _extract_files(items):
    files = set()
    for item in items:
        if item.get("type") == "function_call":
            try:
                a = json.loads(item.get("arguments", "{}"))
                cmd = a.get("cmd", a.get("command", ""))
                for prefix in (">", ">>", " > ", " >> "):
                    for part in cmd.split(prefix)[1:]:
                        f = part.strip().split()[0].strip("'\"")
                        if f and not f.startswith("-") and "/" in f:
                            files.add(f)
            except Exception:
                pass
    return files

def _rtk_compress_tool_output(output_str):
    if not isinstance(output_str, str) or not output_str:
        return output_str
        
    lines = output_str.splitlines()
    compressed_lines = []
    
    # Heuristic 1: Compress git diffs
    is_diff = output_str.startswith("diff --git") or "@@ -" in output_str
    
    if is_diff:
        unchanged_count = 0
        held_lines = []
        for line in lines:
            if line.startswith("+") or line.startswith("-") or line.startswith("@@") or line.startswith("diff --git") or line.startswith("---") or line.startswith("+++"):
                if unchanged_count > 2:
                    compressed_lines.append(f"... [omitted {unchanged_count} unchanged lines] ...")
                else:
                    compressed_lines.extend(held_lines)
                held_lines = []
                unchanged_count = 0
                compressed_lines.append(line)
            else:
                held_lines.append(line)
                unchanged_count += 1
        if held_lines:
            if unchanged_count > 2:
                compressed_lines.append(f"... [omitted {unchanged_count} unchanged lines] ...")
            else:
                compressed_lines.extend(held_lines)
        return "\n".join(compressed_lines)
        
    # Heuristic 2: Compress directory listings / file trees
    is_tree_or_list = any("node_modules" in line or ".git/" in line or "dist/" in line for line in lines[:50])
    if is_tree_or_list:
        ignored_patterns = re.compile(r'(\.git/|node_modules/|__pycache__/|\.venv/|\.pyc$|\.o$|dist/|build/|\.next/)')
        omitted = 0
        for line in lines:
            if ignored_patterns.search(line):
                omitted += 1
            else:
                compressed_lines.append(line)
        if omitted > 0:
            compressed_lines.append(f"... [omitted {omitted} dependency/build files from tree] ...")
        return "\n".join(compressed_lines)
        
    # Heuristic 3: Compress long logs or multiple repeating lines
    last_line = None
    repeat_count = 0
    for line in lines:
        if line == last_line and line.strip():
            repeat_count += 1
            if repeat_count <= 1:
                compressed_lines.append(line)
        else:
            if repeat_count > 1:
                compressed_lines.append(f"... [repeated {repeat_count - 1} times] ...")
            repeat_count = 0
            compressed_lines.append(line)
            last_line = line
    if repeat_count > 1:
        compressed_lines.append(f"... [repeated {repeat_count - 1} times] ...")
        
    return "\n".join(compressed_lines)

def _compact_input(input_data):
    if isinstance(input_data, str):
        return input_data
        
    if RTK_COMPRESSION and isinstance(input_data, list):
        compressed_data = []
        for item in input_data:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                o = item.get("output", "")
                if isinstance(o, str) and o:
                    compressed = _rtk_compress_tool_output(o)
                    if len(compressed) < len(o):
                        print(f"[rtk] compressed tool output length: {len(o)} -> {len(compressed)} (-{((len(o)-len(compressed))/len(o))*100:.1f}%)", file=sys.stderr)
                        item = dict(item)
                        item["output"] = compressed
            compressed_data.append(item)
        input_data = compressed_data

    # Skip compaction entirely — just do optional tool output truncation
    if not AUTO_COMPACT or not isinstance(input_data, list) or len(input_data) <= _MAX_INPUT_ITEMS:
        out = []
        for item in input_data:
            if TOOL_OUTPUT_TRUNCATION and isinstance(item, dict) and item.get("type") == "function_call_output":
                o = item.get("output", "")
                if len(o) > _MAX_TOOL_OUTPUT_CHARS:
                    item = dict(item)
                    item["output"] = o[:_MAX_TOOL_OUTPUT_CHARS] + f"\n... [truncated {len(o) - _MAX_TOOL_OUTPUT_CHARS} chars]"
                    print(f"[compact] tool output truncated {len(o)} -> {_MAX_TOOL_OUTPUT_CHARS}", file=sys.stderr)
            out.append(item)
        return out

    head_end = 0
    for i, item in enumerate(input_data):
        t = item.get("type")
        if t == "message" and item.get("role") in ("developer", "system"):
            head_end = i + 1
        elif t == "message" and item.get("role") == "user" and head_end == i:
            head_end = i + 1
        else:
            break

    head = input_data[:head_end]
    tail_start = len(input_data) - _COMPACT_KEEP_RECENT
    while tail_start > head_end:
        t = input_data[tail_start].get("type")
        r = input_data[tail_start].get("role", "")
        if t == "function_call_output":
            tail_start -= 1
        elif t == "function_call":
            tail_start -= 1
        elif t == "message" and r == "assistant":
            tail_start -= 1
        else:
            break
    tail = input_data[tail_start:]
    body = input_data[head_end:tail_start]

    if not body:
        return head + tail

    for item in tail:
        if TOOL_OUTPUT_TRUNCATION and isinstance(item, dict) and item.get("type") == "function_call_output":
            o = item.get("output", "")
            if len(o) > _MAX_TOOL_OUTPUT_CHARS:
                item["output"] = o[:_MAX_TOOL_OUTPUT_CHARS] + f"\n... [truncated {len(o) - _MAX_TOOL_OUTPUT_CHARS} chars]"

    user_queries = []
    for item in body:
        if item.get("type") == "message" and item.get("role") == "user":
            for p in item.get("content", []):
                if p.get("type") == "input_text":
                    user_queries.append(p.get("text", "")[:300])
    assistant_msgs = []
    for item in body:
        if item.get("type") == "message" and item.get("role") == "assistant":
            for p in item.get("content", []):
                if p.get("type") == "output_text":
                    assistant_msgs.append(p.get("text", "")[:300])

    tool_summaries = []
    for item in body:
        if item.get("type") in ("function_call", "function_call_output"):
            tool_summaries.append(_item_summary(item, max_len=150))

    files = _extract_files(body)
    n_read_tools = sum(1 for it in body if it.get("type") == "function_call"
                       and any(k in it.get("arguments", "") for k in ["cat ", "head ", "tail ", "sed -n", "grep ", "less ", "more ", "python3 -c", ".read()"]))
    n_write_tools = sum(1 for it in body if it.get("type") == "function_call"
                        and any(k in it.get("arguments", "") for k in ["write(", ".write", " > ", " >> ", "sed -i", "patch "]))

    summary_lines = [f"[Auto-compacted: {len(body)} earlier turns summarized to preserve context]"]
    if user_queries:
        summary_lines.append(f"User requests: {'; '.join(user_queries[-3:])}")
    if assistant_msgs:
        summary_lines.append(f"Assistant responses: {'; '.join(assistant_msgs[-3:])}")
    if tool_summaries:
        summary_lines.append(f"Actions taken ({len(tool_summaries)} steps, {n_read_tools} reads, {n_write_tools} writes):")
        for ts in tool_summaries[-5:]:
            summary_lines.append(f"  {ts}")
    if files:
        summary_lines.append(f"Files touched: {', '.join(sorted(files)[-10:])}")
    if n_read_tools > 10 and n_write_tools == 0:
        summary_lines.append("⚠ You have already read these files extensively but made NO edits. You MUST write your changes NOW. Do NOT read any more files.")

    summary_text = "\n".join(summary_lines)
    summary_msg = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": summary_text}]
    }

    print(f"[compact] {len(input_data)} items -> {len(head) + 1 + len(tail)} (compacted {len(body)} old items into summary)", file=sys.stderr)
    return head + [summary_msg] + tail

# ═══════════════════════════════════════════════════════════════════
# Provider policies
# ═══════════════════════════════════════════════════════════════════

_PROVIDER_POLICIES = {
    "crof": {"reasoning_mode": "off", "max_tokens": 32768, "strip_reasoning": True,
             "tool_output_limit": 4000, "max_input_items": 18, "compaction": "aggressive",
             "synthetic_tool_results": True},
    "chats-llm": {"reasoning_mode": "off", "max_tokens": 32768, "strip_reasoning": True,
                  "tool_output_limit": 4000, "max_input_items": 20, "compaction": "aggressive"},
    "z.ai": {"reasoning_mode": "medium", "max_tokens": 65536, "strip_reasoning": True,
             "tool_output_limit": 8000, "max_input_items": 40, "compaction": "balanced"},
    "openrouter": {"reasoning_mode": "provider_default", "max_tokens": 32768, "strip_reasoning": True,
                   "tool_output_limit": 6000, "max_input_items": 35, "compaction": "balanced"},
    "openadapter": {"reasoning_mode": "off", "max_tokens": 32768, "strip_reasoning": True,
                    "tool_output_limit": 1000, "max_input_items": 10, "compaction": "aggressive",
                    "synthetic_tool_results": True},
    "cloudcode-pa": {"compaction": "conservative", "context_size": 1000000,
                     "tool_output_limit": 8000, "max_input_items": 200},
    "googleapis": {"compaction": "conservative", "context_size": 1000000,
                   "tool_output_limit": 8000, "max_input_items": 250},
    "groq": {"reasoning_mode": "off", "max_tokens": 32768, "strip_reasoning": True,
             "tool_output_limit": 4000, "max_input_items": 20, "compaction": "aggressive",
             "prompt_caching": "none"},
    "cerebras": {"reasoning_mode": "off", "max_tokens": 32768, "strip_reasoning": True,
                "tool_output_limit": 4000, "max_input_items": 20, "compaction": "aggressive",
                "prompt_caching": "none"},
    "xiaomimimo": {"reasoning_mode": "provider_default", "max_tokens": 65536, "strip_reasoning": True,
                   "tool_output_limit": 8000, "max_input_items": 40, "compaction": "balanced",
                   "prompt_caching": "none"},
}

_DEFAULT_PROVIDER_POLICY = {
    "compaction": "balanced", "context_size": 128000,
    "tool_output_limit": 6000, "max_input_items": 60,
    "prompt_caching": "auto",
}

def provider_policy(target_url=None, backend=None):
    host = urllib.parse.urlparse(target_url or TARGET_URL).netloc.lower()
    for key, policy in _PROVIDER_POLICIES.items():
        if key in host:
            return policy
    return dict(_DEFAULT_PROVIDER_POLICY)

# ═══════════════════════════════════════════════════════════════════
# Adaptive context compaction (model-aware)
# ═══════════════════════════════════════════════════════════════════

_MODEL_CONTEXT = {
    "gpt-4o": 128000, "gpt-4o-mini": 128000, "gpt-5": 128000,
    "claude-sonnet": 200000, "claude-haiku": 200000,
    "glm-5.1": 128000, "glm-5": 128000, "glm-4": 128000,
    "deepseek": 64000, "gemini-2.5-flash": 1000000, "gemini-2.5-pro": 2000000,
    "gemini-3-flash": 1000000, "gemini-3.5-flash-low": 1000000,
    "gemini-3.1-pro-low": 2000000,
    "gemini-3.5-flash": 1000000, "gemini-3.1-pro": 2000000,
    "Gemini 3.5 Flash": 1000000, "Gemini 3.1 Pro": 2000000,
    "Claude Sonnet 4.6": 200000, "Claude Opus 4.6": 200000,
    "GPT-OSS 120B": 128000,
    "claude-sonnet-4-6": 200000, "claude-opus-4-6-thinking": 200000,
    "gpt-oss-120b-medium": 128000,
    "mimo": 32768, "minimax": 32768, "kimi": 128000,
    "_default": 32768,
}

def _context_limit_for_model(model):
    if not model:
        return _MODEL_CONTEXT["_default"]
    ml = model.lower()
    for key, limit in _MODEL_CONTEXT.items():
        if key != "_default" and key in ml:
            return limit
    return _MODEL_CONTEXT["_default"]

def _estimate_tokens(obj):
    if obj is None:
        return 0
    if isinstance(obj, str):
        return max(1, len(obj) // 4)
    try:
        raw = json.dumps(obj, ensure_ascii=False)
    except Exception:
        raw = str(obj)
    return max(1, len(raw) // 4)

def _adaptive_compact(input_data, model, policy=None):
    policy = policy or {}
    context_size = int(policy.get("context_size", policy.get("max_tokens", _context_limit_for_model(model))))
    input_budget = int(context_size * 0.80)
    estimated = _estimate_tokens(input_data)
    if estimated <= input_budget:
        return input_data, False
    if not isinstance(input_data, list):
        return input_data, False
    reduction = max(0.15, input_budget / max(estimated, 1))
    target_items = max(int(len(input_data) * reduction), 6)
    if target_items >= len(input_data):
        return input_data, False
    head_end = 0
    for i, item in enumerate(input_data):
        t = item.get("type")
        if t == "message" and item.get("role") in ("developer", "system"):
            head_end = i + 1
        elif t == "message" and item.get("role") == "user" and head_end == i:
            head_end = i + 1
        else:
            break
    head = input_data[:head_end]
    keep = max(4, target_items // 3)
    tail_start = max(head_end, len(input_data) - keep)
    while tail_start > head_end:
        t = input_data[tail_start].get("type")
        if t in ("function_call_output", "function_call"):
            tail_start -= 1
        elif t == "message" and input_data[tail_start].get("role") == "assistant":
            tail_start -= 1
        else:
            break
    tail = input_data[tail_start:]
    body = input_data[head_end:tail_start]
    if not body:
        return head + tail, True
    summary_lines = [f"[Auto-compacted: {len(body)} turns removed (budget={input_budget}tok, model={model})]"]
    for item in body[-3:]:
        summary_lines.append(_item_summary(item, max_len=120))
    summary_msg = {"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "\n".join(summary_lines)}]}
    print(f"[adaptive-compact] model={model} est={estimated}tok budget={input_budget}tok "
          f"items {len(input_data)}->{len(head)+1+len(tail)}", file=sys.stderr)
    return head + [summary_msg] + tail, True

# ═══════════════════════════════════════════════════════════════════
# Prompt Enhancer
# ═══════════════════════════════════════════════════════════════════

_PROMPT_ENHANCER_SYSTEM = """You are a prompt enhancement assistant for a coding agent (Codex CLI).
Your job: rewrite the user's latest message to be clearer, more specific, and more actionable.
Rules:
- Preserve the user's EXACT intent — never change what they want done
- Add explicit action verbs and step-by-step clarity
- If the message is vague ("fix it", "make it better"), infer context from prior conversation summary and make it specific
- Keep the enhanced prompt concise — no longer than 2x the original
- If the original prompt is already clear and specific, return it unchanged
- Output ONLY the enhanced prompt text, nothing else
- Never add tasks the user didn't ask for"""

_PROMPT_ENHANCER_OFFLINE = """<prompt-enhancer>
<instructions>
You are a coding agent operating inside a context-compacted session. Follow these rules strictly:

1. ACTION CLARITY: Re-read the user's latest message. Identify every explicit and implicit action request. Execute ALL of them — do not skip any.

2. COMPACTED CONTEXT: Previous conversation was summarized. The summary preserves your task history but may lose details. If the user references earlier work ("fix that", "continue", "update it"), infer from the compacted summary what was done and what remains.

3. NO CLARIFICATION ASKING: Never ask "which file?" or "what exactly?" — infer from context. If truly ambiguous, make a reasonable assumption and proceed. The user can correct you.

4. DECISIVE EXECUTION: When the user says "fix", "update", "change", "add", "remove" — do it immediately in the relevant file(s). Do not describe what you would do — actually do it.

5. COMPLETE EDITS: When editing files, make the FULL change requested. Do not partially apply edits or leave placeholders.

6. PRESERVE WORKING STATE: Never break existing functionality. If changing code, keep all surrounding logic intact.

7. MULTI-STEP REQUESTS: If the user asks for multiple things, do ALL of them in sequence. Do not stop after the first one.
</instructions>
</prompt-enhancer>

"""

def _enhance_prompt_llm(text, compaction_summary=""):
    global PROMPT_ENHANCER_MODEL, PROMPT_ENHANCER_URL, PROMPT_ENHANCER_KEY
    if not PROMPT_ENHANCER_MODEL or not PROMPT_ENHANCER_URL:
        return text
    try:
        messages = [
            {"role": "system", "content": _PROMPT_ENHANCER_SYSTEM},
        ]
        if compaction_summary:
            messages.append({"role": "user", "content": f"Context from earlier conversation (compacted):\n{compaction_summary[:2000]}"})
        messages.append({"role": "user", "content": f"Enhance this prompt:\n{text}"})
        body = json.dumps({"model": PROMPT_ENHANCER_MODEL, "messages": messages, "max_tokens": 2000, "temperature": 0.3}).encode()
        headers = {"Content-Type": "application/json"}
        if PROMPT_ENHANCER_KEY:
            headers["Authorization"] = f"Bearer {PROMPT_ENHANCER_KEY}"
        req = urllib.request.Request(f"{PROMPT_ENHANCER_URL.rstrip('/')}/chat/completions", data=body, headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        enhanced = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if enhanced and len(enhanced) >= len(text) * 0.5:
            print(f"[prompt-enhancer] AI enhanced: {text[:80]}... -> {enhanced[:80]}...", file=sys.stderr)
            return enhanced
    except Exception as e:
        print(f"[prompt-enhancer] AI enhancement failed: {e}", file=sys.stderr)
    return text

def _apply_prompt_enhancer(input_data):
    global PROMPT_ENHANCER_MODE
    if not isinstance(input_data, list) or len(input_data) == 0:
        return input_data
    last_user_idx = None
    for i in range(len(input_data) - 1, -1, -1):
        item = input_data[i]
        if isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx is None:
        return input_data
    item = input_data[last_user_idx]
    content = item.get("content", "")
    if isinstance(content, list):
        text = content[0].get("text", "") if content else ""
    elif isinstance(content, str):
        text = content
    else:
        return input_data
    if not text or len(text) < 5:
        return input_data
    if text.startswith("<prompt-enhancer>"):
        return input_data
    compaction_summary = ""
    for it in input_data:
        if isinstance(it, dict) and it.get("type") == "message" and it.get("role") == "user":
            c = it.get("content", "")
            t = ""
            if isinstance(c, list):
                t = c[0].get("text", "") if c else ""
            elif isinstance(c, str):
                t = c
            if "[Auto-compacted:" in t:
                compaction_summary = t[:3000]
                break
    if PROMPT_ENHANCER_MODE == "ai-powered" and PROMPT_ENHANCER_MODEL and PROMPT_ENHANCER_URL:
        enhanced = _enhance_prompt_llm(text, compaction_summary)
    else:
        enhanced = text
    enhanced = _PROMPT_ENHANCER_OFFLINE + enhanced
    new_item = dict(item)
    if isinstance(item.get("content"), list):
        new_item["content"] = [{"type": "input_text", "text": enhanced}]
    else:
        new_item["content"] = enhanced
    result = list(input_data)
    result[last_user_idx] = new_item
    print(f"[prompt-enhancer] mode={PROMPT_ENHANCER_MODE} enhanced last user message ({len(text)}->{len(enhanced)} chars)", file=sys.stderr)
    return result
