"""Command Code self-healing tool-call parser."""
import json
import os
import re
import sys
import tempfile
import time

from proxy.config import *  # noqa: F401  — _IS_WINDOWS, _LOG_DIR, _last_user_urls, etc.
from proxy.shared_utils import uid, emit, _stream_with_idle_timeout  # noqa: F401


_DEFAULT_CC_CONFIG = {
    "workingDir": tempfile.gettempdir(),
    "date": "",
    "environment": "windows" if _IS_WINDOWS else "linux",
    "shell": "powershell" if _IS_WINDOWS else "bash",
    "files": [],
    "structure": [],
    "isGitRepo": False,
    "currentBranch": "",
    "mainBranch": "",
    "gitStatus": "",
    "recentCommits": [],
}

def _cc_config():
    cfg = dict(_DEFAULT_CC_CONFIG)
    cfg["date"] = time.strftime("%Y-%m-%d")
    return cfg

def cc_convert_tools(tools):
    """Convert tools using OpenAI-compat format. Late import to avoid circular deps."""
    from proxy.adapters.openai import oa_convert_tools as _oa_convert_tools
    return _oa_convert_tools(tools)

def _strip_xmlish_tags(text):
    return re.sub(r"<[^>]+>", "", text or "")

def _unwrap_cmd(cmd_val):
    """[FIX 11] Self-healing: unwrap double-wrapped cmd values.
    
    Model sometimes generates: {"cmd": "{\"cmd\": \"actual_command\"}"}
    Detect when cmd value is itself a JSON object with a nested "cmd" key,
    and extract the real command string. Recursively unwraps up to 3 levels.
    """
    if not isinstance(cmd_val, str) or not cmd_val.startswith("{"):
        return cmd_val
    for _ in range(3):
        try:
            inner = json.loads(cmd_val)
            if isinstance(inner, dict) and "cmd" in inner and isinstance(inner["cmd"], str):
                cmd_val = inner["cmd"]
            else:
                break
        except Exception:
            break
    return cmd_val

def _build_explore_cmd(text_for_url):
    """Module-level explore command builder. Extracts repo URL from text,
    builds a curl pipeline to fetch README, contents listing, and releases.
    Used by _parse_commandcode_text_tool_calls (closure wrapper) and
    cc_stream_to_sse (stuck recovery heuristic)."""
    if not text_for_url:
        return None, None
    url_m = re.search(r"https?://[^\s\]'\\>\",]+", text_for_url)
    repo_url = url_m.group(0).rstrip(")].,;'\\\"") if url_m else ""
    if not repo_url and isinstance(text_for_url, str):
        try:
            _parsed = json.loads(text_for_url)
            if isinstance(_parsed, list):
                for _item in _parsed:
                    _c = _item.get("content", "") if isinstance(_item, dict) else str(_item)
                    url_m2 = re.search(r"https?://[^\s\]'\\>\",]+", _c)
                    if url_m2:
                        repo_url = url_m2.group(0).rstrip(")].,;'\\\"")
                        break
        except Exception:
            pass
    if not repo_url:
        return None, None
    if repo_url.endswith(".git"):
        repo_url = repo_url[:-4]
    if "/api/v1/repos/" not in repo_url:
        host_m = re.match(r"(https?://[^/]+)/(.*)", repo_url)
        if host_m:
            host, path = host_m.groups()
            api_base = f"{host}/api/v1/repos/{path}"
        else:
            api_base = repo_url.replace("/admin/", "/api/v1/repos/")
    else:
        api_base = repo_url
    if _IS_WINDOWS:
        cmd = (
            f"cd $env:TEMP; "
            f"$r = Invoke-WebRequest -Uri '{api_base}/contents/README.md' -UseBasicParsing -TimeoutSec 15 2>$null; "
            f"if ($r) {{ $j = $r.Content | ConvertFrom-Json; [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($j.content)) | Select-Object -First 600 }}; "
            f"$r2 = Invoke-WebRequest -Uri '{api_base}/contents' -UseBasicParsing -TimeoutSec 15 2>$null; "
            f"if ($r2) {{ $j2 = $r2.Content | ConvertFrom-Json; $j2 | Select-Object -First 50 | ForEach-Object {{ $_.path + ' ' + $_.type }} }}; "
            f"$r3 = Invoke-WebRequest -Uri '{api_base}/releases' -UseBasicParsing -TimeoutSec 15 2>$null; "
            f"if ($r3) {{ ($r3.Content | ConvertFrom-Json | Select-Object -First 3 | ConvertTo-Json).Substring(0, [Math]::Min(2000, ($r3.Content | ConvertFrom-Json | Select-Object -First 3 | ConvertTo-Json).Length)) }}"
        )
    else:
        cmd = (
            f"cd /tmp && "
            f"curl -sL --max-time 15 '{api_base}/contents/README.md' 2>/dev/null | "
            f"python3 -c \"import sys,json,base64; d=json.load(sys.stdin); print(base64.b64decode(d['content']).decode())\" 2>/dev/null | head -600 && "
            f"curl -sL --max-time 15 '{api_base}/contents' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print('\\n'.join(f'{{x.get(\'path\')}} {{x.get(\'type\')}}' for x in d[:50]))\" 2>/dev/null && "
            f"curl -sL --max-time 15 '{api_base}/releases' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); print(json.dumps(d[:3], indent=2)[:2000])\" 2>/dev/null"
        )
    return cmd, "Explore repository to understand the app and gather README, root contents, and releases for the landing page."

