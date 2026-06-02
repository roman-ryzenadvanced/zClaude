"""Auto-sensing provider adapter — schema detection and message conversion."""
import collections
import dataclasses
import hashlib
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

from proxy.config import *
from proxy.shared_utils import uid, _load_provider_caps, _save_provider_caps


# ═══════════════════════════════════════════════════════════════════
# Auto-sense provider schema system
# ═══════════════════════════════════════════════════════════════════

_SENTINEL = object()

@dataclasses.dataclass
class ProviderSchema:
    """Describes what message formats a provider supports.

    Populated by probing the endpoint and/or analyzing error responses.
    Cached in provider-caps.json so probing only happens once per provider.
    """
    supported_roles: tuple = ("user", "assistant")
    content_type: str = "string"  # "string" | "array"
    content_block_types: tuple = ()  # e.g. ("text", "tool_result", "tool-call")
    tool_result_style: str = "inline"  # "inline" | "tool_result_block" | "anthropic"
    tool_call_style: str = "openai_function"  # "openai_function" | "tool-call" | "anthropic_tool_use"
    accepts_tool_role: bool = False
    accepts_system_role: bool = True
    cc_body_wrap: bool = False  # needs {config, params, threadId} wrapping
    field_names: dict = dataclasses.field(default_factory=dict)
    auth_type: str = ""  # "bearer" | "x-api-key" | "custom"
    auth_header: str = "Authorization"  # header name for auth
    auth_scheme: str = "Bearer "  # prefix for auth value
    tool_decl_format: str = "openai"  # "openai" | "anthropic" | "command_code"
    param_names: dict = dataclasses.field(default_factory=lambda: {
        "max_tokens": "max_tokens",
        "temperature": "temperature",
        "top_p": "top_p",
    })
    response_format: str = "auto"  # "sse" | "raw_json" | "ndjson" | "auto"
    stream_format: str = "auto"  # "sse_data" | "sse_event" | "raw_lines" | "json_lines"
    supports_vision: bool = True

    def hints(self) -> dict:
        """Return a dict for storing in provider-caps.json."""
        d = {}
        for k, v in dataclasses.asdict(self).items():
            if isinstance(v, (list, tuple)) and not v:
                continue
            if isinstance(v, dict) and not v:
                continue
            if k == "supports_vision":
                if v is not False:
                    continue
            elif v is False:
                continue
            if v == "":
                continue
            if v == "auto":
                continue
            d[k] = v
        return d


