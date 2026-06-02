#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║  zClaude Provider Manager — Edit & Manage AI Providers      ║
║                                                              ║
║  Add / edit / remove providers, models, endpoints, API keys ║
║  Cross-platform: Linux · macOS · Windows · Termux           ║
╚════════════════════════════════════════════════════════════╝

Usage:
  python3 provider_manager.py                    # Interactive TUI
  python3 provider_manager.py list               # List all providers
  python3 provider_manager.py show <name>         # Show provider details
  python3 provider_manager.py add                # Add new provider (interactive)
  python3 provider_manager.py edit <name>         # Edit existing provider
  python3 provider_manager.py remove <name>       # Remove a provider
  python3 provider_manager.py models <name>       # Manage models for a provider
  python3 provider_manager.py set-default <name>  # Set default provider
  python3 provider_manager.py test <name>         # Test provider connectivity
  python3 provider_manager.py export              # Export config as JSON
  python3 provider_manager.py import <file.json>  # Import config from JSON
  python3 provider_manager.py wizard              # Full setup wizard

Zero external dependencies — Python 3.8+ stdlib only.
"""

from __future__ import annotations

import json
import os
import platform as _platform
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─── Paths ──────────────────────────────────────────────────────
CONFIG_DIR = Path.home() / ".codex"
ENDPOINTS_FILE = CONFIG_DIR / "endpoints.json"
PROXY_CONFIG_FILE = CONFIG_DIR / "proxy-config.json"
MODELS_CACHE_DIR = Path.home() / ".cache" / "codex-proxy"

# ─── Colors (cross-platform, safe fallback) ────────────────────
class _C:
    """ANSI colors — auto-disabled on Windows/non-TTY."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"

    _enabled = sys.stdout.isatty() if hasattr(sys.stdout, 'isatty') else False

    @classmethod
    def _c(cls, code: str, text: str) -> str:
        if not cls._enabled:
            return text
        return f"{code}{text}{cls.RESET}"

    @classmethod
    def red(cls, t: str) -> str: return cls._c(cls.RED, t)
    @classmethod
    def green(cls, t: str) -> str: return cls._c(cls.GREEN, t)
    @classmethod
    def yellow(cls, t: str) -> str: return cls._c(cls.YELLOW, t)
    @classmethod
    def cyan(cls, t: str) -> str: return cls._c(cls.CYAN, t)
    @classmethod
    def blue(cls, t: str) -> str: return cls._c(cls.BLUE, t)
    @classmethod
    def magenta(cls, t: str) -> str: return cls._c(cls.MAGENTA, t)
    @classmethod
    def bold(cls, t: str) -> str: return cls._c(cls.BOLD, t)
    @classmethod
    def dim(cls, t: str) -> str: return cls._c(cls.DIM, t)


# ─── Backend type catalog ───────────────────────────────────────
BACKEND_TYPES = {
    "openai-compat": {
        "description": "OpenAI-compatible API (Ollama, vLLM, OpenRouter, LMStudio, Groq, DeepSeek)",
        "default_url": "http://localhost:11434/v1",
        "auth_header": "Bearer",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
    },
    "anthropic": {
        "description": "Anthropic Claude API (direct or via proxy)",
        "default_url": "https://api.anthropic.com",
        "auth_header": "x-api-key",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
    },
    "command-code": {
        "description": "Google Command Code / Antigravity protocol",
        "default_url": "",
        "auth_header": "oauth",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
    },
    "gemini-oauth-antigravity": {
        "description": "Google Gemini via Antigravity OAuth (Gemini CLI / Cloud Code)",
        "default_url": "https://cloudcode-pa.googleapis.com",
        "auth_header": "oauth",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
    },
    "gemini-oauth-codeassist": {
        "description": "Google Gemini Code Assist OAuth",
        "default_url": "",
        "auth_header": "oauth",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
    },
    "kiro-oauth": {
        "description": "AWS Kiro (CodeWhisperer) via SSO/OAuth",
        "default_url": "",
        "auth_header": "oauth",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
    },
    "codebuff": {
        "description": "CodeBuff free tier (DeepSeek, Kimi, MiniMax)",
        "default_url": "https://www.codebuff.com",
        "auth_header": "cookie",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
    },
    "freebuff": {
        "description": "CodeBuff free alternative endpoints",
        "default_url": "",
        "auth_header": "cookie",
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
    },
}

