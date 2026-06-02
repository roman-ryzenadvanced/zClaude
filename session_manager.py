#!/usr/bin/env python3
"""
Session Manager — Multi-provider session scanner and parser.

Scans local session files from Codex CLI, Claude Code, and Gemini CLI.
Zero external dependencies — pure Python stdlib.

Provider adapters:
  - Codex:     ~/.codex/sessions/**/*.jsonl + archived_sessions/
  - Claude:    ~/.claude/projects/**/*.jsonl (skips agent-* subagents)
  - Gemini:    ~/.gemini/antigravity/brain/*/transcript.jsonl
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ═══════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SessionMeta:
    """Metadata for a single session."""
    provider: str          # "codex" | "claude" | "gemini"
    session_id: str
    title: str             # first user message or project basename
    model: str             # last model used
    project_dir: str       # working directory when session was created
    created_at: float      # unix timestamp (seconds)
    last_active: float     # unix timestamp (seconds)
    file_path: str         # absolute path to .jsonl
    resume_cmd: str        # e.g. "codex resume {id}" or "claude --resume {id}"


@dataclass
class SessionMessage:
    """A single message in a session conversation."""
    role: str              # "user" | "assistant" | "tool" | "system"
    content: str
    timestamp: float = 0.0


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _home() -> Path:
    return Path.home()


def _read_head(path: Path, max_lines: int = 20, max_bytes: int = 16384) -> List[str]:
    """Read first N lines of a file, capped at max_bytes. Returns list of stripped lines."""
    lines = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            total = 0
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                total += len(line)
                if total > max_bytes:
                    break
                lines.append(line.strip())
    except (OSError, UnicodeDecodeError):
        pass
    return lines


def _read_tail(path: Path, max_lines: int = 30) -> List[str]:
    """Read last N lines of a file. Returns list of stripped lines (reversed to chronological)."""
    lines = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        lines = [l.strip() for l in all_lines[-max_lines:] if l.strip()]
    except (OSError, UnicodeDecodeError):
        pass
    return lines


def _read_all_lines(path: Path) -> List[str]:
    """Read all lines from a JSONL file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [line.strip() for line in f if line.strip()]
    except (OSError, UnicodeDecodeError):
        return []


