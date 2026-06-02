#!/usr/bin/env python3
"""zClaude Universal Launcher — OpenCode-style TUI Wizard.

Linear step-by-step flow:
  Step 1: Select Provider  (pick AI provider or quick-add preset)
  Step 2: Select Model     (pick model from provider's list)
  Step 3: Select Tool      (pick coding CLI, auto-detected)
  Step 4: Launch           (review config, toggle options, execute)

Layout matches OpenCode: left sidebar (30%) | main dialog (70%) | status bar.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from lib.tui_engine import (
    T, Theme, Term, Box, Keyboard,
    Screen, SplitPane, Container, Sidebar, SidebarItem, StatusBar,
    welcome_banner, auto_scroll, handle_scroll, _strip_ansi,
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

STEP_NAMES = {
    SCREEN_PROVIDER: "Provider",
    SCREEN_MODEL: "Model",
    SCREEN_TOOL: "Tool",
    SCREEN_LAUNCH: "Launch",
}


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
        "scroll_offset": 0,
        "message": "",
        "last_pid": 0,
        "proxy_running": False,
        "proxy_port": 0,
    }


# ═══════════════════════════════════════════════════════════════
# Data loading helpers
# ═══════════════════════════════════════════════════════════════

def load_endpoints() -> Dict[str, Any]:
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
    try:
        from lib.presets import PROVIDER_PRESETS
        return [(k, v) for k, v in PROVIDER_PRESETS.items() if k != "Custom"]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# Rendering helpers
# ═══════════════════════════════════════════════════════════════

def tool_status_icon(tool: ToolInfo) -> str:
    if tool.installed:
        if tool.supports_native:
            return f"{T.success('●')}"
        else:
            return f"{T.primary('●')}"
    return f"{T.dim('○')}"


def summary_line(tools: List[ToolInfo]) -> str:
    installed = sum(1 for t in tools if t.installed)
    names = ", ".join(t.display_name for t in tools if t.installed)
    return f"{installed}/{len(tools)} detected: {names or 'none'}"


def _wait_any(hint: str = "Press any key..."):
    print(f"\n  {T.dim(hint)}", end="", flush=True)
    try:
        Keyboard.getkey()
    except (KeyboardInterrupt, EOFError, KeyboardInterrupt):
        pass
    print()


# ═══════════════════════════════════════════════════════════════
# Sidebar builder — shows wizard progress
# ═══════════════════════════════════════════════════════════════

def build_sidebar(current_screen: str, state: Dict) -> str:
    """Build sidebar showing which step is active."""
    prov_count = len(state.get("endpoints", {}))
    inst_count = len(state.get("installed_tools", []))

    items = [
        SidebarItem("1", "Provider", "1",
                   active=(current_screen == SCREEN_PROVIDER),
                   badge=str(prov_count)),
        SidebarItem("2", "Model", "2",
                   active=(current_screen == SCREEN_MODEL),
                   badge="✓" if state.get("selected_model") else ""),
        SidebarItem("3", "Tool", "3",
                   active=(current_screen == SCREEN_TOOL),
                   badge=str(inst_count)),
        SidebarItem("4", "Launch", "4",
                   active=(current_screen == SCREEN_LAUNCH),
                   badge="▸" if current_screen == SCREEN_LAUNCH else ""),
    ]
    sb = Sidebar(items, title=" zClaude", active_idx={
        SCREEN_PROVIDER: 0, SCREEN_MODEL: 1,
        SCREEN_TOOL: 2, SCREEN_LAUNCH: 3,
    }.get(current_screen, 0))
    return sb.render_compact()


# ═══════════════════════════════════════════════════════════════
# STEP 1: Provider Selection
# ═══════════════════════════════════════════════════════════════

def step_provider(state: Dict) -> Optional[str]:
    def render_main() -> str:
        body: List[str] = []
        endpoints = state["endpoints"]
        cursor = state["list_cursor"]
        n = len(endpoints)

        body.append(f"  {T.bold('Choose an AI provider')}")
        body.append(f"  {T.dim('Configure where AI requests are sent')}")
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
                star = T.secondary("★") if is_default else ""
                name_fmt = T.bold(name) if is_cursor else name

                line = f"{marker}{key_icon} {name_fmt}"
                detail = T.dim(f"  {backend} · {mcount} models{star}")
                if is_cursor:
                    body.append(f"{T.BG_HIGHLIGHT}{line}{detail}{T.RESET}")
                else:
                    body.append(f"{line}{detail}")

            body.append("")
        else:
            body.append(f"  {T.dim('No providers configured.')}")
            body.append(f"  {T.dim('Press [a] to add, [1-9] for a preset.')}")
            body.append("")

        # Presets
        presets = get_presets()
        if presets:
            body.append(f"  {T.secondary('Quick-add:')}")
            row = []
            for idx, (pname, pcfg) in enumerate(presets[:9]):
                backend = pcfg.get("backend_type", "?")
                row.append(f"  {T.dim(str(idx+1))}. {T.text(pname)} "
                          f"{T.dim(f'[{backend}]')}")
                if len(row) == 3:
                    body.append("  ".join(row))
                    row = []
            if row:
                body.append("  ".join(row))

        body.append("")
        body.append(f"  {T.dim('[Enter] Select  [a] Add  [1-9] Preset  [q] Quit')}")

        return Box.dialog("Select Provider", body,
                         scroll_offset=state["scroll_offset"])

    side = build_sidebar(SCREEN_PROVIDER, state)
    scr = Screen()
    hints = "↑↓ Navigate · Enter Select · a Add · q Quit"
    scr.render(sidebar=side, main=render_main(), hints=hints,
             model=f"Step 1/4")
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
    elif handle_scroll(state, key, n):
        pass
    elif key == "enter" and n > 0:
        name = list(endpoints.keys())[cursor]
        state["selected_provider"] = name
        state["list_cursor"] = 0
        state["scroll_offset"] = 0
        state["selected_model"] = None
        return SCREEN_MODEL
    elif key in ("a", "A"):
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
            state["selected_provider"] = pname
            state["selected_model"] = None
            state["list_cursor"] = 0
            state["scroll_offset"] = 0
            return SCREEN_MODEL
    elif key in ("escape", "q"):
        return SCREEN_EXIT

    return SCREEN_PROVIDER


def _add_provider(state: Dict) -> Optional[str]:
    print(Term.clear(), end="")
    print(welcome_banner())
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
            state["scroll_offset"] = 0
            _wait_any(f"Provider '{name}' added!")
            return SCREEN_MODEL
        else:
            _wait_any("Cancelled.")
            return SCREEN_PROVIDER
    except Exception as exc:
        _wait_any(f"Error: {exc}")
        return SCREEN_PROVIDER


# ═══════════════════════════════════════════════════════════════
# STEP 2: Model Selection
# ═══════════════════════════════════════════════════════════════

def step_model(state: Dict) -> Optional[str]:
    prov_name = state.get("selected_provider", "")
    if not prov_name:
        return SCREEN_PROVIDER

    prov_cfg = state["endpoints"].get(prov_name, {})
    if not prov_cfg:
        return SCREEN_PROVIDER

    def render_main() -> str:
        models = prov_cfg.get("models", [])
        default_model = prov_cfg.get("default_model", "")
        cursor = state["list_cursor"]

        if not isinstance(models, list):
            models = [models] if isinstance(models, str) else []

        body: List[str] = []
        body.append(f"  {T.text('Provider:')} {T.secondary(prov_name)}")
        body.append("")

        if models:
            body.append(f"  {T.bold(f'Models ({len(models)})')}:")
            body.append("")
            for idx, model in enumerate(models):
                is_cursor = (idx == cursor)
                is_default = (model == default_model)
                marker = "▸ " if is_cursor else "  "
                check = T.success(" ✓") if is_default else ""
                model_fmt = T.bold(model) if is_cursor else model
                if is_cursor:
                    body.append(f"{T.BG_HIGHLIGHT}{marker}{model_fmt}{check}{T.RESET}")
                else:
                    body.append(f"{marker}{model_fmt}{check}")
        else:
            body.append(f"  {T.dim('No models listed.')}")
            body.append(f"  {T.bold('Default:')} {default_model or '(unknown)'}")

        body.append("")
        body.append(f"  {T.dim('[Enter] Select  [PgUp/PgDn] Scroll  [Esc] Back')}")

        auto_scroll(state, len(models))
        return Box.dialog(f"Model — {prov_name}", body,
                         scroll_offset=state["scroll_offset"])

    side = build_sidebar(SCREEN_MODEL, state)
    scr = Screen()
    hints = "↑↓ Navigate · Enter Select · Esc Back"
    scr.render(sidebar=side, main=render_main(), hints=hints,
             model=f"Step 2/4 · {prov_name}")
    return _handle_model_keys(state, prov_cfg)


def _handle_model_keys(state: Dict, prov_cfg: Dict) -> Optional[str]:
    key = Keyboard.getkey()
    models = prov_cfg.get("models", [])
    if not isinstance(models, list):
        models = [models] if isinstance(models, str) else []

    n = len(models)

    if key in ("up", "k", "K"):
        state["list_cursor"] = max(0, state["list_cursor"] - 1) if n > 0 else 0
    elif key in ("down", "j", "J") or key == "\t":
        state["list_cursor"] = min(n - 1, state["list_cursor"] + 1) if n > 0 else 0
    elif handle_scroll(state, key, n):
        pass
    elif key == "enter":
        if n > 0:
            state["selected_model"] = models[state["list_cursor"]]
        else:
            state["selected_model"] = prov_cfg.get("default_model", "")
        state["list_cursor"] = 0
        state["scroll_offset"] = 0
        return SCREEN_TOOL
    elif key in ("escape", "q"):
        state["list_cursor"] = 0
        state["scroll_offset"] = 0
        return SCREEN_PROVIDER

    return SCREEN_MODEL


# ═══════════════════════════════════════════════════════════════
# STEP 3: Tool Selection
# ═══════════════════════════════════════════════════════════════

def step_tool(state: Dict) -> Optional[str]:

    def render_main() -> str:
        all_tools = state["tools"]
        installed = state["installed_tools"]
        cursor = state["list_cursor"]

        body: List[str] = []
        body.append(f"  {T.bold('Select your coding assistant')}")
        body.append("")

        # Installed
        body.append(f"  {T.secondary(f'Installed ({len(installed)})')}")
        for idx, tool in enumerate(installed):
            is_cursor = (idx == cursor)
            icon = tool_status_icon(tool)
            native = "/native" if tool.supports_native else ""
            name_fmt = T.bold(tool.display_name) if is_cursor else tool.display_name
            ver = tool.version or "--"
            backend = tool.backend_preference.upper()

            marker = "▸ " if is_cursor else "  "
            if is_cursor:
                body.append(f"{T.BG_HIGHLIGHT}{marker}{icon} {name_fmt}"
                           f" {T.dim(f'v{ver}')}"
                           f" {T.dim(f'{backend}{native}')}{T.RESET}")
            else:
                body.append(f"{marker}{icon} {name_fmt}"
                           f" {T.dim(f'v{ver} {backend}{native}')}")

        # Uninstalled
        unavailable = [t for t in all_tools if not t.installed]
        if unavailable:
            body.append("")
            body.append(f"  {T.dim(f'Available ({len(unavailable)})')}")
            for idx, tool in enumerate(unavailable):
                offset = len(installed) + idx
                is_cursor = (offset == cursor)
                marker = "▸ " if is_cursor else "  "
                body.append(f"{marker}{T.dim('○')} {T.text(tool.display_name)}"
                           f"{T.dim('  (not found)')}")

        body.append("")
        body.append(f"  {T.dim('[Enter] Select  [r] Rescan  [Esc] Back')}")

        auto_scroll(state, len(all_tools))
        return Box.dialog("Select Tool", body,
                         scroll_offset=state["scroll_offset"])

    side = build_sidebar(SCREEN_TOOL, state)
    scr = Screen()
    hints = "↑↓ Navigate · Enter Select · r Rescan · Esc Back"
    scr.render(sidebar=side, main=render_main(), hints=hints,
             model=f"Step 3/4")
    return _handle_tool_keys(state)


def _handle_tool_keys(state: Dict) -> Optional[str]:
    key = Keyboard.getkey()
    all_tools = state["tools"]
    total = len(all_tools)

    if key in ("enter", " "):
        if total > 0 and all_tools[state["list_cursor"]].installed:
            state["selected_tool"] = all_tools[state["list_cursor"]]
            state["list_cursor"] = 0
            state["scroll_offset"] = 0
            return SCREEN_LAUNCH
    elif key in ("r", "R"):
        state["tools"] = detect_all_tools()
        state["installed_tools"] = get_installed_tools()
    elif key in ("escape", "q"):
        state["list_cursor"] = 0
        state["scroll_offset"] = 0
        return SCREEN_MODEL
    elif key in ("up", "k", "K"):
        state["list_cursor"] = max(0, state["list_cursor"] - 1)
    elif key in ("down", "j", "J") or key == "\t":
        state["list_cursor"] = min(total - 1, state["list_cursor"] + 1)
    elif handle_scroll(state, key, total):
        pass

    return SCREEN_TOOL


# ═══════════════════════════════════════════════════════════════
# STEP 4: Launch Confirmation
# ═══════════════════════════════════════════════════════════════

def step_launch(state: Dict) -> Optional[str]:
    tool = state.get("selected_tool")
    if not tool:
        return SCREEN_TOOL

    prov_name = state.get("selected_provider", "")
    prov_cfg = state["endpoints"].get(prov_name, {})
    if not prov_cfg:
        return SCREEN_PROVIDER

    options = state.get("launch_options", LaunchOptions())

    def render_main() -> str:
        body: List[str] = []

        body.append(f"  {T.bold('Launch Configuration')}")
        body.append("")

        compat = check_compatibility(tool, prov_cfg)
        mode = compat.get("mode", "?")
        mode_color = "success" if mode == "native" else "primary"

        body.append(f"  {T.text('Tool:')}     "
                   f"{T.highlight(tool.icon + ' ' + tool.display_name)}"
                   f"{T.dim(' v' + tool.version)}")
        prov_bt = prov_cfg.get("backend_type", "?")
        body.append(f"  {T.text('Provider:')} "
                   f"{T.secondary(prov_name)}"
                   f"{T.dim(' [' + prov_bt + ']')}")
        selected_model = state.get("selected_model") or prov_cfg.get("default_model", "?")
        body.append(f"  {T.text('Model:')}    "
                   f"{T.bold(selected_model)}")

        reason = compat.get("reason", "")
        mode_detail = T.dim(" — " + reason) if reason else ""
        body.append(f"  {T.text('Mode:')}     "
                   f"{T.c(mode_color, mode.upper())}{mode_detail}")

        for w in compat.get("warnings", []):
            body.append(f"  {T.warn('  ⚠ ' + w)}")

        body.append("")
        body.append(f"  {Box.horizontal_rule(width=48)}")
        body.append(f"  {T.text('Options (toggle with key)')}")

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
        body.append(f"  {T.dim('[Enter] Launch!  [p] Prov  [m] Model  [t] Tool  [Esc]')}")

        return Box.dialog("Confirm Launch", body)

    side = build_sidebar(SCREEN_LAUNCH, state)
    scr = Screen()
    hints = "Enter Launch · p/m/t Re-pick · c/r/e/s/a Toggle · Esc Cancel"
    scr.render(sidebar=side, main=render_main(), hints=hints,
             model=f"Step 4/4 · {tool.display_name}",
             message=state.get("message", ""))
    return _handle_launch_keys(state)


def _handle_launch_keys(state: Dict) -> Optional[str]:
    key = Keyboard.getkey()

    if key == "enter":
        try:
            tool = state["selected_tool"]
            prov_name = state.get("selected_provider", "") or ""
            prov_cfg = state["endpoints"].get(prov_name, {})
            options = state.get("launch_options", LaunchOptions())

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
                    print(f"  {T.text('Proxy on port')} "
                          f"{T.bold(str(plan.proxy_port))}")
                _wait_any()
                return SCREEN_EXIT
            else:
                print()
                print(f"  {T.error('✗ Launch failed.')}")
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
        state["scroll_offset"] = 0
        return SCREEN_PROVIDER
    elif key == "m":
        state["list_cursor"] = 0
        state["scroll_offset"] = 0
        return SCREEN_MODEL
    elif key == "t":
        state["list_cursor"] = 0
        state["scroll_offset"] = 0
        return SCREEN_TOOL
    elif key in ("escape", "q"):
        return SCREEN_EXIT

    return SCREEN_LAUNCH


# ═══════════════════════════════════════════════════════════════
# Main Loop
# ═══════════════════════════════════════════════════════════════

def run_wizard() -> None:
    """Main event loop — linear 4-step wizard with split-pane UI."""
    state = create_state()

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
    Theme.detect()

    # Welcome banner
    print()
    print(welcome_banner())
    print()

    # --help
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help", "help"):
        print(f"  {T.text('Usage:')}")
        print(f"    zclaude [options]")
        print()
        print(f"  {T.text('Wizard Flow:')}")
        print(f"    Step 1: Select Provider   Choose AI provider (or quick-add)")
        print(f"    Step 2: Select Model      Pick model from provider's list")
        print(f"    Step 3: Select Tool       Pick coding CLI (auto-detected)")
        print(f"    Step 4: Launch            Review config & launch")
        print()
        print(f"  {T.text('Keys:')}")
        print(f"    ↑↓/jk       Navigate    Enter    Select/Next")
        print(f"    Esc/q       Back/Quit    PgUp/Dn Scroll")
        print(f"    a           Add provider (step 1)")
        print(f"    1-9         Quick-add preset (step 1)")
        print(f"    r           Rescan tools (step 3)")
        print(f"    c/r/e/s/a   Toggle options (step 4)")
        print()
        return

    run_wizard()


if __name__ == "__main__":
    main()