class ErrorAnalyzer:
    """Parse upstream error responses to infer provider schema.
    Analyzes 400, 401, 422 errors for hints about auth, roles, content format,
    parameter names, field names, tool format, and response format.
    """

    @staticmethod
    def analyze(error_text: str, current: ProviderSchema = None) -> dict:
        hints = {}
        if not error_text:
            return hints
        err = error_text.lower()

        # ── Auth detection (401 errors) ──
        if re.search(r"unauthorized|invalid.*api.?key|missing.*api.?key|x-api-key", err):
            hints["auth_type"] = "x-api-key"
            hints["auth_header"] = "x-api-key"
            hints["auth_scheme"] = ""
        elif re.search(r"invalid.*bearer|bearer.*token|authorization.*header|invalid.*token", err):
            hints["auth_type"] = "bearer"
            hints["auth_header"] = "Authorization"
            hints["auth_scheme"] = "Bearer "

        # ── Role validation ──
        if re.search(r"role.*expected.*(?:user|assistant)", err):
            hints["accepts_tool_role"] = False
            hints["accepts_function_role"] = False

        if re.search(r"role.*(?:tool|function).*(?:invalid|not.*(?:support|allow))", err):
            hints["accepts_tool_role"] = False
            hints["accepts_function_role"] = False

        if re.search(r"role.*system.*(?:invalid|not.*(?:support|allow))", err):
            hints["accepts_system_role"] = False

        # ── Content format (top-level only, not content[i].xxx) ──
        if re.search(r'params\.messages\[\d+\]\.content', err):
            # Explicit path to content field in a messages array (e.g. /alpha/generate)
            if re.search(r"expected string.*received array", err):
                hints["content_type"] = "string"
                hints["tool_result_style"] = "inline"  # no tool_result blocks allowed
            elif re.search(r"expected array.*received string", err):
                hints["content_type"] = "array"
        elif re.search(r"(?<!\w)content(?!\[)\s*(?:of type|field|should be|expected|must be).*(?:string|array)", err) or \
             re.search(r"expected (?:string|array).*content", err):
            if re.search(r"expected string", err) and not re.search(r"expected array", err):
                hints["content_type"] = "string"
            elif re.search(r"expected array", err):
                hints["content_type"] = "array"
        elif re.search(r"content.*expected string.*received array", err) and not re.search(r"\[\d*\]", err):
            hints["content_type"] = "string"
        elif re.search(r"content.*expected array.*received string", err) and not re.search(r"\[\d*\]", err):
            hints["content_type"] = "array"

        # ── Content block types ──
        types = set()
        for m in re.finditer(
            r'expected\s+"('
            r'text|image|document|search_result|thinking|redacted_thinking|reasoning|'
            r'tool_use|tool-call|tool_result|tool-result|'
            r'server_tool_use|web_search_tool_result|web_fetch_tool_result|tool'
            r')"', err
        ):
            types.add(m.group(1))
        # Also detect from "expected string, received array at params.messages[i].content" pattern
        # where the "or" clauses list valid block types
        if not types and re.search(r'params\.messages\[\d+\]\.content', err):
            for valid_type in ("text", "image", "document", "tool_use", "tool-call", "tool_result"):
                if re.search(r'expected\s+"' + re.escape(valid_type) + r'"', err):
                    types.add(valid_type)
        if types:
            hints["content_block_types"] = tuple(sorted(types))

        # ── Tool result style ──
        if re.search(r"tool_result", err):
            hints["tool_result_style"] = "tool_result_block"
        elif re.search(r"tool_use", err) and not re.search(r"tool.use", err):
            hints["tool_result_style"] = "anthropic"

        # ── Tool call style ──
        if re.search(r"tool-call", err) or re.search(r"tool_call", err):
            hints["tool_call_style"] = "tool-call"
        elif re.search(r"tool_use", err):
            hints["tool_call_style"] = "anthropic_tool_use"

        # ── CC body wrap detection ──
        if re.search(r"(?:params\.|body\.)config", err) or re.search(r"threadId", err):
            hints["cc_body_wrap"] = True

        # ── Field name mappings (keys MUST match SchemaAdapter lookups) ──
        fields = {}
        if re.search(r"tool_use_id", err):
            fields["tool_use_id"] = "tool_use_id"
        if re.search(r"toolCallId", err):
            fields["toolCallId"] = "toolCallId"
            # SchemaAdapter._tool_result_block looks up "tool_use_id"
            fields["tool_use_id"] = "toolCallId"
        if re.search(r"tool_result", err) and not re.search(r"tool.result", err):
            fields["tool_result_type"] = "tool_result"
        if re.search(r"tool-result", err):
            fields["tool_result_type"] = "tool-result"
        # Detect tool call field names from errors
        if re.search(r"(?:id|call_id|callId|tool_use_id).*(?:invalid|unknown|expected|required)", err) or \
           re.search(r"(?:expected|required).*(?:id|call_id|callId)", err):
            for alt in ("id", "call_id", "callId", "tool_use_id"):
                if alt in err:
                    fields["tool_call_id_field"] = alt
                    break
        if re.search(r"(?:name|tool_name|function).*(?:invalid|unknown|expected|required)", err) or \
           re.search(r"(?:expected|required).*(?:name|tool_name)", err):
            for alt in ("name", "tool_name", "function"):
                if alt in err:
                    fields["tool_call_name_field"] = alt
                    break
        if re.search(r"arguments.*(?:invalid|unknown|expect|required)", err) or \
           re.search(r"input.*(?:invalid|unknown|expect|required)", err):
            if re.search(r"input_schema|input\b", err) and not re.search(r"arguments", err):
                fields["tool_call_args_field"] = "input"
                fields["tool_args_field"] = "input"
            else:
                fields["tool_call_args_field"] = "arguments"
                fields["tool_args_field"] = "arguments"

        # ── Supported roles from error ──
        if re.search(r"params\.messages\[\d+\]\.role", err):
            roles = re.findall(r'expected one of\s+"([^"]+)"', err)
            if roles:
                hints["supported_roles"] = tuple(r.strip() for r in roles[0].split("|"))
        if fields:
            hints["field_names"] = fields

        # ── Parameter name negotiation ──
        param_hints = {}
        if re.search(r"max_tokens.*(?:invalid|unknown|not.*(?:support|recognize))", err) or \
           re.search(r"(?:unknown|invalid).*param.*max_tokens", err):
            for alt in ("max_output_tokens", "max_tokens_to_sample", "max_new_tokens", "max_token"):
                if alt.lower() in err:
                    param_hints["max_tokens"] = alt
                    break
        if re.search(r"temperature.*(?:invalid|unknown)", err):
            for alt in ("creation_temperature", "temp", "model_temperature"):
                if alt.lower() in err:
                    param_hints["temperature"] = alt
                    break
        if re.search(r"top_p.*(?:invalid|unknown)", err):
            for alt in ("top_p", "nucleus_sampling"):
                if alt.lower() in err:
                    param_hints["top_p"] = alt
                    break
        if param_hints:
            hints["param_names"] = param_hints

        # ── Tool declaration format ──
        if re.search(r"tools.*input_schema", err) or re.search(r"input_schema.*required", err):
            hints["tool_decl_format"] = "anthropic"
        elif re.search(r"tools.*function.*(?:required|expected)", err):
            hints["tool_decl_format"] = "openai"
        elif re.search(r"tool-call|tool_call.*format", err):
            hints["tool_decl_format"] = "command_code"

        # ── Vision support detection ──
        if re.search(r"unknown variant\b.*image_url", err) or \
           re.search(r"unexpected.*image_url", err) or \
           re.search(r"does not support.*image", err) or \
           re.search(r"image.*not.*support", err) or \
           re.search(r"unsupported.*content.*type.*image", err):
            hints["supports_vision"] = False

        # ── Response/Stream format hints from content-type or error ──
        # ── Vision support detection ──
        if re.search(r"unknown variant\b.*image_url", err) or \
           re.search(r"unexpected.*image_url", err) or \
           re.search(r"does not support.*image", err) or \
           re.search(r"image.*not.*support", err) or \
           re.search(r"unsupported.*content.*type.*image", err):
            hints["supports_vision"] = False

        # ── Response/Stream format hints from content-type or error ──
        if re.search(r"content.type.*text/event.stream", err) or \
           re.search(r"stream.*sse|sse.*expected", err):
            hints["stream_format"] = "sse_data"
        if re.search(r"ndjson|json.*lines", err):
            hints["stream_format"] = "json_lines"

        return hints

    @staticmethod
    def merge_into_schema(hints: dict, schema: ProviderSchema) -> ProviderSchema:
        for k, v in hints.items():
            if k == "field_names" and isinstance(v, dict):
                schema.field_names.update(v)
            elif k == "param_names" and isinstance(v, dict):
                schema.param_names.update(v)
            elif hasattr(schema, k):
                setattr(schema, k, v)
        return schema