# ─── Popular model catalogs (for quick-add) ─────────────────────
MODEL_CATALOGS = {
    "openai-compat": [
        {"id": "gpt-4o", "display": "GPT-4o", "context": 128000},
        {"id": "gpt-4o-mini", "display": "GPT-4o Mini", "context": 128000},
        {"id": "gpt-5", "display": "GPT-5", "context": 128000},
        {"id": "gpt-5-codex", "display": "GPT-5 Codex", "context": 200000},
        {"id": "o3", "display": "o3 (reasoning)", "context": 200000},
        {"id": "o4-mini", "display": "o4-mini", "context": 200000},
        {"id": "deepseek-chat", "display": "DeepSeek Chat", "context": 64000},
        {"id": "deepseek-reasoner", "display": "DeepSeek Reasoner", "context": 64000},
        {"id": "llama3.1-70b", "display": "Llama 3.1 70B", "context": 131072},
        {"id": "qwen2.5-72b", "display": "Qwen 2.5 72B", "context": 131072},
        {"id": "mistral-large", "display": "Mistral Large", "context": 128000},
    ],
    "anthropic": [
        {"id": "claude-sonnet-4-20250514", "display": "Claude Sonnet 4", "context": 200000},
        {"id": "claude-opus-4-20250514", "display": "Claude Opus 4", "context": 200000},
        {"id": "claude-haiku-4-5-20251001", "display": "Claude Haiku 4.5", "context": 200000},
        {"id": "claude-sonnet-4-6", "display": "Claude Sonnet 4.6 (Thinking)", "context": 200000},
        {"id": "claude-opus-4-6-thinking", "display": "Claude Opus 4.6 (Thinking)", "context": 200000},
    ],
    "gemini-oauth-antigravity": [
        {"id": "gemini-3-flash", "display": "Gemini 3 Flash", "context": 1000000},
        {"id": "gemini-3.5-flash-high", "display": "Gemini 3.5 Flash High", "context": 1000000},
        {"id": "gemini-3.5-flash-medium", "display": "Gemini 3.5 Flash Medium", "context": 1000000},
        {"id": "gemini-3.5-flash-low", "display": "Gemini 3.5 Flash Low", "context": 1000000},
        {"id": "gemini-3.1-pro-high", "display": "Gemini 3.1 Pro High", "context": 2000000},
        {"id": "gemini-3.1-pro-low", "display": "Gemini 3.1 Pro Low", "context": 2000000},
        {"id": "gemini-2.5-pro", "display": "Gemini 2.5 Pro", "context": 1000000},
        {"id": "gemini-2.5-flash", "display": "Gemini 2.5 Flash", "context": 1000000},
    ],
    "codebuff": [
        {"id": "deepseek/deepseek-v4-pro", "display": "DeepSeek V4 Pro (Free)", "context": 64000},
        {"id": "deepseek/deepseek-v4-flash", "display": "DeepSeek V4 Flash (Free)", "context": 64000},
        {"id": "moonshotai/kimi-k2.6", "display": "Kimi K2.6 (Free)", "context": 64000},
        {"id": "minimax/minimax-m2.7", "display": "MiniMax M2.7 (Free)", "context": 64000},
    ],
}


# ════════════════════════════════════════════════════════════════════
# Config I/O
# ════════════════════════════════════════════════════════════════════

def load_endpoints() -> Dict[str, Any]:
    """Load endpoints config. Normalizes both formats into {name: cfg} dict."""
    if not ENDPOINTS_FILE.exists():
        return {}
    try:
        raw = json.loads(ENDPOINTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}

        # Format 1: Flat dict {name: cfg} ← our native format
        if "endpoints" not in raw and any(
            isinstance(v, dict) and "backend_type" in v or "base_url" in v
            for v in raw.values()
        ):
            return raw

        # Format 2: List-based {"endpoints": [...], "default": "..."}
        # Normalize → flat dict keyed by endpoint name
        if "endpoints" in raw and isinstance(raw["endpoints"], list):
            normalized: Dict[str, Any] = {}
            for ep in raw["endpoints"]:
                if not isinstance(ep, dict) or "name" not in ep:
                    continue
                name = ep.pop("name")
                # Map list-format keys to our standard keys
                cfg = {
                    "backend_type": ep.get("backend_type", ""),
                    "base_url": ep.get("base_url", ""),
                    "api_key": ep.get("api_key", ""),
                    "model": ep.get("default_model", ep.get("model", "")),
                    "is_default": (name == raw.get("default", "")),
                    "reasoning_effort": ep.get("reasoning_effort", "medium"),
                    "stream_idle_timeout": ep.get("stream_idle_timeout", 30),
                    "caveman_mode": ep.get("caveman_mode", False),
                    "rtk_compression": ep.get("rtk_compression", False),
                    "reasoning_enabled": ep.get("reasoning_enabled", True),
                    "prompt_enhancer": ep.get("prompt_enhancer", False),
                    "_models_list": ep.get("models", []),
                    "_provider_preset": ep.get("provider_preset", ""),
                    "cc_version": ep.get("cc_version", ""),
                }
                # Only include non-empty api_key from local config
                if not cfg["api_key"]:
                    cfg.pop("api_key", None)
                normalized[name] = cfg
            return normalized

        return raw
    except (json.JSONDecodeError, OSError):
        return {}


def save_endpoints(data: Dict[str, Any]) -> None:
    """Atomically write endpoints config."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ENDPOINTS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_proxy_config() -> Dict[str, Any]:
    """Load proxy config. Returns empty dict if missing/invalid."""
    if not PROXY_CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(PROXY_CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def save_proxy_config(data: Dict[str, Any]) -> None:
    """Atomically write proxy config."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROXY_CONFIG_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ════════════════════════════════════════════════════════════════════
# Input helpers (cross-platform)
# ════════════════════════════════════════════════════════════════════

def input_str(prompt: str, default: str = "") -> str:
    """Cross-platform string input with default support."""
    suffix = f" [{_C.green(default)}]" if default else ""
    try:
        value = input(f"  {_C.cyan('❯')} {prompt}{suffix}: ").strip()
        return value if value else default
    except EOFError:
        return default


def input_bool(prompt: str, default: bool = False) -> bool:
    """Yes/no input."""
    d = "Y/n" if default else "y/N"
    try:
        val = input(f"  {_C.cyan('❯')} {prompt} [{_C.yellow(d)}]: ").strip().lower()
        if not val:
            return default
        return val in ("y", "yes", "1", "true")
    except EOFError:
        return default


