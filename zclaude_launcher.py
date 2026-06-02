#!/usr/bin/env python3
"""zClaude Universal Launcher — Modern TUI Wizard.

One command:  python3 zclaude_launcher.py

Linear step-by-step flow:
  Step 1: Select Provider  (pick AI provider from configured list or quick-add)
  Step 2: Select Model     (pick model from provider's model list)
  Step 3: Select Tool      (pick coding CLI tool, auto-detected)
  Step 4: Launch           (review config, toggle options, execute)

Navigation:
  ↑↓/j/k    : navigate list
  Enter     : select / confirm / advance to next step
  Esc       : go back (or quit on step 1)
  a         : add provider (step 1)
  1-9       : quick-add preset provider (step 1)
  r         : rescan tools (step 3)
  c/r/e/s/a : toggle launch options (step 4)
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
    Spinner, ProgressBar, _strip_ansi,
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

SCREEN_PROVIDER = "provider"
SCREEN_MODEL = "model"
SCREEN_TOOL = "tool"
SCREEN_LAUNCH = "launch"


# ═══════════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════════

def create_state() -> Dict[str, Any]:
    return {
        "tools": [],
        "installed_tools": [],
        "endpoints": {},
        "selected_provider": None,
        "selected_model": None,
        "selected_tool": None,
        "launch_options": LaunchOptions(),
        "list_cursor": 0,
        "message": "",
        "last_pid": 0,
        "proxy_running": False,
        "proxy_port": 0,
    }


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
# Rendering helpers
# ═══════════════════════════════════════════════════════════════

def tool_status_icon(tool: ToolInfo) -> str:
    """Return a single-char status indicator for a tool."""
    if tool.installed:
        if tool.supports_native:
            return f"{T.success('●')}"
        else:
            return f"{T.primary('●')}"
    return f"{T.dim('○')}"


def summary_line(tools: List[ToolInfo]) -> str:
    """One-line summary of detection results."""
    installed = sum(1 for t in tools if t.installed)
    names = ", ".join(t.display_name for t in tools if t.installed)
    return f"{installed}/{len(tools)} detected: {names or 'none'}"


def _wait_any(hint: str = "Press any key to continue..."):
    """Show hint and wait for any keypress."""
    print(f"\n  {T.dim(hint)}", end="", flush=True)
    try:
        Keyboard.getkey()
    except (KeyboardInterrupt, EOFError, KeyboardInterrupt):
        pass
    print()


# ═══════════════════════════════════════════════════════════════
# STEP 1: Provider Selection
# ═══════════════════════════════════════════════════════════════

def step_provider(state: Dict) -> Optional[str]:
    """Step 1: Pick an AI provider from configured list."""

    def render_dialog() -> str:
        body: List[str] = []
        endpoints = state["endpoints"]
        cursor = state["list_cursor"]
        n = len(endpoints)

        body.append(f"  {T.bold('Choose an AI provider for your coding session')}")
        body.append("")

        if endpoints:
            items = list(endpoints.items())
            for idx, (name, cfg) in enumerate(items):
                is_cursor = (idx == cursor)
                backend = cfg.get("backend_type", "?")
                models = cfg.get("models", [])
                mcount = len(models) if isinstance(models, list) else 0
                has_key = bool(cfg.get("api_key", ""))
                key_icon = T.success("🔑") if has_key else T.error("◇")
                is_default = name == state.get("selected_provider")

                marker = "▸ " if is_cursor else "  "
                star = T.secondary(" ★") if is_default else ""
                name_fmt = T.bold(name) if is_cursor else name

                line = f"{marker}{key_icon} {name_fmt}"
                detail = T.dim(f"  {backend} · {mcount} models{star}")
                if is_cursor:
                    body.append(f"{T.BG_HIGHLIGHT}{line}{detail}{T.RESET}")
                else:
                    body.append(f"{line}{detail}")

            body.append("")
        else:
            body.append(f"  {T.dim('No providers configured yet.')}")
            body.append(f"  {T.dim('Press [a] to add one, or [1-9] for a preset.')}")
            body.append("")

        # Show available presets
        presets = get_presets()
        if presets:
            body.append(f"  {T.secondary(f'Quick-add Presets ({len(presets)})')}:")
            show = presets[:9]
            for idx, (pname, pcfg) in enumerate(show):
                backend = pcfg.get("backend_type", "?")
                body.append(f"    {T.dim(str(idx+1))}. {T.text(pname)} "
                           f"{T.dim(f'[{backend}]')}")

        body.append("")
        hint_text = "[Enter] Select  [a] Add  [1-9] Quick-add  [q] Quit"
        body.append(f"  {T.dim(hint_text)}")

        return Box.dialog("Select Provider", body, width=58, height=min(20, len(body)+4))

    scr = Screen()
    scr.render_frame(main=render_dialog())
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
        state["selected_provider"] = name
        state["list_cursor"] = 0
        # Reset model selection when provider changes
        state["selected_model"] = None
        return SCREEN_MODEL
    elif key in ("a", "A"):
        # Try wizard first, fall back to preset prompt
        return _add_provider(state)
    elif key.isdigit():
        num = int(key)
        presets = get_presets()
        if 1 <= num <= len(presets):
            pname, pcfg = presets[num - 1]
            new_cfg = dict(pcfg)
            new_cfg["api_key"] = ""
            endpoints[pname] = new_cfg
            save_endpoints(endpoints)
            state["endpoints"] = endpoints
            state["message"] = f"Preset '{pname}' added! Set API key via Edit."
            # Auto-select it
            state["selected_provider"] = pname
            state["selected_model"] = None
            state["list_cursor"] = 0
            return SCREEN_MODEL
    elif key in ("escape", "q"):
        return SCREEN_EXIT

    return SCREEN_PROVIDER


def _add_provider(state: Dict) -> Optional[str]:
    """Add a provider — try wizard, then auto-select and advance."""
    print(Term.clear(), end="")
    print(render_welcome_banner())
    print()
    print(f"  {T.bold('Adding new provider...')}")
    print()

    try:
        from provider_manager import cmd_wizard, banner
        banner()
        new_ep = cmd_wizard()
        if new_ep:
            name = new_ep.get("name", "new-provider")
            state["endpoints"][name] = new_ep
            save_endpoints(state["endpoints"])
            state["selected_provider"] = name
            state["selected_model"] = None
            state["list_cursor"] = 0
            _wait_any(f"Provider '{name}' added! Press any key...")
            return SCREEN_MODEL
        else:
            _wait_any("Add cancelled.")
            return SCREEN_PROVIDER
    except Exception as exc:
        state["message"] = f"Wizard error: {exc}"
        _wait_any(f"Error: {exc}")
        return SCREEN_PROVIDER


# ═══════════════════════════════════════════════════════════════
# STEP 2: Model Selection
# ═══════════════════════════════════════════════════════════════

def step_model(state: Dict) -> Optional[str]:
    """Step 2: Pick a model from the selected provider's model list."""

    prov_name = state.get("selected_provider", "")
    if not prov_name:
        return SCREEN_PROVIDER

    prov_cfg = state["endpoints"].get(prov_name, {})
    if not prov_cfg:
        state["message"] = f"Provider '{prov_name}' not found."
        return SCREEN_PROVIDER

    def render_dialog() -> str:
        body: List[str] = []
        models = prov_cfg.get("models", [])
        default_model = prov_cfg.get("default_model", "")
        cursor = state["list_cursor"]

        # If models is not a list, try to make it one
        if not isinstance(models, list):
            if isinstance(models, str):
                models = [models]
            else:
                models = []

        body.append(f"  {T.text('Provider:')} {T.secondary(prov_name)}")
        body.append("")

        if models:
            models_label = f"Available Models ({len(models)})"
            body.append(f"  {T.bold(models_label)}:")
            body.append("")
            for idx, model in enumerate(models):
                is_cursor = (idx == cursor)
                is_default = (model == default_model)
                marker = "▸ " if is_cursor else "  "
                check = T.success(" ✓") if is_default else ""
                model_fmt = T.bold(model) if is_cursor else model
                if is_cursor:
                    body.append(f"{marker}{T.BG_HIGHLIGHT}{model_fmt}{check}{T.RESET}")
                else:
                    body.append(f"{marker}{model_fmt}{check}")
        else:
            body.append(f"  {T.dim('No models listed for this provider.')}")
            body.append(f"  {T.dim('The default model will be used:')}")
            body.append(f"  {T.bold(default_model or '(unknown)')}")
            body.append("")
            body.append(f"  {T.dim('Press Enter to use default, or go back '
                         'to choose a different provider.')}")

        body.append("")
        body.append("  " + T.dim("[Enter] Select                      [Esc] Back"))

        title = f"Select Model — {prov_name}"
        return Box.dialog(title, body, width=54, height=min(18, len(body)+4))

    scr = Screen()
    scr.render_frame(main=render_dialog())
    return _handle_model_keys(state, prov_cfg)