def _schema_cache_key(target_url=None, backend=None, model=None):
    host = urllib.parse.urlparse(target_url or TARGET_URL).netloc.lower()
    return f"auto-schema|{backend or BACKEND}|{host}|{model or '*'}"


def _load_schema(target_url=None, backend=None, model=None):
    caps = _load_provider_caps()
    key = _schema_cache_key(target_url, backend, model)
    raw = caps.get(key)
    generic = caps.get(_schema_cache_key(target_url, backend, model="*"))
    data = raw or generic or {}
    if not data:
        return ProviderSchema()
    # Staleness check: re-learn after 24h (86400s)
    updated = data.get("_updated", 0)
    if isinstance(updated, (int, float)) and time.time() - updated > 86400:
        print(f"[auto-sense] cached schema stale ({int(time.time()-updated)}s old), re-learning", file=sys.stderr)
        return ProviderSchema()
    return ProviderSchema(
        supported_roles=tuple(data.get("supported_roles", ("user", "assistant"))),
        content_type=data.get("content_type", "string"),
        content_block_types=tuple(data.get("content_block_types", ())),
        tool_result_style=data.get("tool_result_style", "inline"),
        tool_call_style=data.get("tool_call_style", "openai_function"),
        accepts_tool_role=data.get("accepts_tool_role", False),
        accepts_system_role=data.get("accepts_system_role", True),
        cc_body_wrap=data.get("cc_body_wrap", False),
        field_names=dict(data.get("field_names", {})),
        auth_type=data.get("auth_type", ""),
        auth_header=data.get("auth_header", "Authorization"),
        auth_scheme=data.get("auth_scheme", "Bearer "),
        tool_decl_format=data.get("tool_decl_format", "openai"),
        param_names=dict(data.get("param_names", {
            "max_tokens": "max_tokens",
            "temperature": "temperature",
            "top_p": "top_p",
        })),
        response_format=data.get("response_format", "auto"),
        stream_format=data.get("stream_format", "auto"),
        supports_vision=data.get("supports_vision", True),
    )