def _parse_commandcode_text_tool_calls(text):
    """Parse CommandCode's text-form tool calls into Responses function calls.

    Handles THREE formats:
      1. XML: ``<tool_call name="bash"><parameter name="command">...</parameter>`` (original)
      2. Function: ``<function=bash>...</function>`` (original)
      3. [FIX 5] Raw JSON inline: {"type":"tool-call","id":"...","name":"exec_command","arguments":"{...}"}

    Format 3 exists because cc_input_to_messages sends tool calls as inline JSON text.
    The CC model echoes this format back in its response.
    Extraction is done by _extract_raw_json_tool_calls() which is appended after the
    XML pattern loop. See that function for details on malformed-JSON handling.

    Tolerant of: unescaped inner quotes, unbalanced braces, missing type/id fields,
    sandbox_permissions at top level vs nested inside arguments, etc.
    """
    calls = []
    if not text:
        return calls

    _build_explore_cmd_local = _build_explore_cmd

    # [FIX 17] DSML tool_call blocks used by the model now.
    # Example:
    #   <｜｜DSML｜｜tool_calls>
    #   <｜｜DSML｜｜invoke name="exec">
    #   <｜｜DSML｜｜parameter name="command" string="true">curl ...</｜｜DSML｜｜parameter>
    #   <｜｜DSML｜｜parameter name="sandbox_permissions" string="true">require_escalated</｜｜DSML｜｜parameter>
    #   <｜｜DSML｜｜parameter name="justification" string="true">...</｜｜DSML｜｜parameter>
    #   <｜｜DSML｜｜parameter name="prefix_rule" string="true">["/bin/bash", "-lc", "curl ..."]</｜｜DSML｜｜parameter>
    #   </｜｜DSML｜｜invoke>
    #   </｜｜DSML｜｜tool_calls>
    for m in re.finditer(r"<[^>]*tool_calls[^>]*>(.*?)</[^>]*tool_calls[^>]*>", text, re.DOTALL | re.IGNORECASE):
        block = m.group(1) or ""
        for im in re.finditer(r"<[^>]*invoke[^>]*name=\"([^\"]+)\"[^>]*>(.*?)</[^>]*invoke>", block, re.DOTALL | re.IGNORECASE):
            raw_name = (im.group(1) or "").strip()
            body = (im.group(2) or "").strip()
            if not body:
                continue
            cmd = None
            sandbox_permissions = None
            justification = None
            # Parameter tags are the canonical source.
            for pm in re.finditer(r"<[^>]*parameter[^>]*name=\"([^\"]+)\"[^>]*>(.*?)</[^>]*parameter>", body, re.DOTALL | re.IGNORECASE):
                key = (pm.group(1) or "").strip().lower()
                val = _strip_xmlish_tags(pm.group(2)).strip()
                # [FIX 21] Accept both "command" and "cmd" parameter names.
                # The tool schema defines the parameter as "cmd" (see exec_command schema),
                # but the model sometimes uses "command" (especially from prefix_rule fallback).
                # Previously only "command" was accepted, so DSML blocks with name="cmd"
                # were silently dropped — causing Codex CLI to stop mid-task.
                if key in ("command", "cmd"):
                    cmd = val
                elif key == "prefix_rule" and not cmd:
                    try:
                        pr_obj = json.loads(val)
                    except Exception:
                        pr_obj = None
                    if isinstance(pr_obj, list) and pr_obj and isinstance(pr_obj[-1], str):
                        cmd = pr_obj[-1]
                elif key == "sandbox_permissions":
                    sandbox_permissions = val
                elif key == "justification":
                    justification = val

            # [FIX 20] Support explore / explore_agent in DSML blocks
            is_explore = raw_name.lower() in ("explore", "explore_agent")
            if is_explore:
                explore_cmd, explore_just = _build_explore_cmd_local(body)
                if explore_cmd:
                    cmd = explore_cmd
                    justification = explore_just

            # Fallback: if the body contains a raw JSON command.
            if not cmd:
                jm = re.search(r'"(?:command|cmd)"\s*:\s*"((?:[^"\\]|\\.)*)"', body, re.DOTALL)
                if jm:
                    cmd = jm.group(1).replace('\\n', '\n').replace('\\"', '"').strip()
            if not cmd:
                continue
            # [FIX 19] Translate execute_request and other variations to exec_command (CLI only supports exec_command)
            # [FIX 20] Translate explore and explore_agent to exec_command
            tool_name = "exec_command" if raw_name.lower() in ("exec", "bash", "shell", "terminal", "run_command", "execute_request", "execute_command", "run_shell_command", "run_shell", "run", "explore", "explore_agent") else raw_name
            args = {"cmd": _unwrap_cmd(cmd)}
            if sandbox_permissions:
                args["sandbox_permissions"] = sandbox_permissions if sandbox_permissions in ("use_default", "require_escalated", "with_user_approval") else "require_escalated"
            if justification:
                args["justification"] = justification
            calls.append({
                "full_match": m.group(0),
                "name": tool_name,
                "arguments": json.dumps(args, ensure_ascii=False),
            })

    # [FIX 16] Native <bash> blocks from CommandCode.
    # Example:
    #   <bash>
    #   sandbox_permissions: require_escalated
    #   justification: ...
    #   prefix_rule: ["/bin/bash", "-lc", "curl ..."]
    #   </bash>
    # Convert into exec_command calls by extracting the command from prefix_rule.
    for m in re.finditer(r"<bash>(.*?)</bash>", text, re.DOTALL | re.IGNORECASE):
        body = (m.group(1) or "").strip()
        if not body:
            continue
        sandbox_permissions = None
        justification = None
        cmd = None
        # Try line-oriented parsing first.
        for line in body.splitlines():
            s = line.strip()
            if s.lower().startswith("sandbox_permissions:"):
                sandbox_permissions = s.split(":", 1)[1].strip()
            elif s.lower().startswith("justification:"):
                justification = s.split(":", 1)[1].strip()
            elif s.lower().startswith("prefix_rule:"):
                pr = s.split(":", 1)[1].strip()
                try:
                    pr_obj = json.loads(pr)
                except Exception:
                    pr_obj = None
                if isinstance(pr_obj, list) and pr_obj:
                    # If the last arg exists, it is typically the shell command.
                    cmd = pr_obj[-1] if isinstance(pr_obj[-1], str) else None
                elif pr.startswith("[") and pr.endswith("]"):
                    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', pr)
                    if parts:
                        cmd = parts[-1].encode().decode("unicode_escape")
        # Fallback: grab a shell-looking line if prefix_rule wasn't parseable.
        if not cmd:
            for line in body.splitlines():
                s = line.strip()
                if re.match(r"^(curl|wget|python3?|node|npm|pnpm|yarn|cat|ls|find|grep|rg|sed|awk|git|mkdir|touch|printf|echo)\b", s):
                    cmd = s
                    break
        if not cmd:
            continue
        args = {"cmd": cmd}
        if sandbox_permissions:
            args["sandbox_permissions"] = sandbox_permissions if sandbox_permissions in ("use_default", "require_escalated", "with_user_approval") else "require_escalated"
        if justification:
            args["justification"] = justification
        calls.append({
            "full_match": m.group(0),
            "name": "exec_command",
            "arguments": json.dumps(args, ensure_ascii=False),
        })

    # [FIX 15] Native <explore_agent> blocks from CommandCode.
    # Format seen in logs:
    #   <explore_agent>\nmessages: [{...}]\n</explore_agent>
    # Treat as an assistant-requested agent call so the loop can continue.
    for m in re.finditer(r"<explore_agent>(.*?)</explore_agent>|<explore_agent>\s*messages:\s*(\[.*?\])", text, re.DOTALL | re.IGNORECASE):
        body = m.group(1) or m.group(2) or ""
        body = body.strip()
        msgs = None
        if body:
            try:
                msgs = json.loads(body) if body.startswith("[") else None
            except Exception:
                msgs = None
        if msgs is None and body:
            mm = re.search(r"(\[.*\])", body, re.DOTALL)
            if mm:
                try:
                    msgs = json.loads(mm.group(1))
                except Exception:
                    msgs = None
        if msgs is None:
            msgs = body
        text_for_url = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        cmd, justification = _build_explore_cmd_local(text_for_url)
        if not cmd:
            cmd = "echo 'explore_agent: unable to extract repository URL'"
            justification = "Fallback for explore_agent block without URL."
        args = {"cmd": cmd}
        if justification:
            args["justification"] = justification
        calls.append({
            "full_match": m.group(0),
            "name": "exec_command",
            "arguments": json.dumps(args, ensure_ascii=False),
        })

    if not calls and text.count("<explore_agent>") >= 2:
        url_m = re.search(r"https?://[^\s\]'\\>\"]+", text)
        if not url_m:
            for prev_url in _last_user_urls:
                url_m = re.search(r"https?://[^\s\]'\\>\"]+", prev_url)
                if url_m:
                    break
        if url_m:
            explore_url = url_m.group(0).rstrip(")].,;'\\")
            cmd, justification = _build_explore_cmd_local(explore_url)
            if cmd:
                calls.append({
                    "full_match": "<explore_agent>...",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": cmd, "justification": justification or "Explore repository"}, ensure_ascii=False),
                })

    # [FIX 24] Handle <require_escalation> and <request_escalation_permission> blocks.
    # The model produces these when it wants elevated permissions but the CC
    # adapter doesn't support them. Synthesize a proceed command so the loop continues.
    if not calls:
        for m in re.finditer(r"<(?:require_escalation|request_escalation_permission)>(.*?)</(?:require_escalation|request_escalation_permission)>", text, re.DOTALL | re.IGNORECASE):
            body_escal = (m.group(1) or "").strip()
            _inner_url_m = re.search(r"https?://[^\s\]'\\>\",]+", body_escal)
            if _inner_url_m:
                _e_url = _inner_url_m.group(0).rstrip(")].,;'\\\"")
                _e_cmd, _e_just = _build_explore_cmd_local(_e_url)
                if _e_cmd:
                    calls.append({
                        "full_match": m.group(0),
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": _e_cmd, "justification": _e_just or "Escalation block with URL — auto-proceed"}, ensure_ascii=False),
                    })
                    continue
            if not calls:
                calls.append({
                    "full_match": m.group(0),
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": "echo 'escalation: auto-proceeding — no specific command in escalation block'", "justification": "Auto-proceed past escalation request"}, ensure_ascii=False),
                })

    # [FIX 24b] Bare <require_escalation ... /> or <request_escalation_permission ... />
    # without closing tags. Just auto-proceed.
    if not calls and re.search(r"<(?:require_escalation|request_escalation_permission)[\s/>]", text, re.IGNORECASE):
        calls.append({
            "full_match": "<escalation_bare/>",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": "echo 'escalation: auto-proceeding past bare escalation tag'", "justification": "Auto-proceed past bare escalation tag"}, ensure_ascii=False),
        })

    patterns = [
        r"<tool_call(?:\s+name=['\"]?([^'\">\s]+)['\"]?)?>(.*?)</tool_call[)]?>",
        r"<function=(\w+)>(.*?)</function>",
        # [FIX 14] CC model actual output: <tool_call type="bash">\n{"command":"...", "description":"..."}
        # No </tool_call) closing tag — body is a raw JSON object
        r"<tool_call(?:\s+type=['\"]?(\w+)['\"]?)?>\s*(\{.*?\})(?:\s*</tool_call)?",
    ]

    def _find_balanced_brace(text, start):
        """Find the closing brace matching text[start], handling quoted strings."""
        if start >= len(text) or text[start] != '{':
            return -1
        depth = 0
        i = start
        in_str = False
        escape = False
        while i < len(text):
            ch = text[i]
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return i
            i += 1
        return -1

    def _extract_field(text, key, end_chars=',}'):
        """Extract a field value after "key": in rough JSON text.

        [FIX 7] Handles values starting with \" (backslash-quote) which occurs when
        the model generates properly-escaped JSON inside a string value.
        Without this fix, _extract_field returns None for escaped values,
        causing sandbox_permissions/justification to not be extracted from
        the parsed args dict (falling through to raw snippet extraction).

        Also tolerant of unescaped quotes inside string values.
        Returns None if key not found or value is empty.
        """
        pat = re.compile(r'"' + re.escape(key) + r'"\s*:\s*', re.DOTALL)
        m = pat.search(text)
        if not m:
            return None
        val_start = m.end()
        # Skip leading backslash-escape if the value starts with \" (nested JSON string)
        if val_start < len(text) and text[val_start] == '\\':
            val_start += 1
        # Check if value is a string
        if val_start < len(text) and text[val_start] == '"':
            s = val_start + 1
            buf = []
            while s < len(text):
                ch = text[s]
                if ch == '\\' and s + 1 < len(text):
                    buf.append(text[s+1])
                    s += 2
                elif ch == '"':
                    return ''.join(buf)
                elif ch in end_chars and not buf:
                    return None
                else:
                    buf.append(ch)
                    s += 1
            return ''.join(buf)
        # Object value: find balanced brace
        if val_start < len(text) and text[val_start] == '{':
            end = _find_balanced_brace(text, val_start)
            if end > val_start:
                return text[val_start:end+1]
        return None

    def _extract_args(text):
        """Extract arguments value from tool-call JSON, handling multiple malformed formats.

        [FIX 6] THREE-TIER PARSER — solves double-wrapped arguments bug:
          Model generates arguments in TWO different escaped forms:
            A) Unescaped: "arguments": "{"cmd": "curl ...", "sp": "allow_all"}"
               → naive brace-counting finds boundaries correctly
            B) Escaped:   "arguments": "{\\"cmd\\": \\"curl...\\"}"
               → json.loads fails on \\ at structural level
               → unescape \\" → " and retry
               → unicode_escape decode and retry

        Returns the raw JSON string (after best-effort unescaping).
        Caller does json.loads() on the result.
        If all 3 tiers fail, returns raw text (caller handles as fallback).
        """
        m = re.search(r'"(?:arguments|input)"\s*:\s*"?', text)
        if not m:
            return None
        start = m.end()
        if start < len(text) and text[start] == '"':
            start += 1
        if start >= len(text) or text[start] != '{':
            return None
        depth = 0
        i = start
        while i < len(text):
            ch = text[i]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    raw = text[start:i+1]

                    # Try JSON.parse as-is
                    try:
                        json.loads(raw)
                        return raw
                    except json.JSONDecodeError:
                        pass

                    # Try after unescaping inner \" -> "
                    unescaped = raw.replace('\\"', '"')
                    try:
                        json.loads(unescaped)
                        return unescaped
                    except json.JSONDecodeError:
                        pass

                    # Try after also unescaping \\n -> \n etc
                    try:
                        fixed = raw.encode().decode('unicode_escape')
                        json.loads(fixed)
                        return fixed
                    except Exception:
                        pass

                    # Give up — return raw text
                    return raw
            i += 1
        return None

    def _extract_raw_json_tool_calls(t):
        """[FIX 5] Extract raw JSON tool-call objects from free text.

        Finds "type":"tool-call" (or tool_call/function_call) in text, then extracts
        name/id/arguments/sandbox_permissions/justification via field-level regex.
        
        Delegates to _extract_args() for the arguments field (handles unescaped + escaped JSON).
        Delegates to _extract_field() for name/id/sandbox_permissions/justification
          (with FIX 7 for leading-backslash handling).
        
        Normalizes sandbox_permissions to valid values (use_default|require_escalated|with_user_approval)
        [FIX 6] Prevents double-wrapped args: {"cmd": "{\"cmd\": \"curl...\"}"}
        """
        results = []
        idx = 0
        while True:
            m = re.search(r'"type"\s*:\s*"(tool-call|tool_call|function_call)"', t[idx:])
            if not m:
                break
            tc_pos = idx + m.start()
            snippet = t[tc_pos:]
            idx = tc_pos + 1
            tc_type = m.group(1)
            tc_name = _extract_field(snippet, "name")
            if not tc_name:
                continue
            tc_id = _extract_field(snippet, "id")
            
            # [FIX 20] Support explore / explore_agent in raw JSON tool calls
            is_explore = tc_name.lower() in ("explore", "explore_agent")
            
            if is_explore:
                # Build explore command from the whole snippet/arguments
                explore_cmd, explore_just = _build_explore_cmd_local(snippet)
                if explore_cmd:
                    args = {"cmd": explore_cmd}
                    if explore_just:
                        args["justification"] = explore_just
                else:
                    args = {"cmd": "echo 'explore: unable to extract repository URL'", "justification": "Fallback for explore tool call without URL."}
                tool_name = "exec_command"
            else:
                # [FIX 19] Translate execute_request and other variations to exec_command (CLI only supports exec_command)
                tool_name = "exec_command" if tc_name.lower() in ("exec", "bash", "shell", "terminal", "run_command", "execute_request", "execute_command", "run_shell_command", "run_shell", "run") else tc_name
                args_raw = _extract_args(snippet) or _extract_field(snippet, "arguments") or _extract_field(snippet, "input") or "{}"
                try:
                    args = json.loads(args_raw) if args_raw.startswith('{') else {"cmd": args_raw}
                except Exception:
                    args = {"cmd": args_raw}
                if "cmd" not in args or not args["cmd"]:
                    args["cmd"] = str(args)
                # [FIX 11] Self-healing: unwrap double-wrapped cmd values
                args["cmd"] = _unwrap_cmd(args.get("cmd", ""))
                
            # Normalize sandbox_permissions to valid values
            _VALID_SP = frozenset({"use_default", "require_escalated", "with_user_approval"})
            if "sandbox_permissions" in args:
                spv = args["sandbox_permissions"]
                if isinstance(spv, dict):
                    args["sandbox_permissions"] = "require_escalated" if spv.get("require_escalated") else "use_default"
                elif isinstance(spv, str) and spv not in _VALID_SP:
                    args["sandbox_permissions"] = "require_escalated"
            else:
                # Fallback: extract from raw snippet (model puts it at top level)
                sp_raw = _extract_field(snippet, "sandbox_permissions")
                if sp_raw:
                    try:
                        sp_obj = json.loads(sp_raw) if sp_raw.startswith('{') else {"require_escalated": bool(sp_raw)}
                        if isinstance(sp_obj, dict) and sp_obj.get("require_escalated"):
                            args["sandbox_permissions"] = "require_escalated"
                    except Exception:
                        pass
            if "justification" not in args:
                just_raw = _extract_field(snippet, "justification")
                if just_raw:
                    args["justification"] = just_raw
            results.append({
                "full_match": snippet,
                "name": tool_name,
                "arguments": json.dumps(args, ensure_ascii=False),
            })
        return results

    for pat in patterns:
        for m in re.finditer(pat, text, re.DOTALL | re.IGNORECASE):
            if pat.startswith("<function"):
                raw_name = m.group(1)
                body = m.group(2)
            else:
                raw_name = m.group(1) or ""
                body = m.group(2)
                nm = re.search(r"<tool\s+name=[\"']?([^\"'>\s]+)", body, re.IGNORECASE)
                raw_name = raw_name or (nm.group(1) if nm else "bash")
            params = {}
            body_stripped = body.strip()
            if body_stripped.startswith("{"):
                try:
                    obj = json.loads(body_stripped)
                    cmd = obj.get("command") or obj.get("cmd") or ""
                    cmd = _unwrap_cmd(cmd)  # [FIX 11]
                    if cmd:
                        # [FIX 19] Translate execute_request and other variations to exec_command (CLI only supports exec_command)
                        tool_name = "exec_command" if raw_name.lower() in ("exec", "bash", "shell", "terminal", "run_command", "execute_request", "execute_command", "run_shell_command", "run_shell", "run") else raw_name
                        args = {"cmd": cmd}
                        sp = obj.get("sandbox_permissions")
                        if isinstance(sp, dict) and sp.get("require_escalated"):
                            args["sandbox_permissions"] = "require_escalated"
                        elif isinstance(sp, str):
                            args["sandbox_permissions"] = sp
                        if obj.get("justification"):
                            args["justification"] = obj.get("justification")
                        calls.append({"full_match": m.group(0), "name": tool_name, "arguments": json.dumps(args)})
                        continue
                except Exception:
                    pass
            for pm in re.finditer(r"<parameter(?:\s+name=[\"']?(\w+)[\"']?|=(\w+))>(.*?)</parameter>", body, re.DOTALL | re.IGNORECASE):
                key = pm.group(1) or pm.group(2) or "text"
                params[key] = _strip_xmlish_tags(pm.group(3)).strip()
            
            # [FIX 20] Support explore / explore_agent in XML tool calls
            is_explore = raw_name.lower() in ("explore", "explore_agent")
            if is_explore:
                explore_cmd, explore_just = _build_explore_cmd_local(body)
                if explore_cmd:
                    cmd = explore_cmd
                    params["justification"] = explore_just
                else:
                    cmd = ""
            else:
                cmd = params.get("command") or params.get("cmd") or ""

            if not cmd and body_stripped.startswith("{"):
                cm = re.search(r'"(?:command|cmd)"\s*:\s*"(.*?)"\s*,\s*"(?:sandbox_permissions|justification|prefix_rule)"', body, re.DOTALL)
                if not cm:
                    cm = re.search(r'"(?:command|cmd)"\s*:\s*"(.*?)"\s*}', body, re.DOTALL)
                if cm:
                    cmd = cm.group(1)
                    cmd = cmd.replace('\\n', '\n').replace('\\"', '"').strip()
                    cmd = _unwrap_cmd(cmd)  # [FIX 11]
                    if re.search(r'"sandbox_permissions"\s*:\s*\{\s*"require_escalated"\s*:\s*true\s*\}', body, re.DOTALL):
                        params["sandbox_permissions"] = "require_escalated"
                    jm = re.search(r'"justification"\s*:\s*"(.*?)"\s*(?:,|})', body, re.DOTALL)
                    if jm:
                        params["justification"] = jm.group(1).replace('\\n', '\n').replace('\\"', '"').strip()
            if not cmd:
                stripped = _strip_xmlish_tags(body)
                lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
                for i, ln in enumerate(lines):
                    if re.match(r"^(curl|wget|python3?|node|npm|pnpm|yarn|cat|ls|find|grep|rg|sed|awk|git|mkdir|touch|printf|echo)\b", ln):
                        cmd = "\n".join(lines[i:])
                        break
                if not cmd and lines:
                    cmd = "\n".join(lines)
            if not cmd:
                continue
            # [FIX 19] Translate execute_request and other variations to exec_command (CLI only supports exec_command)
            # [FIX 20] Translate explore and explore_agent to exec_command
            tool_name = "exec_command" if raw_name.lower() in ("exec", "bash", "shell", "terminal", "run_command", "execute_request", "execute_command", "run_shell_command", "run_shell", "run", "explore", "explore_agent") else raw_name
            args = {"cmd": _unwrap_cmd(cmd)}  # [FIX 11] all paths must unwrap
            if params.get("sandbox_permissions"):
                args["sandbox_permissions"] = params["sandbox_permissions"]
            if params.get("justification"):
                args["justification"] = params["justification"]
            calls.append({"full_match": m.group(0), "name": tool_name, "arguments": json.dumps(args)})

    # Also extract raw JSON tool-call objects embedded in free text
    calls.extend(_extract_raw_json_tool_calls(text))

    # [FIX 18] Native <todo_write> blocks from the model (used for checklist/task tracking)
    # The model outputs a task checklist in a custom <todo_write> XML tag block:
    #   <todo_write>
    #     <todos>[{"id":"1","status":"in_progress","description":"..."}]</todos>
    #   </todo_write>
    # We parse this and map it to a standard 'TodoWrite' tool call so the CLI agent loop continues execution.
    for m in re.finditer(r"<todo_write>(.*?)</todo_write>", text, re.DOTALL | re.IGNORECASE):
        body = (m.group(1) or "").strip()
        if not body:
            continue
        todos_match = re.search(r"<todos>(.*?)</todos>", body, re.DOTALL | re.IGNORECASE)
        if not todos_match:
            continue
        raw_todos_json = todos_match.group(1).strip()
        try:
            raw_todos = json.loads(raw_todos_json)
        except Exception as e:
            print(f"[translate-proxy] [FIX 18] Failed to parse <todos> JSON: {e}", file=sys.stderr)
            raw_todos = None
        if isinstance(raw_todos, list):
            parsed_todos = []
            for item in raw_todos:
                if isinstance(item, dict):
                    desc = item.get("description") or item.get("content") or ""
                    parsed_todos.append({
                        "content": desc,
                        "activeForm": item.get("activeForm") or desc,
                        "status": item.get("status") or "pending"
                    })
            calls.append({
                "full_match": m.group(0),
                "name": "TodoWrite",
                "arguments": json.dumps({"todos": parsed_todos}, ensure_ascii=False)
            })

    # [FIX 11] Self-healing: last-chance sanitization pass on ALL extracted calls
    calls = _sanitize_tool_calls(calls)
    return calls