def input_int(prompt: str, default: int = 0, min_val: int = None, max_val: int = None) -> int:
    """Integer input with validation."""
    while True:
        try:
            val = input(f"  {_C.cyan('❯')} {prompt} [{_C.green(str(default))}]: ").strip()
            if not val:
                return default
            n = int(val)
            if min_val is not None and n < min_val:
                print(f"    {_C.red('Must be ≥ {min_val}')}")
                continue
            if max_val is not None and n > max_val:
                print(f"    {_C.red('Must be ≤ {max_val}')}")
                continue
            return n
        except ValueError:
            print(f"    {_C.red('Please enter a valid number')}")


def input_choice(prompt: str, choices: List[str], default_idx: int = 0) -> str:
    """Select from a numbered list."""
    for i, c in enumerate(choices):
        marker = f" {_C.bold('►')}" if i == default_idx else "  "
        print(f"    {marker} {_C.cyan(str(i + 1))}) {c}")
    while True:
        try:
            val = input(f"  {_C.cyan('❯')} {prompt} [{_C.green(str(default_idx + 1))}]: ").strip()
            if not val:
                return choices[default_idx]
            idx = int(val) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
            print(f"    {_C.red('Enter 1-{}')}{len(choices)}")
        except (ValueError, IndexError):
            print(f"    {_C.red('Enter 1-{}')}{len(choices)}")


def input_password(prompt: str = "API Key / Token") -> str:
    """Password input (uses getpass on Unix, falls back to plain input)."""
    try:
        import getpass
        try:
            return getpass.getpass(f"  {_C.cyan('❯')} {prompt}: ")
        except Exception:
            pass
    except ImportError:
        pass
    return input_str(prompt)


def press_enter_to_continue(msg: str = "Press Enter to continue...") -> None:
    try:
        input(f"\n  {_C.dim(msg)}")
    except EOFError:
        pass


# ════════════════════════════════════════════════════════════════════
# Display helpers
# ════════════════════════════════════════════════════════════════════

def banner(text: str) -> None:
    width = min(64, shutil.get_terminal_size((64, 20)).columns - 4)
    print(f"\n  {'═' * width}")
    centered = text.center(width - 4)
    print(f"  {_C.bold(centered)}")
    print(f"  {'═' * width}\n")


def show_provider(name: str, cfg: Dict[str, Any], index: int = 0) -> None:
    """Pretty-print a single provider configuration."""
    backend = cfg.get("backend_type", "?")
    url = cfg.get("base_url", "(none)")
    model = cfg.get("model", "(none)")
    is_def = cfg.get("is_default", False)

    tag = ""
    if is_def:
        tag = f" {_C.green('⭐ DEFAULT')}"
    elif index == 0:
        tag = ""

    print(f"  {_C.bold(f'{index + 1}.')}{_C.magenta(f' {name}')}{tag}")
    print(f"    Backend:     {_C.cyan(backend)}")
    print(f"    URL:         {_C.dim(url)}")
    print(f"    Model:       {_C.green(model)}")

    key = cfg.get("api_key", "")
    if key:
        masked = key[:6] + "..." + key[-4:] if len(key) > 12 else "***"
        print(f"    API Key:     {masked}")

    extra = []
    if cfg.get("reasoning_effort"):
        extra.append(f"reasoning={cfg['reasoning_effort']}")
    if cfg.get("context_size"):
        extra.append(f"context={cfg['context_size']:,}")
    if cfg.get("stream_idle_timeout"):
        extra.append(f"idle_timeout={cfg['stream_idle_timeout']}s")
    if cfg.get("caveman_mode"):
        extra.append("caveman=on")
    if cfg.get("rtk_compression"):
        extra.append("rtk_compress=on")

    if extra:
        print(f"    Options:     {', '.join(extra)}")

    bt = BACKEND_TYPES.get(backend, {})
    caps = []
    if bt.get("supports_tools"):
        caps.append("tools")
    if bt.get("supports_streaming"):
        caps.append("streaming")
    if bt.get("supports_vision"):
        caps.append("vision")
    if caps:
        print(f"    Capabilities: {', '.join(caps)}")


def show_all_providers() -> Tuple[Dict[str, Any], List[str]]:
    """Load and display all providers. Returns (config, name_list)."""
    data = load_endpoints()
    names = list(data.keys())

    if not names:
        print(f"\n  {_C.yellow('⚠ No providers configured.')}")
        print(f"  Use {_C.green('python3 provider_manager.py add')} to add one,")
        print(f"  or {_C.green('python3 provider_manager.py wizard')} for guided setup.\n")
        return data, names

    banner(f"Configured Providers ({len(names)})")

    for i, name in enumerate(names):
        print()
        show_provider(name, data[name], i)

    # Show defaults summary
    defaults = [n for n, c in data.items() if c.get("is_default")]
    if len(defaults) > 1:
        print(f"\n  {_C.yellow('⚠ Multiple defaults set:')}, {', '.join(defaults)}")
    elif not defaults and names:
        print(f"\n  {_C.yellow('⚠ No default provider set.')} Use {_C.green('set-default')}.")

    print()
    return data, names


# ════════════════════════════════════════════════════════════════════
# Provider CRUD operations
# ════════════════════════════════════════════════════════════════════

def cmd_list(args: Optional[List[str]]) -> int:
    """List all configured providers."""
    show_all_providers()
    return 0