def _handle_model_keys(state: Dict, prov_cfg: Dict) -> Optional[str]:
    key = Keyboard.getkey()
    models = prov_cfg.get("models", [])
    if not isinstance(models, list):
        if isinstance(models, str):
            models = [models]
        else:
            models = []

    n = len(models)
    cursor = state["list_cursor"]

    if key in ("up", "k", "K"):
        state["list_cursor"] = max(0, cursor - 1) if n > 0 else 0
    elif key in ("down", "j", "J") or key == "\t":
        state["list_cursor"] = min(n - 1, cursor + 1) if n > 0 else 0
    elif key == "enter":
        if n > 0:
            state["selected_model"] = models[cursor]
        else:
            # No model list — use default
            state["selected_model"] = prov_cfg.get("default_model", "")
        state["list_cursor"] = 0
        return SCREEN_TOOL
    elif key in ("escape", "q"):
        state["list_cursor"] = 0
        return SCREEN_PROVIDER

    return SCREEN_MODEL


# ═══════════════════════════════════════════════════════════════
# STEP 3: Tool Selection
# ═══════════════════════════════════════════════════════════════

def step_tool(state: Dict) -> Optional[str]:
    """Step 3: Pick a coding tool from auto-detected installed tools."""

    def render_dialog() -> str:
        all_tools = state["tools"]
        installed = state["installed_tools"]
        cursor = state["list_cursor"]

        body: List[str] = []
        body.append(f"  {T.bold('Select your coding assistant')}")
        body.append("")

        # Installed tools
        body.append(f"  {T.secondary(f'Installed ({len(installed)})')}")
        for idx, tool in enumerate(installed):
            marker = "▸ " if idx == cursor else "  "
            icon = tool_status_icon(tool)
            native = "/native" if tool.supports_native else ""
            name_fmt = T.bold(tool.display_name) if idx == cursor else tool.display_name
            ver = tool.version or "--"
            backend = tool.backend_preference.upper()

            if idx == cursor:
                body.append(f"{marker}{T.BG_HIGHLIGHT}{icon} {name_fmt}"
                           f" {T.dim(f'v{ver}')}"
                           f" {T.dim(f'{backend}{native}')}{T.RESET}")
            else:
                body.append(f"{marker}{icon} {name_fmt}"
                           f" {T.dim(f'v{ver} {backend}{native}')}")

        # Uninstalled tools
        unavailable = [t for t in all_tools if not t.installed]
        if unavailable:
            body.append("")
            body.append(f"  {T.dim(f'Available ({len(unavailable)})')}")
            for idx, tool in enumerate(unavailable):
                offset = len(installed) + idx
                marker = "▸ " if offset == cursor else "  "
                body.append(f"{marker}{T.dim('○')} {T.text(tool.display_name)}"
                           f"{T.dim('  (not found)')}")

        body.append("")
        body.append("  " + T.dim("[Enter] Select                      [Esc] Back"))

        return Box.dialog("Select Coding Tool", body, width=56, height=min(18, len(body)+4))

    scr = Screen()
    scr.render_frame(main=render_dialog())
    return _handle_tool_keys(state)