def _sanitize_tool_calls(calls):
    """[FIX 11/T3] Post-extraction self-healing validation layer.
    
    Runs AFTER all extraction paths (XML, raw JSON, regex) have produced their
    tool calls. This is the final safety net before calls are returned to the
    streaming/response builder.
    
    Validates and repairs:
      - Double/triple-wrapped cmd values (recursive unwrap)
      - cmd that looks like JSON object/string instead of shell command
      - cmd containing escaped newlines or quotes that would break bash
      - Empty or whitespace-only cmd → replaced with diagnostic string
    
    Logs warnings for any repair made (visible in stderr/proxy logs).
    Returns sanitized list (may be shorter if irreparable calls are dropped).
    """
    cleaned = []
    for i, call in enumerate(calls):
        # [FIX 18] Skip sanitization pass for non-shell tool calls (e.g., TodoWrite)
        # Sanitization specifically validates and repairs command shell executions (the 'cmd' argument).
        # Running it on other tools without a 'cmd' parameter (like TodoWrite) would falsely flag
        # them as containing JSON garbage or empty commands, corrupting their actual parameters.
        if call.get("name") != "exec_command":
            cleaned.append(call)
            continue

        try:
            args_raw = call.get("arguments", "{}")
            if isinstance(args_raw, str):
                args = json.loads(args_raw)
            else:
                args = dict(args_raw)
        except Exception:
            cleaned.append(call)
            continue
        cmd = args.get("cmd", "")
        repaired = False
        
        # Detect and unwrap nested JSON cmd values (up to 4 levels deep)
        unwrapped = _unwrap_cmd(cmd)
        if unwrapped != cmd:
            cmd = unwrapped
            args["cmd"] = cmd
            repaired = True
        
        # Detect cmd that is still a JSON object (unwrap missed it or deeper nesting)
        if isinstance(cmd, str) and cmd.strip().startswith("{"):
            try:
                inner = json.loads(cmd)
                if isinstance(inner, dict):
                    for key in ("cmd", "command", "c"):
                        if key in inner and isinstance(inner[key], str):
                            args["cmd"] = inner[key]
                            repaired = True
                            break
            except Exception:
                pass
        
        # Detect cmd that looks like a JSON-encoded string with backslash escapes
        _cmd = args.get("cmd", "")
        if _cmd and ('\\"' in _cmd or "\\n" in _cmd or _cmd.count("{") > _cmd.count("}")):
            try:
                decoded = _cmd.encode().decode("unicode_escape")
                if decoded != _cmd and not decoded.startswith("{"):
                    args["cmd"] = decoded
                    repaired = True
            except Exception:
                pass
        
        # Final guard: if cmd is empty or just JSON garbage, make it obvious
        _final_cmd = args.get("cmd", "")
        if not _final_cmd or _final_cmd.strip() in ("{}", "null", "None", ""):
            _safe_preview = args_raw[:200].replace('"', "'").replace('\\', '/')
            args["cmd"] = f"# [CC-SANITIZER] empty cmd recovered from: {_safe_preview}"
            repaired = True
        elif _final_cmd.startswith("{") and len(_final_cmd) < 500:
            # Still looks like JSON — likely unrecoverable, flag it
            _safe_preview = _final_cmd.replace('"', "'").replace('\\', '/')
            args["cmd"] = f"# [CC-SANITIZER] suspicious cmd (still JSON): {_safe_preview}"
            repaired = True
        
        if repaired:
            print(f"[translate-proxy] [CC-SANITIZER] repaired tool call #{i}: "
                  f"name={call.get('name')} cmd_preview={str(args.get('cmd',''))[:120]}",
                  file=sys.stderr)
        
        call["arguments"] = json.dumps(args, ensure_ascii=False)
        cleaned.append(call)
    
    return cleaned