def cmd_show(args: Optional[List[str]]) -> int:
    """Show detailed info about one provider."""
    if not args:
        print(f"{_C.red('Error:')} Provider name required.")
        print(f"  Usage: provider_manager.py show <name>")
        return 1

    name = args[0]
    data = load_endpoints()

    if name not in data:
        print(f"{_C.red('Error:')} Provider '{name}' not found.")
        print(f"  Available: {', '.join(data.keys()) or '(none)'}")
        return 1

    banner(f"Provider: {name}")
    cfg = data[name]

    # Dump full config as pretty JSON
    # Mask API keys for safety
    safe = dict(cfg)
    if "api_key" in safe and safe["api_key"]:
        k = safe["api_key"]
        safe["api_key"] = k[:6] + "..." + k[-4:] if len(k) > 12 else "***"

    print(json.dumps(safe, indent=2, ensure_ascii=False))
    print()

    # Show backend capabilities
    bt = BACKEND_TYPES.get(cfg.get("backend_type", ""), {})
    if bt:
        print(f"  {_C.bold('Backend Description:')} {bt.get('description', '?')}")

    return 0


def cmd_add(args: Optional[List[str]]) -> int:
    """Interactive provider addition wizard."""
    banner("Add New Provider")

    data = load_endpoints()

    # Name
    name = input_str("Provider name (e.g., my-openai, claude-pro)")
    if not name:
        print(f"{_C.red('Error:')} Name is required.")
        return 1
    if name in data:
        overwrite = input_bool(f"Provider '{name}' already exists. Overwrite?", False)
        if not overwrite:
            print("Aborted.")
            return 0

    # Backend type
    backend_names = list(BACKEND_TYPES.keys())
    backend = input_choice("Backend type", backend_names)

    # Base URL
    bt = BACKEND_TYPES[backend]
    default_url = bt["default_url"]
    url = input_str("Base URL", default_url)

    # API Key
    auth_hint = {
        "Bearer": "sk-...",
        "x-api-key": "sk-ant-...",
        "oauth": "(will use OAuth flow)",
        "cookie": "(session cookie)",
    }.get(bt.get("auth_header", ""), "...")

    key = ""
    if bt.get("auth_header") != "oauth":
        key = input_password(f"API Key ({auth_hint})")

    # Model
    catalog = MODEL_CATALOGS.get(backend, [])
    if catalog:
        print(f"\n  {_C.bold('Popular models for this backend:')}")
        model_names = [f"{m['id']} ({m['display']})" for m in catalog]
        model_names.append("(custom...)")
        choice = input_choice("Select model (or choose custom)", model_names)

        if choice.startswith("(custom"):
            model = input_str("Model ID")
        else:
            model = choice.split(" ")[0]

        # Also allow adding more models
        if input_bool("Add more models to this provider?", False):
            extra_models = [model]
            while True:
                m = input_str("Additional model ID (or blank to stop)")
                if not m:
                    break
                extra_models.append(m)
            model = extra_models[0]  # primary model
    else:
        model = input_str("Model ID")

    # Options
    print(f"\n  {_C.bold('Advanced options (press Enter for defaults):')}")
    reasoning = input_choice("Reasoning effort", ["none", "low", "medium", "high"], 2)
    context_size = input_int("Context window size (tokens)", 0)
    idle_timeout = input_int("Stream idle timeout (seconds)", 30, 10, 3600)
    caveman = input_bool("Caveman mode (concise output)?", False)
    rtk = input_bool("RTK compression?", False)

    # Default?
    is_default = input_bool("Set as default provider?",
                            len(data) == 0)  # auto-default if first

    # Build config
    cfg: Dict[str, Any] = {
        "backend_type": backend,
        "base_url": url,
        "model": model,
        "is_default": is_default,
        "reasoning_effort": reasoning,
        "stream_idle_timeout": idle_timeout,
        "caveman_mode": caveman,
        "rtk_compression": rtk,
    }
    if key:
        cfg["api_key"] = key
    if context_size > 0:
        cfg["context_size"] = context_size

    # If setting new default, clear old defaults
    if is_default:
        for existing_cfg in data.values():
            existing_cfg.pop("is_default", None)

    data[name] = cfg
    save_endpoints(data)

    print(f"\n  {_C.green('✓')} Provider {_C.bold(name)} added successfully!")
    if is_default:
        print(f"  Set as {_C.green('default')} provider.")

    # Offer to also update proxy-config
    if input_bool("Apply this provider to active proxy config?", True):
        apply_to_proxy_config(name, cfg)

    return 0


