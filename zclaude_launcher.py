#!/usr/bin/env python3
"""zClaude Universal Launcher — Unified TUI Control Center.

One command to rule them all:
  python3 zclaude_launcher.py

Screens:
  [Dashboard]    Tool status + active provider + quick actions
  [Providers]     Browse/add/edit/activate providers (20+ presets)
  [Tool Select]   Pick coding tool (auto-detected)
  [Launch]        Review & execute launch with proxy auto-config
  [Sessions]      Browse recent coding sessions
  [Settings]      Preferences, defaults, theme

Navigation: Arrow keys / vim keys / number shortcuts / Esc=back / q=quit

Architecture: Screen-based TUI — each screen function renders a frame,
reads a keystroke, then returns the next screen ID (or None to exit).
No curses dependency; uses print-based rendering with ANSI escapes.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

# ── Ensure project root is on sys.path so "lib.*" imports work ────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lib.tui_engine import C, Term, Box, Keyboard, Menu, ProgressBar
from lib.tool_detector import (
    ToolInfo, TOOL_REGISTRY, detect_all_tools, get_installed_tools,
    format_tool_row, summary_line,
)
from lib.tool_launcher import LaunchOptions, LaunchPlan, build_launch_plan, execute_launch


# ════════════════════════════════════════════════════════════════════
# Constants & Screen IDs
# ════════════════════════════════════════════════════════════════════

SCREEN_DASHBOARD = "dashboard"
SCREEN_PROVIDERS = "providers"
SCREEN_PROVIDER_ADD = "provider_add"
SCREEN_PROVIDER_EDIT = "provider_edit"
SCREEN_TOOL_SELECT = "tool_select"
SCREEN_LAUNCH = "launch"
SCREEN_SESSIONS = "sessions"
SCREEN_SETTINGS = "settings"
SCREEN_EXIT = None  # Signals exit

VERSION = "1.0.0"

# Action menu items on dashboard
ACTIONS = [
    ("1", "🚀", "Launch Tool",   SCREEN_TOOL_SELECT),
    ("2", "📡", "Providers",     SCREEN_PROVIDERS),
    ("3", "📂", "Sessions",      SCREEN_SESSIONS),
    ("4", "⚙",  "Settings",      SCREEN_SETTINGS),
    ("q", "",    "Quit",          SCREEN_EXIT),
]


# ════════════════════════════════════════════════════════════════════
# Launcher state — mutable dict shared between screens
# ════════════════════════════════════════════════════════════════════

def create_state() -> Dict[str, Any]:
    """Create fresh launcher state dict."""
    return {
        "tools": [],                  # List[ToolInfo] — detected tools
        "installed_tools": [],        # List[ToolInfo] — only installed
        "endpoints": {},              # Dict[str, dict] — loaded endpoints
        "default_provider": "",       # str — active/default provider name
        "selected_tool": None,        # ToolInfo or None
        "selected_provider": None,    # str or None
        "launch_options": LaunchOptions(),
        "sessions": [],               # List[SessionMeta]
        "cursor": 0,                  # int — menu cursor position
        "page": 0,                    # int — current page
        "filter_text": "",            # str — search filter
        "message": "",                # str — status message to show
        "message_color": "info",      # str — color for status message
        "last_launch_pid": 0,         # int — PID of last launched process
        "proxy_running": False,       # bool
        "proxy_port": 0,              # int
    }


# ════════════════════════════════════════════════════════════════════
# Rendering helpers
# ════════════════════════════════════════════════════════════════════

def render_header(subtitle: str = "") -> str:
    """Render the top header bar."""
    try:
        from universal_runtime import detect_environment
        env_profile = detect_environment().get("profile", "unknown")
    except Exception:
        env_profile = os.environ.get("ZCLAUDE_PROFILE", "desktop")

    w = Term.width()
    title = C.bold(C.magenta(" 🔮 zClaude Universal Launcher "))
    ver = C.dim(f"v{VERSION}")
    env_tag = C.dim(f"· {env_profile}")
    sub = f"  {C.dim(subtitle)}" if subtitle else ""

    # Pad to full width
    inner = f"{title}{ver}{env_tag}{sub}"
    padding = w - len(inner) - 2
    return f"{inner}{' ' * max(padding, 0)}"


def render_footer(hint: str = "↑↓ Navigate · Enter Select · Esc Back · q Quit") -> str:
    """Render the bottom footer bar."""
    w = Term.width()
    text = C.dim(f"  {hint}")
    padding = w - len(text) - 2
    return f"{text}{' ' * max(padding, 0)}"


def render_message(state: Dict) -> str:
    """Render the status message area (one line)."""
    msg = state.get("message", "")
    if not msg:
        return ""
    color = state.get("message_color", "info")
    return f"\n  {C.c(color, msg)}"


def draw_screen(lines: List[str], footer_hint: str = "") -> None:
    """Complete screen render: clear, print header+body+footer.

    This is called by every screen handler to produce output.
    """
    Term.clear()
    body = "\n".join(lines)
    print(body)
    if state.get("message"):
        print(render_message(state))
    print()
    print(render_footer(footer_hint))
    sys.stdout.flush()


def box_section(title: str, content_lines: List[str],
                width: int = None) -> str:
    """Quick helper: wrap content in a Box.panel."""
    w = width or (Term.width() - 4)
    return Box.panel(title, content_lines, w)


def load_endpoints_for_ui() -> Dict[str, Any]:
    """Load endpoints from config, normalizing format.

    Handles both flat-dict and list-based endpoints.json formats.
    """
    # Try provider_manager's loader first (handles both formats)
    try:
        from provider_manager import load_endpoints as pm_load
        result = pm_load()
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    # Fallback: direct file read
    cfg_path = os.path.expanduser("~/.codex/endpoints.json")
    try:
        with open(cfg_path, "r") as f:
            data = json.load(f)

        # List format: {"endpoints": [...], "default": "..."}
        if isinstance(data, dict) and "endpoints" in data:
            flat = {}
            for ep in data["endpoints"]:
                if isinstance(ep, dict):
                    name = ep.get("name", "unnamed")
                    flat[name] = ep
            return flat

        # Already flat dict
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return {}


def save_endpoints_for_ui(endpoints: Dict) -> bool:
    """Save endpoints back to config."""
    try:
        from provider_manager import save_endpoints as pm_save
        pm_save(endpoints)
        return True
    except Exception:
        pass

    cfg_path = os.path.expanduser("~/.codex/endpoints.json")
    try:
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        # Save in list format (what existing code expects)
        output = {
            "endpoints": list(endpoints.values()),
            "default": "",
        }
        with open(cfg_path, "w") as f:
            json.dump(output, f, indent=2)
        return True
    except Exception:
        return False


def get_presets_list() -> List[tuple]:
    """Return list of (name, preset_dict) from presets module."""
    try:
        from lib.presets import PROVIDER_PRESETS
        return [(k, v) for k, v in PROVIDER_PRESETS.items() if k != "Custom"]
    except Exception:
        return []


# ════════════════════════════════════════════════════════════════════
# Input helper
# ════════════════════════════════════════════════════════════════════

def read_key() -> str:
    """Read a single keystroke with fallback."""
    try:
        return Keyboard.getkey()
    except Exception:
        try:
            # Fallback for environments without full keyboard support
            line = input("").strip()
            return line[0] if line else ""
        except EOFError:
            return "q"


def wait_key(hint: str = "Press any key...") -> str:
    """Show hint and wait for a keypress."""
    print(C.dim(f"\n  {hint}"), end="", flush=True)
    return read_key()


# ════════════════════════════════════════════════════════════════════
# SCREEN 1: Dashboard
# ════════════════════════════════════════════════════════════════════

def screen_dashboard(state: Dict) -> Optional[str]:
    """Main dashboard — tool status table, active provider summary, actions."""

    def render() -> List[str]:
        lines: List[str] = []
        w = Term.width()

        # Header
        lines.append(render_header())
        lines.append("")

        # ── Tool Detection Table ──────────────────────────────
        tools = state["tools"]
        installed_count = sum(1 for t in tools if t.installed)

        lines.append(f"  {C.bold('🛠️  Coding Tools Detected')}"
                     f"{C.dim(f' ({installed_count}/{len(tools)} installed)')}")
        lines.append("")

        if tools:
            for idx, tool in enumerate(tools):
                lines.append(format_tool_row(tool, idx))
        else:
            lines.append(f"  {C.dim('No tools found in registry.')}")

        lines.append("")

        # ── Active Provider ───────────────────────────────────
        default_name = state.get("default_provider", "")
        endpoints = state.get("endpoints", {})
        prov_cfg = endpoints.get(default_name, {}) if default_name else {}

        if prov_cfg:
            backend = prov_cfg.get("backend_type", "?")
            model = prov_cfg.get("default_model", "?")
            url = prov_cfg.get("base_url", "?")
            lines.append(f"  {C.bold('⭐ Active Provider')}"
                         f"{C.dim(f'                              {default_name}')}")
            lines.append(f"     {C.c('accent', backend)}"
                         f"{C.dim(f' · {model}')}"
                         f"{C.dim(f' · {url[:50]}')}")
        else:
            lines.append(f"  {C.bold('⭐ Active Provider')}"
                         f"{C.dim('                              None configured')}")

        lines.append("")

        # ── Last launch status ────────────────────────────────
        pid = state.get("last_launch_pid", 0)
        if pid:
            proxy_info = ""
            if state.get("proxy_running"):
                port = state.get("proxy_port", 0)
                proxy_info = C.success(f"· Proxy on :{port}")
            lines.append(f"  {C.dim(f'Last launch: PID={pid} {proxy_info}')}")

        lines.append("")

        # ── Action Menu ───────────────────────────────────────
        lines.append(f"  {C.dim('─' * (w - 4))}")
        lines.append("")
        for key, icon, label, _target in ACTIONS:
            if label == "Quit":
                continue
            line = f"  [{C.bold(key)}] {icon} {label}"
            lines.append(line)
        lines.append(f"  [{C.bold('q')}] Quit")

        return lines

    while True:
        lines = render()
        Term.clear()
        print("\n".join(lines))
        print(render_message(state))
        print()
        print(render_footer("1-4 Select · q Quit"))
        sys.stdout.flush()

        key = read_key()

        # Number shortcuts
        if key == "1":
            return SCREEN_TOOL_SELECT
        if key == "2":
            return SCREEN_PROVIDERS
        if key == "3":
            return SCREEN_SESSIONS
        if key == "4":
            return SCREEN_SETTINGS
        if key in ("q", "Q"):
            return SCREEN_EXIT
        if key in ("escape",):
            return SCREEN_EXIT

        state["message"] = f"Unknown key: {key}. Press 1-4 or q."
        state["message_color"] = "warn"


# ════════════════════════════════════════════════════════════════════
# SCREEN 2: Provider Browser
# ════════════════════════════════════════════════════════════════════

def screen_provider_browse(state: Dict) -> Optional[str]:
    """Browse, activate, add, edit providers. Paginated list with cursor."""

    def render() -> List[str]:
        lines: List[str] = []
        endpoints = state["endpoints"]
        presets = get_presets_list()

        lines.append(render_header("Provider Browser"))
        lines.append("")

        # ── Configured Providers ──────────────────────────────
        if endpoints:
            lines.append(f"  {C.bold('📡 Configured Providers')}"
                         f"{C.dim(f' ({len(endpoints)} configured)')}")
            lines.append("")

            items = list(endpoints.items())
            cursor = state.get("cursor", 0)
            for idx, (name, cfg) in enumerate(items):
                marker = "▸ " if idx == cursor else "  "
                backend = cfg.get("backend_type", "?")
                model = cfg.get("default_model", "--")
                has_key = bool(cfg.get("api_key", ""))
                key_icon = C.success("🔑") if has_key else C.error("◇")

                is_default = name == state.get("default_provider", "")
                default_tag = C.success(" ★ DEFAULT") if is_default else ""

                sel = C.highlight(name) if idx == cursor else name
                line = (f"{marker}{key_icon} {sel}"
                        f"{C.dim(f' [{backend}] {model}')}"
                        f"{default_tag}")
                lines.append(line)
        else:
            lines.append(f"  {C.dim('No providers configured yet.')}")
            lines.append(f"  {C.dim('Add one below or activate a preset!')}")

        lines.append("")

        # ── Quick-activate Presets ────────────────────────────
        if presets:
            lines.append(f"  {C.bold('✨ One-click Presets')}"
                         f"{C.dim(f' ({len(presets)} available)')}")
            lines.append("")
            # Show first few presets
            for idx, (pname, pcfg) in enumerate(presets[:6]):
                pbackend = pcfg.get("backend_type", "?")
                pmodels = pcfg.get("models", [])
                mcount = len(pmodels) if pmodels else 0
                lines.append(
                    f"    {C.dim(f'{idx + 1}.')} {C.c('secondary', pname)}"
                    f"{C.dim(f' [{pbackend}, {mcount} models]')}"
                )
            if len(presets) > 6:
                lines.append(f"    {C.dim(f'... and {len(presets) - 6} more')}")

        lines.append("")
        lines.append(f"  {C.dim('─' * (Term.width() - 4))}")
        lines.append("")
        lines.append("  [Enter] Activate  [a] Add Custom  [e] Edit  [d] Delete")
        lines.append("  [1-6] Quick-add Preset  [Esc/q] Back")

        return lines

    # Reset cursor when entering screen
    if state.get("cursor", 0) >= len(state.get("endpoints", {})):
        state["cursor"] = 0

    while True:
        lines = render()
        Term.clear()
        print("\n".join(lines))
        print(render_message(state))
        sys.stdout.flush()

        key = read_key()
        endpoints = state["endpoints"]
        n_items = len(endpoints)
        cursor = state.get("cursor", 0)

        # Navigation
        if key in ("up", "k", "K"):
            state["cursor"] = max(0, cursor - 1) if n_items > 0 else 0
        elif key in ("down", "j", "J"):
            state["cursor"] = min(n_items - 1, cursor + 1) if n_items > 0 else 0
        elif key == "enter" and n_items > 0:
            # Set as default (activate)
            items = list(endpoints.items())
            name = items[cursor][0]
            state["default_provider"] = name
            state["selected_provider"] = name
            state["message"] = f"Activated: {name}"
            state["message_color"] = "success"
        elif key == "a" or key == "A":
            return SCREEN_PROVIDER_ADD
        elif key == "e" or key == "E":
            if n_items > 0:
                items = list(endpoints.items())
                state["selected_provider"] = items[cursor][0]
                return SCREEN_PROVIDER_EDIT
            else:
                state["message"] = "No provider to edit. Add one first."
                state["message_color"] = "warn"
        elif key == "d" or key == "D":
            if n_items > 0:
                items = list(endpoints.items())
                name = items[cursor][0]
                del endpoints[name]
                save_endpoints_for_ui(endpoints)
                state["endpoints"] = endpoints
                state["cursor"] = min(cursor, max(0, n_items - 2))
                state["message"] = f"Deleted: {name}"
                state["message_color"] = "success"
        elif key in ("escape", "q", "Q"):
            return SCREEN_DASHBOARD
        # Numeric: quick-add preset
        elif key.isdigit():
            num = int(key)
            presets = get_presets_list()
            if 1 <= num <= len(presets):
                pname, pcfg = presets[num - 1]
                # Copy preset into endpoints (user still needs API key)
                new_cfg = dict(pcfg)
                new_cfg["api_key"] = ""  # Must be filled by user
                endpoints[pname] = new_cfg
                save_endpoints_for_ui(endpoints)
                state["endpoints"] = endpoints
                state["message"] = (
                    f"Preset '{pname}' added! Go to Edit to set your API key."
                )
                state["message_color"] = "info"
            elif 1 <= num <= 6 and len(presets) >= num:
                # Same logic but limited display range
                pass


# ════════════════════════════════════════════════════════════════════
# SCREEN 3: Provider Add (delegates to provider_manager wizard)
# ════════════════════════════════════════════════════════════════════

def screen_provider_add(state: Dict) -> Optional[str]:
    """Add a new custom provider using guided wizard."""

    print(render_header("Add Provider"))
    print()
    print(C.bold("  Adding a new AI provider..."))
    print()
    print(C.dim("  This will open the provider setup wizard."))
    print()

    try:
        from provider_manager import cmd_wizard, banner
        banner()
        # Run the wizard — it handles its own I/O
        new_ep = cmd_wizard()
        if new_ep:
            name = new_ep.get("name", "new-provider")
            state["endpoints"][name] = new_ep
            save_endpoints_for_ui(state["endpoints"])
            state["message"] = f"Provider '{name}' added successfully!"
            state["message_color"] = "success"
        else:
            state["message"] = "Provider addition cancelled."
            state["message_color"] = "muted"
    except Exception as exc:
        state["message"] = f"Wizard error: {exc}"
        state["message_color"] = "error"

    wait_key("Press any key to continue...")
    return SCREEN_PROVIDERS


# ════════════════════════════════════════════════════════════════════
# SCREEN 4: Provider Edit (delegates to provider_manager)
# ════════════════════════════════════════════════════════════════════

def screen_provider_edit(state: Dict) -> Optional[str]:
    """Edit an existing provider's configuration."""

    name = state.get("selected_provider", "")
    if not name:
        state["message"] = "No provider selected."
        state["message_color"] = "warn"
        return SCREEN_PROVIDERS

    cfg = state["endpoints"].get(name, {})
    if not cfg:
        state["message"] = f"Provider '{name}' not found."
        state["message_color"] = "error"
        return SCREEN_PROVIDERS

    print(render_header(f"Edit Provider: {name}"))
    print()

    # Show current config
    print(f"  {C.bold('Name:')} {name}")
    print(f"  {C.bold('Backend:')} {cfg.get('backend_type', '?')}")
    print(f"  {C.bold('URL:')} {cfg.get('base_url', '?')}")
    print(f"  {C.bold('Model:')} {cfg.get('default_model', '?')}")
    print(f"  {C.bold('API Key:')} {'***' + (cfg.get('api_key', '')[-4:] if len(cfg.get('api_key', '')) > 4 else '(not set)')}")
    models = cfg.get("models", [])
    print(f"  {C.bold('Models:')} {', '.join(models[:8])}{f' ... (+{len(models)-8})' if len(models) > 8 else '' or '(none)'}")
    print()

    try:
        from provider_manager import cmd_edit, banner
        banner()
        updated = cmd_edit(name)
        if updated:
            state["endpoints"][name] = updated
            save_endpoints_for_ui(state["endpoints"])
            state["message"] = f"Provider '{name}' updated!"
            state["message_color"] = "success"
        else:
            state["message"] = "Edit cancelled or no changes."
            state["message_color"] = "muted"
    except Exception as exc:
        state["message"] = f"Edit error: {exc}"
        state["message_color"] = "error"

    wait_key("Press any key to continue...")
    return SCREEN_PROVIDERS