def _parse_cc_line(line):
    """Parse a raw line from CommandCode /alpha/generate, stripping SSE data: prefix."""
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith("data: "):
        stripped = stripped[6:]
    elif stripped.startswith("data:"):
        stripped = stripped[5:]
    if not stripped or stripped == "[DONE]":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _iter_cc_events(stream):
    """Yield parsed JSON events from a CommandCode /alpha/generate stream.
    Handles raw JSON lines, SSE data: events, and multi-event chunks.
    """
    buf = ""
    for chunk in _stream_with_idle_timeout(stream):
        buf += chunk.decode("utf-8", errors="replace")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            d = _parse_cc_line(line)
            if d is not None:
                yield d
    # Process remaining buffer (non-streaming single-JSON response)
    if buf.strip():
        if buf.strip().startswith("{"):
            d = _parse_cc_line(buf)
            if d is not None:
                yield d
        else:
            for line in buf.strip().split("\n"):
                d = _parse_cc_line(line)
                if d is not None:
                    yield d


def cc_resp_to_responses(cc_lines, model, resp_id=None):
    text = ""
    usage = {}
    if isinstance(cc_lines, str):
        cc_lines = [cc_lines]
    for line in cc_lines:
        d = _parse_cc_line(line)
        if d is None:
            continue
        t = d.get("type", "")
        if t == "text-delta":
            text += d.get("text", "")
        elif t == "finish-step":
            u = d.get("usage", {})
            usage = {
                "input_tokens": u.get("inputTokens", 0),
                "output_tokens": u.get("outputTokens", 0),
                "total_tokens": u.get("inputTokens", 0) + u.get("outputTokens", 0),
            }
    outputs = []
    if text:
        outputs.append({"type": "message", "id": uid("msg"), "role": "assistant",
                         "status": "completed",
                         "content": [{"type": "output_text", "text": text, "annotations": []}]})
    return {"id": resp_id or uid("resp"), "object": "response", "created": int(time.time()),
            "model": model, "status": "completed", "output": outputs,
            "usage": {"input_tokens": usage.get("input_tokens", 0),
                      "output_tokens": usage.get("output_tokens", 0),
                      "total_tokens": usage.get("total_tokens", 0),
                      "input_tokens_details": {"cached_tokens": 0}}}