def cmd_edit(args: Optional[List[str]]) -> int:
    """Edit an existing provider's settings."""
    if not args:
        print(f"{_C.red('Error:')} Provider name required.")
        print(f"  Usage: provider_manager.py edit <name>")
        return 1

    name = args[0]
    data = load_endpoints()

    if name not in data:
        print(f"{_C.red('Error:')} Provider '{name}' not found.")
        return 1

    cfg = dict(data[name])  # copy
    banner(f"Edit Provider: {name}")
    print(f"  {_C.dim('Press Enter to keep current value.')}\n")

    # Edit each field
    new_name = input_str("Provider name", name)
    new_backend = input_choice("Backend type", list(BACKEND_TYPES.keys()),
                               list(BACKEND_TYPES.keys()).index(cfg.get("backend_type", "openai-compat"))
                               if cfg.get("backend_type") in BACKEND_TYPES else 0)
    new_url = input_str("Base URL", cfg.get("base_url", ""))

    key = cfg.get("api_key", "")
    key_display = key[:6] + "..." + key[-4:] if len(key) > 12 else "(set)"
    new_key_prompt = input_str(f"API Key [{_C.dim(key_display)}] (enter '*' to keep, blank to clear)")
    if new_key_prompt == "*":
        new_key = key
    elif new_key_prompt == "":
        new_key = ""
    else:
        new_key = new_key_prompt

    new_model = input_str("Model", cfg.get("model", ""))

    reasoning_choices = ["none", "low", "medium", "high"]
    current_reasoning = cfg.get("reasoning_effort", "medium")
    ri = reasoning_choices.index(current_reasoning) if current_reasoning in reasoning_choices else 2
    new_reasoning = input_choice("Reasoning effort", reasoning_choices, ri)
    new_context = input_int("Context window size", cfg.get("context_size", 0), 0)
    new_idle = input_int("Stream idle timeout", cfg.get("stream_idle_timeout", 30), 10, 3600)
    new_caveman = input_bool("Caveman mode", cfg.get("caveman_mode", False))
    new_rtk = input_bool("RTK compression", cfg.get("rtk_compression", False))
    new_default = input_bool("Set as default?", cfg.get("is_default", False))

    # Build updated config
    new_cfg: Dict[str, Any] = {
        "backend_type": new_backend,
        "base_url": new_url,
        "model": new_model,
        "reasoning_effort": new_reasoning,
        "stream_idle_timeout": new_idle,
        "caveman_mode": new_caveman,
        "rtk_compression": new_rtk,
        "is_default": new_default,
    }
    if new_key:
        new_cfg["api_key"] = new_key
    if new_context > 0:
        new_cfg["context_size"] = new_context

    # Handle rename
    if new_name != name:
        if new_name in data and new_name != name:
            print(f"{_C.red('Error:')} Provider '{new_name}' already exists.")
            return 1
        del data[name]
        name = new_name

    # Handle default change
    if new_default:
        for existing_cfg in data.values():
            existing_cfg.pop("is_default", None)

    data[name] = new_cfg
    save_endpoints(data)

    print(f"\n  {_C.green('✓')} Provider {_C.bold(name)} updated!")

    if input_bool("Apply changes to active proxy config?", True):
        apply_to_proxy_config(name, new_cfg)

    return 0


def cmd_remove(args: Optional[List[str]]) -> int:
    """Remove a provider."""
    if not args:
        print(f"{_C.red('Error:')} Provider name required.")
        return 1

    name = args[0]
    data = load_endpoints()

    if name not in data:
        print(f"{_C.red('Error:')} Provider '{name}' not found.")
        return 1

    confirm = input_bool(f"Remove provider '{_C.red(name)}'? This cannot be undone.", False)
    if not confirm:
        print("Aborted.")
        return 0

    del data[name]
    save_endpoints(data)
    print(f"  {_C.green('✓')} Provider '{name}' removed.")
    return 0


# ════════════════════════════════════════════════════════════════════
# Model management
# ════════════════════════════════════════════════════════════════════

def cmd_models(args: Optional[List[str]]) -> int:
    """Manage models for a provider — list, add, remove, reorder."""
    if not args:
        print(f"{_C.red('Error:')} Provider name required.")
        print(f"  Usage: provider_manager.py models <provider-name>")
        return 1

    name = args[0]
    data = load_endpoints()

    if name not in data:
        print(f"{_C.red('Error:')} Provider '{name}' not found.")
        return 1

    cfg = data[name]
    current_model = cfg.get("model", "")

    banner(f"Models for: {name}")
    print(f"  Current primary model: {_C.green(current_model)}\n")

    # Show available models from catalog
    backend = cfg.get("backend_type", "")
    catalog = MODEL_CATALOGS.get(backend, [])

    if catalog:
        print(f"  {_C.bold('Available models for this backend:')}")
        for i, m in enumerate(catalog):
            marker = " ◆" if m["id"] == current_model else "  "
            print(f"    {marker} {_C.cyan(str(i + 1))}) {m['id']:40s} {_C.dim(m['display'])}  (ctx: {m.get('context', '?'):,})")
        print()

    # Model management menu
    actions = [
        "Set/change primary model",
        "Add custom model",
        "View model details from catalog",
        "Back",
    ]
    action = input_choice("What do you want to do?", actions)

    if action == actions[0]:  # Set primary
        if catalog:
            options = [m["id"] for m in catalog] + ["(custom...)"]
            choice = input_choice("Select primary model", options)
            if choice.startswith("(custom"):
                new_model = input_str("Custom model ID")
            else:
                new_model = choice
        else:
            new_model = input_str("Primary model ID")

        cfg["model"] = new_model
        data[name] = cfg
        save_endpoints(data)
        print(f"\n  {_C.green('✓')} Primary model set to: {_C.green(new_model)}")

    elif action == actions[1]:  # Add custom
        model_id = input_str("Custom model ID")
        display_name = input_str("Display name (optional)", model_id)
        context_sz = input_int("Context window size (0=unknown)", 0)
        # Store custom models in a _custom_models list
        customs = cfg.get("_custom_models", [])
        customs.append({
            "id": model_id,
            "display": display_name,
            "context": context_sz,
        })
        cfg["_custom_models"] = customs
        data[name] = cfg
        save_endpoints(data)
        print(f"\n  {_C.green('✓')} Custom model '{model_id}' added.")

    elif action == actions[2]:  # View details
        if not catalog:
            print(f"  {_C.yellow('No model catalog available for this backend.')}")
            return 0
        for m in catalog:
            if m["id"] == current_model:
                print(f"\n  {_C.bold('Model Details:')}")
                print(f"    ID:          {_C.cyan(m['id'])}")
                print(f"    Display:     {m['display']}")
                print(f"    Context:     {m.get('context', '?'):,} tokens")
                bt = BACKEND_TYPES.get(backend, {})
                print(f"    Tools:       {'Yes' if bt.get('supports_tools') else 'No'}")
                print(f"    Streaming:   {'Yes' if bt.get('supports_streaming') else 'No'}")
                print(f"    Vision:      {'Yes' if bt.get('supports_vision') else 'No'}")
                break
        else:
            print(f"  {_C.yellow('Current model not in standard catalog (may be custom).')}")

    return 0