def _save_schema(schema: ProviderSchema, target_url=None, backend=None, model=None):
    caps = _load_provider_caps()
    key = _schema_cache_key(target_url, backend, model)
    caps[key] = schema.hints()
    caps[key]["_updated"] = time.time()
    caps[key]["_backend"] = backend or BACKEND
    _save_provider_caps()
    print(f"[auto-sense] cached schema {key}", file=sys.stderr)


class SchemaAdapter:
    """Convert Responses API messages based on a detected ProviderSchema."""

    def __init__(self, schema: ProviderSchema):
        self.s = schema

    def convert(self, input_data, instructions=""):
        if self.s.content_type == "string" and not self.s.content_block_types:
            return self._to_plain_string(input_data, instructions)
        return self._to_content_blocks(input_data, instructions)

    def _to_plain_string(self, input_data, instructions=""):
        """Fallback: user/assistant string content — no tool roles."""
        msgs = []
        if instructions and self.s.accepts_system_role:
            msgs.append({"role": "system", "content": instructions})
        elif instructions:
            msgs.append({"role": "user", "content": instructions})
        if isinstance(input_data, str):
            msgs.append({"role": "user", "content": input_data})
            return msgs
        if not isinstance(input_data, list):
            return msgs
        last_flushed = []
        pending = []
        for item in input_data:
            t = item.get("type")
            if t == "function_call":
                cid = item.get("call_id") or item.get("id") or uid("fc")
                pending.append({"id": cid, "name": item.get("name", ""),
                                "arguments": item.get("arguments", "{}")})
                continue
            if pending:
                last_flushed = [p["id"] for p in pending]
                msgs.append({"role": "assistant", "content": None,
                             "tool_calls": [{"id": p["id"], "type": "function",
                                             "function": {"name": p["name"],
                                                          "arguments": p["arguments"]}}
                                            for p in pending]})
                pending = []
            if t == "message":
                role = "user" if item.get("role") in ("user", "developer") else "assistant"
                text = _extract_text(item.get("content", []))
                if text:
                    msgs.append({"role": role, "content": text})
            elif t == "function_call_output":
                out = item.get("output", "")
                if not isinstance(out, str):
                    out = json.dumps(out, ensure_ascii=False)
                msgs.append({"role": "user", "content": out[:8000]})
        if pending:
            last_flushed = [p["id"] for p in pending]
            msgs.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": p["id"], "type": "function",
                                         "function": {"name": p["name"],
                                                      "arguments": p["arguments"]}}
                                        for p in pending]})
        return msgs

    def _to_content_blocks(self, input_data, instructions=""):
        msgs = []
        pending_tc = []
        tool_name_by_id = {}
        last_ids = []

        def flush():
            nonlocal last_ids
            if not pending_tc:
                return
            last_ids = [t["id"] for t in pending_tc]
            msgs.append({"role": "assistant", "content": pending_tc})
            pending_tc.clear()

        _str = self.s.content_type == "string"

        if instructions:
            msgs.append({"role": "user", "content": instructions if _str else [{"type": "text", "text": instructions}]})

        if isinstance(input_data, str):
            msgs.append({"role": "user", "content": input_data if _str else [{"type": "text", "text": input_data}]})
            return msgs
        if not isinstance(input_data, list):
            return msgs

        for item in input_data:
            t = item.get("type")
            if t == "function_call":
                cid = item.get("call_id") or item.get("id") or uid("call")
                nm = item.get("name") or "exec_command"
                tool_name_by_id[cid] = nm
                tc_block = self._tool_call_block(cid, nm, item.get("arguments", "{}"))
                if tc_block:
                    pending_tc.append(tc_block)
                continue
            flush()
            if t == "message":
                role = "user" if item.get("role") in ("user", "developer") else "assistant"
                text = _extract_text(item.get("content", []))
                if text:
                    msgs.append({"role": role, "content": text if _str else [{"type": "text", "text": text}]})
            elif t == "function_call_output":
                cid = item.get("call_id") or item.get("id") or ""
                if not cid and last_ids:
                    idx = sum(1 for m in msgs for c in (m.get("content") or [])
                              if isinstance(c, dict) and c.get("type") in
                              ("tool_result", "tool-result"))
                    if idx < len(last_ids):
                        cid = last_ids[idx]
                out = item.get("output", "")
                if not isinstance(out, str):
                    out = json.dumps(out, ensure_ascii=False)
                tr = self._tool_result_block(cid, out)
                if tr:
                    msgs.append({"role": "user", "content": [tr]})
        flush()
        return msgs

    def _tool_call_block(self, cid, name, args):
        style = self.s.tool_call_style
        fn = self.s.field_names
        if style == "tool-call":
            return {
                "type": fn.get("tool_call_type", "tool-call"),
                fn.get("tool_call_id_field", "id"): cid,
                fn.get("tool_call_name_field", "name"): name,
                fn.get("tool_call_args_field", "arguments"): args,
            }
        elif style == "anthropic_tool_use":
            try:
                parsed = json.loads(args)
            except Exception:
                parsed = {}
            return {
                "type": fn.get("tool_use_type", "tool_use"),
                fn.get("tool_call_id_field", "id"): cid,
                fn.get("tool_call_name_field", "name"): name,
                fn.get("tool_call_args_field", "input"): parsed,
            }
        else:
            return None  # handled as OpenAI function call

    def _tool_result_block(self, cid, output):
        style = self.s.tool_result_style
        fn = self.s.field_names
        if style == "tool_result_block":
            return {
                "type": fn.get("tool_result_type", "tool_result"),
                fn.get("tool_use_id", "tool_use_id"): cid or "",
                "content": [{"type": "text", "text": output[:8000]}],
            }
        elif style == "anthropic":
            return {
                "type": fn.get("tool_result_type", "tool_result"),
                fn.get("tool_use_id", "tool_use_id"): cid or "",
                "content": output[:8000],
            }
        return None  # inline — handled by _to_plain_string