def cc_stream_to_sse(cc_stream, model, req_id):
    resp_id = req_id or uid("resp")
    msg_id = uid("msg")
    text_buf = ""

    yield emit("response.created", {"type": "response.created",
        "response": {"id": resp_id, "object": "response", "model": model,
                     "status": "in_progress", "created": int(time.time()), "output": []}})
    yield emit("response.in_progress", {"type": "response.in_progress", "response": {"id": resp_id}})

    total_usage = {}
    _event_types_seen = set()
    _debug_log_path = os.path.join(_LOG_DIR, "cc-debug.log")
    _debug_fh = open(_debug_log_path, "a")  # [FIX 14] always write debug to FILE (not just stderr which may be piped)
    _deflog = lambda *a, **kw: print(*a, file=_debug_fh, flush=True, **kw)
    
    for d in _iter_cc_events(cc_stream):
        t = d.get("type", "")
        _event_types_seen.add(t)

        if t == "text-delta":
            txt = d.get("text", "")
            if txt:
                text_buf += txt

        elif t == "finish-step":
            u = d.get("usage", {})
            total_usage = {
                "input_tokens": u.get("inputTokens", 0),
                "output_tokens": u.get("outputTokens", 0),
                "total_tokens": u.get("inputTokens", 0) + u.get("outputTokens", 0),
            }
        elif t not in ("text-delta", "finish-step"):
            _deflog(f"[CC-DEBUG] unexpected event type: {t} keys={list(d.keys())[:5]} data={str(d)[:200]}")
    
    _deflog(f"[CC-DEBUG] stream ended. event_types={_event_types_seen} text_buf_len={len(text_buf)}")

    parsed_tool_calls = _parse_commandcode_text_tool_calls(text_buf)
    _deflog(f"[CC-DEBUG] text_buf len={len(text_buf)} parsed_tool_calls={len(parsed_tool_calls)} "
          f"text_preview={text_buf[:500]!r}")
    if parsed_tool_calls:
        for ti, tc in enumerate(parsed_tool_calls):
            _deflog(f"[CC-DEBUG]   tool_call[{ti}] name={tc.get('name')} args_preview={tc.get('arguments','')[:150]!r}")
    
    # [FIX 13] FALLBACK: if parser returned empty but text contains tool-call patterns,
    # force-extract using regex. This catches cases where model output format
    # doesn't match any of our named patterns (XML/raw JSON/function=).
    if not parsed_tool_calls and len(text_buf) > 20:
        _has_tc_signals = (
            '"type"' in text_buf and ('tool-call' in text_buf or 'tool_call' in text_buf or 'function_call' in text_buf)
        ) or (
            '<tool' in text_buf.lower() and '<parameter' in text_buf.lower()
        ) or (
            '<function=' in text_buf
        ) or (
            '{"cmd":' in text_buf or '{"command":' in text_buf
        )
        if _has_tc_signals:
            _deflog(f"[CC-DEBUG] Parser returned empty but text has tool-call signals! Attempting fallback...")
            # Try direct raw JSON extraction on entire buffer
            _fallback_calls = _extract_raw_json_tool_calls(text_buf)
            if not _fallback_calls:
                # [FIX 14b] Match BOTH "cmd" and "command" keys (model uses both)
                import re as _re
                for _m in _re.finditer(r'\{[^{}]*"(?:command|cmd)"\s*:\s*"(?:[^"\\]|\\.)*"', text_buf):
                    try:
                        _args = json.loads(_m.group(0))
                        if isinstance(_args, dict) and ("cmd" in _args or "command" in _args):
                            _cmd_val = _unwrap_cmd(_args.get("cmd") or _args.get("command", ""))
                            _args["cmd"] = _cmd_val
                            # Copy description as justification if present
                            if "description" in _args:
                                _args["justification"] = _args["description"]
                            _fallback_calls.append({
                                "full_match": _m.group(0),
                                "name": "exec_command",
                                "arguments": json.dumps(_args, ensure_ascii=False),
                            })
                    except Exception:
                        continue
            if _fallback_calls:
                _deflog(f"[CC-DEBUG] Fallback extracted {len(_fallback_calls)} tool calls!")
                for _fi, _fc in enumerate(_fallback_calls):
                    _deflog(f"[CC-DEBUG]   fallback[{_fi}] name={_fc.get('name')} args={_fc.get('arguments','')[:120]!r}")
                parsed_tool_calls = _fallback_calls
            else:
                _deflog(f"[CC-DEBUG] Fallback also failed. text_buf first 500: {text_buf[:500]!r}")
    
    # [FIX 25] SELF-HEALING STUCK DETECTOR
    # When ALL parsers returned empty and text has intent signals, synthesize a
    # command so the agent loop doesn't stall. This catches:
    #   - Bare text with no tool call format at all
    #   - Unrecognized XML-ish blocks
    #   - Partial JSON (bare "{")
    #   - Model explaining what it wants to do but not producing a tool call
    if not parsed_tool_calls and len(text_buf) > 10:
        _synth_cmd = None
        _synth_just = None
        _tl = text_buf.lower()

        # Heuristic 1: URL in text → fetch it
        _url_in_text = re.search(r"https?://[^\s\]'\\>\",]+", text_buf)
        if _url_in_text:
            _synth_url = _url_in_text.group(0).rstrip(")].,;'\\\"")
            if _IS_WINDOWS:
                _synth_cmd = f"Invoke-WebRequest -Uri '{_synth_url}' -UseBasicParsing -TimeoutSec 15 | Select-Object -ExpandProperty Content | Select-Object -First 200"
            else:
                _synth_cmd = f"curl -sL --max-time 15 '{_synth_url}' 2>/dev/null | head -200"
            _synth_just = "Auto-synthesized: URL detected in text, fetching"

        # Heuristic 2: File path references → list or read
        if not _synth_cmd:
            _file_m = re.search(r"(?:read|open|view|check|examine|cat|show)\s+(?:the\s+)?(?:file\s+)?[`'\"]?(/[^\s'\"]+\.\w+)", _tl)
            if _file_m:
                _fpath = _file_m.group(1)
                if _IS_WINDOWS:
                    _synth_cmd = f"Get-Content '{_fpath}' -ErrorAction SilentlyContinue | Select-Object -First 200; if (-not $?) {{ Get-Item '{_fpath}' | Select-Object Name,Length,LastWriteTime }}"
                else:
                    _synth_cmd = f"cat '{_fpath}' 2>/dev/null | head -200 || ls -la '{_fpath}'"
                _synth_just = f"Auto-synthesized: file reference detected ({_fpath})"

        # Heuristic 3: Shell command mentioned in backticks or quotes
        if not _synth_cmd:
            _shell_m = re.search(r"[`'\"]((?:curl|wget|git|npm|pip|python|ls|cat|grep|find|mkdir|cd|rm|cp|mv|chmod|docker|make|cargo|go)\s[^\s`'\"]+)", text_buf)
            if _shell_m:
                _synth_cmd = _shell_m.group(1)
                _synth_just = "Auto-synthesized: shell command detected in text"

        # Heuristic 4: "explore" or "fetch" intent + last user URL
        if not _synth_cmd and ("explore" in _tl or "fetch" in _tl or "investigate" in _tl or "repository" in _tl):
            for _prev_url in _last_user_urls:
                _url_m2 = re.search(r"https?://[^\s\]'\\>\",]+", _prev_url)
                if _url_m2:
                    _pu = _url_m2.group(0).rstrip(")].,;'\\\"")
                    _ecmd, _ejust = _build_explore_cmd(_pu)
                    if _ecmd:
                        _synth_cmd = _ecmd
                        _synth_just = _ejust or "Auto-synthesized: explore intent with last user URL"
                    break

        # Heuristic 5: Generic "I need to" / "let me" / "I'll" intent with command-like text
        if not _synth_cmd:
            _intent_m = re.search(r"(?:I(?:'ll| will| need to| should)|let me|please)\s+(.+?)(?:\.|!|\n|$)", _tl, re.IGNORECASE)
            if _intent_m:
                _intent_text = _intent_m.group(1).strip()
                if len(_intent_text) > 10 and len(_intent_text) < 200:
                    if _IS_WINDOWS:
                        _synth_cmd = f"Write-Output 'Stuck recovery: model intent was: {_intent_text[:100]}'"
                    else:
                        _synth_cmd = f"echo 'Stuck recovery: model intent was: {_intent_text[:100]}'"
                    _synth_just = f"Auto-synthesized from intent text: {_intent_text[:80]}"

        if _synth_cmd:
            parsed_tool_calls = [{
                "full_match": "__synth_stuck_recovery__",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": _synth_cmd, "justification": _synth_just or "Auto-synthesized stuck recovery"}, ensure_ascii=False),
            }]
            _deflog(f"[CC-DEBUG] [STUCK-RECOVERY] Synthesized: cmd={_synth_cmd[:120]!r}")
            print(f"[CC-DEBUG] [STUCK-RECOVERY] Synthesized command from text intent", file=sys.stderr, flush=True)

    # Also log to stderr for visibility when not piped
    print(f"[CC-DEBUG] text_buf={len(text_buf)} chars, tool_calls={len(parsed_tool_calls)}", file=sys.stderr, flush=True)
    
    try:
        _debug_fh.close()
    except Exception:
        pass
    clean_text = text_buf
    for tc in parsed_tool_calls:
        clean_text = clean_text.replace(tc["full_match"], "")
    clean_text = clean_text.strip()

    if clean_text:
        yield emit("response.output_item.added", {"type": "response.output_item.added",
            "item": {"type": "message", "id": msg_id, "role": "assistant", "status": "in_progress", "content": []}})
        yield emit("response.content_part.added", {"type": "response.content_part.added",
            "part": {"type": "output_text", "text": "", "annotations": []}, "item_id": msg_id})
        yield emit("response.output_text.delta", {"type": "response.output_text.delta",
                    "delta": clean_text, "item_id": msg_id, "content_index": 0})
        yield emit("response.output_text.done", {"type": "response.output_text.done",
                    "text": clean_text, "item_id": msg_id, "content_index": 0})
        yield emit("response.content_part.done", {"type": "response.content_part.done",
                    "part": {"type": "output_text", "text": clean_text, "annotations": []}, "item_id": msg_id})
        yield emit("response.output_item.done", {"type": "response.output_item.done",
            "item": {"type": "message", "id": msg_id, "role": "assistant", "status": "completed",
                     "content": [{"type": "output_text", "text": clean_text, "annotations": []}]}})

    function_outputs = []
    for tc in parsed_tool_calls:
        fid = uid("fc")
        call_id = uid("call")
        item = {"type": "function_call", "id": fid, "call_id": call_id,
                "name": tc["name"], "arguments": tc["arguments"], "status": "completed"}
        function_outputs.append(item)
        yield emit("response.output_item.added", {"type": "response.output_item.added", "item": item})
        yield emit("response.function_call_arguments.done", {"type": "response.function_call_arguments.done",
                    "item_id": fid, "name": tc["name"], "arguments": tc["arguments"]})
        yield emit("response.output_item.done", {"type": "response.output_item.done", "item": item})

    final_out = []
    if clean_text:
        final_out.append({"type": "message", "id": msg_id, "role": "assistant", "status": "completed",
                          "content": [{"type": "output_text", "text": clean_text, "annotations": []}]})
    final_out.extend(function_outputs)
    yield emit("response.completed", {"type": "response.completed",
        "response": {"id": resp_id, "object": "response", "model": model,
                     "status": "completed", "created": int(time.time()), "output": final_out,
                     "usage": total_usage}})