# ════════════════════════════════════════════════════════════════════
# Default management
# ════════════════════════════════════════════════════════════════════

def cmd_set_default(args: Optional[List[str]]) -> int:
    """Set a provider as the default."""
    if not args:
        # Interactive selection
        data = load_endpoints()
        names = list(data.keys())
        if not names:
            print(f"{_C.red('No providers configured.')}")
            return 1
        choice = input_choice("Select default provider", names)
        args = [choice]

    name = args[0]
    data = load_endpoints()

    if name not in data:
        print(f"{_C.red('Error:')} Provider '{name}' not found.")
        return 1

    for n in data:
        data[n].pop("is_default", None)
    data[name]["is_default"] = True
    save_endpoints(data)
    print(f"  {_C.green('✓')} {_C.bold(name)} is now the default provider.")
    return 0


# ════════════════════════════════════════════════════════════════════
# Connectivity testing
# ════════════════════════════════════════════════════════════════════

def cmd_test(args: Optional[List[str]]) -> int:
    """Test connectivity to a provider's endpoint."""
    if not args:
        data = load_endpoints()
        names = list(data.keys())
        if not names:
            print(f"{_C.red('No providers configured.')}")
            return 1
        choice = input_choice("Select provider to test", names)
        args = [choice]

    name = args[0]
    data = load_endpoints()

    if name not in data:
        print(f"{_C.red('Error:')} Provider '{name}' not found.")
        return 1

    cfg = data[name]
    url = cfg.get("base_url", "").rstrip("/")
    backend = cfg.get("backend_type", "")
    key = cfg.get("api_key", "")

    banner(f"Testing: {name}")

    if not url:
        print(f"  {_C.yellow('No base URL configured for this provider.')}")
        print(f"  Providers using OAuth (like Antigravity) need authentication first.")
        return 1

    print(f"  URL:     {_C.dim(url)}")
    print(f"  Backend: {_C.cyan(backend)}")
    print()

    # Determine test endpoint based on backend
    bt = BACKEND_TYPES.get(backend, {})
    if backend == "anthropic":
        test_url = url + "/v1/messages"
    else:
        test_url = url + "/models" if "/v1" not in url else url.rstrip("/") + "/models"

    print(f"  Testing connection to: {_C.dim(test_url)}")

    headers = {"Content-Type": "application/json"}
    auth_type = bt.get("auth_header", "Bearer")
    if auth_type == "x-api-key":
        headers["x-api-key"] = key
    elif auth_type == "Bearer" and key:
        headers["Authorization"] = f"Bearer {key}"
    elif key:
        headers["Authorization"] = f"{auth_type} {key}"

    try:
        req = urllib.request.Request(test_url, headers=headers, method="GET")
        start = time.time()
        resp = urllib.request.urlopen(req, timeout=15)
        elapsed = (time.time() - start) * 1000
        status = resp.status
        body = resp.read().decode(errors="replace")[:500]

        print(f"\n  {_C.green('✓ CONNECTED')}")
        print(f"    Status:    {status}")
        print(f"    Latency:   {elapsed:.0f}ms")

        # Try to parse response
        try:
            jdata = json.loads(body)
            if isinstance(jdata, dict):
                if "data" in jdata and isinstance(jdata["data"], list):
                    models = jdata["data"]
                    print(f"    Models:    {len(models)} available")
                    for m in models[:5]:
                        mid = m.get("id", m.get("name", "?"))
                        print(f"              - {mid}")
                    if len(models) > 5:
                        print(f"              ... and {len(models) - 5} more")
                elif "error" in jdata:
                    print(f"    Response:  {jstr(jdata.get('error'))[:200]}")
            else:
                print(f"    Response:  {body[:200]}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"    Response:  {body[:200]}")

    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")[:300]
        print(f"\n  {_C.red('✗ HTTP ERROR')} {e.code}")
        print(f"    {err_body}")
        return 1
    except urllib.error.URLError as e:
        print(f"\n  {_C.red('✗ CONNECTION FAILED')}")
        print(f"    Reason: {e.reason}")
        return 1
    except Exception as e:
        print(f"\n  {_C.red('✗ ERROR')}")
        print(f"    {e}")
        return 1

    return 0


# ════════════════════════════════════════════════════════════════════
# Export / Import
# ════════════════════════════════════════════════════════════════════

def cmd_export(args: Optional[List[str]]) -> int:
    """Export full configuration as JSON (with API keys masked)."""
    data = load_endpoints()
    proxy_cfg = load_proxy_config()

    export_data = {
        "version": "1.0",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": _platform.system(),
        "providers": {},
    }

    # Mask API keys in exported data
    for name, cfg in data.items():
        safe = dict(cfg)
        if safe.get("api_key"):
            k = safe["api_key"]
            safe["api_key"] = k[:6] + "..." + k[-4:] if len(k) > 12 else "***"
        export_data["providers"][name] = safe

    if proxy_cfg:
        safe_proxy = dict(proxy_cfg)
        if safe_proxy.get("api_key"):
            k = safe_proxy["api_key"]
            safe_proxy["api_key"] = k[:6] + "..." + k[-4:] if len(k) > 12 else "***"
        export_data["proxy_config"] = safe_proxy

    output = json.dumps(export_data, indent=2, ensure_ascii=False)
    print(output)

    # Optionally save to file
    if input_bool("\nSave to file?", False):
        path = input_str("Output file path", "zclaude-export.json")
        Path(path).write_text(output, encoding="utf-8")
        print(f"  {_C.green('✓')} Exported to {_C.bold(path)}")

    return 0


def cmd_import(args: Optional[List[str]]) -> int:
    """Import configuration from a JSON file."""
    if not args:
        print(f"{_C.red('Error:')} File path required.")
        print(f"  Usage: provider_manager.py import <file.json>")
        return 1

    path = Path(args[0])
    if not path.exists():
        print(f"{_C.red('Error:')} File not found: {path}")
        return 1

    try:
        import_data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"{_C.red('Error:')} Invalid JSON: {e}")
        return 1

    providers = import_data.get("providers", {})
    if not providers:
        print(f"{_C.red('Error:')} No providers found in file.")
        return 1

    data = load_endpoints()
    count = 0
    for name, cfg in providers.items():
        # Skip masked keys
        if cfg.get("api_key", "") and "..." in str(cfg["api_key"]):
            cfg.pop("api_key", None)

        # Handle merge vs overwrite
        if name in data and not input_bool(f"Overwrite existing provider '{name}'?", False):
            continue

        data[name] = cfg
        count += 1

    save_endpoints(data)
    print(f"  {_C.green('✓')} Imported {count} provider(s).")

    # Import proxy config too
    if "proxy_config" in import_data:
        pcfg = import_data["proxy_config"]
        if pcfg.get("api_key") and "..." in str(pcfg["api_key"]):
            pcfg.pop("api_key", None)
        if input_bool("Import proxy config as well?", False):
            save_proxy_config(pcfg)
            print(f"  {_C.green('✓')} Proxy config imported.")

    return 0


