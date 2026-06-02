#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  zClaude — Linux / macOS Installer
#  Installs proxy, GUI, provider manager, and all dependencies
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

VERSION="1.0.0"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ─── Detect platform ──────────────────────────────────────
detect_platform() {
    case "$(uname -s)" in
        Linux*)  PLATFORM="linux" ;;
        Darwin*) PLATFORM="macos" ;;
        *)       PLATFORM="unknown" ;;
    esac

    # Check for desktop environment
    HAS_DESKTOP=false
    if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
        HAS_DESKTOP=true
    fi

    # Detect Python command
    PYTHON_CMD=""
    if command -v python3 &>/dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &>/dev/null; then
        PYTHON_CMD="python"
    else
        error "Python 3.8+ is required but not found."
        info "Install it from: https://www.python.org/downloads/"
        exit 1
    fi

    PY_VER=$($PYTHON_CMD -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    ok "Python ${PY_VER} detected on ${PLATFORM}"
}

# ─── Install files ────────────────────────────────────────
install_files() {
    local BIN_DIR="${HOME}/.local/bin"
    local LIB_DIR="${HOME}/.local/lib/zclaude"

    mkdir -p "$BIN_DIR" "$LIB_DIR"

    # Copy source tree (excluding __pycache__)
    rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
          "$SCRIPT_DIR/" "$LIB_DIR/"

    # Create launcher symlinks/scripts
    local SCRIPTS=(
        "translate-proxy.py:zclaude-proxy"
        "provider_manager.py:zclaude-providers"
        "session_manager.py:zclaude-sessions"
        "codex-launcher-gui.py:zclaude-gui"
        "codex-launcher-gui-x.py:zclaude-gui-x"
        "universal_runtime.py:zclaude-runtime"
        "config_schema.py:zclaude-validate"
    )

    local installed=0
    for item in "${SCRIPTS[@]}"; do
        src="${item%%:*}"
        dst="${item##*:}"
        cat > "$BIN_DIR/${dst}" <<LAUNCHER
#!/usr/bin/env bash
exec "$PYTHON_CMD" "$LIB_DIR/${src}" "\$@"
LAUNCHER
        chmod +x "$BIN_DIR/${dst}"
        ((installed++)) || true
    done

    # Copy package directories needed by shims
    for pkg in lib gui gui_x proxy antigravity_grpc plugins locales; do
        if [ -d "$LIB_DIR/$pkg" ]; then
            chmod -R +r "$LIB_DIR/$pkg"
        fi
    done

    ok "${installed} commands installed to ${BIN_DIR}"
}

# ─── Verify installation ──────────────────────────────────
verify() {
    info "Verifying installation..."
    local failed=0

    for cmd in zclaude-proxy zclaude-providers zclaude-sessions; do
        if command -v "$cmd" &>/dev/null; then
            if $PYTHON_CMD -c "import py_compile; py_compile.compile('$(which $cmd)', doraise=True)" 2>/dev/null; then
                ok "$cmd"
            else
                warn "$cmd — syntax check issued warnings"
            fi
        else
            fail "$cmd not found in PATH"
            ((failed++)) || true
        fi
    done

    return $failed
}

# ─── PATH check ───────────────────────────────────────────
check_path() {
    local BIN_DIR="${HOME}/.local/bin"
    if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
        warn "${BIN_DIR} is not in your PATH."
        info "Add this line to your ~/.bashrc or ~/.zshrc:"
        echo ""
        echo '  export PATH="${HOME}/.local/bin:${PATH}"'
        echo ""
        info "Then run: source ~/.bashrc  (or restart your terminal)"
    fi
}

# ─── Main ──────────────────────────────────────────────────
main() {
    echo ""
    echo "  ╔══════════════════════════════════════════════╗"
    echo "  ║         zClaude Installer v${VERSION}           ║"
    echo "  ║   Universal AI Proxy & Launcher              ║"
    echo "  ╚══════════════════════════════════════════════╝"
    echo ""

    detect_platform
    install_files
    verify
    check_path

    echo ""
    echo "  ╔══════════════════════════════════════════════╗"
    echo "  ✅ Installation complete!"
    echo "  ╠══════════════════════════════════════════════╣"
    echo "  ║                                          ║"
    echo "  ║  Quick Start:                             ║"
    echo "  ║    zclaude-providers wizard               ║"
    echo "  ║    zclaude-proxy                          ║"
    echo "  ║    zclaude-gui                             ║"
    echo "  ║                                          ║"
    echo "  ║  Provider Management:                     ║"
    echo "  ║    zclaude-providers list                 ║"
    echo "  ║    zclaude-providers add                   ║"
    echo "  ║    zclaude-providers test <name>           ║"
    echo "  ║                                          ║"
    echo "  ╚══════════════════════════════════════════════╝"
    echo ""
}

# Run uninstall if requested
case "${1:-install}" in
    uninstall|remove|--uninstall)
        info "Uninstalling zClaude..."
        BIN_DIR="${HOME}/.local/bin"
        LIB_DIR="${HOME}/.local/lib/zclaude"
        rm -f "$BIN_DIR"/zclaude-*
        rm -rf "$LIB_DIR"
        ok "Uninstalled. Config preserved in ~/.codex/"
        ;;
    *)
        main
        ;;
esac