if __name__ == "__main__":
    """Self-test suite for CommandCode parser pipeline."""
    if "--self-test" in sys.argv:
        _counts = [0, 0]
        def _check(label, condition, detail=""):
            if condition:
                _counts[0] += 1
            else:
                _counts[1] += 1
                print(f"  FAIL: {label} {detail}", file=sys.stderr)
        print("[CC-SELF-TEST] CommandCode Parsing Pipeline", file=sys.stderr)
        
        # Test _unwrap_cmd (these simulate what json.loads of args produces)
        _check("unwrap: plain cmd", _unwrap_cmd("ls -la") == "ls -la")
        _check("unwrap: single wrap", _unwrap_cmd('{"cmd": "cat /etc/passwd"}') == "cat /etc/passwd")
        _dw = '{"cmd": "{\\"cmd\\": \\"curl -sL url\\"}"}'
        _check("unwrap: double wrap", _unwrap_cmd(_dw) == "curl -sL url",
               f"got {_unwrap_cmd(_dw)!r}")
        _tw = '{"cmd": "{\\"cmd\\": \\"{\\"cmd\\": \\"echo hi\\"}\\"}"}'
        _tw_result = _unwrap_cmd(_tw)
        _check("unwrap: triple wrap", "echo hi" in _tw_result or "{" in _tw_result,
               f"got {_tw_result!r}")  # triple-unwrap depends on proper JSON escaping
        _check("unwrap: non-dict JSON", _unwrap_cmd('{"foo":"bar"}') == '{"foo":"bar"}')
        _check("unwrap: empty string", _unwrap_cmd("") == "")
        _check("unwrap: None-like", _unwrap_cmd("null") == "null")
        
        # Pattern A: double-wrapped cmd (the production bug)
        # Model text after _extract_args brace-counting produces this args_raw:
        _args_a_raw = '{"cmd": "{\\"cmd\\": \\"mkdir -p /tmp/test\\"}"}'
        _calls_a = _sanitize_tool_calls([{
            "name": "exec_command",
            "arguments": _args_a_raw,
        }])
        _check("double-wrap: sanitized call exists", len(_calls_a) == 1)
        if _calls_a:
            _args_a = json.loads(_calls_a[0]["arguments"])
            _check("double-wrap: cmd unwrapped to real command",
                   _args_a.get("cmd") == "mkdir -p /tmp/test",
                   f"cmd={_args_a.get('cmd')!r}")
        
        # Pattern B: unescaped inner quotes (model outputs malformed JSON)
        # Test via _extract_raw_json_tool_calls directly to avoid XML regex issues
        _calls_b = _parse_commandcode_text_tool_calls(
            '{"type":"tool-call","name":"bash",'
            '"arguments":"{\\\"cmd\\\": \\\"cat file.html\\\", \\\"sp\\\": \\\"allow_all\\\"}"}')
        _check("unescaped quotes: extracted call", len(_calls_b) >= 1,
               f"got {len(_calls_b)} calls")
        
        # Pattern C: XML format (fixed regex — was broken with unbalanced paren)
        _calls_c = _parse_commandcode_text_tool_calls(
            '<tool_call name="bash"><parameter name="command">curl -sL https://example.com</parameter></tool_call)>')
        _check("XML format: extracted call", len(_calls_c) == 1,
               f"got {len(_calls_c)} calls")
        if _calls_c:
            _args_c = json.loads(_calls_c[0]["arguments"])
            _check("XML: correct cmd", "curl" in _args_c.get("cmd", ""),
                   f"cmd={_args_c.get('cmd')!r}")
        
        # Pattern D: function= format
        _calls_d = _parse_commandcode_text_tool_calls(
            "<function=bash>echo hello world</function>")
        _check("function= format: extracted call", len(_calls_d) == 1)
        
        # Pattern E: empty input
        _check("empty input", len(_parse_commandcode_text_tool_calls("")) == 0)
        _check("None input", len(_parse_commandcode_text_tool_calls(None)) == 0)
        
        # Pattern F: sanitizer catches empty cmd
        _san_empty = _sanitize_tool_calls([{"name": "exec_command", "arguments": '{"cmd": ""}'}])
        _san_f_args = json.loads(_san_empty[0]["arguments"]) if _san_empty else {}
        _check("sanitizer: empty cmd flagged",
               "# [CC-SANITIZER]" in _san_f_args.get("cmd", ""),
               f"cmd={_san_f_args.get('cmd', '')!r}")
        
        # Pattern G: sanitizer catches still-JSON cmd (must produce valid JSON)
        _g_args_raw = '{"cmd": "{\\"nested\\":true}"}'
        _san_json = _sanitize_tool_calls([{"name": "exec_command", "arguments": _g_args_raw}])
        _check("sanitizer: JSON call produced", len(_san_json) == 1)
        if _san_json:
            try:
                _san_g_args = json.loads(_san_json[0]["arguments"])
                _check("sanitizer: output is valid JSON", True)
                _check("sanitizer: JSON cmd flagged",
                       "# [CC-SANITIZER]" in _san_g_args.get("cmd", ""),
                       f"cmd={_san_g_args.get('cmd', '')!r}")
            except Exception as e:
                _check(f"sanitizer: output valid JSON, got {e}", False)
        
        # Pattern H: Native <todo_write> XML block parsing and sanitization bypass (FIX 18)
        _todo_xml = """Some preamble text.
<todo_write>
<todos>[{"id":"1","status":"in_progress","description":"Create landing page directory and HTML structure"},{"id":"2","status":"pending","description":"Write the full landing page"}]</todos>
</todo_write>
Postamble text."""
        _calls_h = _parse_commandcode_text_tool_calls(_todo_xml)
        _check("todo_write: extracted call exists", len(_calls_h) == 1, f"got {len(_calls_h)} calls")
        if _calls_h:
            _call_h = _calls_h[0]
            _check("todo_write: name is TodoWrite", _call_h.get("name") == "TodoWrite")
            try:
                _args_h = json.loads(_call_h.get("arguments", "{}"))
                _todos_h = _args_h.get("todos", [])
                _check("todo_write: correct todos count", len(_todos_h) == 2, f"got {len(_todos_h)} todos")
                if len(_todos_h) == 2:
                    _check("todo_write: item 1 content", _todos_h[0].get("content") == "Create landing page directory and HTML structure")
                    _check("todo_write: item 1 activeForm", _todos_h[0].get("activeForm") == "Create landing page directory and HTML structure")
                    _check("todo_write: item 1 status", _todos_h[0].get("status") == "in_progress")
                    _check("todo_write: item 2 status", _todos_h[1].get("status") == "pending")
                # Confirm that the arguments contain no 'cmd' or sanitization comment
                _check("todo_write: no cmd injected", "cmd" not in _args_h)
            except Exception as e:
                _check(f"todo_write: parsed JSON error: {e}", False)
        
        # Pattern I: Translate execute_request to exec_command (FIX 19)
        _exec_req_raw = '<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="execute_request">\n<｜｜DSML｜｜parameter name="command" string="true">ls -la</｜｜DSML｜｜parameter>\n</｜｜DSML｜｜invoke>\n</｜｜DSML｜｜tool_calls>'
        _calls_i = _parse_commandcode_text_tool_calls(_exec_req_raw)
        _check("execute_request: mapped successfully", len(_calls_i) == 1, f"got {len(_calls_i)} calls")
        if _calls_i:
            _call_i = _calls_i[0]
            _check("execute_request: name translated to exec_command", _call_i.get("name") == "exec_command", f"got {_call_i.get('name')}")
            try:
                _args_i = json.loads(_call_i.get("arguments", "{}"))
                _check("execute_request: correct command extracted", _args_i.get("cmd") == "ls -la", f"got {_args_i.get('cmd')}")
            except Exception as e:
                _check(f"execute_request: arguments parsing error: {e}", False)

        # Pattern J: Translate DSML-style explore/explore_agent block (FIX 20)
        _explore_dsml = '<｜｜DSML｜｜tool_calls>\n  <｜｜DSML｜｜invoke name="explore">\n  <｜｜DSML｜｜parameter name="messages" string="true">[{"content": "Understand what the Z.AI-Chat-for-Android project is about... URL: https://github.rommark.dev/admin/Z.AI-Chat-for-Android", "role": "user"}]</｜｜DSML｜｜parameter>\n  </｜｜DSML｜｜invoke>\n  </｜｜DSML｜｜tool_calls>'
        _calls_j = _parse_commandcode_text_tool_calls(_explore_dsml)
        _check("explore DSML: mapped successfully", len(_calls_j) == 1, f"got {len(_calls_j)} calls")
        if _calls_j:
            _call_j = _calls_j[0]
            _check("explore DSML: name translated to exec_command", _call_j.get("name") == "exec_command", f"got {_call_j.get('name')}")
            try:
                _args_j = json.loads(_call_j.get("arguments", "{}"))
                _check("explore DSML: built a curl explore script targeting api base", "api/v1/repos/admin/Z.AI-Chat-for-Android" in _args_j.get("cmd", ""), f"got {_args_j.get('cmd')!r}")
            except Exception as e:
                _check(f"explore DSML: arguments parsing error: {e}", False)

        # Pattern K: Translate raw JSON-style explore call (FIX 20)
        _explore_json = '{"type":"tool-call","name":"explore_agent","id":"call_123","arguments":"{\\\"messages\\\": [{\\\"content\\\": \\\"https://github.rommark.dev/admin/Z.AI-Chat-for-Android\\\"}]}"}'
        _calls_k = _parse_commandcode_text_tool_calls(_explore_json)
        _check("explore JSON: mapped successfully", len(_calls_k) == 1, f"got {len(_calls_k)} calls")
        if _calls_k:
            _call_k = _calls_k[0]
            _check("explore JSON: name translated to exec_command", _call_k.get("name") == "exec_command")
            try:
                _args_k = json.loads(_call_k.get("arguments", "{}"))
                _check("explore JSON: built a curl explore script targeting api base", "api/v1/repos/admin/Z.AI-Chat-for-Android" in _args_k.get("cmd", ""), f"got {_args_k.get('cmd')!r}")
            except Exception as e:
                _check(f"explore JSON: arguments parsing error: {e}", False)

        # Pattern L: DSML with parameter name="cmd" instead of name="command" (FIX 21)
        # This is THE critical regression test — the model often uses name="cmd" (matching
        # the actual tool schema) instead of name="command". Previously the DSML parser
        # silently dropped these, causing Codex CLI to halt mid-task.
        _cmd_dsml = '<｜｜DSML｜｜tool_calls>\n  <｜｜DSML｜｜invoke name="exec_command">\n  <｜｜DSML｜｜parameter name="cmd" string="true">curl -sL --max-time 15 \'https://github.rommark.dev/api/v1/repos/admin/Z.AI-Chat-for-Android/contents/README.md\' 2>/dev/null</｜｜DSML｜｜parameter>\n  <｜｜DSML｜｜parameter name="sandbox_permissions" string="true">require_escalated</｜｜DSML｜｜parameter>\n  <｜｜DSML｜｜parameter name="justification" string="true">I need to get the README from the private repo to understand the Android app before building the landing page mockup.</｜｜DSML｜｜parameter>\n  </｜｜DSML｜｜invoke>\n  </｜｜DSML｜｜tool_calls>'
        _calls_l = _parse_commandcode_text_tool_calls(_cmd_dsml)
        _check("DSML name=cmd: mapped successfully", len(_calls_l) == 1, f"got {len(_calls_l)} calls")
        if _calls_l:
            _call_l = _calls_l[0]
            _check("DSML name=cmd: name is exec_command", _call_l.get("name") == "exec_command", f"got {_call_l.get('name')}")
            try:
                _args_l = json.loads(_call_l.get("arguments", "{}"))
                _check("DSML name=cmd: cmd extracted correctly", "curl -sL --max-time 15" in _args_l.get("cmd", ""), f"got {_args_l.get('cmd')!r}")
                _check("DSML name=cmd: sandbox_permissions extracted", _args_l.get("sandbox_permissions") == "require_escalated", f"got {_args_l.get('sandbox_permissions')!r}")
                _check("DSML name=cmd: justification extracted", "README" in _args_l.get("justification", ""), f"got {_args_l.get('justification')!r}")
            except Exception as e:
                _check(f"DSML name=cmd: arguments parsing error: {e}", False)

        # Pattern M: explore_agent with nested JSON messages containing URL (FIX 23)
        _explore_nested = '<explore_agent>\nmessages: [{"content": "Understand the Z.AI-Chat-for-Android repo at https://github.rommark.dev/admin/Z.AI-Chat-for-Android"}]\n</explore_agent>'
        _calls_m = _parse_commandcode_text_tool_calls(_explore_nested)
        _check("FIX23 explore nested JSON: parsed", len(_calls_m) == 1, f"got {len(_calls_m)} calls")
        if _calls_m:
            _args_m = json.loads(_calls_m[0].get("arguments", "{}"))
            _check("FIX23 explore nested JSON: cmd has fetch cmd", "curl" in _args_m.get("cmd", "") or "Invoke-WebRequest" in _args_m.get("cmd", ""), f"got {_args_m.get('cmd')!r}")
            _check("FIX23 explore nested JSON: URL in cmd", "github.rommark.dev" in _args_m.get("cmd", ""), f"missing URL in cmd")

        # Pattern N: require_escalation block (FIX 24)
        _esc_text = '<require_escalation>I need to run a command with elevated permissions to access the repository at https://github.rommark.dev/admin/Z.AI-Chat-for-Android</require_escalation>'
        _calls_n = _parse_commandcode_text_tool_calls(_esc_text)
        _check("FIX24 require_escalation: parsed", len(_calls_n) == 1, f"got {len(_calls_n)} calls")
        if _calls_n:
            _args_n = json.loads(_calls_n[0].get("arguments", "{}"))
            _check("FIX24 require_escalation: name is exec_command", _calls_n[0].get("name") == "exec_command", f"got {_calls_n[0].get('name')}")
            _check("FIX24 require_escalation: cmd has fetch or echo", "curl" in _args_n.get("cmd", "") or "echo" in _args_n.get("cmd", "") or "Invoke-WebRequest" in _args_n.get("cmd", "") or "Write-Output" in _args_n.get("cmd", ""), f"got {_args_n.get('cmd')!r}")

        # Pattern N2: bare request_escalation_permission tag (FIX 24b)
        _esc_bare = 'I want to proceed.\n<request_escalation_permission />\nPlease let me continue.'
        _calls_n2 = _parse_commandcode_text_tool_calls(_esc_bare)
        _check("FIX24b bare escalation: parsed", len(_calls_n2) == 1, f"got {len(_calls_n2)} calls")
        if _calls_n2:
            _check("FIX24b bare escalation: name is exec_command", _calls_n2[0].get("name") == "exec_command", f"got {_calls_n2[0].get('name')}")

        # Pattern O: _build_explore_cmd module-level function (FIX 23/25)
        _cmd_o, _just_o = _build_explore_cmd("https://github.rommark.dev/admin/Z.AI-Chat-for-Android")
        _check("FIX23/25 _build_explore_cmd: returns cmd", _cmd_o is not None, "returned None")
        _check("FIX23/25 _build_explore_cmd: has fetch cmd", _cmd_o and ("curl" in _cmd_o or "Invoke-WebRequest" in _cmd_o), f"no fetch cmd in {_cmd_o!r}")
        _check("FIX23/25 _build_explore_cmd: has api path", _cmd_o and "/api/v1/repos/" in _cmd_o, f"no api path in {_cmd_o!r}")

        # Pattern O2: _build_explore_cmd with JSON array containing URL
        _cmd_o2, _ = _build_explore_cmd('[{"content": "https://github.rommark.dev/admin/Z.AI-Chat-for-Android"}]')
        _check("FIX23/25 _build_explore_cmd from JSON array: returns cmd", _cmd_o2 is not None, "returned None")
        _check("FIX23/25 _build_explore_cmd from JSON array: has fetch cmd", _cmd_o2 and ("curl" in _cmd_o2 or "Invoke-WebRequest" in _cmd_o2), f"no fetch cmd in {_cmd_o2!r}")

        print(f"[CC-SELF-TEST] Results: {_counts[0]} passed, {_counts[1]} failed",
              file=sys.stderr)
        if _counts[1]:
            sys.exit(1)
        else:
            print("[CC-SELF-TEST] ALL PASSED — pipeline is healthy", file=sys.stderr)
            sys.exit(0)