# ════════════════════════════════════════════════════════════════════
# Apply provider to proxy config
# ════════════════════════════════════════════════════════════════════

def apply_to_proxy_config(name: str, provider_cfg: Dict[str, Any]) -> None:
    """Sync a provider's settings into the active proxy config."""
    proxy = load_proxy_config()
    proxy["port"] = proxy.get("port", 8080)
    proxy["backend_type"] = provider_cfg.get("backend_type", "openai-compat")
    proxy["target_url"] = provider_cfg.get("base_url", "")
    proxy["api_key"] = provider_cfg.get("api_key", "")
    proxy["model"] = provider_cfg.get("model", "")
    if provider_cfg.get("reasoning_effort"):
        proxy["reasoning_effort"] = provider_cfg["reasoning_effort"]
    if provider_cfg.get("context_size"):
        proxy["context_size"] = provider_cfg["context_size"]
    if provider_cfg.get("stream_idle_timeout"):
        proxy["stream_idle_timeout"] = provider_cfg["stream_idle_timeout"]
    proxy["caveman_mode"] = provider_cfg.get("caveman_mode", False)
    proxy["rtk_compression"] = provider_cfg.get("rtk_compression", False)
    save_proxy_config(proxy)
    print(f"  {_C.green('✓')} Proxy config updated.")


# ════════════════════════════════════════════════════════════════════
# Full setup wizard
# ════════════════════════════════════════════════════════════════════

def cmd_wizard(args: Optional[List[str]]) -> int:
    """Guided full-setup wizard for new users."""
    banner("zClaude Setup Wizard")
    print(f"  This wizard will help you configure your AI providers.\n")
    print(f"  You can always run {_C.green('python3 provider_manager.py edit')} later\n"
          f"  to modify settings.\n")

    data = load_endpoints()

    # Step 1: How many providers?
    print(f"  {_C.bold('Step 1:')} How many AI providers do you want to configure?")
    n_providers = input_int("Number of providers", 1, 1, 10)

    for i in range(n_providers):
        print(f"\n  {_C.bold('─' * 40)}")
        print(f"  {_C.bold(f'Provider {i + 1} of {n_providers}')}")
        print(f"  {'─' * 40}\n")

        name = input_str("Provider name (e.g., openai-main, claude-backup)", f"provider-{i+1}")
        while name in data:
            name = input_str("Name already exists, choose another", f"{name}-2")

        backend_names = list(BACKEND_TYPES.keys())
        backend = input_choice("Backend type", backend_names)
        bt = BACKEND_TYPES[backend]

        url = input_str("Base URL", bt["default_url"])

        key = ""
        if bt.get("auth_header") not in ("oauth",):
            key = input_password("API Key")

        catalog = MODEL_CATALOGS.get(backend, [])
        if catalog:
            model_names = [f"{m['id']} ({m['display']})" for m in catalog] + ["(custom)"]
            choice = input_choice("Model", model_names)
            model = choice.split(" ")[0] if not choice.startswith("(") else input_str("Custom model ID")
        else:
            model = input_str("Model ID")

        is_default = (i == 0) or (len(data) == 0 and i == 0)

        cfg: Dict[str, Any] = {
            "backend_type": backend,
            "base_url": url,
            "model": model,
            "is_default": is_default,
            "reasoning_effort": "medium",
            "stream_idle_timeout": 30,
        }
        if key:
            cfg["api_key"] = key

        if is_default:
            for ec in data.values():
                ec.pop("is_default", None)

        data[name] = cfg
        print(f"  {_C.green('✓')} Provider '{name}' configured.")

    # Save all
    save_endpoints(data)

    # Step 2: Proxy port
    print(f"\n  {_C.bold('Step 2:')} Proxy server settings")
    port = input_int("Proxy port", 8080, 1, 65535)

    # Step 3: Apply to proxy config
    # Find default provider
    default_name = next((n for n, c in data.items() if c.get("is_default")), list(data.keys())[0] if data else None)
    if default_name:
        apply_to_proxy_config(default_name, data[default_name])
        proxy = load_proxy_config()
        proxy["port"] = port
        save_proxy_config(proxy)

    # Summary
    banner("Setup Complete!")
    print(f"  {_C.green('✓')} {len(data)} provider(s) configured:")
    for n, c in data.items():
        d = " ⭐" if c.get("is_default") else ""
        print(f"    {d} {_C.magenta(n):25s} {_C.cyan(c.get('backend_type','?')):20s} {_C.green(c.get('model','?'))}")

    print(f"\n  Next steps:")
    print(f"    1. Test a provider: {_C.green(f'python3 provider_manager.py test {default_name}')}")
    print(f"    2. Start the proxy:  {_C.green('python3 translate-proxy.py')}")
    print(f"    3. Launch GUI:       {_C.green('python3 codex-launcher-gui.py')}")
    print()

    return 0