# ════════════════════════════════════════════════════════════════════
# SCREEN 5: Tool Selection
# ════════════════════════════════════════════════════════════════════

def screen_tool_select(state: Dict) -> Optional[str]:
    """Select which coding tool to launch."""

    installed = state["installed_tools"]
    all_tools = state["tools"]

    def render() -> List[str]:
        lines: List[str] = []
        cursor = state.get("cursor", 0)

        lines.append(render_header("Select Coding Tool"))
        lines.append("")

        # Show installed tools first, then uninstalled
        lines.append(f"  {C.bold('✓ Installed Tools')}")
        for idx, tool in enumerate(installed):
            marker = "▸ " if idx == cursor else "  "
            native_tag = "/ native" if tool.supports_native else ""
            sel = C.highlight(f"{tool.icon} {tool.display_name}") if idx == cursor else f"{tool.icon} {tool.display_name}"
            lines.append(f"{marker}{sel}"
                         f"{C.dim(f' v{tool.version}{native_tag}')}")

        if not installed:
            lines.append(f"  {C.dim('  No tools detected on PATH.')}")

        lines.append("")
        offset = len(installed)

        # Show uninstalled (dimmed)
        unavailable = [t for t in all_tools if not t.installed]
        if unavailable:
            lines.append(f"  {C.dim('○ Not Installed')}")
            for idx, tool in enumerate(unavailable):
                abs_idx = offset + idx
                marker = "▸ " if abs_idx == cursor else "  "
                lines.append(f"{marker}{C.dim(f'{tool.icon} {tool.display_name}')}"
                             f"{C.dim('  (not found)')}")

        lines.append("")
        lines.append(f"  {C.dim('─' * (Term.width() - 4))}")
        lines.append("")
        lines.append("  [Enter] Select & Continue  [r] Re-scan  [Esc/q] Back")

        return lines

    if not installed:
        state["cursor"] = 0

    while True:
        lines = render()
        Term.clear()
        print("\n".join(lines))
        print(render_message(state))
        sys.stdout.flush()

        key = read_key()
        total = len(all_tools)
        cursor = state.get("cursor", 0)

        if key in ("up", "k", "K"):
            state["cursor"] = max(0, cursor - 1)
        elif key in ("down", "j", "J"):
            state["cursor"] = min(total - 1, cursor + 1)
        elif key == "enter":
            if total > 0:
                selected = all_tools[cursor]
                if selected.installed:
                    state["selected_tool"] = selected
                    state["message"] = f"Selected: {selected.display_name}"
                    state["message_color"] = "success"
                    return SCREEN_LAUNCH
                else:
                    state["message"] = (
                        f"{selected.display_name} is not installed. "
                        f"Install it first or pick another tool."
                    )
                    state["message_color"] = "warn"
        elif key in ("r", "R"):
            # Re-scan tools
            state["tools"] = detect_all_tools()
            state["installed_tools"] = get_installed_tools()
            state["message"] = f"Re-scanned: {summary_line(state['tools'])}"
            state["message_color"] = "info"
        elif key in ("escape", "q", "Q"):
            return SCREEN_DASHBOARD