def _handle_tool_keys(state: Dict) -> Optional[str]:
    key = Keyboard.getkey()
    all_tools = state["tools"]
    total = len(all_tools)
    cursor = state["list_cursor"]

    if key in ("enter", " "):
        if total > 0 and all_tools[cursor].installed:
            state["selected_tool"] = all_tools[cursor]
            state["list_cursor"] = 0
            return SCREEN_LAUNCH
        elif total > 0:
            state["message"] = (
                f"{all_tools[cursor].display_name} is not installed. "
                f"Install it or pick another tool."
            )
    elif key in ("r", "R"):
        state["tools"] = detect_all_tools()
        state["installed_tools"] = get_installed_tools()
        state["message"] = f"Re-scanned. {summary_line(state['tools'])}"
    elif key in ("escape", "q"):
        state["list_cursor"] = 0
        return SCREEN_MODEL
    elif key in ("up", "k", "K"):
        state["list_cursor"] = max(0, cursor - 1)
    elif key in ("down", "j", "J") or key == "\t":
        state["list_cursor"] = min(total - 1, cursor + 1)

    return SCREEN_TOOL


# ═══════════════════════════════════════════════════════════════
# STEP 4: Launch Confirmation
# ═══════════════════════════════════════════════════════════════

def step_launch(state: Dict) -> Optional[str]:
    """Step 4: Review configuration, toggle options, launch."""

    tool = state.get("selected_tool")
    if not tool:
        return SCREEN_TOOL

    prov_name = state.get("selected_provider", "")
    prov_cfg = state["endpoints"].get(prov_name, {})
    if not prov_cfg:
        return SCREEN_PROVIDER

    options = state.get("launch_options", LaunchOptions())

    def render_dialog() -> str:
        body: List[str] = []

        # Summary section
        body.append(f"  {T.bold('Launch Configuration')}")
        body.append("")

        compat = check_compatibility(tool, prov_cfg)
        mode = compat.get("mode", "?")
        mode_color = "success" if mode == "native" else "primary"

        # Tool
        body.append(f"  {T.text('Tool:')}     "
                   f"{T.highlight(tool.icon + ' ' + tool.display_name)}"
                   f"{T.dim(' v' + tool.version)}")

        # Provider
        body.append(f"  {T.text('Provider:')} "
                   f"{T.secondary(prov_name)}"
                   f"{T.dim(' [' + prov_cfg.get('backend_type', '?') + ']')}")

        # Model
        selected_model = state.get("selected_model") or prov_cfg.get("default_model", "?")
        body.append(f"  {T.text('Model:')}    "
                   f"{T.bold(selected_model)}")

        # Mode
        reason = compat.get("reason", "")
        mode_detail = T.dim(" — " + reason) if reason else ""
        body.append(f"  {T.text('Mode:')}     "
                   f"{T.c(mode_color, mode.upper())}{mode_detail}")

        # Warnings
        for w in compat.get("warnings", []):
            body.append(f"  {T.warn('  ⚠ ' + w)}")

        body.append("")
        body.append(f"  {Box.horizontal_rule(width=50)}")
        body.append(f"  {T.text('Options (toggle with key)')}")

        # Option toggles
        toggles = [
            ("c", "Caveman Mode", options.caveman_mode),
            ("r", "RTK Compression", options.rtk_compression),
            ("e", f"Reasoning: {options.reasoning_effort}", None),
            ("s", "Sandbox Mode", options.sandbox_mode),
            ("a", f"Approval: {options.approval_mode}", None),
        ]
        for tkey, label, val in toggles:
            on_off = T.success("ON ") if val else T.dim("OFF ")
            body.append(f"    [{T.bold(tkey)}] {label:<24s} {on_off}")

        body.append("")
        body.append(f"  {Box.horizontal_rule(width=50)}")
        hint_text = "[Enter] Launch!  [p] Prov  [m] Model  [t] Tool  [Esc] Quit"
        body.append(f"  {T.dim(hint_text)}")

        return Box.dialog("Confirm Launch", body, width=58, height=len(body)+4)

    scr = Screen()
    scr.render_frame(main=render_dialog())
    return _handle_launch_keys(state)


