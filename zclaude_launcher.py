#!/usr/bin/env python3
"""zClaude Universal Launcher — OpenCode/Crush-inspired TUI.

One command:  python3 zclaude_launcher.py

Visual design language (modeled after OpenCode/Crush by Charm):
  - Dark theme: #1e2327 bg, #89b4fa accent, #cdd6f4 text
  - Sidebar + Main content split layout
  - Rounded border panels (╭╮╰╯) with double-line headers
  - Dialog overlays for selections (provider picker, tool picker, model chooser)
  - Status bar at bottom with keyboard hints
  - Consistent spacing, typography, and color hierarchy

Screens:
  [Dashboard]    Split view: sidebar nav + main dashboard panel
  [Providers]     Provider browser with dialog overlay for add/edit
  [Tool Select]   Tool picker dialog with auto-detection status
  [Launch]        Launch confirmation dialog with option toggles
  [Sessions]      Session browser with resume capability
  [Settings]      Settings panel with info/stats

Navigation:
  Tab/Shift-Tab or ←/→ : switch sidebar focus
  ↑↓/j/k           : navigate within panel
  Enter             : select / activate / confirm
  Esc/q             : back / exit
  1-4               : quick action shortcuts
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lib.tui_engine import (
    T, Theme, Term, Box, Layout, Keyboard, Menu,
    Screen, Sidebar, SidebarItem, StatusBar,
    Spinner, ProgressBar,
)
from lib.tool_detector import (
    ToolInfo, TOOL_REGISTRY, detect_all_tools, get_installed_tools,
)
from lib.tool_launcher import (
    LaunchOptions, LaunchPlan, build_launch_plan,
    check_compatibility, execute_launch, ENV_MAP, TOOL_COMMANDS,
)


# ═══════════════════════════════════════════════════════════════
# Constants & Screen IDs
# ═══════════════════════════════════════════════════════════════

VERSION = "1.0.0"
SCREEN_EXIT = None

SCREEN_DASHBOARD = "dashboard"
SCREEN_PROVIDERS = "providers"
SCREEN_PROVIDER_ADD = "provider_add"
SCREEN_PROVIDER_EDIT = "provider_edit"
SCREEN_TOOL_SELECT = "tool_select"
SCREEN_LAUNCH = "launch"
SCREEN_SESSIONS = "sessions"
SCREEN_SETTINGS = "settings"


# ═══════════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════════

def create_state() -> Dict[str, Any]:
    return {
        "tools": [],
        "installed_tools": [],
        "endpoints": {},
        "default_provider": "",
        "selected_tool": None,
        "selected_provider": None,
        "launch_options": LaunchOptions(),
        "sessions": [],
        "sidebar_idx": 0,       # which sidebar item is focused
        "list_cursor": 0,        # cursor position in lists
        "page": 0,
        "message": "",
        "message_color": "info",
        "last_pid": 0,
        "proxy_running": False,
        "proxy_port": 0,
        "dialog": None,          # active overlay dialog
        "dialog_data": {},       # dialog-specific data
    }


# Sidebar navigation items
def build_sidebar_items(state: Dict) -> List[SidebarItem]:
    """Build sidebar from current context."""
    inst_count = len(state.get("installed_tools", []))
    prov_count = len(state.get("endpoints", {}))
    has_proxy = state.get("proxy_running", False)

    return [
        SidebarItem("◉", "Dashboard", "1", active=(state.get("dialog") is None),
                   badge=f"{inst_count}"),
        SidebarItem("⚡", "Launch Tool", "2",
                   badge="▸" if state.get("selected_tool") else "",
                   active=False),
        SidebarItem("📡", "Providers", "3",
                   badge=str(prov_count),
                   active=False),
        SidebarItem("📂", "Sessions", "4",
                   badge=str(len(state.get("sessions", []))),
                   active=False),
        SidebarItem("⚙", "Settings", "5", active=False),
    ]


# ═══════════════════════════════════════════════════════════════
# Data loading helpers
# ═══════════════════════════════════════════════════════════════

def load_endpoints() -> Dict[str, Any]:
    """Load endpoints, normalizing both flat-dict and list formats."""
    try:
        from provider_manager import load_endpoints as pm_load
        result = pm_load()
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    cfg_path = os.path.expanduser("~/.codex/endpoints.json")
    try:
        with open(cfg_path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict) and "endpoints" in data:
            flat = {}
            for ep in data["endpoints"]:
                if isinstance(ep, dict):
                    flat[ep.get("name", "unnamed")] = ep
            return flat
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def save_endpoints(endpoints: Dict) -> bool:
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
        output = {"endpoints": list(endpoints.values()), "default": ""}
        with open(cfg_path, "w") as f:
            json.dump(output, f, indent=2)
        return True
    except Exception:
        return False


def get_presets() -> List[tuple]:
    """Get provider presets list."""
    try:
        from lib.presets import PROVIDER_PRESETS
        return [(k, v) for k, v in PROVIDER_PRESETS.items() if k != "Custom"]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# Rendering helpers — OpenCode-style panels
# ═══════════════════════════════════════════════════════════════

def render_header_line(title: str = "", subtitle: str = "") -> str:
    """Top header bar line (double-border style)."""
    w = Term.w()
    if subtitle:
        text = (
            f"{T.bold(T.mauve(' ◆ '))}{T.title(title)} "
            f"{T.muted(subtitle)}"
        )
    else:
        text = (
            f"{T.bold(T.mauve(' ◆ '))}{T.title(title)} "
            f"{T.muted(f'v{VERSION}')}"
        )
    pad = w - _strip_ansi(text)
    return (
        f"{T.BG_DARK}{T.BORDER_FOCUS}{text}"
        f"{' ' * max(0, pad)}{T.BORDER_FOCUS}{T.RESET}"
    )


def tool_status_icon(tool: ToolInfo) -> str:
    """Return a single-char status indicator for a tool."""
    if tool.installed:
        if tool.supports_native:
            return f"{T.success('●')}"
        else:
            return f"{T.primary('●')}"
    return f"{T.dim('○')}"


def format_tool_list_item(tool: ToolInfo, idx: int, cursor: int) -> str:
    """Format one tool as a list item line."""
    is_cursor = (idx == cursor)
    icon = tool_status_icon(tool)
    name = T.bold(tool.display_name) if is_cursor else tool.display_name
    ver = tool.version if tool.installed else T.dim("--")
    backend = tool.backend_preference.upper()
    native = "/native" if tool.supports_native else ""
    marker = "▸ " if is_cursor else "  "

    if is_cursor:
        return (
            f"{marker}{T.BG_HIGHLIGHT}{icon} {name}"
            f" {T.dim(ver)} {T.dim(backend + native)}{T.RESET}"
        )
    else:
        return f"{marker}{icon} {T.text(name)} {T.dim(ver)}"


def format_provider_item(name: str, cfg: Dict, idx: int,
                       cursor: int, is_default: bool) -> str:
    """Format one provider as a list item."""
    is_cursor = (idx == cursor)
    backend = cfg.get("backend_type", "?")
    model = cfg.get("default_model", "--")
    has_key = bool(cfg.get("api_key", ""))
    key_icon = T.success("🔑") if has_key else T.error("◇")
    star = T.secondary("★") if is_default else ""

    marker = "▸ " if is_cursor else "  "
    name_fmt = T.bold(name) if is_cursor else T.text(name)
    details = f"{T.dim(backend)} · {T.dim(model)}"

    if is_cursor:
        return (
            f"{marker}{T.BG_HIGHLIGHT}{key_icon} {name_fmt}"
            f" {details}{star}{T.RESET}"
        )
    else:
        return f"{marker}{key_icon} {name_fmt} {details}{star}"


# ═══════════════════════════════════════════════════════════════
# SCREEN 1: Dashboard
# ═══════════════════════════════════════════════════════════════

def screen_dashboard(state: Dict) -> Optional[str]:
    """Main dashboard — split view with sidebar + main panel."""

    def render_main() -> str:
        lines: List[str] = []
        tools = state["tools"]
        installed = state["installed_tools"]
        prov_name = state.get("default_provider", "")
        prov_cfg = state["endpoints"].get(prov_name, {})

        # ── Title section ──
        lines.append("")
        lines.append(f"  {T.bold(T.sky('⚡ Launch Pad'))}")
        lines.append(f"  {T.dim('Select a tool and provider, then launch')}")
        lines.append("")

        # ── Quick Stats ──
        lines.append(f"  {Box.horizontal_rule(char='─', double=True)}")
        stats_left = f"{T.text('Tools')}: {T.bold(str(len(installed)) + '/' + str(len(tools)))}"
        stats_right = f"{T.text('Providers')}: {T.bold(str(len(state['endpoints'])))}"
        pid_info = f"T.PID={state['last_pid']}" if state['last_pid'] else ""
        proxy_info = f"Proxy:{T.bold(':')} {state['proxy_port']}" if state.get('proxy_running') else ""
        lines.append(f"  {stats_left}    {stats_right}  {pid_info}{proxy_info}")
        lines.append("")

        # ── Active Configuration ──
        lines.append(f"  {Box.horizontal_rule(char='─', double=True)}")
        if prov_cfg:
            lines.append(f"  {T.text('Provider')}: {T.secondary(prov_name)}")
            lines.append(f"  {T.text('Model')}: "
                        f"{T.bold(prov_cfg.get('default_model', '--'))}")
            lines.append(f"  {T.text('Backend')}: "
                        f"{T.dim(prov_cfg.get('backend_type', '?'))}")
            lines.append(f"  {T.text('URL')}: "
                        f"{T.dim(prov_cfg.get('base_url', '')[:50])}")
        else:
            lines.append(f"  {T.dim('No provider configured. Go to [3] Providers.')}")
        lines.append("")

        # ── Detected Tools ──
        lines.append(f"  {Box.horizontal_rule(char='─', double=True)}")
        lines.append(f"  {T.text('Coding Tools')}")
        lines.append("")

        for idx, tool in enumerate(tools):
            lines.append(format_tool_list_item(tool, idx, state["list_cursor"]))

        lines.append("")
        lines.append("")
        lines.append(f"  {Box.horizontal_rule(char='─', double=True)}")

        # ── Quick Actions ──
        lines.append(f"  {T.dim('Shortcuts:')}")
        lines.append(f"    {T.bold('[1]')} Launch    {T.bold('[3]')} Providers "
                     f"{T.bold('[4]')} Sessions  {T.bold('[5]')} Settings")

        return "\n".join(lines)

    def render_sidebar() -> str:
        items = build_sidebar_items(state)
        sb = Sidebar(items, width=26, title="zClaude")
        return sb.render(active_idx=0)

    # Build screen
    scr = Screen()
    side = render_sidebar()
    main = render_main()
    hints = "1 Launch · 3 Providers · 5 Settings · q Quit"
    scr.render_frame(sidebar=side, main=main, hints=hints)

    return _handle_dashboard_keys(state)


def _handle_dashboard_keys(state: Dict) -> Optional[str]:
    """Process keypress on dashboard screen."""
    key = Keyboard.getkey()

    if key == "1" or key == "l" or key == "L":
        return SCREEN_TOOL_SELECT
    if key == "3" or key == "p" or key == "P":
        return SCREEN_PROVIDERS
    if key == "4" or key == "s" or key == "S":
        return SCREEN_SESSIONS
    if key == "5" or key == "," or (key == "q"):
        return SCREEN_SETTINGS
    if key in ("escape", "q"):
        return SCREEN_EXIT

    # Navigate tool list
    tools = state["tools"]
    n = len(tools)
    if key in ("up", "k", "K"):
        state["list_cursor"] = max(0, state["list_cursor"] - 1)
    elif key in ("down", "j", "J") or key == "\t":
        state["list_cursor"] = min(n - 1, state["list_cursor"] + 1)
    elif key == "enter":
        if n > 0 and tools[state["list_cursor"]].installed:
            state["selected_tool"] = tools[state["list_cursor"]]
            return SCREEN_LAUNCH
        elif n > 0:
            state["message"] = (
                f"{tools[state['list_cursor']].display_name} is not installed. "
                f"Pick an installed tool, or install it first."
            )
            state["message_color"] = "warn"

    return SCREEN_DASHBOARD


# ═══════════════════════════════════════════════════════════════
# SCREEN 2: Provider Browser
# ═══════════════════════════════════════════════════════════════

def screen_provider_browse(state: Dict) -> Optional[str]:
    """Browse providers — main list + preset quick-add."""

    def render_main() -> str:
        lines: List[str] = []
        endpoints = state["endpoints"]
        presets = get_presets()
        n = len(endpoints)
        cursor = state["list_cursor"]

        lines.append("")
        lines.append(f"  {T.bold(T.primary('📡 Providers'))}")
        lines.append(f"  {T.dim('Browse, add, edit, or activate AI providers')}")
        lines.append("")

        # ── Configured Providers ──
        if endpoints:
            lines.append(f"  {T.secondary(f'Configured Providers ({n})')}")
            lines.append("")
            items = list(endpoints.items())
            for idx, (name, cfg) in enumerate(items):
                is_default = name == state.get("default_provider", "")
                lines.append(format_provider_item(
                    name, cfg, idx, cursor, is_default))
        else:
            lines.append(f"  {T.dim('No providers configured yet.')}")

        lines.append("")

        # ── Quick-add Presets ──
        if presets:
            lines.append(f"  {T.secondary(f'One-click Presets ({len(presets)})')}")
            lines.append("")
            show = presets[:8]
            for idx, (pname, pcfg) in enumerate(show):
                backend = pcfg.get("backend_type", "?")
                mcount = len(pcfg.get("models", []))
                lines.append(f"    {T.dim(str(idx+1))}. "
                           f"{T.c('secondary', pname)} "
                           f"{T.dim(f'[{backend}, {mcount} models]')}")
            if len(presets) > 8:
                lines.append(f"    {T.dim(f'... and {len(presets)-8} more')}")

        lines.append("")
        lines.append(f"  {Box.horizontal_rule(char='─', double=True)}")
        lines.append(f"  {T.dim('[a] Add  [e] Edit  [d] Delete  '
                     f'[1-8] Quick-add  [Esc] Back')}")

        return "\n".join(lines)

    def render_sidebar() -> str:
        items = build_sidebar_items(state)
        # Highlight providers item
        for i, item in enumerate(items):
            item.active = (i == 2)  # Providers is index 2
        sb = Sidebar(items, width=28, title="zClaude")
        return sb.render(active_idx=2)

    scr = Screen()
    scr.render_frame(sidebar=render_sidebar(), main=render_main(),
                    hints="a Add · e Edit · d Delete · Esc Back")
    return _handle_provider_keys(state)


def _handle_provider_keys(state: Dict) -> Optional[str]:
    key = Keyboard.getkey()
    endpoints = state["endpoints"]
    n = len(endpoints)
    cursor = state["list_cursor"]

    if key in ("up", "k", "K"):
        state["list_cursor"] = max(0, cursor - 1) if n > 0 else 0
    elif key in ("down", "j", "J") or key == "\t":
        state["list_cursor"] = min(n - 1, cursor + 1) if n > 0 else 0
    elif key == "enter" and n > 0:
        name = list(endpoints.keys())[cursor]
        state["default_provider"] = name
        state["selected_provider"] = name
        state["message"] = f"Activated: {T.secondary(name)}"
        state["message_color"] = "success"
    elif key in ("a", "A"):
        return SCREEN_PROVIDER_ADD
    elif key in ("e", "E") and n > 0:
        name = list(endpoints.keys())[cursor]
        state["selected_provider"] = name
        return SCREEN_PROVIDER_EDIT
    elif key in ("d", "D") and n > 0:
        name = list(endpoints.keys())[cursor]
        del endpoints[name]
        save_endpoints(endpoints)
        state["endpoints"] = endpoints
        state["list_cursor"] = min(cursor, max(0, n - 2))
        state["message"] = f"Deleted: {name}"
        state["message_color"] = "error"
    elif key.isdigit():
        num = int(key)
        presets = get_presets()
        if 1 <= num <= len(presets):
            pname, pcfg = presets[num - 1]
            new_cfg = dict(pcfg)
            new_cfg["api_key"] = ""  # User must set API key
            endpoints[pname] = new_cfg
            save_endpoints(endpoints)
            state["endpoints"] = endpoints
            state["message"] = f"Preset '{pname}' added! Set API key via Edit."
            state["message_color"] = "info"
    elif key in ("escape", "q"):
        return SCREEN_DASHBOARD

    return SCREEN_PROVIDERS


# ═══════════════════════════════════════════════════════════════
# SCREEN 3: Provider Add (wizard delegate)
# ═══════════════════════════════════════════════════════════════

def screen_provider_add(state: Dict) -> Optional[str]:
    """Add provider — delegates to provider_manager wizard."""
    print(render_header_line("Add Provider"))
    print()
    print(f"  {T.bold('Opening setup wizard...')}")
    print()

    try:
        from provider_manager import cmd_wizard, banner
        banner()
        new_ep = cmd_wizard()
        if new_ep:
            name = new_ep.get("name", "new-provider")
            state["endpoints"][name] = new_ep
            save_endpoints(state["endpoints"])
            state["message"] = f"Provider '{name}' added!"
            state["message_color"] = "success"
        else:
            state["message"] = "Add cancelled."
            state["message_color"] = "muted"
    except Exception as exc:
        state["message"] = f"Wizard error: {exc}"
        state["message_color"] = "error"

    _wait_any("Press any key to continue...")
    return SCREEN_PROVIDERS


def _wait_any(hint: str = "Press any key to continue..."):
    """Show hint and wait for any keypress."""
    print(f"\n  {T.dim(hint)}", end="", flush=True)
    try:
        Keyboard.getkey()
    except (KeyboardInterrupt, EOFError, KeyboardInterrupt):
        pass
    print()


# ═══════════════════════════════════════════════════════════════
# SCREEN 4: Provider Edit
# ═══════════════════════════════════════════════════════════════

def screen_provider_edit(state: Dict) -> Optional[str]:
    """Edit existing provider."""
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

    print(render_header_line(f"Edit Provider: {name}"))
    print()
    print(f"  {T.text('Name:')}         {T.bold(name)}")
    print(f"  {T.text('Backend:')}       {T.bold(cfg.get('backend_type', '?'))}")
    print(f"  {T.text('Base URL:')}      {T.bold(cfg.get('base_url', '?'))}")
    print(f"  {T.text('Model:')}         {T.bold(cfg.get('default_model', '?'))}")
    print(f"  {T.text('API Key:')}       "
        f"{T.bold('***' + (cfg.get('api_key', '')[-4:] if len(cfg.get('api_key', '')) > 4 else '(not set)'))}")
    models = cfg.get("models", [])
    print(f"  {T.text('Models:')}        "
        f"{', '.join(models[:8])}{f' ... (+{len(models)-8})' if len(models) > 8 else '' or '(none)'}")
    print()

    try:
        from provider_manager import cmd_edit, banner
        banner()
        updated = cmd_edit(name)
        if updated:
            state["endpoints"][name] = updated
            save_endpoints(state["endpoints"])
            state["message"] = f"Provider '{name}' updated!"
            state["message_color"] = "success"
        else:
            state["message"] = "Edit cancelled."
            state["message_color"] = "muted"
    except Exception as exc:
        state["message"] = f"Error: {exc}"
        state["message_color"] = "error"

    _wait_any()
    return SCREEN_PROVIDERS


# ═══════════════════════════════════════════════════════════════
# SCREEN 5: Tool Selection (Dialog)
# ═══════════════════════════════════════════════════════════════

def screen_tool_select(state: Dict) -> Optional[str]:
    """Tool selection dialog — shows detected tools with install status."""

    def render_dialog() -> str:
        all_tools = state["tools"]
        installed = state["installed_tools"]
        cursor = state["list_cursor"]

        body: List[str] = []

        # Header
        body.append(f"  {T.bold('Select Coding Tool')}")
        body.append(f"  {T.dim('Auto-detected CLI coding assistants')}")
        body.append("")

        # Grouped: Installed first, then available
        body.append(f"  {T.secondary(f'Installed ({len(installed)})')}")
        for idx, tool in enumerate(installed):
            marker = "▸ " if idx == cursor else "  "
            sel = T.BG_HIGHLIGHT if idx == cursor else ""
            native = "/native" if tool.supports_native else ""
            body.append(f"{marker}{sel}{T.success('●')} "
                       f"{T.bold(tool.display_name)}"
                       f"{T.dim(f'v{tool.version}{native}')}{T.RESET}")

        unavailable = [t for t in all_tools if not t.installed]
        if unavailable:
            body.append(f"  {T.dim(f'Available ({len(unavailable)})')}")
            for idx, tool in enumerate(unavailable):
                offset = len(installed) + idx
                marker = "▸ " if offset == cursor else "  "
                body.append(f"{marker}{T.dim('○')} {T.text(tool.display_name)}"
                       f"{T.dim('  (not found)')}")

        return Box.dialog("Select Tool", body, width=56, height=min(16, len(body)+4))

    scr = Screen()
    scr.render_frame(main=render_dialog(),
                    hints="↑↓ Navigate · Enter Select · r Rescan · Esc Back")
    return _handle_tool_select_keys(state)


def _handle_tool_select_keys(state: Dict) -> Optional[str]:
    key = Keyboard.getkey()
    all_tools = state["tools"]
    total = len(all_tools)
    cursor = state["list_cursor"]

    if key in ("enter", " "):
        if total > 0 and all_tools[cursor].installed:
            state["selected_tool"] = all_tools[cursor]
            state["message"] = f"Selected: {all_tools[cursor].display_name}"
            state["message_color"] = "success"
            return SCREEN_LAUNCH
        elif total > 0:
            state["message"] = (
                f"{all_tools[cursor].display_name} is not installed. "
                f"Install it or pick another tool."
            )
            state["message_color"] = "warn"
    elif key in ("r", "R"):
        state["tools"] = detect_all_tools()
        state["installed_tools"] = get_installed_tools()
        state["message"] = (
            f"Re-scanned. "
            f"{summary_line(state['tools'])}"
        )
        state["message_color"] = "info"
    elif key in ("escape", "q"):
        return SCREEN_DASHBOARD
    elif key in ("up", "k", "K"):
        state["list_cursor"] = max(0, cursor - 1)
    elif key in ("down", "j", "J") or key == "\t":
        state["list_cursor"] = min(total - 1, cursor + 1)

    return SCREEN_TOOL_SELECT


def summary_line(tools: List[ToolInfo]) -> str:
    """One-line summary of detection results."""
    installed = sum(1 for t in tools if t.installed)
    names = ", ".join(t.display_name for t in tools if t.installed)
    return f"{installed}/{len(tools)} detected: {names or 'none'}"


# ═══════════════════════════════════════════════════════════════
# SCREEN 6: Launch Confirmation (Dialog)
# ═══════════════════════════════════════════════════════════════

def screen_launch_confirm(state: Dict) -> Optional[str]:
    """Launch confirmation dialog with option toggles."""

    tool = state.get("selected_tool")
    if not tool:
        state["message"] = "No tool selected. Go back and pick one."
        state["message_color"] = "error"
        return SCREEN_TOOL_SELECT

    prov_name = state.get("selected_provider") or state.get("default_provider", "")
    prov_cfg = state["endpoints"].get(prov_name, {})
    if not prov_cfg:
        state["message"] = "No provider. Configure one first."
        state["message_color"] = "error"
        return SCREEN_PROVIDERS

    options = state.get("launch_options", LaunchOptions())

    def render_dialog() -> str:
        body: List[str] = []

        # Summary section
        body.append(f"  {T.bold('🚀 Launch Configuration')}")
        body.append("")

        # Tool + Provider
        compat = check_compatibility(tool, prov_cfg)
        mode = compat.get("mode", "?")
        mode_color = "success" if mode == "native" else "primary"

        body.append(f"  {T.text('Tool:')}      "
                   f"{T.highlight(tool.icon + ' ' + tool.display_name)}"
                   f"{T.dim(' v' + tool.version)}")
        bt = prov_cfg.get("backend_type", "?")
        provider_detail = T.dim(" [" + bt + "]")
        body.append(f"  {T.text('Provider:')}  "
                   f"{T.secondary(prov_name)}{provider_detail}")
        body.append(f"  {T.text('Model:')}     "
                   f"{T.bold(prov_cfg.get('default_model', 'default'))}")
        reason = compat.get("reason", "")
        mode_detail = T.dim(" — " + reason)
        body.append(f"  {T.text('Mode:')}      "
                   f"{T.c(mode_color, mode.upper())}{mode_detail}")
        for w in compat.get("warnings", []):
            body.append(f"  {T.warn(f'  ⚠ {w}')}")

        body.append("")
        body.append(f"  {Box.horizontal_rule(double=True)}")
        body.append(f"  {T.text('Options (toggle with key)')}")

        # Option toggles
        toggles = [
            ("c", "Caveman Mode", options.caveman_mode),
            ("r", "RTK Compression", options.rtk_compression),
            ("e", f"Reasoning: {options.reasoning_effort}", None),
            ("s", "Sandbox Mode", options.sandbox_mode),
            ("a", f"Approval: {options.approval_mode}", None),
        ]
        for key, label, val in toggles:
            on_off = T.success("ON ") if val else T.dim("OFF ")
            body.append(f"    [{T.bold(key)}] {label:<24s} {on_off}")

        body.append("")
        body.append(f"  {Box.horizontal_rule(double=True)}")
        hint_text = "[Enter] Launch  [p] Change Provider  [Esc] Back  [e] Toggle Options"
        body.append(f"  {T.dim(hint_text)}")

        return Box.dialog("Confirm Launch", body, width=58,
                          height=len(body)+4)

    scr = Screen()
    scr.render_frame(main=render_dialog(),
                hints="Enter Launch · p Provider · e Toggle · Esc Back")
    return _handle_launch_keys(state)


def _handle_launch_keys(state: Dict) -> Optional[str]:
    key = Keyboard.getkey()

    if key == "enter":
        try:
            tool = state["selected_tool"]
            prov_name = state.get("selected_provider") or state.get("default_provider", "")
            prov_cfg = state["endpoints"].get(prov_name, {})
            options = state.get("launch_options", LaunchOptions())
            plan = build_launch_plan(tool, prov_name, prov_cfg, options)
            pid = execute_launch(plan, logfn=lambda msg: print(f"  {msg}"))
            if pid:
                state["last_pid"] = pid
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

        _wait_any("Press any key...")
        return SCREEN_DASHBOARD

    elif key in ("c", "C"):
        state["launch_options"].caveman_mode = not state["launch_options"].caveman_mode
    elif key in ("r", "R"):
        state["launch_options"].rtk_compression = not state["launch_options"].rtk_compression
    elif key in ("e", "E"):
        efforts = ["low", "medium", "high"]
        idx = (efforts.index(state["launch_options"].reasoning_effort) + 1) % len(efforts)
        state["launch_options"].reasoning_effort = efforts[idx]
    elif key in ("s", "S"):
        state["launch_options"].sandbox_mode = not state["launch_options"].sandbox_mode
    elif key in ("a", "A"):
        modes = ["default", "auto-accept", "edit-only"]
        idx = (modes.index(state["launch_options"].approval_mode) + 1) % len(modes)
        state["launch_options"].approval_mode = modes[idx]
    elif key == "p":
        return SCREEN_PROVIDERS
    elif key in ("escape", "q"):
        return SCREEN_TOOL_SELECT

    return SCREEN_LAUNCH


# ═══════════════════════════════════════════════════════════════
# SCREEN 7: Sessions
# ═══════════════════════════════════════════════════════════════

def screen_sessions(state: Dict) -> Optional[str]:
    """Session browser."""

    def load_sessions() -> list:
        try:
            from session_manager import scan_all
            sessions = scan_all()
            sessions.sort(key=lambda s: s.last_active, reverse=True)
            return sessions
        except Exception:
            return []

    def render_main() -> str:
        sessions = state.get("sessions", [])
        cursor = state["list_cursor"]

        lines: List[str] = []
        lines.append("")
        lines.append(f"  {T.bold(T.primary('📂 Recent Sessions'))}")
        lines.append(f"  {T.dim('Browse and resume past coding sessions')}")
        lines.append("")

        if sessions:
            lines.append(f"  {T.secondary(f'{len(sessions)} sessions found')}")
            lines.append("")
            page_size = Term.h() - 10
            start = cursor
            end = min(start + page_size, len(sessions))

            for idx in range(start, end):
                s = sessions[idx]
                marker = "▸ " if idx == cursor else "  "
                provider_icon = {"codex": "C", "claude": "c", "gemini": "G"}.get(s.provider, "?")
                import datetime as dt
                time_str = dt.datetime.fromtimestamp(s.last_active).strftime("%Y-%m-%d %H:%M")
                sel = T.BG_HIGHLIGHT if idx == cursor else ""
                lines.append(f"{marker}{sel}{T.c('secondary', provider_icon)}"
                             f" {T.text(s.title[:45])}"
                             f"{T.dim(f' · {s.model}')}"
                             f"{T.dim(time_str)}")
        else:
            lines.append(f"  {T.dim('No sessions found.')}")
            lines.append("")
            lines.append(f"  {T.dim('Sessions appear after using coding tools.')}")

        lines.append("")
        lines.append(f"  {Box.horizontal_rule(double=True)}")
        hint_text = "[r] Refresh  [↑↓] Navigate  [Enter] Resume  [Esc] Back"
        lines.append(f"  {T.dim(hint_text)}")

        return "\n".join(lines)

    scr = Screen()
    scr.render_frame(main=render_main(), hints="r Refresh · ↑↓ Nav · Esc Back")
    return _handle_session_keys(state)


def _handle_session_keys(state: Dict) -> Optional[str]:
    key = Keyboard.getkey()
    sessions = state.get("sessions", [])
    n = len(sessions)
    cursor = state["list_cursor"]

    if key in ("up", "k", "K"):
        state["list_cursor"] = max(0, cursor - 1)
    elif key in ("down", "j", "J"):
        state["list_cursor"] = min(max(0, n - 1), cursor + 1)
    elif key == "enter" and n > 0:
        s = sessions[cursor]
        state["message"] = f"To resume: {getattr(s, 'resume_cmd', s.session_id)}"
        state["message_color"] = "info"
    elif key in ("r", "R"):
        state["sessions"] = load_sessions()
        state["message"] = "Sessions refreshed."
        state["message_color"] = "success"
    elif key in ("escape", "q"):
        return SCREEN_DASHBOARD

    return SCREEN_SESSIONS


# ═══════════════════════════════════════════════════════════════
# SCREEN 8: Settings
# ═══════════════════════════════════════════════════════════════

def screen_settings(state: Dict) -> Optional[str]:
    """Settings panel."""

    def render_main() -> str:
        lines: List[str] = []
        lines.append("")
        lines.append(f"  {T.bold(T.mauve('⚙ Settings'))}")
        lines.append("")

        # Info section
        lines.append(f"  {Box.horizontal_rule(double=True)}")
        lines.append(f"  {T.text('Application')}")
        lines.append(f"    Version:      {T.bold(VERSION)}")
        lines.append(f"    Python:      {sys.version.split()[0]}")
        lines.append(f"    Platform:    {sys.platform}")
        lines.append(f"    Config:      {os.path.expanduser('~/.codex/')}")
        lines.append("")

        # Defaults
        lines.append(f"  {T.text('Defaults')}")
        opts = state.get("launch_options", LaunchOptions())
        prov = state.get("default_provider", "(none)")
        lines.append(f"    Provider:    {T.secondary(prov) if prov != '(none)' else T.dim('(none)')}")
        lines.append(f"    Reasoning:    {opts.reasoning_effort}")
        lines.append(f"    Approval:     {opts.approval_mode}")
        lines.append("")

        # Stats
        lines.append(f"  {T.text('Stats')}")
        tools = state.get("tools", [])
        eps = state.get("endpoints", {})
        lines.append(f"    Tools:       "
                   f"{T.bold(str(sum(1 for t in tools if t.installed)))}/"
                   f"{T.bold(str(len(tools)))}")
        lines.append(f"    Providers:    {T.bold(str(len(eps)))}")
        lines.append(f"    Proxy:       "
                   f"{'Yes :' + str(state.get('proxy_port', '')) if state.get('proxy_running') else 'No'}")
        lines.append(f"    Last PID:    {state.get('last_pid', 'N/A')}")

        lines.append("")
        lines.append(f"  {Box.horizontal_rule(double=True)}")
        hint_text = "[d] Set Default Provider  [o] Toggle Options  [Esc] Dashboard"
        lines.append(f"  {T.dim(hint_text)}")

        return "\n".join(lines)

    scr = Screen()
    scr.render_frame(main=render_main(), hints="d Provider · o Options · Esc Back")
    return _handle_settings_keys(state)


def _handle_settings_keys(state: Dict) -> Optional[str]:
    key = Keyboard.getkey()

    if key in ("d", "D"):
        return SCREEN_PROVIDERS
    elif key in ("o", "O"):
        opts = state.get("launch_options", LaunchOptions())
        efforts = ["low", "medium", "high"]
        idx = (efforts.index(opts.reasoning_effort) + 1) % len(efforts)
        opts.reasoning_effort = efforts[idx]
        state["launch_options"] = opts
        state["message"] = f"Reasoning effort: {opts.reasoning_effort}"
        state["message_color"] = "info"
    elif key in ("escape", "q"):
        return SCREEN_DASHBOARD

    return SCREEN_SETTINGS


# ═══════════════════════════════════════════════════════════════
# Screen Router
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
# Main Loop
# ═══════════════════════════════════════════════════════════════

def run_launcher(initial: str = SCREEN_DASHBOARD) -> None:
    """Main event loop — routes between screens until exit."""
    state = create_state()

    # Initialize
    state["tools"] = detect_all_tools()
    state["installed_tools"] = get_installed_tools()
    state["endpoints"] = load_endpoints()
    if state["endpoints"]:
        state["default_provider"] = next(iter(state["endpoints"]))
    current = initial

    try:
        while current is not None:
            handler = SCREEN_HANDLERS.get(current)
            if handler is None:
                break
            current = handler(state)
    except KeyboardInterrupt:
        print(f"\n\n  {T.dim('Interrupted. Goodbye! 👋')}\n")
    except Exception as exc:
        print(f"\n{T.error(f' Error: {exc}')}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            from lib.proxy_lifecycle import stop_proxy
            stop_proxy()
        except Exception:
            pass
        print(f"\n  {T.dim('Thanks for using zClaude! ✨')}\n")


def main() -> entry_point():
    """Entry point — detect capabilities, show banner, run loop."""
    Theme.detect()

    # Print welcome banner (OpenCode style: rounded border box)
    w = Term.w()
    ch = Box.chars("round")
    inner = w - 4
    print()
    print(f"{ch['tl']}{'═' * inner}{ch['tr']}")
    title = f" {T.bold(T.mauve(' ◆ '))}{T.title(' zClaude ')}"
    sub = T.dim(f"Universal Launcher v{VERSION} — OpenCode-style TUI")
    print(f"{ch['l']} {Layout.center(title, inner)}{ch['r']}")
    print(f"{ch['hl']}{'═' * inner}{ch['vr']}")
    print(f"{ch['l']} {Layout.center(sub, inner)}{ch['r']}")
    print(f"{ch['vl']}{'─' * inner}{ch['br']}")
    print()

    # Handle --help
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "help"):
        print(f"  {T.text('Usage:')}")
        print(f"    zclaude [options]")
        print()
        print(f"  {T.text('Screens:')}")
        print(f"    Dashboard    Tool status + active provider + quick actions")
        print(f"    Providers   Browse/add/edit/activate AI providers (30+ presets)")
        print(f"    Tool Select Pick coding CLI (auto-detected)")
        print(f"        Launch      Review config & launch with proxy auto-config")
        print(f"    Sessions    Browse/resume past coding sessions")
        print(f"    Settings    Preferences, defaults, info")
        print()
        print(f"  {T.text('Keys:')}")
        print(f"    ↑↓/jk       Navigate    Enter    Select/Confirm")
        print(f"    Esc/q       Back/Quit    Tab      Next item")
        print(f"    1-5         Shortcuts")
        print()
        return

    run_launcher()


if __name__ == "__main__":
    main()