# ════════════════════════════════════════════════════════════════════
# SCREEN 6: Launch Confirm
# ════════════════════════════════════════════════════════════════════

def screen_launch_confirm(state: Dict) -> Optional[str]:
    """Review launch plan, toggle options, confirm & execute."""

    tool = state.get("selected_tool")
    if not tool:
        state["message"] = "No tool selected. Go back and pick one."
        state["message_color"] = "error"
        return SCREEN_TOOL_SELECT

    provider_name = state.get("selected_provider") or state.get("default_provider", "")
    endpoints = state.get("endpoints", {})
    prov_cfg = endpoints.get(provider_name, {})

    if not prov_cfg:
        state["message"] = "No provider available. Configure one first."
        state["message_color"] = "error"
        return SCREEN_PROVIDERS

    options = state.get("launch_options", LaunchOptions())

    def render() -> List[str]:
        lines: List[str] = []

        lines.append(render_header("Launch Confirmation"))
        lines.append("")

        # ── Summary ─────────────────────────────────────────
        lines.append(f"  {C.bold('🚀 Launch Configuration')}")
        lines.append("")
        lines.append(f"    Tool:      {C.highlight(f'{tool.icon} {tool.display_name}')}"
                     f"{C.dim(f' (v{tool.version})')}")
        prov_backend = prov_cfg.get("backend_type", "?")
        prov_info = C.c('secondary', provider_name) + C.dim(" [" + prov_backend + "]")
        lines.append(f"    Provider:  {prov_info}")
        lines.append(f"    Model:     {C.bold(prov_cfg.get('default_model', 'default'))}")

        # Compatibility check
        from lib.tool_launcher import check_compatibility
        compat = check_compatibility(tool, prov_cfg)
        mode = compat.get("mode", "?")
        mode_color = "success" if mode == "native" else "info"
        compat_reason = compat.get("reason", "")
        mode_text = C.c(mode_color, mode.upper()) + C.dim(" — " + compat_reason)
        lines.append(f"    Mode:      {mode_text}")
        for w in compat.get("warnings", []):
            warn_text = C.warn("⚠ " + w)
            lines.append(f"              {warn_text}")

        lines.append("")

        # ── Options toggles ─────────────────────────────────
        lines.append(f"  {C.bold('⚙ Options')}")
        lines.append(f"    [c] Caveman Mode:     {'ON ' if options.caveman_mode else 'OFF'}")
        lines.append(f"    [r] RTK Compression:  {'ON ' if options.rtk_compression else 'OFF'}")
        lines.append(f"    [e] Reasoning Effort: {options.reasoning_effort}")
        lines.append(f"    [s] Sandbox Mode:     {'ON ' if options.sandbox_mode else 'OFF'}")
        lines.append(f"    [a] Approval Mode:    {options.approval_mode}")

        lines.append("")
        lines.append(f"  {C.dim('─' * (Term.width() - 4))}")
        lines.append("")
        lines.append(f"  [Enter] {C.success('LAUNCH')}  [c/r/e/s/a] Toggle  [p] Change Provider  [Esc] Back")

        return lines

    while True:
        lines = render()
        Term.clear()
        print("\n".join(lines))
        print(render_message(state))
        sys.stdout.flush()

        key = read_key()

        if key == "enter":
            # Execute launch!
            try:
                plan = build_launch_plan(tool, provider_name, prov_cfg, options)
                pid = execute_launch(plan, logfn=lambda msg: print(f"  {msg}"))
                if pid:
                    state["last_launch_pid"] = pid
                    state["proxy_running"] = plan.use_proxy
                    state["proxy_port"] = plan.proxy_port or 0
                    state["message"] = f"Launched {tool.display_name} (PID={pid})"
                    state["message_color"] = "success"
                else:
                    state["message"] = "Launch failed. Check configuration."
                    state["message_color"] = "error"
            except Exception as exc:
                state["message"] = f"Launch error: {exc}"
                state["message_color"] = "error"

            wait_key("Press any key to return to dashboard...")
            return SCREEN_DASHBOARD

        elif key == "c" or key == "C":
            options.caveman_mode = not options.caveman_mode
        elif key == "r" or key == "R":
            options.rtk_compression = not options.rtk_compression
        elif key == "e" or key == "E":
            # Cycle reasoning effort
            efforts = ["low", "medium", "high"]
            idx = (efforts.index(options.reasoning_effort) + 1) % len(efforts)
            options.reasoning_effort = efforts[idx]
        elif key == "s" or key == "S":
            options.sandbox_mode = not options.sandbox_mode
        elif key == "a" or key == "A":
            modes = ["default", "auto-accept", "edit-only"]
            idx = (modes.index(options.approval_mode) + 1) % len(modes)
            options.approval_mode = modes[idx]
        elif key == "p" or key == "P":
            # Change provider — go back to provider browse
            return SCREEN_PROVIDERS
        elif key in ("escape", "q", "Q"):
            return SCREEN_TOOL_SELECT