def _handle_launch_keys(state: Dict) -> Optional[str]:
    key = Keyboard.getkey()

    if key == "enter":
        try:
            tool = state["selected_tool"]
            prov_name = state.get("selected_provider", "") or ""
            prov_cfg = state["endpoints"].get(prov_name, {})
            options = state.get("launch_options", LaunchOptions())

            # Override model if user selected one
            launch_prov_cfg = dict(prov_cfg)
            if state.get("selected_model"):
                launch_prov_cfg["default_model"] = state["selected_model"]

            plan = build_launch_plan(tool, prov_name, launch_prov_cfg, options)
            pid = execute_launch(plan, logfn=lambda msg: print(f"  {msg}"))
            if pid:
                state["last_pid"] = pid
                state["proxy_running"] = plan.use_proxy
                state["proxy_port"] = plan.proxy_port or 0
                print()
                print(f"  {T.success('✓ Launched!')} {T.bold(tool.display_name)} "
                      f"(PID={pid})")
                if plan.use_proxy:
                    print(f"  {T.text('Proxy running on port')} "
                          f"{T.bold(str(plan.proxy_port))}")
                _wait_any()
                return SCREEN_EXIT
            else:
                print()
                print(f"  {T.error('✗ Launch failed. Check configuration.')}")
                _wait_any()
                return SCREEN_LAUNCH
        except Exception as exc:
            print()
            print(f"  {T.error(f' Error: {exc}')}")
            _wait_any()
            return SCREEN_LAUNCH

    elif key in ("c", "C"):
        state["launch_options"].caveman_mode = not state["launch_options"].caveman_mode
    elif key in ("r", "R"):
        state["launch_options"].rtk_compression = not state["launch_options"].rtk_compression
    elif key in ("e", "E"):
        efforts = ["low", "medium", "high"]
        opts = state["launch_options"]
        idx = (efforts.index(opts.reasoning_effort) + 1) % len(efforts)
        opts.reasoning_effort = efforts[idx]
    elif key in ("s", "S"):
        state["launch_options"].sandbox_mode = not state["launch_options"].sandbox_mode
    elif key in ("a", "A"):
        modes = ["default", "auto-accept", "edit-only"]
        opts = state["launch_options"]
        idx = (modes.index(opts.approval_mode) + 1) % len(modes)
        opts.approval_mode = modes[idx]
    elif key == "p":
        state["list_cursor"] = 0
        return SCREEN_PROVIDER
    elif key == "m":
        state["list_cursor"] = 0
        return SCREEN_MODEL
    elif key == "t":
        state["list_cursor"] = 0
        return SCREEN_TOOL
    elif key in ("escape", "q"):
        return SCREEN_EXIT

    return SCREEN_LAUNCH


