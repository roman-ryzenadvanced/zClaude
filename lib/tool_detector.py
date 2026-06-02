"""Multi-tool auto-detection for CLI coding assistants.

Scans for installed coding tools (codex, claude, opencode, gemini, kiro,
aider, cursor, warp, cline, copilot) and returns structured ToolInfo
objects with path, version, and capability metadata.

Pattern follows lib/codex_detect.py exactly: shutil.which() for presence,
subprocess.run([tool, "--version"]) for version string.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional


# ════════════════════════════════════════════════════════════════════
# Data structures
# ════════════════════════════════════════════════════════════════════

@dataclass
class ToolInfo:
    """Structured info about a detected (or undetected) coding tool."""
    tool_id: str              # e.g. "codex", "claude"
    display_name: str         # e.g. "Codex CLI"
    binary: str               # executable name to search PATH
    version_flag: str         # flag that prints version, e.g. "--version"
    installed: bool           # True if found on PATH
    path: str = ""            # full path if installed, "" otherwise
    version: str = ""         # version string or "--" if not installed
    backend_preference: str = "openai-compat"  # preferred backend type
    supports_native: bool = False             # can connect without proxy
    icon: str = "?"          # single-char icon for TUI display
    description: str = ""     # one-line description


# ════════════════════════════════════════════════════════════════════
# Tool Registry — all known coding tools
# ════════════════════════════════════════════════════════════════════

TOOL_REGISTRY: Dict[str, dict] = {
    "codex": {
        "display_name": "Codex CLI",
        "binary": "codex",
        "version_flag": "--version",
        "backend_preference": "openai-compat",
        "supports_native": True,
        "icon": "C",
        "description": "OpenAI's terminal-based coding agent",
    },
    "claude": {
        "display_name": "Claude Code",
        "binary": "claude",
        "version_flag": "--version",
        "backend_preference": "anthropic",
        "supports_native": True,
        "icon": "c",
        "description": "Anthropic's official CLI coding assistant",
    },
    "opencode": {
        "display_name": "OpenCode",
        "binary": "opencode",
        "version_flag": "--version",
        "backend_preference": "openai-compat",
        "supports_native": False,
        "icon": "o",
        "description": "Open-source AI coding terminal (OpenAI-compatible)",
    },
    "gemini": {
        "display_name": "Gemini CLI",
        "binary": "gemini",
        "version_flag": "--version",
        "backend_preference": "google",
        "supports_native": True,
        "icon": "G",
        "description": "Google's Gemini CLI coding tool",
    },
    "kiro": {
        "display_name": "Kiro",
        "binary": "kiro",
        "version_flag": "--version",
        "backend_preference": "kiro-oauth",
        "supports_native": True,
        "icon": "K",
        "description": "AWS / Amazon Q Developer CLI (Kiro)",
    },
    "aider": {
        "display_name": "Aider",
        "binary": "aider",
        "version_flag": "--version",
        "backend_preference": "openai-compat",
        "supports_native": False,
        "icon": "A",
        "description": "AI pair programming in your terminal",
    },
    "cursor": {
        "display_name": "Cursor",
        "binary": "cursor",
        "version_flag": "--version",
        "backend_preference": "openai-compat",
        "supports_native": False,
        "icon": "C",
        "description": "Cursor IDE command-line interface",
    },
    "warp": {
        "display_name": "Warp",
        "binary": "warp",
        "version_flag": "--version",
        "backend_preference": "openai-compat",
        "supports_native": False,
        "icon": "W",
        "description": "Warp Terminal AI features",
    },
    "cline": {
        "display_name": "Cline",
        "binary": "cline",
        "version_flag": "--version",
        "backend_preference": "openai-compat",
        "supports_native": False,
        "icon": "L",
        "description": "Cline autonomous coding agent (VS Code extension CLI)",
    },
    "copilot": {
        "display_name": "Copilot CLI",
        "binary": "github-copilot-cli",
        "version_flag": "--version",
        "backend_preference": "openai-compat",
        "supports_native": False,
        "icon": "\U0001f916",  # robot emoji
        "description": "GitHub Copilot command-line interface",
    },
}


# ════════════════════════════════════════════════════════════════════
# Detection functions
# ════════════════════════════════════════════════════════════════════

def detect_tool(tool_id: str) -> Optional[ToolInfo]:
    """Detect a single tool by its registry ID.

    Returns ToolInfo with installed=True if found, or installed=False
    with placeholder values if not found. Returns None if tool_id
    is not in the registry.
    """
    reg = TOOL_REGISTRY.get(tool_id)
    if not reg:
        return None

    binary = reg["binary"]
    try:
        path = shutil.which(binary)
    except Exception:
        path = None

    if not path:
        return ToolInfo(
            tool_id=tool_id,
            display_name=reg["display_name"],
            binary=binary,
            version_flag=reg["version_flag"],
            installed=False,
            path="",
            version="--",
            backend_preference=reg["backend_preference"],
            supports_native=reg["supports_native"],
            icon=reg["icon"],
            description=reg["description"],
        )

    # Found on PATH — try to get version
    version = _get_version(binary, reg["version_flag"])

    return ToolInfo(
        tool_id=tool_id,
        display_name=reg["display_name"],
        binary=binary,
        version_flag=reg["version_flag"],
        installed=True,
        path=path,
        version=version,
        backend_preference=reg["backend_preference"],
        supports_native=reg["supports_native"],
        icon=reg["icon"],
        description=reg["description"],
    )


def detect_all_tools() -> List[ToolInfo]:
    """Scan every tool in the registry. Returns list of all ToolInfo objects."""
    results: List[ToolInfo] = []
    for tool_id in TOOL_REGISTRY:
        info = detect_tool(tool_id)
        if info:
            results.append(info)
    return results


def get_installed_tools() -> List[ToolInfo]:
    """Return only tools that are actually installed (found on PATH)."""
    return [t for t in detect_all_tools() if t.installed]


def get_tool_by_id(tool_id: str) -> Optional[ToolInfo]:
    """Convenience alias for detect_tool()."""
    return detect_tool(tool_id)


def get_tool_names() -> List[str]:
    """Return list of all registered tool IDs."""
    return list(TOOL_REGISTRY.keys())


def get_compatible_providers(tool: ToolInfo, endpoints: Dict) -> List[str]:
    """Find providers compatible with a given tool.

    Returns list of provider names from `endpoints` dict whose
    backend_type matches or is compatible with the tool's
    backend_preference.

    Compatibility rules:
      - anthropic tool + anthropic provider → native (best)
      - openai-compat tool + any provider → via proxy
      - google/gemini tool + google/gemini-oauth provider → native
      - kiro tool + kiro-oauth provider → native
      - anything else → needs proxy translation
    """
    compatible = []
    tool_backend = tool.backend_preference

    for name, cfg in endpoints.items():
        prov_backend = cfg.get("backend_type", "")
        # Direct match
        if prov_backend == tool_backend:
            compatible.append(name)
        # OpenAI-compatible tools work with any provider through proxy
        elif tool_backend == "openai-compat":
            compatible.append(name)
        # Anthropic tools can use anthropic-like backends
        elif tool_backend == "anthropic" and prov_backend in (
            "anthropic", "openai-compat"
        ):
            compatible.append(name)

    return compatible


# ════════════════════════════════════════════════════════════════════
# Internal helpers
# ════════════════════════════════════════════════════════════════════

def _get_version(binary: str, flag: str) -> str:
    """Run <binary> <flag> and extract version string.

    Handles various output formats gracefully. Follows the exact
    pattern from lib/codex_detect.py:detect_codex_cli().
    """
    try:
        out = subprocess.run(
            [binary, flag],
            capture_output=True,
            text=True,
            timeout=10,
        )
        ver = (out.stdout or "").strip()
        if not ver:
            ver = (out.stderr or "").strip()
        if not ver:
            ver = "unknown"
        # Truncate very long version strings
        if len(ver) > 60:
            ver = ver[:57] + "..."
        return ver
    except FileNotFoundError:
        return "not found"
    except subprocess.TimeoutExpired:
        return "timeout"
    except PermissionError:
        return "no permission"
    except OSError as e:
        if e.errno == 2:
            return "not configured"
        return f"error ({e.errno})"
    except Exception:
        return "unknown"


# ════════════════════════════════════════════════════════════════════
# Summary helpers (for TUI display)
# ════════════════════════════════════════════════════════════════════

def format_tool_row(tool: ToolInfo, index: int) -> str:
    """Format a single tool as a TUI table row string.

    Returns a string like:
      ● codex CLI     v2.1   OPENAI / proxy-capable   ✓ Ready
    """
    from lib.tui_engine import C

    status_icon = C.green("●") if tool.installed else C.dim("○")
    idx_str = f"{index + 1:>2}."
    name_col = f"{tool.icon} {tool.display_name:<18s}"
    ver_col = f"{tool.version:<12s}" if tool.installed else C.dim(f"{tool.version:<12s}")
    backend = tool.backend_preference.upper()
    native_tag = "/ native" if tool.supports_native else "/ proxy-capable"
    backend_col = C.dim(f"{backend}{native_tag}")
    status = C.success("✓ Ready") if tool.installed else C.error("✗ Missing")

    return f"  {idx_str} {status_icon} {name_col} {ver_col} {backend_col}  {status}"


def summary_line(tools: List[ToolInfo]) -> str:
    """One-line summary of detection results."""
    installed = sum(1 for t in tools if t.installed)
    total = len(tools)
    names = ", ".join(t.display_name for t in tools if t.installed)
    return f"{installed}/{total} tools detected: {names or 'none'}"