# ════════════════════════════════════════════════════════════════════
# SCREEN 7: Sessions
# ════════════════════════════════════════════════════════════════════

def screen_sessions(state: Dict) -> Optional[str]:
    """Browse recent coding sessions across all tools."""

    def load_sessions() -> list:
        try:
            from session_manager import scan_all
            sessions = scan_all()
            # Sort by last_active descending
            sessions.sort(key=lambda s: s.last_active, reverse=True)
            return sessions
        except Exception:
            return []

    def render() -> List[str]:
        lines: List[str] = []
        sessions = state.get("sessions", [])
        cursor = state.get("cursor", 0)

        lines.append(render_header("Recent Sessions"))
        lines.append("")

        if sessions:
            lines.append(f"  {C.bold(f'📂 {len(sessions)} sessions found')}")
            lines.append("")

            page_size = Term.height() - 10
            start = cursor
            end = min(start + page_size, len(sessions))

            for idx in range(start, end):
                s = sessions[idx]
                marker = "▸ " if idx == cursor else "  "

                # Format timestamp
                import datetime
                dt = datetime.datetime.fromtimestamp(s.last_active)
                time_str = dt.strftime("%Y-%m-%d %H:%M")

                provider_icon = {"codex": "C", "claude": "c", "gemini": "G"}.get(s.provider, "?")
                line = (
                    f"{marker}{C.c('secondary', provider_icon)}"
                    f" {s.title[:50]}"
                    f"{C.dim(f' · {s.model}')}"
                    f"{C.dim(f' · {time_str}')}"
                )
                lines.append(line)

            if len(sessions) > page_size:
                lines.append(f""
                            f"  {C.dim(f'Showing {start+1}-{end} of {len(sessions)}')}")
        else:
            lines.append(f"  {C.dim('  No sessions found.')}")
            lines.append(f"  {C.dim('  Sessions appear after you use coding tools.')}")

        lines.append("")
        lines.append(f"  {C.dim('─' * (Term.width() - 4))}")
        lines.append("")
        lines.append("  [r] Refresh  [↑↓] Navigate  [Enter] Resume  [Esc/q] Back")

        return lines

    # Load sessions on first enter
    if not state.get("sessions"):
        state["sessions"] = load_sessions()

    while True:
        lines = render()
        Term.clear()
        print("\n".join(lines))
        print(render_message(state))
        sys.stdout.flush()

        key = read_key()
        sessions = state.get("sessions", [])
        n = len(sessions)
        cursor = state.get("cursor", 0)

        if key in ("up", "k", "K"):
            state["cursor"] = max(0, cursor - 1)
        elif key in ("down", "j", "J"):
            state["cursor"] = min(max(0, n - 1), cursor + 1)
        elif key == "enter" and n > 0:
            s = sessions[cursor]
            resume_cmd = getattr(s, 'resume_cmd', '')
            state["message"] = f"To resume: {resume_cmd or s.session_id}"
            state["message_color"] = "info"
        elif key in ("r", "R"):
            state["sessions"] = load_sessions()
            state["message"] = "Sessions refreshed."
            state["message_color"] = "success"
        elif key in ("escape", "q", "Q"):
            return SCREEN_DASHBOARD