# ═══════════════════════════════════════════════════════════════
# Welcome Banner
# ═══════════════════════════════════════════════════════════════

def render_welcome_banner() -> str:
    """Render the welcome banner as a string (for printing)."""
    w = Term.w()
    ch = Box.chars("round")
    inner = w - 4
    lines = []
    lines.append(f"{ch['tl']}{'═' * inner}{ch['tr']}")
    title = f" {T.bold(T.mauve(' ◆ '))}{T.title(' zClaude ')}"
    sub = T.dim(f"Universal Launcher v{VERSION} — Modern TUI")
    lines.append(f"{ch['l']} {Layout.center(title, inner)}{ch['r']}")
    lines.append(f"{ch['hl']}{'═' * inner}{ch['vr']}")
    lines.append(f"{ch['l']} {Layout.center(sub, inner)}{ch['r']}")
    lines.append(f"{ch['vl']}{'─' * inner}{ch['br']}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Main Loop — Wizard
# ═══════════════════════════════════════════════════════════════

def run_wizard() -> None:
    """Main event loop — linear 4-step wizard."""
    state = create_state()

    # Initialize
    state["tools"] = detect_all_tools()
    state["installed_tools"] = get_installed_tools()
    state["endpoints"] = load_endpoints()

    current = SCREEN_PROVIDER

    try:
        while current is not None:
            if current == SCREEN_PROVIDER:
                current = step_provider(state)
            elif current == SCREEN_MODEL:
                current = step_model(state)
            elif current == SCREEN_TOOL:
                current = step_tool(state)
            elif current == SCREEN_LAUNCH:
                current = step_launch(state)
            else:
                break
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


def main() -> None:
    """Entry point — show banner, handle --help, run wizard."""
    Theme.detect()

    # Print welcome banner
    print()
    print(render_welcome_banner())
    print()

    # Handle --help
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "help"):
        print(f"  {T.text('Usage:')}")
        print(f"    zclaude [options]")
        print()
        print(f"  {T.text('Wizard Flow:')}")
        print(f"    Step 1: Select Provider   Choose AI provider (or quick-add)")
        print(f"    Step 2: Select Model      Pick model from provider's list")
        print(f"    Step 3: Select Tool       Pick coding CLI (auto-detected)")
        print(f"    Step 4: Launch            Review config & launch with proxy")
        print()
        print(f"  {T.text('Keys:')}")
        print(f"    ↑↓/jk       Navigate    Enter    Select/Confirm/Next")
        print(f"    Esc/q       Back/Quit    Tab      Next item")
        print(f"    a           Add provider (step 1)")
        print(f"    1-9         Quick-add preset (step 1)")
        print(f"    r           Rescan tools (step 3)")
        print(f"    c/r/e/s/a   Toggle options (step 4)")
        print()
        return

    run_wizard()


if __name__ == "__main__":
    main()
