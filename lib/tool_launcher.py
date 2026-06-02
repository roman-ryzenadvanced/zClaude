"""Launch execution engine — bridges tool + provider → running process.

Takes a ToolInfo (from tool_detector) and provider config (from
endpoints.json / presets), determines whether proxy is needed,
builds environment variables, constructs the command, and spawns
the process in a new terminal or background.

Critical path:
  1. check_compatibility()  — validate tool+provider pair
  2. start_proxy_for()      — from lib/proxy_lifecycle (if needed)
  3. build_env_for_launch() — merge env vars for this combo
  4. build_command()        — per-tool argument list
  5. execute_launch()       — spawn & return to dashboard
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib.constants import IS_WINDOWS, HOME, CONFIG_DIR, PROXY_CONFIG_DIR
from lib.tool_detector import ToolInfo


# ════════════════════════════════════════════════════════════════════
# Data structures
# ════════════════════════════════════════════════════════════════════

@dataclass
class LaunchOptions:
    """User-configurable options for a launch session."""
    caveman_mode: bool = False
    rtk_compression: bool = False
    reasoning_effort: str = "medium"      # low | medium | high
    sandbox_mode: bool = False
    approval_mode: str = "default"        # default | auto-accept | edit-only
    extra_args: List[str] = field(default_factory=list)


@dataclass
class LaunchPlan:
    """Fully resolved launch plan ready to execute."""
    tool: ToolInfo
    provider_name: str
    provider_cfg: Dict[str, Any]
    options: LaunchOptions
    use_proxy: bool = True
    proxy_port: Optional[int] = None
    env_overrides: Dict[str, str] = field(default_factory=dict)
    command: List[str] = field(default_factory=list)
    compatibility: Dict[str, Any] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════════════
# Environment variable mapping per (tool, provider) pair
# ════════════════════════════════════════════════════════════════════

# Maps (tool_id, backend_type) → list of (env_var, value_template)
# Templates may contain {port}, {api_key}, {base_url}, {model}
ENV_MAP: Dict[Tuple[str, str], List[Tuple[str, str]]] = {
    # ── Codex CLI ──────────────────────────────────────
    ("codex", "openai-compat"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
    ],
    ("codex", "anthropic"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
    ],
    # Native OpenAI endpoint for codex (no proxy needed if base_url matches)
    ("codex", "native-openai"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "{base_url}"),
    ],

    # ── Claude Code ────────────────────────────────────
    ("claude", "anthropic"): [
        ("ANTHROPIC_API_KEY", "{api_key}"),
    ],
    ("claude", "openai-compat"): [
        ("ANTHROPIC_BASE_URL", "http://127.0.0.1:{port}"),
        ("ANTHROPIC_API_KEY", "{api_key}"),
    ],

    # ── OpenCode ───────────────────────────────────────
    ("opencode", "openai-compat"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
    ],
    ("opencode", "anthropic"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
        ("ANTHROPIC_BASE_URL", "http://127.0.0.1:{port}"),
        ("ANTHROPIC_API_KEY", "{api_key}"),
    ],

    # ── Gemini CLI ─────────────────────────────────────
    ("gemini", "google"): [
        # Gemini uses its own OAuth; we set model override via env
        ("GEMINI_MODEL", "{model}"),
    ],
    ("gemini", "gemini-oauth"): [
        ("GEMINI_MODEL", "{model}"),
    ],
    ("gemini", "openai-compat"): [
        # Some gemini forks accept OPENAI env vars
        ("GEMINI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
    ],

    # ── Kiro ───────────────────────────────────────────
    ("kiro", "kiro-oauth"): [
        # Kiro manages its own auth
        ("KIRO_MODEL", "{model}"),
    ],
    ("kiro", "openai-compat"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
    ],

    # ── Aider ──────────────────────────────────────────
    ("aider", "openai-compat"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
    ],
    ("aider", "anthropic"): [
        ("ANTHROPIC_API_KEY", "{api_key}"),
    ],

    # ── Cursor ─────────────────────────────────────────
    ("cursor", "openai-compat"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
    ],

    # ── Warp ───────────────────────────────────────────
    ("warp", "openai-compat"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
    ],

    # ── Cline ──────────────────────────────────────────
    ("cline", "openai-compat"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
    ],

    # ── Copilot CLI ────────────────────────────────────
    ("copilot", "openai-compat"): [
        ("OPENAI_API_KEY", "{api_key}"),
        ("OPENAI_BASE_URL", "http://127.0.0.1:{port}/v1"),
    ],
}


# Per-tool default command templates (binary + common args)
TOOL_COMMANDS: Dict[str, List[str]] = {
    "codex": ["codex"],
    "claude": ["claude"],
    "opencode": ["opencode"],
    "gemini": ["gemini"],
    "kiro": ["kiro"],
    "aider": ["aider"],
    "cursor": ["cursor"],
    "warp": ["warp"],
    "cline": ["cline"],
    "copilot": ["github-copilot-cli"],
}

# Extra args per approval mode
APPROVAL_ARGS: Dict[str, List[str]] = {
    "default": [],
    "auto-accept": ["--dangerously-skip-permissions"] if not IS_WINDOWS else ["/y"],
    "edit-only": ["--allowedTools", "Edit,Read,Bash,Write"],
}


# ════════════════════════════════════════════════════════════════════
# Compatibility checking
# ════════════════════════════════════════════════════════════════════

def check_compatibility(tool: ToolInfo, provider_cfg: Dict) -> Dict[str, Any]:
    """Validate that a tool+provider pair can work together.

    Returns dict with:
      - compatible (bool): can they work together?
      - mode (str): 'native' | 'proxy' | 'partial'
      - reason (str): human-readable explanation
      - warnings (list): any caveats
    """
    prov_backend = provider_cfg.get("backend_type", "unknown")
    tool_backend = tool.backend_preference
    warnings: List[str] = []

    # Perfect match: native support
    if prov_backend == tool_backend and tool.supports_native:
        return {
            "compatible": True,
            "mode": "native",
            "reason": f"{tool.display_name} natively supports {prov_backend}",
            "warnings": warnings,
        }

    # OpenAI-compatible tools work with anything through proxy
    if tool_backend == "openai-compat":
        return {
            "compatible": True,
            "mode": "proxy",
            "reason": f"{tool.display_name} will connect via translate-proxy",
            "warnings": warnings,
        }

    # Anthropic tool with anthropic-like backend
    if tool_backend == "anthropic" and prov_backend in ("anthropic", "openai-compat"):
        if prov_backend == "anthropic":
            return {
                "compatible": True,
                "mode": "native",
                "reason": f"{tool.display_name} with native Anthropic API",
                "warnings": warnings,
            }
        return {
            "compatible": True,
            "mode": "proxy",
            "reason": f"{tool.display_name} via Anthropic-compatible proxy",
            "warnings": warnings,
        }

    # Google/Gemini specific
    if tool_backend in ("google", "gemini-oauth") and prov_backend in (
        "google", "gemini-oauth"
    ):
        return {
            "compatible": True,
            "mode": "native",
            "reason": f"{tool.display_name} with Google backend",
            "warnings": warnings,
        }

    # Kiro specific
    if tool_backend == "kiro-oauth" and prov_backend == "kiro-oauth":
        return {
            "compatible": True,
            "mode": "native",
            "reason": f"{tool.display_name} with Kiro OAuth",
            "warnings": warnings,
        }

    # Fallback: try through proxy anyway (most openai-compat tools handle it)
    if tool_backend == "openai-compat":
        warnings.append(
            f"Provider {prov_backend} may need translation; "
            f"proxy will attempt conversion"
        )
        return {
            "compatible": True,
            "mode": "proxy",
            "reason": "Attempting connection via translation proxy",
            "warnings": warnings,
        }

    # Unknown pairing — warn but allow
    warnings.append(
        f"Untested combination: {tool.display_name} ({tool_backend}) "
        f"+ provider ({prov_backend})"
    )
    return {
        "compatible": True,  # Allow but warn
        "mode": "proxy",
        "reason": "Untested combination — using proxy as bridge",
        "warnings": warnings,
    }


# ════════════════════════════════════════════════════════════════════
# Environment builder
# ════════════════════════════════════════════════════════════════════

def build_env_for_launch(plan: LaunchPlan) -> Dict[str, str]:
    """Build environment variables for the launch.

    Merges overrides on top of current os.environ so the child
    process inherits everything plus our additions.
    """
    env = dict(os.environ)

    # Apply mapped env vars for this (tool, provider) pair
    key = (plan.tool.tool_id, plan.provider_cfg.get("backend_type", ""))
    mappings = ENV_MAP.get(key, [])

    api_key = plan.provider_cfg.get("api_key", "")
    base_url = plan.provider_cfg.get("base_url", "")
    model = plan.provider_cfg.get("default_model", "") or ""
    port = plan.proxy_port or 0

    for var_name, template in mappings:
        value = template.format(
            port=port,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        env[var_name] = value

    # Apply explicit overrides from plan
    env.update(plan.env_overrides)

    # Reasoning effort
    if plan.options.reasoning_effort != "medium":
        env["REASONING_EFFORT"] = plan.options.reasoning_effort

    return env


def build_command_for_tool(
    tool: ToolInfo,
    provider_cfg: Dict,
    options: LaunchOptions,
) -> List[str]:
    """Construct the command argument list for launching a tool."""
    cmd = list(TOOL_COMMANDS.get(tool.tool_id, [tool.binary]))

    # Add model override if specified
    model = provider_cfg.get("default_model", "")
    if model:
        # Each tool has its own flag for model selection
        model_flags = {
            "codex": ["--model", model],
            "claude": ["--model", model],
            "opencode": ["--model", model],
            "gemini": ["--model", model],
            "aider": ["--model", model],
            "cursor": ["--model", model],
        }
        cmd.extend(model_flags.get(tool.tool_id, []))

    # Approval mode args
    cmd.extend(APPROVAL_ARGS.get(options.approval_mode, []))

    # Caveman mode (streaming, minimal overhead)
    if options.caveman_mode:
        if tool.tool_id == "codex":
            cmd.append("--caveman-mode")
        elif tool.tool_id == "claude":
            cmd.extend(["--max-turns", "50"])

    # RTK compression
    if options.rtk_compression:
        if tool.tool_id == "codex":
            cmd.append("--rtk-compression")

    # User's extra args
    if options.extra_args:
        cmd.extend(options.extra_args)

    return cmd


# ════════════════════════════════════════════════════════════════════
# Plan builder — orchestrates all checks into one LaunchPlan
# ════════════════════════════════════════════════════════════════════

def build_launch_plan(
    tool: ToolInfo,
    provider_name: str,
    provider_cfg: Dict,
    options: LaunchOptions = None,
) -> LaunchPlan:
    """Build a complete LaunchPlan from tool + provider + options.

    This is the main entry point called by the launcher TUI.
    It runs compatibility check, determines proxy need, builds
    env vars and command — everything except actually spawning.
    """
    if options is None:
        options = LaunchOptions()

    # Compatibility check
    compat = check_compatibility(tool, provider_cfg)

    # Determine if proxy is needed
    use_proxy = compat["mode"] != "native"
    proxy_port = None

    plan = LaunchPlan(
        tool=tool,
        provider_name=provider_name,
        provider_cfg=provider_cfg,
        options=options,
        use_proxy=use_proxy,
        proxy_port=proxy_port,
        compatibility=compat,
    )

    # Build command
    plan.command = build_command_for_tool(tool, provider_cfg, options)

    # Build env (proxy port filled in later during execute_launch)
    plan.env_overrides = build_env_for_launch(plan)

    return plan


# ════════════════════════════════════════════════════════════════════
# Execute launch — the critical path
# ════════════════════════════════════════════════════════════════════

def execute_launch(
    plan: LaunchPlan,
    logfn: Callable[[str], None] = print,
) -> int:
    """Execute a fully built LaunchPlan.

    Steps:
      1. Verify compatibility
      2. Start proxy if needed (via lib/proxy_lifecycle.start_proxy_for)
      3. Build complete environment
      4. Spawn process in new terminal/session
      5. Return PID (0 on failure)

    The caller (launcher TUI) should return to dashboard after this;
    the spawned process runs independently.
    """
    logfn(f"\n  Launching {C.bold(plan.tool.display_name)} ...")

    # Step 1: Compatibility
    if not plan.compatibility.get("compatible", False):
        logfn(C.error(f"  ✗ Incompatible: {plan.compatibility.get('reason', 'Unknown')}"))
        return 0

    mode = plan.compatibility.get("mode", "proxy")
    logfn(f"  Mode: {C.info(mode)} — {plan.compatibility.get('reason', '')}")

    for w in plan.compatibility.get("warnings", []):
        logfn(C.warn(f"  ⚠ {w}"))

    # Step 2: Start proxy if needed
    if plan.use_proxy:
        try:
            from lib.proxy_lifecycle import start_proxy_for

            logfn(f"  Starting translate-proxy for {C.highlight(plan.provider_name)} ...")
            port = start_proxy_for(plan.provider_cfg, logfn)
            plan.proxy_port = port
            logfn(C.success(f"  ✓ Proxy ready on port {port}"))
        except Exception as exc:
            logfn(C.error(f"  ✗ Proxy failed: {exc}"))
            return 0
    else:
        logfn(f"  No proxy needed (native {plan.provider_cfg.get('backend_type', '')} mode)")

    # Step 3: Build final environment
    env = build_env_for_launch(plan)

    # Mask API key in logs
    safe_env = {k: ("***" if "key" in k.lower() or "token" in k.lower() else v)
                for k, v in env.items()
                if k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                         "COPILOT_TOKEN")}
    if safe_env:
        logfn(f"  Env overrides: {safe_env}")

    # Step 4: Spawn process
    cmd = plan.command
    logfn(f"  Command: {' '.join(cmd)}")

    try:
        proc = _spawn_process(cmd, env, logfn)
        pid = proc.pid
        logfn(C.success(f"  ✓ Launched! PID={pid}"))
        logfn(f"  Returning to dashboard (process runs in background)")
        return pid
    except Exception as exc:
        logfn(C.error(f"  ✗ Launch failed: {exc}"))
        return 0


def _spawn_process(
    cmd: List[str],
    env: Dict[str, str],
    logfn: Callable[[str], None],
) -> subprocess.Popen:
    """Spawn the tool process in a way that survives launcher exit.

    On Unix: setsid creates a new session so the child outlives us.
    On Windows: CREATE_NEW_PROCESS_GROUP does the same.
    """
    popen_kwargs = {
        "env": env,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }

    if IS_WINDOWS:
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        popen_kwargs["preexec_fn"] = os.setsid

    proc = subprocess.Popen(cmd, **popen_kwargs)

    # Give it a moment to start
    time.sleep(0.2)

    # Check it didn't immediately crash
    if proc.poll() is not None:
        raise RuntimeError(
            f"Process exited immediately with code {proc.returncode}"
        )

    return proc


# ════════════════════════════════════════════════════════════════════
# Terminal spawning alternative (for GUI tools like Cursor/Warp)
# ════════════════════════════════════════════════════════════════════

def spawn_in_terminal(
    cmd: List[str],
    env: Dict[str, str],
    logfn: Callable[[str], None] = print,
) -> Optional[subprocess.Popen]:
    """Spawn a command inside a new terminal window.

    Useful for tools that need interactive terminal access.
    Detects available terminal emulator and launches accordingly.
    """
    from lib.platform_utils import detect_terminal

    term_info = detect_terminal()
    if not term_info:
        # Fall back to background spawn
        logfn("No terminal emulator found, spawning in background")
        return _spawn_process(cmd, env, logfn)

    term_name, term_args, term_path = term_info
    cmd_str = " ".join(cmd)

    # Build env export string for shell wrapper
    env_exports = "".join(
        f'export {k}="{v}"\n' for k, v in env.items()
        if any(s in k.upper() for s in ("API_KEY", "BASE_URL", "MODEL", "PROXY"))
    )

    wrapper_script = (
        "#!/bin/bash\n"
        f"{env_exports}"
        f"exec {cmd_str}\n"
    )

    # Write temp script
    import tempfile
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False, prefix="zclaude-launch-"
    )
    tmp.write(wrapper_script)
    tmp.close()
    os.chmod(tmp.name, 0o755)

    full_cmd = [term_path] + term_args + [tmp.name]

    try:
        proc = subprocess.Popen(
            full_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid if not IS_WINDOWS else None,
        )
        logfn(f"Spawned in {term_name} (PID={proc.pid})")

        # Schedule cleanup of temp file
        def _cleanup():
            time.sleep(2)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        import threading
        threading.Thread(target=_cleanup, daemon=True).start()
        return proc
    except Exception as exc:
        logfn(f"Failed to spawn in terminal: {exc}")
        os.unlink(tmp.name)
        return None


# ════════════════════════════════════════════════════════════════════
# Lazy color helper (avoid circular import at module level)
# ════════════════════════════════════════════════════════════════════

def C():
    """Lazy-import Colors class to avoid circular imports."""
    from lib.tui_engine import C as _C
    return _C
