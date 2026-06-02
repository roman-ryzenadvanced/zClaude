#!/usr/bin/env python3
"""Universal runtime utilities for Codex Launcher cross-platform support."""

from __future__ import annotations

import json
import os
import platform as _platform
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

BACKENDS_SUPPORTING_TOOLS = {
    "openai-compat",
    "native",
    "anthropic",
    "command-code",
    "gemini-oauth-antigravity",
}
VISION_MODEL_TOKENS = ("vision", "vl", "gpt-4o", "gemini", "claude")
REASONING_MODEL_TOKENS = ("reason", "thinking", "deepseek", "o3", "o4", "gpt-5")

__all__ = [
    "detect_environment",
    "default_runtime_profiles",
    "build_runtime_profile",
    "setup_wizard_steps",
    "negotiate_provider_capabilities",
    "classify_incident",
    "select_policy_route",
    "OfflineQueue",
    "doctor_plus",
    "export_session_pack",
    "import_session_pack",
]


def detect_environment(env: Optional[Dict[str, str]] = None, platform_name: Optional[str] = None) -> Dict[str, object]:
    env = env or os.environ
    plat = (platform_name or _platform.system()).lower()
    home = str(Path.home())
    is_termux = bool(env.get("TERMUX_VERSION") or "com.termux" in home or env.get("PREFIX", "").startswith("/data/data/com.termux"))
    is_android = is_termux or "android" in home.lower() or bool(env.get("ANDROID_ROOT"))
    is_windows = plat.startswith("win")
    is_wsl = (not is_windows) and ("microsoft" in _platform.release().lower() or bool(env.get("WSL_DISTRO_NAME")))
    is_linux = plat == "linux" and not is_wsl and not is_termux
    shell = env.get("SHELL") or ("powershell" if is_windows else "sh")
    desktop = bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    if is_termux:
        profile = "termux"
    elif is_android:
        profile = "android-shell"
    elif is_windows:
        profile = "windows-desktop"
    elif is_wsl:
        profile = "wsl"
    elif is_linux:
        profile = "linux-desktop" if desktop else "linux-server"
    else:
        profile = "unknown"
    return {
        "platform": plat,
        "profile": profile,
        "is_windows": is_windows,
        "is_wsl": is_wsl,
        "is_linux": is_linux,
        "is_termux": is_termux,
        "is_android": is_android,
        "shell": shell,
        "desktop_session": desktop,
    }


def default_runtime_profiles() -> Dict[str, Dict[str, object]]:
    return {
        "linux-desktop": {
            "dependencies": ["python3", "bash", "curl"],
            "optional_dependencies": ["lsof", "codex"],
            "ui_mode": "gtk",
            "network_timeout_s": 120,
            "retry_budget": 3,
        },
        "windows-desktop": {
            "dependencies": ["python"],
            "optional_dependencies": ["codex"],
            "ui_mode": "tkinter",
            "network_timeout_s": 150,
            "retry_budget": 3,
        },
        "termux": {
            "dependencies": ["python", "bash", "curl", "termux-setup-storage"],
            "optional_dependencies": ["proot-distro", "codex"],
            "ui_mode": "cli",
            "network_timeout_s": 90,
            "retry_budget": 4,
            "mobile_safe_defaults": True,
        },
        "android-shell": {
            "dependencies": ["python", "sh"],
            "optional_dependencies": ["curl"],
            "ui_mode": "web-lite",
            "network_timeout_s": 90,
            "retry_budget": 4,
            "mobile_safe_defaults": True,
        },
        "wsl": {
            "dependencies": ["python3", "bash", "curl"],
            "optional_dependencies": ["codex"],
            "ui_mode": "cli",
            "network_timeout_s": 120,
            "retry_budget": 3,
        },
    }