# ════════════════════════════════════════════════════════════════════
# SCREEN 8: Settings
# ════════════════════════════════════════════════════════════════════

def screen_settings(state: Dict) -> Optional[str]:
    """Launcher preferences and settings."""

    def render() -> List[str]:
        lines: List[str] = []

        lines.append(render_header("Settings"))
        lines.append("")

        # ── Info ───────────────────────────────────────────
        lines.append(f"  {C.bold('ℹ zClaude Info')}")
        lines.append(f"    Version:     {VERSION}")
        lines.append(f"    Python:      {sys.version.split()[0]}")
        lines.append(f"    Platform:    {sys.platform}")
        lines.append(f"    Config dir:  {os.path.expanduser('~/.codex/')}")
        lines.append("")

        # ── Defaults ────────────────────────────────────────
        lines.append(f"  {C.bold('Defaults')}")
        default_prov = state.get("default_provider", "(none)")
        lines.append(f"    Default provider: {C.c('secondary', default_prov)}")
        opts = state.get("launch_options", LaunchOptions())
        lines.append(f"    Reasoning effort: {opts.reasoning_effort}")
        lines.append(f"    Approval mode:    {opts.approval_mode}")
        lines.append("")

        # ── Stats ──────────────────────────────────────────
        tools = state.get("tools", [])
        inst = sum(1 for t in tools if t.installed)
        eps = state.get("endpoints", {})
        lines.append(f"  {C.bold('Stats')}")
        lines.append(f"    Tools detected:  {inst}/{len(tools)}")
        lines.append(f"    Providers:       {len(eps)}")
        lines.append(f"    Proxy running:   {'Yes :' + str(state.get('proxy_port', '')) if state.get('proxy_running') else 'No'}")
        lines.append(f"    Last launch PID: {state.get('last_launch_pid', 'N/A')}")

        lines.append("")
        lines.append(f"  {C.dim('─' * (Term.width() - 4))}")
        lines.append("")
        lines.append("  [d] Set Default Provider  [o] Toggle Options  [Esc/q] Back")

        return lines

    while True:
        lines = render()
        Term.clear()
        print("\n".join(lines))
        print(render_message(state))
        sys.stdout.flush()

        key = read_key()

        if key == "d" or key == "D":
            # Set default provider — jump to provider browser
            return SCREEN_PROVIDERS
        elif key == "o" or key == "O":
            # Toggle global defaults
            opts = state.get("launch_options", LaunchOptions())
            efforts = ["low", "medium", "high"]
            idx = (efforts.index(opts.reasoning_effort) + 1) % len(efforts)
            opts.reasoning_effort = efforts[idx]
            state["launch_options"] = opts
            state["message"] = f"Default reasoning effort: {opts.reasoning_effort}"
            state["message_color"] = "info"
        elif key in ("escape", "q", "Q"):
            return SCREEN_DASHBOARD