def _extract_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for p in content:
        if isinstance(p, str):
            parts.append(p)
        elif isinstance(p, dict):
            pt = p.get("type")
            if pt in ("input_text", "output_text", "text"):
                parts.append(p.get("text", ""))
            elif pt in ("input_file", "file", "document", "inlineData", "inline_data"):
                b64 = ""
                mime = "text/plain"
                filename = p.get("filename") or p.get("name") or "attachment"
                
                if pt in ("inlineData", "inline_data"):
                    mime = p.get("mimeType") or p.get("mime_type") or "text/plain"
                    b64 = p.get("data", "")
                elif pt == "document" and isinstance(p.get("source"), dict):
                    src = p["source"]
                    mime = src.get("media_type") or src.get("mime_type") or "text/plain"
                    b64 = src.get("data", "")
                else:
                    fu = p.get("file_url") or p.get("document_url") or p.get("url", {})
                    url = fu.get("url", fu) if isinstance(fu, dict) else fu
                    if isinstance(url, str) and url.startswith("data:"):
                        mime_part, _, b64 = url.partition(";base64,")
                        mime = mime_part.replace("data:", "") or "text/plain"
                    else:
                        b64 = p.get("data", "")
                        mime = p.get("mimeType") or p.get("mime_type") or "text/plain"
                
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
                        parts.append(f"\n[Attached File: {filename}]\n" + "-"*40 + f"\n{decoded_text}\n" + "-"*40 + "\n")
                    except Exception as e:
                        print(f"[auto-sense-file] failed to decode text: {e}", file=sys.stderr)
    return "".join(parts)


