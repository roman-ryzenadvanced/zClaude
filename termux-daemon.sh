#!/data/data/com.termux/files/usr/bin/bash
# Codex Launcher Termux Daemon Wrapper
# Manages background proxy process on Android/Termux

set -euo pipefail

PROG_NAME="codex-launcher"
DAEMON_NAME="translate-proxy"
CONFIG_DIR="$HOME/.codex"
PID_FILE="$CONFIG_DIR/proxy.pid"
LOG_DIR="$HOME/.cache/codex-proxy"
LOG_FILE="$LOG_DIR/proxy.log"
PORT="${CODEX_PORT:-8080}"

# Find the proxy script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROXY_SCRIPT=""
for loc in "$SCRIPT_DIR/translate-proxy.py" \
           "$HOME/.local/bin/translate-proxy.py" \
           "/usr/lib/codex-launcher/translate-proxy.py"; do
    if [ -f "$loc" ]; then
        PROXY_SCRIPT="$loc"
        break
    fi
done

# Ensure directories exist
mkdir -p "$CONFIG_DIR" "$LOG_DIR"

is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$PID_FILE"
    fi
    return 1
}

do_start() {
    if is_running; then
        echo "[$PROG_NAME] Already running (PID $(cat "$PID_FILE"))"
        return 1
    fi

    if [ -z "$PROXY_SCRIPT" ]; then
        echo "[$PROG_NAME] Error: translate-proxy.py not found"
        return 1
    fi

    echo "[$PROG_NAME] Starting proxy on port $PORT..."

    # Acquire wake lock to prevent battery optimization killing the process
    if command -v termux-wake-lock &>/dev/null; then
        termux-wake-lock
    fi

    # Start proxy in background
    nohup python "$PROXY_SCRIPT" --port "$PORT" >> "$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"

    # Wait briefly and verify
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        echo "[$PROG_NAME] Started (PID $pid)"

        # Send notification
        if command -v termux-notification &>/dev/null; then
            termux-notification --title "Codex Launcher" --content "Proxy started on port $PORT" --id codex-proxy
        fi
        return 0
    else
        echo "[$PROG_NAME] Failed to start. Check $LOG_FILE"
        rm -f "$PID_FILE"
        return 1
    fi
}

do_stop() {
    if ! is_running; then
        echo "[$PROG_NAME] Not running"
        return 1
    fi

    local pid
    pid=$(cat "$PID_FILE")
    echo "[$PROG_NAME] Stopping proxy (PID $pid)..."
    kill "$pid" 2>/dev/null || true
    sleep 2
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"

    # Release wake lock
    if command -v termux-wake-unlock &>/dev/null; then
        termux-wake-unlock
    fi

    if command -v termux-notification &>/dev/null; then
        termux-notification --title "Codex Launcher" --content "Proxy stopped" --id codex-proxy
    fi

    echo "[$PROG_NAME] Stopped"
    return 0
}

do_restart() {
    do_stop
    sleep 1
    do_start
}

do_status() {
    if is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo "[$PROG_NAME] Running (PID $pid)"

        # Show health if proxy is up
        if command -v curl &>/dev/null; then
            local health
            health=$(curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null || echo "unreachable")
            echo "[$PROG_NAME] Health: $health"
        fi
    else
        echo "[$PROG_NAME] Not running"
    fi
}

do_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "No log file found at $LOG_FILE"
    fi
}

case "${1:-}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_restart ;;
    status)  do_status ;;
    logs)    do_logs ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        echo ""
        echo "  start    Start the proxy daemon"
        echo "  stop     Stop the proxy daemon"
        echo "  restart  Restart the proxy daemon"
        echo "  status   Show daemon status"
        echo "  logs     Tail proxy logs"
        exit 1
        ;;
esac