# ════════════════════════════════════════════════════════════════════
# Screen Router — maps screen IDs to handler functions
# ════════════════════════════════════════════════════════════════════

SCREEN_HANDLERS: Dict[Optional[str], Callable] = {
    SCREEN_DASHBOARD:     screen_dashboard,
    SCREEN_PROVIDERS:     screen_provider_browse,
    SCREEN_PROVIDER_ADD:  screen_provider_add,
    SCREEN_PROVIDER_EDIT: screen_provider_edit,
    SCREEN_TOOL_SELECT:   screen_tool_select,
    SCREEN_LAUNCH:        screen_launch_confirm,
    SCREEN_SESSIONS:      screen_sessions,
    SCREEN_SETTINGS:      screen_settings,
}


# ════════════════════════════════════════════════════════════════════
# Main entry point & event loop
# ════════════════════════════════════════════════════════════════════

def run_launcher(initial_screen: str = SCREEN_DASHBOARD) -> None:
    """Main screen loop. Runs until a handler returns SCREEN_EXIT (None)."""
    state = create_state()

    # Initialize: detect tools, load providers
    state["tools"] = detect_all_tools()
    state["installed_tools"] = get_installed_tools()
    state["endpoints"] = load_endpoints_for_ui()

    # Detect default provider
    eps = state["endpoints"]
    if eps:
        first_name = next(iter(eps.keys()))
        state["default_provider"] = first_name

    current_screen = initial_screen

    try:
        while current_screen is not None:
            handler = SCREEN_HANDLERS.get(current_screen)
            if handler is None:
                print(C.error(f"Unknown screen: {current_screen}"))
                break
            current_screen = handler(state)
    except KeyboardInterrupt:
        print(f"\n\n{C.dim('  Interrupted. Goodbye! 👋')}")
    except Exception as exc:
        print(f"\n{C.error(f'  Launcher error: {exc}')}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup: stop proxy if running
        try:
            from lib.proxy_lifecycle import stop_proxy
            stop_proxy()
        except Exception:
            pass

        print(f"\n  {C.dim('Thanks for using zClaude! ✨')}\n")


def main() -> None:
    """Entry point."""
    # Auto-detect terminal capabilities
    C._detect()

    # Print welcome banner
    print()
    print(C.bold(C.magenta(
        "  ╔══════════════════════════════════════════════════╗"
    )))
    print(C.bold(C.magenta(
        "  ║     🔮 zClaude Universal Launcher v" + VERSION + "        ║"
    )))
    print(C.bold(C.magenta(
        "  ╚══════════════════════════════════════════════════╝"
    )))
    print()

    # Check for --help flag
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "help"):
        print(C.bold("  Usage:"))
        print(f"    python3 {os.path.basename(__file__)}")
        print()
        print(C.bold("  Screens:"))
        print("    Dashboard   — Tool status, active provider, quick actions")
        print("    Providers   — Browse, add, edit, activate AI providers")
        print("    Tool Select — Pick which coding CLI to launch")
        print("    Launch      — Review config & launch tool with provider")
        print("    Sessions    — Browse recent coding sessions")
        print("    Settings    — Preferences, defaults, info")
        print()
        print(C.bold("  Keys:"))
        print("    ↑↓/jk     Navigate    Enter    Select/Confirm")
        print("    Esc/q     Back/Quit    1-4      Dashboard shortcuts")
        print()
        return

    run_launcher()


if __name__ == "__main__":
    main()