# Persistent cache: image hash → description (survives across requests)
_vision_desc_cache = collections.OrderedDict()
_vision_desc_lock = threading.Lock()
_VISION_DESC_CACHE_MAX = 256


def _vision_describe_image(img_data):
    """Call vision fallback API to describe a single image.

    Uses a module-level LRU cache so descriptions survive across requests.
    A single image in a multi-turn conversation is only described once.

    Returns:
        description string or None on failure
    """
    global _vision_desc_cache

    if not VISION_FALLBACK_URL:
        return None

    # Normalize image URL from various formats
    if isinstance(img_data, dict):
        img_url = img_data.get("url", "")
        if not img_url:
            inner = img_data.get("image_url", img_data)
            img_url = inner.get("url", "") if isinstance(inner, dict) else str(inner)
    else:
        img_url = str(img_data)

    if not img_url:
        return None

    img_hash = hashlib.sha256(img_url.encode("utf-8", errors="replace")).hexdigest()

    # Check persistent cache first (no API call needed)
    with _vision_desc_lock:
        if img_hash in _vision_desc_cache:
            return _vision_desc_cache[img_hash]

    try:
        payload = json.dumps({
            "model": VISION_FALLBACK_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Describe the content of this image in detail. If it contains text, transcribe it fully."},
                {"type": "image_url", "image_url": {"url": img_url}},
            ]}],
            "max_tokens": 1024,
            "stream": False,
        }).encode()

        headers = {"Content-Type": "application/json"}
        if VISION_FALLBACK_KEY:
            headers["Authorization"] = f"Bearer {VISION_FALLBACK_KEY}"

        req = urllib.request.Request(VISION_FALLBACK_URL, data=payload, headers=headers)
        resp = urllib.request.urlopen(req, timeout=30)
        body = json.loads(resp.read().decode())

        choices = body.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            desc = msg.get("content", "")
            if desc:
                with _vision_desc_lock:
                    _vision_desc_cache[img_hash] = desc
                    if len(_vision_desc_cache) > _VISION_DESC_CACHE_MAX:
                        _vision_desc_cache.popitem(last=False)
                return desc
    except Exception as e:
        print(f"[vision-fallback] error describing image: {e}", file=sys.stderr)

    return None


def _preprocess_vision(messages, schema):
    """Replace image blocks with text descriptions when provider lacks vision support.

    Works on OpenAI Chat Completions message format (post-conversion).
    """
    if schema.supports_vision:
        return messages

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        new_parts = []
        changed = False
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("image_url", "input_image"):
                changed = True
                img_data = part.get("image_url", part)
                description = _vision_describe_image(img_data)
                if description:
                    new_parts.append({"type": "text", "text": f"[Image: {description}]"})
                else:
                    new_parts.append({"type": "text", "text": "[Image: description non disponible - modele text-only]"})
            else:
                new_parts.append(part)
        if changed:
            msg["content"] = new_parts

    return messages


def _preprocess_vision_input(input_data, schema):
    """Replace input_image blocks in Responses API input format with text descriptions.

    This runs BEFORE adapter.convert() so images are replaced before any
    conversion function can silently drop them.
    """
    if schema.supports_vision:
        return input_data
    if not isinstance(input_data, list):
        return input_data

    changed_any = False

    for item in input_data:
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        new_parts = []
        changed = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "input_image":
                changed = True
                changed_any = True
                img_data = part.get("image_url", part)
                description = _vision_describe_image(img_data)
                if description:
                    new_parts.append({"type": "input_text", "text": f"[Image: {description}]"})
                else:
                    new_parts.append({"type": "input_text", "text": "[Image: description non disponible - modele text-only]"})
            else:
                new_parts.append(part)
        if changed:
            item["content"] = new_parts

    return input_data
