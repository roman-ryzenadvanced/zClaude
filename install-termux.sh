#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# Codex Launcher - Termux/Android Installer
# Enhanced with battery awareness, daemon support, boot hooks,
# notifications, widget shortcuts, and self-update capability.
# ──────────────────────────────────────────────────────────────

VERSION="10.13.8"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TOOLS_DIR="$REPO_DIR/tools"
BIN_DIR="$PREFIX/bin"
CONF_DIR="$HOME/.codex"
CACHE_DIR="$HOME/.cache/codex-proxy"
BOOT_DIR="$HOME/.termux/boot"
SHORTCUT_DIR="$HOME/.termux/shortcuts"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ─── Pre-flight checks ──────────────────────────────────────

check_termux() {
    if [[ -z "${TERMUX_VERSION:-}" && "${PREFIX:-}" != /data/data/com.termux/* ]]; then
        error "This installer is for Termux only."
        echo "Use install.sh for Linux/macOS or install.ps1 for Windows."
        exit 1
    fi
}

check_battery() {
    local level=100
    if command -v termux-battery-status &>/dev/null; then
        level=$(termux-battery-status 2>/dev/null | jq -r '.percentage // 100' 2>/dev/null || echo 100)
    fi
    echo "$level"
}

check_network() {
    if ! ping -c 1 -W 3 google.com &>/dev/null 2>&1; then
        warn "No network connectivity detected."
        warn "Installation will continue but some features may not work."
        return 1
    fi
    return 0
}

# ─── Install commands ───────────────────────────────────────

do_install() {
    check_termux

    local battery_level
    battery_level=$(check_battery)

    echo ""
    echo "  ╔══════════════════════════════════════════════╗"
    echo "  ║   Codex Launcher - Termux Installer         ║"
    echo "  ║   Version ${VERSION}                           ║"
    echo "  ╚══════════════════════════════════════════════╝"
    echo ""
    info "Battery level: ${battery_level}%"

    if [[ "$battery_level" -lt 15 ]]; then
        warn "Battery low (${battery_level}%). Some heavy operations will be skipped."
        local LOW_BATTERY=1
    fi

    # Step 1: Update packages
    echo ""
    info "[1/8] Updating package index..."
    pkg update -y 2>/dev/null || warn "Package update failed (continuing)"

    # Step 2: Install dependencies
    info "[2/8] Installing core dependencies..."
    local deps="python bash curl"
    if [[ "${LOW_BATTERY:-0}" -eq 0 ]]; then
        deps="$deps jq"
    fi
    pkg install -y $deps 2>/dev/null || warn "Some packages failed to install"

    # Verify Python
    if ! command -v python &>/dev/null; then
        error "Python installation failed. Run: pkg install python"
        exit 1
    fi

    local py_ver
    py_ver=$(python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    ok "Python $py_ver installed"

    # Step 3: Storage permissions
    echo ""
    info "[3/8] Requesting storage permissions..."
    termux-setup-storage 2>/dev/null || warn "Storage permission denied (optional)"

    # Step 4: Install scripts
    echo ""
    info "[4/8] Installing launcher scripts..."
    mkdir -p "$BIN_DIR" "$CONF_DIR" "$CACHE_DIR"

    local src_files=(
        "translate-proxy.py"
        "codex_launcher_lib.py"
        "universal_runtime.py"
        "config_schema.py"
        "session_manager.py"
    )

    local tool_files=(
        "mobile-control-panel.py"
        "cleanup-codex-stale.py"
        "codex-tui.py"
        "codex-dashboard.py"
        "codex-wizard.py"
        "codex-benchmark.py"
        "codex-health-monitor.py"
        "codex-backup.py"
    )

    local installed=0
    for f in "${src_files[@]}"; do
        if [[ -f "$SCRIPT_DIR/$f" ]]; then
            install -m 0755 "$SCRIPT_DIR/$f" "$BIN_DIR/${f}"
            ((installed++)) || true
        fi
    done
    for f in "${tool_files[@]}"; do
        if [[ -f "$TOOLS_DIR/$f" ]]; then
            install -m 0755 "$TOOLS_DIR/$f" "$BIN_DIR/${f}"
            ((installed++)) || true
        fi
    done

    # Install daemon wrapper
    if [[ -f "$SCRIPT_DIR/termux-daemon.sh" ]]; then
        install -m 0755 "$SCRIPT_DIR/termux-daemon.sh" "$BIN_DIR/codex-daemon"
    fi

    # Package directories — required by the shims above
    for pkg in lib gui proxy antigravity_grpc plugins locales; do
        if [[ -d "$SCRIPT_DIR/$pkg" ]]; then
            rm -rf "$BIN_DIR/$pkg"
            cp -r "$SCRIPT_DIR/$pkg" "$BIN_DIR/$pkg"
            chmod -R 0755 "$BIN_DIR/$pkg"
            # Make all .py files executable/readable
            find "$BIN_DIR/$pkg" -name '*.py' -exec chmod 0644 {} \; 2>/dev/null || true
        fi
    done

    ok "${installed} scripts installed to ${BIN_DIR}"

    # Step 5: Runtime profile
    echo ""
    info "[5/8] Creating runtime profile..."
    python - <<'PY'
import json, os, pathlib
cfg_dir = pathlib.Path.home() / ".codex"
cfg_dir.mkdir(parents=True, exist_ok=True)

profile = cfg_dir / "runtime-profile.termux.json"
profile.write_text(json.dumps({
    "profile": "termux",
    "version": "10.13.8",
    "ui_mode": "cli",
    "mobile_safe_defaults": True,
    "network_timeout_s": 90,
    "retry_budget": 4,
    "battery_aware": True,
    "daemon_support": True,
    "notification_support": True,
}, indent=2), encoding="utf-8")
print(f"  Saved {profile}")
PY

    # Step 6: Boot hooks
    echo ""
    info "[6/8] Setting up Termux:Boot integration..."
    mkdir -p "$BOOT_DIR"
    cat > "$BOOT_DIR/codex-launcher.sh" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
# Auto-start Codex Launcher proxy on device boot
export PATH="$PREFIX/bin:$PATH"
daemon="$(which codex-daemon 2>/dev/null || echo '')"
if [[ -n "$daemon" ]]; then
    bash "$daemon" start
fi
BOOT
    chmod +x "$BOOT_DIR/codex-launcher.sh"
    ok "Boot hook installed"

    # Widget shortcuts
    mkdir -p "$SHORTCUT_DIR"
    echo '#!/data/data/com.termux/files/usr/bin/bash
codex-daemon status' > "$SHORTCUT_DIR/Codex Status"
    echo '#!/data/data/com.termux/files/usr/bin/bash
codex-daemon start' > "$SHORTCUT_DIR/Codex Start"
    echo '#!/data/data/com.termux/files/usr/bin/bash
codex-daemon stop' > "$SHORTCUT_DIR/Codex Stop"
    echo '#!/data/data/com.termux/files/usr/bin/bash
python "$PREFIX/bin/codex-tui.py"' > "$SHORTCUT_DIR/Codex TUI"
    chmod +x "$SHORTCUT_DIR"/Codex\ *
    ok "Widget shortcuts installed"

    # Step 7: Verify
    echo ""
    info "[7/8] Verifying installation..."
    local failed=0
    for f in translate-proxy.py codex-tui.py codex-wizard.py; do
        if [[ -f "$BIN_DIR/$f" ]]; then
            if python -c "import py_compile; py_compile.compile('$BIN_DIR/$f', doraise=True)" 2>/dev/null; then
                ok "$f"
            else
                warn "$f - syntax check failed"
                ((failed++)) || true
            fi
        fi
    done

    # Step 8: Summary
    echo ""
    info "[8/8] Installation complete!"
    echo ""
    echo "  ╔══════════════════════════════════════════════╗"
    echo "  ║   Installed Successfully!                   ║"
    echo "  ╠══════════════════════════════════════════════╣"
    echo "  ║                                              ║"
    echo "  ║   Quick Start:                               ║"
    echo "  ║     codex-wizard.py         (setup)          ║"
    echo "  ║     codex-tui.py            (TUI launcher)   ║"
    echo "  ║     codex-daemon start      (background)     ║"
    echo "  ║     codex-daemon status     (check status)   ║"
    echo "  ║                                              ║"
    echo "  ║   Web Dashboard:                             ║"
    echo "  ║     codex-dashboard.py 8090                  ║"
    echo "  ║     Then open http://localhost:8090           ║"
    echo "  ║                                              ║"
    echo "  ║   Tools:                                     ║"
    echo "  ║     codex-benchmark.py       (latency test)  ║"
    echo "  ║     codex-health-monitor.py  (monitoring)    ║"
    echo "  ║     codex-backup.py create   (backup config) ║"
    echo "  ║                                              ║"
    echo "  ╚══════════════════════════════════════════════╝"
    echo ""

    if [[ "$failed" -gt 0 ]]; then
        warn "$failed file(s) failed syntax verification"
    fi
}

do_uninstall() {
    check_termux
    info "Uninstalling Codex Launcher..."

    # Stop daemon first
    if [[ -f "$BIN_DIR/codex-daemon" ]]; then
        bash "$BIN_DIR/codex-daemon" stop 2>/dev/null || true
    fi

    local files=(
        "translate-proxy.py" "codex_launcher_lib.py" "mobile-control-panel.py"
        "cleanup-codex-stale.py" "universal_runtime.py" "codex-tui.py"
        "codex-dashboard.py" "codex-wizard.py" "codex-benchmark.py"
        "codex-health-monitor.py" "codex-backup.py" "config_schema.py"
        "session_manager.py" "codex-daemon"
    )

    for f in "${files[@]}"; do
        rm -f "$BIN_DIR/$f"
    done

    # Remove package directories
    for d in lib gui proxy antigravity_grpc plugins locales; do
        rm -rf "$BIN_DIR/$d"
    done

    rm -f "$BOOT_DIR/codex-launcher.sh"
    rm -f "$SHORTCUT_DIR"/Codex\ *

    ok "Uninstalled. Config preserved in $CONF_DIR"
}

do_update() {
    check_termux
    info "Checking for updates..."

    local repo_url="https://api.github.com/repos/roman-ryzenadvanced/Codex-Launcher-Any-AI-Provider/releases/latest"
    local latest
    latest=$(curl -s "$repo_url" 2>/dev/null | jq -r '.tag_name // empty' 2>/dev/null || echo "")

    if [[ -z "$latest" ]]; then
        warn "Could not check for updates. Network may be unavailable."
        return 1
    fi

    if [[ "v${VERSION}" == "$latest" ]]; then
        ok "Already up to date (v${VERSION})"
        return 0
    fi

    info "New version available: $latest (current: v${VERSION})"
    info "To update: git pull in the repo directory, then re-run this installer."
}

# ─── Main ────────────────────────────────────────────────────

case "${1:-install}" in
    install|-i|"")  do_install ;;
    uninstall|-u)   do_uninstall ;;
    update|--update) do_update ;;
    help|-h|--help)
        echo "Usage: $0 {install|uninstall|update|help}"
        echo ""
        echo "  install    Install Codex Launcher (default)"
        echo "  uninstall  Remove Codex Launcher"
        echo "  update     Check for updates"
        echo "  help       Show this help"
        ;;
    *)
        error "Unknown command: $1"
        echo "Use: $0 {install|uninstall|update|help}"
        exit 1
        ;;
esac
