"""Tool-call validation and pairing — pure functions, zero side effects."""
import json


def validate_tool_pairs(input_items):
    if not isinstance(input_items, list):
        return []
    calls = {}
    errors = []
    for idx, item in enumerate(input_items):
        t = item.get("type")
        if t == "function_call":
            cid = item.get("call_id") or item.get("id")
            if cid:
                calls[cid] = idx
        elif t == "function_call_output":
            cid = item.get("call_id") or item.get("id")
            if not cid or cid not in calls:
                errors.append({"index": idx, "call_id": cid, "error": "orphan_function_call_output"})
    return errors


def repair_orphan_tool_outputs(input_items, errors):
    bad = {e["index"] for e in errors}
    repaired = []
    for idx, item in enumerate(input_items):
        if idx in bad:
            output = item.get("output", "")
            repaired.append({"type": "message", "role": "user",
                             "content": [{"type": "input_text",
                                          "text": f"[Proxy: unmatched tool output]\n{str(output)[:4000]}"}]})
        else:
            repaired.append(item)
    return repaired


def synthesize_tool_results_for_chat(input_items):
    """Convert Responses function_call/function_call_output pairs into plain text.

    Some OpenAI-compatible providers accept tool calls on the first turn but fail
    on the next request when role=tool messages are present. For those providers,
    encode tool outputs as normal user text so the model can continue.
    """
    if not isinstance(input_items, list):
        return input_items, False
    calls = {}
    changed = False
    out = []
    for item in input_items:
        t = item.get("type")
        if t == "function_call":
            cid = item.get("call_id") or item.get("id") or ""
            calls[cid] = item
            changed = True
            continue
        if t == "function_call_output":
            cid = item.get("call_id") or item.get("id") or ""
            call = calls.get(cid, {})
            name = call.get("name", "tool")
            args = call.get("arguments", "{}")
            output = item.get("output", "")
            text = (
                "Tool execution result. Continue the task using this result. "
                "Do not repeat the same tool call unless more information is required.\n\n"
                f"Tool: {name}\nArguments:\n```json\n{str(args)[:2000]}\n```\n"
                f"Output:\n```\n{str(output)[:8000]}\n```"
            )
            out.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})
            changed = True
            continue
        out.append(item)
    return out, changed


def has_function_call_output(input_items):
    return isinstance(input_items, list) and any(i.get("type") == "function_call_output" for i in input_items)


def _text_looks_like_tool_calls(text):
    import re
    _TOOL_CALL_TEXT_PATTERNS = re.compile(
        r'(?:^|\n)[\s•\-\*]*\(?(?:exec_command|write_to_file|exec_bash|bash|run_command|shell|edit_file|read_file|search_files|list_files)'
        r'[\s:]',
        re.I | re.MULTILINE
    )
    if not text or len(text) < 6:
        return False
    return bool(_TOOL_CALL_TEXT_PATTERNS.search(text))