def _parse_json_safe(line: str) -> Optional[dict]:
    """Parse a JSON line, return None on failure."""
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_text_from_content(content) -> str:
    """Extract plain text from a content array (OpenAI/Claude format)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text") or block.get("input_text") or block.get("output_text") or ""
            if text:
                parts.append(text)
    return " ".join(parts)


def _clean_title(text: str) -> str:
    """Clean a session title: strip XML tags, collapse whitespace, remove newlines."""
    # Remove known wrapper tags
    cleaned = re.sub(r"<local-command-caveat>.*?</local-command-caveat>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<ide_selection>.*?</ide_selection>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<command-message>.*?</command-message>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<command-name>.*?</command-name>", "", cleaned, flags=re.DOTALL)
    # Strip any remaining XML tags
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    # Collapse whitespace and newlines
    cleaned = " ".join(cleaned.split())
    return cleaned.strip()


def _truncate(text: str, max_len: int = 160) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _parse_iso_timestamp(ts_str: str) -> float:
    """Parse ISO 8601 timestamp to unix float. Returns 0.0 on failure."""
    if not ts_str:
        return 0.0
    try:
        # Handle various ISO formats: 2026-05-28T14:34:26.729Z, 2026-05-28T16:34:25
        clean = ts_str.replace("Z", "").replace("+00:00", "")
        if "." in clean:
            clean = clean.split(".")[0]
        # Simple parse: YYYY-MM-DDTHH:MM:SS
        import time as _time
        dt = _time.mktime(_time.strptime(clean[:19], "%Y-%m-%dT%H:%M:%S"))
        return dt
    except (ValueError, OverflowError, OSError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════════
# Codex adapter
# ═══════════════════════════════════════════════════════════════════════

def scan_codex() -> List[SessionMeta]:
    """Scan Codex sessions from ~/.codex/sessions/ and archived_sessions/."""
    codex_dir = _home() / ".codex"
    sessions = []
    seen_ids = set()

    for subdir in ("sessions", "archived_sessions"):
        base = codex_dir / subdir
        if not base.is_dir():
            continue
        for jsonl in base.rglob("*.jsonl"):
            meta = _parse_codex_meta(jsonl)
            if meta and meta.session_id not in seen_ids:
                seen_ids.add(meta.session_id)
                sessions.append(meta)

    sessions.sort(key=lambda s: s.last_active, reverse=True)
    return sessions


def _parse_codex_meta(path: Path) -> Optional[SessionMeta]:
    """Extract metadata from a Codex session JSONL (head lines only)."""
    lines = _read_head(path, max_lines=30)
    if not lines:
        return None

    session_id = ""
    cwd = ""
    model_provider = ""
    model = ""
    created_ts = 0.0
    last_ts = 0.0
    first_user_msg = ""

    for line in lines:
        obj = _parse_json_safe(line)
        if not obj:
            continue

        line_type = obj.get("type", "")
        payload = obj.get("payload", {})
        ts = _parse_iso_timestamp(obj.get("timestamp", ""))

        if ts > last_ts:
            last_ts = ts
        if created_ts == 0.0 and ts > 0:
            created_ts = ts

        if line_type == "session_meta":
            session_id = payload.get("id", "")
            cwd = payload.get("cwd", "")
            model_provider = payload.get("model_provider", "")

        elif line_type == "turn_context":
            m = payload.get("model", "")
            if m:
                model = m

        elif line_type == "response_item":
            inner_type = payload.get("type", "")
            if inner_type == "message" and payload.get("role") == "user":
                content = payload.get("content", [])
                text = _extract_text_from_content(content)
                # Skip environment_context blocks
                if text and "<environment_context>" not in text and not first_user_msg:
                    first_user_msg = text

    if not session_id:
        # Fallback: extract ID from filename
        name = path.stem
        parts = name.split("-")
        if len(parts) >= 2:
            session_id = parts[-1] if len(parts[-1]) >= 20 else name

    if not session_id:
        return None

    cleaned_msg = _clean_title(first_user_msg)
    title = _truncate(cleaned_msg, 80) if cleaned_msg else path.stem
    display_model = model or model_provider

    return SessionMeta(
        provider="codex",
        session_id=session_id,
        title=title,
        model=display_model,
        project_dir=cwd,
        created_at=created_ts,
        last_active=last_ts,
        file_path=str(path),
        resume_cmd=f"codex resume {session_id}",
    )


def load_codex_messages(path: str) -> List[SessionMessage]:
    """Load all messages from a Codex session JSONL."""
    messages = []
    lines = _read_all_lines(Path(path))
    for line in lines:
        obj = _parse_json_safe(line)
        if not obj:
            continue

        line_type = obj.get("type", "")
        payload = obj.get("payload", {})
        ts = _parse_iso_timestamp(obj.get("timestamp", ""))

        if line_type == "response_item":
            inner_type = payload.get("type", "")
            if inner_type == "message":
                role = payload.get("role", "")
                if role == "developer":
                    role = "system"
                content = _extract_text_from_content(payload.get("content", []))
                if content:
                    messages.append(SessionMessage(role=role, content=content, timestamp=ts))

            elif inner_type == "function_call":
                name = payload.get("name", "")
                args = payload.get("arguments", "")
                text = f"[Tool: {name}] {args[:500]}"
                messages.append(SessionMessage(role="tool", content=text, timestamp=ts))

            elif inner_type == "function_call_output":
                output = payload.get("output", "")
                text = output[:2000] if output else "(empty output)"
                messages.append(SessionMessage(role="tool", content=text, timestamp=ts))

    return messages


# ═══════════════════════════════════════════════════════════════════════
# Claude Code adapter
# ═══════════════════════════════════════════════════════════════════════

def scan_claude() -> List[SessionMeta]:
    """Scan Claude Code sessions from ~/.claude/projects/."""
    claude_dir = _home() / ".claude" / "projects"
    if not claude_dir.is_dir():
        return []

    sessions = []
    seen_ids = set()

    for jsonl in claude_dir.rglob("*.jsonl"):
        # Skip subagent files
        if "agent-" in jsonl.name:
            continue
        # Skip files inside subagent directories
        if "subagent" in str(jsonl.parent):
            continue

        meta = _parse_claude_meta(jsonl)
        if meta and meta.session_id not in seen_ids:
            seen_ids.add(meta.session_id)
            sessions.append(meta)

    sessions.sort(key=lambda s: s.last_active, reverse=True)
    return sessions


def _parse_claude_meta(path: Path) -> Optional[SessionMeta]:
    """Extract metadata from a Claude Code session JSONL (head + tail lines)."""
    head_lines = _read_head(path, max_lines=30)
    # Also read tail lines to find model (it often appears late in the conversation)
    tail_lines = _read_tail(path, max_lines=30)
    all_lines = head_lines + tail_lines

    if not all_lines:
        return None

    session_id = ""
    cwd = ""
    model = ""
    created_ts = 0.0
    last_ts = 0.0
    first_user_msg = ""

    for line in all_lines:
        obj = _parse_json_safe(line)
        if not obj:
            continue

        line_type = obj.get("type", "")
        ts = _parse_iso_timestamp(obj.get("timestamp", ""))

        if ts > last_ts:
            last_ts = ts
        if created_ts == 0.0 and ts > 0:
            created_ts = ts

        sid = obj.get("sessionId", "")
        if sid and not session_id:
            session_id = sid

        c = obj.get("cwd", "")
        if c and not cwd:
            cwd = c

        if line_type == "user":
            msg = obj.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                text = _extract_text_from_content(content)
            elif isinstance(content, str):
                if "=== USER MESSAGE ===" in content:
                    text = content.split("=== USER MESSAGE ===")[-1].strip()
                elif "<command-message>" in content:
                    text = re.sub(r"<[^>]+>", "", content).strip()
                else:
                    text = content.strip()
            else:
                text = ""
            if text and "<system_context>" not in text and "<environment_context>" not in text and not first_user_msg:
                first_user_msg = text

        elif line_type == "assistant":
            msg = obj.get("message", {})
            m = msg.get("model", "")
            if m:
                model = m

    if not session_id:
        session_id = path.stem

    cleaned_msg = _clean_title(first_user_msg)
    title = _truncate(cleaned_msg, 80) if cleaned_msg else path.parent.name

    return SessionMeta(
        provider="claude",
        session_id=session_id,
        title=title,
        model=model,
        project_dir=cwd,
        created_at=created_ts,
        last_active=last_ts,
        file_path=str(path),
        resume_cmd=f"claude --resume {session_id}",
    )


def load_claude_messages(path: str) -> List[SessionMessage]:
    """Load all messages from a Claude Code session JSONL."""
    messages = []
    lines = _read_all_lines(Path(path))
    for line in lines:
        obj = _parse_json_safe(line)
        if not obj:
            continue

        line_type = obj.get("type", "")
        ts = _parse_iso_timestamp(obj.get("timestamp", ""))

        if line_type == "user":
            msg = obj.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                text = _extract_text_from_content(content)
            elif isinstance(content, str):
                if "=== USER MESSAGE ===" in content:
                    text = content.split("=== USER MESSAGE ===")[-1].strip()
                elif "<command-message>" in content:
                    text = re.sub(r"<[^>]+>", "", content).strip()
                else:
                    text = content.strip()
            else:
                text = ""
            if text:
                messages.append(SessionMessage(role="user", content=text, timestamp=ts))

        elif line_type == "assistant":
            msg = obj.get("message", {})
            content_blocks = msg.get("content", [])
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    text = block.get("text", "")
                    if text:
                        messages.append(SessionMessage(role="assistant", content=text, timestamp=ts))
                elif block_type == "tool_use":
                    name = block.get("name", "")
                    inp = json.dumps(block.get("input", {}), ensure_ascii=False)[:500]
                    messages.append(SessionMessage(role="tool", content=f"[Tool: {name}] {inp}", timestamp=ts))
                elif block_type == "tool_result":
                    content = block.get("content", "")
                    if isinstance(content, list):
                        content = _extract_text_from_content(content)
                    text = str(content)[:2000] if content else "(empty)"
                    messages.append(SessionMessage(role="tool", content=text, timestamp=ts))

    return messages


# ═══════════════════════════════════════════════════════════════════════
# Gemini CLI adapter
# ═══════════════════════════════════════════════════════════════════════

def scan_gemini() -> List[SessionMeta]:
    """Scan Gemini CLI sessions from ~/.gemini/antigravity/brain/."""
    gemini_base = _home() / ".gemini" / "antigravity" / "brain"
    if not gemini_base.is_dir():
        return []

    sessions = []
    for brain_dir in gemini_base.iterdir():
        if not brain_dir.is_dir():
            continue
        transcript = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
        if transcript.is_file():
            meta = _parse_gemini_meta(brain_dir, transcript)
            if meta:
                sessions.append(meta)

    sessions.sort(key=lambda s: s.last_active, reverse=True)
    return sessions


def _parse_gemini_meta(brain_dir: Path, transcript: Path) -> Optional[SessionMeta]:
    """Extract metadata from a Gemini CLI brain session."""
    lines = _read_head(transcript, max_lines=10)
    if not lines:
        return None

    session_id = brain_dir.name
    created_ts = 0.0
    last_ts = 0.0
    first_user_msg = ""

    for line in lines:
        obj = _parse_json_safe(line)
        if not obj:
            continue

        ts = _parse_iso_timestamp(obj.get("created_at", ""))
        if ts > last_ts:
            last_ts = ts
        if created_ts == 0.0 and ts > 0:
            created_ts = ts

        if obj.get("type") == "USER_INPUT":
            content = obj.get("content", "")
            # Strip XML tags
            clean = re.sub(r"<[^>]+>", "", content).strip()
            if clean and not first_user_msg:
                first_user_msg = clean

    cleaned_msg = _clean_title(first_user_msg)
    title = _truncate(cleaned_msg, 80) if cleaned_msg else session_id[:16]

    return SessionMeta(
        provider="gemini",
        session_id=session_id,
        title=title,
        model="Gemini",
        project_dir="",
        created_at=created_ts,
        last_active=last_ts,
        file_path=str(transcript),
        resume_cmd="",  # Gemini CLI has no --resume
    )


def load_gemini_messages(path: str) -> List[SessionMessage]:
    """Load all messages from a Gemini CLI transcript JSONL."""
    messages = []
    lines = _read_all_lines(Path(path))
    for line in lines:
        obj = _parse_json_safe(line)
        if not obj:
            continue

        line_type = obj.get("type", "")
        ts = _parse_iso_timestamp(obj.get("created_at", ""))
        content = obj.get("content", "")

        if line_type == "USER_INPUT":
            clean = re.sub(r"<[^>]+>", "", content).strip()
            if clean:
                messages.append(SessionMessage(role="user", content=clean, timestamp=ts))

        elif line_type == "PLANNER_RESPONSE":
            text = content.strip() if content else ""
            thinking = obj.get("thinking", "")
            if thinking:
                text = f"(thinking: {thinking[:500]}...)\n\n{text}" if text else f"(thinking: {thinking[:500]}...)"
            if text:
                messages.append(SessionMessage(role="assistant", content=text, timestamp=ts))

            tool_calls = obj.get("tool_calls", [])
            for tc in tool_calls:
                name = tc.get("name", "")
                args = json.dumps(tc.get("args", {}), ensure_ascii=False)[:500]
                messages.append(SessionMessage(role="tool", content=f"[Tool: {name}] {args}", timestamp=ts))

        elif line_type in ("VIEW_FILE", "GREP_SEARCH"):
            if content:
                messages.append(SessionMessage(role="tool", content=content[:2000], timestamp=ts))

    return messages


# ═══════════════════════════════════════════════════════════════════════
# Unified API
# ═══════════════════════════════════════════════════════════════════════

def scan_all(providers: Optional[List[str]] = None, limit: int = 200) -> List[SessionMeta]:
    """Scan all providers and return merged, sorted session list.

    Args:
        providers: list of provider names to scan. None = all.
        limit: max sessions to return (most recent first).
    """
    providers = providers or ["codex", "claude", "gemini"]
    all_sessions = []

    for provider in providers:
        if provider == "codex":
            all_sessions.extend(scan_codex())
        elif provider == "claude":
            all_sessions.extend(scan_claude())
        elif provider == "gemini":
            all_sessions.extend(scan_gemini())

    all_sessions.sort(key=lambda s: s.last_active, reverse=True)
    return all_sessions[:limit]


def load_messages(meta: SessionMeta) -> List[SessionMessage]:
    """Load messages for a session based on its provider."""
    if meta.provider == "codex":
        return load_codex_messages(meta.file_path)
    elif meta.provider == "claude":
        return load_claude_messages(meta.file_path)
    elif meta.provider == "gemini":
        return load_gemini_messages(meta.file_path)
    return []


def delete_session(meta: SessionMeta) -> bool:
    """Delete a session file. Returns True on success.

    Security: validates the file path is within the expected provider root.
    """
    path = Path(meta.file_path)
    if not path.exists():
        return False

    # Security: validate path is within expected provider root
    home = _home()
    if meta.provider == "codex":
        expected_root = home / ".codex"
    elif meta.provider == "claude":
        expected_root = home / ".claude"
    elif meta.provider == "gemini":
        expected_root = home / ".gemini"
    else:
        return False

    try:
        resolved = path.resolve()
        root_resolved = expected_root.resolve()
        if not str(resolved).startswith(str(root_resolved)):
            return False
        path.unlink()
        return True
    except OSError:
        return False