def build_runtime_profile(mode: str = "basic", overrides: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    env_info = detect_environment()
    profiles = default_runtime_profiles()
    selected = dict(profiles.get(env_info["profile"], profiles["linux-desktop"]))
    if mode == "advanced":
        selected["retry_budget"] = max(5, int(selected.get("retry_budget", 3)))
        selected["telemetry_level"] = "detailed"
    else:
        selected["telemetry_level"] = "minimal"
    if overrides:
        selected.update(overrides)
    deps = selected.get("dependencies", [])
    missing = [dep for dep in deps if shutil.which(dep) is None]
    selected["missing_dependencies"] = missing
    selected["fallback_mode"] = "doctor++" if missing else "ready"
    selected["environment"] = env_info
    return selected


def setup_wizard_steps(mode: str = "basic") -> List[Dict[str, str]]:
    steps = [
        {"id": "detect", "title": "Detect environment", "action": "Inspect OS, shell, desktop/mobile capabilities"},
        {"id": "profile", "title": "Create runtime profile", "action": "Apply safe defaults and dependency checks"},
        {"id": "verify", "title": "Run verification", "action": "Execute install/launch/provider/streaming checks"},
    ]
    if mode == "advanced":
        steps.extend([
            {"id": "policy", "title": "Tune policy routing", "action": "Choose latency/cost/reliability priorities"},
            {"id": "resilience", "title": "Enable resilience engine", "action": "Activate auto-recovery playbooks"},
        ])
    return steps


def negotiate_provider_capabilities(provider_name: str, backend_type: str, model: str) -> Dict[str, object]:
    model_l = (model or "").lower()
    backend_l = (backend_type or "").lower()
    supports_tools = backend_l in BACKENDS_SUPPORTING_TOOLS
    supports_vision = any(token in model_l for token in VISION_MODEL_TOKENS)
    supports_reasoning = any(token in model_l for token in REASONING_MODEL_TOKENS)
    supports_streaming = backend_l != "legacy"
    return {
        "provider": provider_name,
        "backend_type": backend_type,
        "model": model,
        "supports_tools": supports_tools,
        "supports_vision": supports_vision,
        "supports_reasoning": supports_reasoning,
        "supports_streaming": supports_streaming,
        "profile_hint": "high-capability" if supports_tools and supports_streaming else "compatibility",
    }


def classify_incident(log_text: str) -> Dict[str, object]:
    text = (log_text or "").lower()
    if "rate limit" in text or "429" in text:
        return {"category": "rate_limit", "playbook": "backoff_and_route_failover", "confidence": 0.93}
    if "auth" in text or "401" in text or "token" in text:
        return {"category": "authentication", "playbook": "refresh_auth_and_retry", "confidence": 0.92}
    if "timeout" in text or "timed out" in text:
        return {"category": "network_timeout", "playbook": "retry_with_longer_timeout", "confidence": 0.88}
    if "connection refused" in text or "broken pipe" in text:
        return {"category": "proxy_lifecycle", "playbook": "restart_proxy_and_replay", "confidence": 0.9}
    return {"category": "unknown", "playbook": "collect_diagnostics", "confidence": 0.55}


def select_policy_route(routes: List[Dict[str, object]], policy: Optional[Dict[str, float]] = None) -> Dict[str, object]:
    policy = policy or {"latency": 0.4, "cost": 0.3, "reliability": 0.3}
    available = [r for r in routes if r.get("available", True)]
    if not available:
        return {}

    def score(route: Dict[str, object]) -> float:
        latency = float(route.get("latency_ms", 1000))
        cost = float(route.get("cost_per_1k", 1.0))
        reliability = float(route.get("reliability", 0.5))
        return (policy.get("reliability", 0.3) * reliability) - (policy.get("latency", 0.4) * latency / 1000.0) - (policy.get("cost", 0.3) * cost)

    return max(available, key=score)


class OfflineQueue:
    """Disk-backed queue for offline-safe replay."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def enqueue(self, payload: Dict[str, object]) -> str:
        item_id = f"q-{int(time.time() * 1000)}"
        item = {"id": item_id, "ts": time.time(), "payload": payload}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        return item_id

    def read_all(self) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def replay(self, sender) -> Dict[str, object]:
        sent = 0
        failed = 0
        remaining: List[Dict[str, object]] = []
        for item in self.read_all():
            try:
                sender(item["payload"])
                sent += 1
            except Exception:
                failed += 1
                remaining.append(item)
        with self.path.open("w", encoding="utf-8") as fh:
            for item in remaining:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        return {"sent": sent, "failed": failed, "remaining": len(remaining)}


def doctor_plus(dependencies: List[str], writable_paths: List[Path], env: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    env_info = detect_environment(env=env)
    checks = []
    for dep in dependencies:
        ok = shutil.which(dep) is not None
        checks.append({
            "name": f"dependency:{dep}",
            "ok": ok,
            "confidence": 0.99,
            "fix": f"Install '{dep}' and rerun doctor" if not ok else "none",
        })
    for p in writable_paths:
        path = Path(p)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Attempt file creation in the target directory as a writability probe.
            with tempfile.NamedTemporaryFile(dir=str(path.parent), delete=True):
                pass
            ok = True
        except Exception:
            ok = False
        checks.append({
            "name": f"writable:{path}",
            "ok": ok,
            "confidence": 0.95,
            "fix": f"Adjust permissions for {path.parent}" if not ok else "none",
        })
    health = "healthy" if all(c["ok"] for c in checks) else "degraded"
    return {"environment": env_info, "health": health, "checks": checks}


def export_session_pack(destination: Path, metadata: Dict[str, object], files: List[Path]) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json", json.dumps(metadata, indent=2, sort_keys=True))
        for fp in files:
            path = Path(fp)
            if path.exists() and path.is_file():
                zf.write(path, arcname=f"files/{path.name}")
    return destination


def import_session_pack(bundle_path: Path, destination_dir: Path) -> Dict[str, object]:
    bundle_path = Path(bundle_path)
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    extracted_files: List[str] = []
    metadata: Dict[str, object] = {}
    with zipfile.ZipFile(bundle_path, "r") as zf:
        for member in zf.namelist():
            if member == "metadata.json":
                metadata = json.loads(zf.read(member).decode("utf-8"))
            elif member.startswith("files/"):
                out = destination_dir / Path(member).name
                out.write_bytes(zf.read(member))
                extracted_files.append(str(out))
    return {"metadata": metadata, "files": extracted_files}