# ════════════════════════════════════════════════════════════════════
# Interactive TUI mode
# ════════════════════════════════════════════════════════════════════

def cmd_interactive(args: Optional[List[str]]) -> int:
    """Full interactive TUI with menu loop."""
    while True:
        banner("zClaude Provider Manager")
        print(f"  {_C.bold('1.')} List all providers")
        print(f"  {_C.bold('2.')} Add provider")
        print(f"  {_C.bold('3.')} Edit provider")
        print(f"  {_C.bold('4.')} Remove provider")
        print(f"  {_C.bold('5.')} Manage models")
        print(f"  {_C.bold('6.')} Set default")
        print(f"  {_C.bold('7.')} Test connectivity")
        print(f"  {_C.bold('8.')} Export config")
        print(f"  {_C.bold('9.')} Import config")
        print(f"  {_C.bold('10.')} Setup wizard")
        print(f"  {_C.bold('0.')} Exit")
        print()

        choice = input_str("Select action", "0").strip()

        handlers = {
            "1": lambda: cmd_list(None),
            "2": lambda: cmd_add(None),
            "3": lambda: (lambda: cmd_edit([input_str("Provider name")]))(),
            "4": lambda: (lambda: cmd_remove([input_str("Provider name")]))(),
            "5": lambda: (lambda: cmd_models([input_str("Provider name")]))(),
            "6": lambda: cmd_set_default(None),
            "7": lambda: cmd_test(None),
            "8": lambda: cmd_export(None),
            "9": lambda: (lambda: cmd_import([input_str("JSON file path")]))(),
            "10": lambda: cmd_wizard(None),
            "0": (lambda: (print(f"\n  {_C.dim('Goodbye!')}\n"), exit(0)), None)[0],
        }

        handler = handlers.get(choice)
        if handler:
            try:
                handler()
            except KeyboardInterrupt:
                print(f"\n  {_C.yellow('Cancelled.')}")
            press_enter_to_continue()
        else:
            print(f"  {_C.red('Invalid choice.')}")

    return 0


# ════════════════════════════════════════════════════════════════════
# CLI entry point
# ════════════════════════════════════════════════════════════════════

COMMANDS = {
    "list": cmd_list,
    "ls": cmd_list,
    "show": cmd_show,
    "add": cmd_add,
    "create": cmd_add,
    "edit": cmd_edit,
    "update": cmd_edit,
    "modify": cmd_edit,
    "remove": cmd_remove,
    "rm": cmd_remove,
    "delete": cmd_remove,
    "del": cmd_remove,
    "models": cmd_models,
    "model": cmd_models,
    "set-default": cmd_set_default,
    "default": cmd_set_default,
    "test": cmd_test,
    "ping": cmd_test,
    "export": cmd_export,
    "dump": cmd_export,
    "import": cmd_import,
    "load": cmd_import,
    "wizard": cmd_wizard,
    "setup": cmd_wizard,
    "help": lambda a: (print(__doc__), 0),
    "--help": lambda a: (print(__doc__), 0),
    "-h": lambda a: (print(__doc__), 0),
}


def main() -> int:
    args = sys.argv[1:]

    if not args:
        return cmd_interactive(None)

    cmd = args[0].lower()
    rest = args[1:] if len(args) > 1 else None

    handler = COMMANDS.get(cmd)
    if handler:
        try:
            return handler(rest)
        except KeyboardInterrupt:
            print(f"\n{_C.yellow('\nCancelled.')}")
            return 130
        except Exception as e:
            print(f"{_C.red(f'\nError: {e}')}")
            return 1
    else:
        print(f"{_C.red(f'Unknown command: {cmd}')}")
        print(f"\nAvailable commands: {', '.join(COMMANDS.keys())}")
        print(f"\nRun without arguments for interactive mode.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
