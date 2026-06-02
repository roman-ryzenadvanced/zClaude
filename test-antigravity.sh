#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# test-antigravity.sh — End-to-end Antigravity proxy test + real task
#
# Phases:
#   1. Token validity
#   2. Direct REST endpoint probe
#   3. Proxy adapter (start proxy, test /responses)
#   4. Real Codex CLI task (if --task flag given)
#   5. Anomaly detection + analysis
#
# Usage:
#   bash ~/.local/bin/test-antigravity.sh              # quick tests
#   bash ~/.local/bin/test-antigravity.sh --task        # + real CLI task
#   bash ~/.local/bin/test-antigravity.sh --verbose     # show all logs
# Exit:  0 = all pass, 1 = some fail
# ═══════════════════════════════════════════════════════════════════
set -uo pipefail

VERBOSE=0; RUN_TASK=0
for arg in "$@"; do
    case "$arg" in
        --verbose|-v) VERBOSE=1 ;;
        --task|-t) RUN_TASK=1 ;;
    esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
PASS=0; FAIL=0; SKIP=0; RESULTS=()
log_pass() { echo -e "  ${GREEN}PASS${NC} $1"; ((PASS++)); RESULTS+=("PASS $1"); }
log_fail() { echo -e "  ${RED}FAIL${NC} $1"; ((FAIL++)); RESULTS+=("FAIL $1"); }
log_skip() { echo -e "  ${YELLOW}SKIP${NC} $1"; ((SKIP++)); RESULTS+=("SKIP $1"); }
log_info() { echo -e "  ${CYAN}INFO${NC} $1"; }

TOKEN_PATH="$HOME/.cache/codex-proxy/google-antigravity-oauth-token.json"
[ ! -f "$TOKEN_PATH" ] && { echo "ERROR: No token file. Login via GUI first."; exit 1; }

ACCESS_TOKEN=$(python3 -c "
import json, os, sys, time, urllib.request, urllib.parse
tp = os.path.expanduser('~/.cache/codex-proxy/google-antigravity-oauth-token.json')
d = json.load(open(tp))
if d.get('expires_at', 0) > time.time(): print(d['access_token']); sys.exit(0)
cid, cs, rt = d.get('client_id',''), d.get('client_secret',''), d.get('refresh_token','')
if not all([cid, cs, rt]): print('ERROR'); sys.exit(1)
data = urllib.parse.urlencode({'client_id':cid,'client_secret':cs,'refresh_token':rt,'grant_type':'refresh_token'}).encode()
resp = urllib.request.urlopen(urllib.request.Request('https://oauth2.googleapis.com/token', data=data), timeout=15)
tok = json.loads(resp.read()); d.update(tok); d['expires_at'] = time.time() + tok.get('expires_in',3600)
json.dump(d, open(tp,'w')); print(tok.get('access_token','ERROR'))
" 2>&1) || true
[[ "$ACCESS_TOKEN" == ERROR* ]] || [ -z "$ACCESS_TOKEN" ] && { echo "ERROR: Token refresh failed: $ACCESS_TOKEN"; exit 1; }

PROJECT_ID=$(python3 -c "import json; print(json.load(open('$TOKEN_PATH')).get('project_id',''))")
[ -z "$PROJECT_ID" ] && { echo "ERROR: No project_id"; exit 1; }

echo "═══════════════════════════════════════════════════════════════"
echo " Antigravity E2E Test Suite"
echo "═══════════════════════════════════════════════════════════════"
echo " Project: $PROJECT_ID  Token: ${ACCESS_TOKEN:0:20}..."

# ── Test 1: Token validity ────────────────────────────────────────
echo ""; echo "─── Test 1: Token Validity ───"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ACCESS_TOKEN" \
    "https://www.googleapis.com/oauth2/v1/userinfo" --max-time 5)
[ "$HTTP" = "200" ] && log_pass "Token valid" || log_fail "Token invalid (HTTP $HTTP)"

# ── Test 2: Direct REST probe (prod first, fast timeout) ─────────
echo ""; echo "─── Test 2: Direct REST Endpoint Probe ───"
ENDPOINTS=(
    "https://cloudcode-pa.googleapis.com"
    "https://daily-cloudcode-pa.sandbox.googleapis.com"
    "https://autopush-cloudcode-pa.sandbox.googleapis.com"
)
MODELS=("gemini-3-flash")
BEST_EP=""; BEST_MODEL=""

for model in "${MODELS[@]}"; do
    for ep in "${ENDPOINTS[@]}"; do
        ep_s=$(echo "$ep" | sed 's|https://||;s|.googleapis.com||')
        RESP=$(curl -s -w "\n%{http_code}" -X POST "${ep}/v1internal:generateContent" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Antigravity/2.0.6 Chrome/138.0.7204.235 Electron/37.3.1 Safari/537.36" \
            -H 'Client-Metadata: {"ideType":"ANTIGRAVITY","platform":"LINUX","pluginType":"GEMINI"}' \
            -d "{\"project\":\"$PROJECT_ID\",\"model\":\"$model\",\"requestType\":\"agent\",\"userAgent\":\"antigravity/2.0.6 linux/x64\",\"requestId\":\"t$(date +%s)\",\"request\":{\"contents\":[{\"role\":\"user\",\"parts\":[{\"text\":\"Say hi\"}]}],\"sessionId\":\"t$(date +%s%N)\",\"generationConfig\":{\"maxOutputTokens\":256}}}" \
            --connect-timeout 5 --max-time 20 2>&1)
        HTTP=$(echo "$RESP" | tail -1); BODY=$(echo "$RESP" | sed '$d')
        if [ "$HTTP" = "200" ]; then
            TEXT=$(echo "$BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    parts = d.get('response',{}).get('candidates',[{}])[0].get('content',{}).get('parts',[])
    texts = [p['text'] for p in parts if 'text' in p and p['text']]
    print(' '.join(texts)[:80] if texts else 'EMPTY')
except: print('EMPTY')" 2>/dev/null)
            if [ "$TEXT" != "EMPTY" ] && ! echo "$TEXT" | grep -qi "no longer supported"; then
                log_pass "$model @ ${ep_s} → \"$TEXT\""
                [ -z "$BEST_EP" ] && BEST_EP="$ep" && BEST_MODEL="$model"
            else
                log_fail "$model @ ${ep_s} → 200 but empty/deprecated"
            fi
        else
            ERR=$(echo "$BODY" | python3 -c "
import sys, json
try: print(json.load(sys.stdin).get('error',{}).get('status','')[:50])
except: pass" 2>/dev/null)
            log_skip "$model @ ${ep_s} → $HTTP $ERR"
        fi
    done
done

# ── Test 3: Proxy adapter (start proxy, test /responses) ──────────
echo ""; echo "─── Test 3: Proxy Adapter (end-to-end) ───"
set +e

TEST_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")
PROXY_API_KEY="test-$RANDOM"

find /home/roman/.local/bin -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true

PROXY_PID=""
export PROXY_PORT=$TEST_PORT
export PROXY_API_KEY=$PROXY_API_KEY
export PROXY_BACKEND=gemini-oauth-antigravity
export PROXY_TARGET_URL=https://cloudcode-pa.googleapis.com
python3 /home/roman/.local/bin/translate-proxy.py >/tmp/antigravity-test-proxy.log 2>&1 &
PROXY_PID=$!

cleanup() { kill $PROXY_PID 2>/dev/null || true; wait $PROXY_PID 2>/dev/null || true; }
trap cleanup EXIT

sleep 3
if ! kill -0 $PROXY_PID 2>/dev/null; then
    log_fail "Proxy failed to start (port $TEST_PORT)"
    cat /tmp/antigravity-test-proxy.log 2>/dev/null | tail -5
else
    log_pass "Proxy started on :$TEST_PORT"

    # /v1/models
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $PROXY_API_KEY" \
        "http://127.0.0.1:$TEST_PORT/v1/models" --max-time 5)
    [ "$HTTP" = "200" ] && log_pass "/v1/models → 200" || log_fail "/v1/models → $HTTP"

    # /responses (non-stream)
    RESP_HTTP=$(curl -s -w "%{http_code}" -o /tmp/antigravity-test-response.json \
        -X POST "http://127.0.0.1:$TEST_PORT/responses" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $PROXY_API_KEY" \
        -d '{
            "model":"gemini-3.5-flash-high",
            "stream":false,
            "input":[{"type":"message","role":"user","content":[{"type":"input_text","text":"Say hello in exactly 3 words"}]}],
            "tools":[{"type":"function","name":"test_tool","description":"test","parameters":{"type":"object","properties":{"cmd":{"type":"string"}}}}],
            "instructions":"You are a helpful assistant.",
            "max_output_tokens":256
        }' --connect-timeout 10 --max-time 60 2>&1)

    if [ "$RESP_HTTP" = "200" ]; then
        TEXT=$(python3 -c "
import json
d = json.load(open('/tmp/antigravity-test-response.json'))
out = d.get('output', [])
texts = []
for item in out:
    for p in (item.get('content', []) if isinstance(item, dict) else []):
        if isinstance(p, dict): texts.append(p.get('text', ''))
print(' '.join(t for t in texts if t).strip()[:120] or 'EMPTY')
" 2>/dev/null)
        if [ "$TEXT" = "EMPTY" ]; then
            log_fail "Proxy /responses → 200 but EMPTY"
        else
            log_pass "Proxy /responses → 200: \"$TEXT\""
        fi
    else
        ERR=$(python3 -c "
import json; d = json.load(open('/tmp/antigravity-test-response.json'))
print(d.get('error',{}).get('message','')[:120])" 2>/dev/null || echo "unknown")
        log_fail "Proxy /responses → $RESP_HTTP: $ERR"
    fi

    # Verify model resolution in logs
    if grep -q "model resolved: gemini-3.5-flash-high -> gemini-3-flash" /tmp/antigravity-test-proxy.log; then
        log_pass "Model resolution: gemini-3.5-flash-high → gemini-3-flash"
    else
        log_fail "Model resolution not found in proxy logs"
    fi

    [ "$VERBOSE" = "1" ] && cat /tmp/antigravity-test-proxy.log
fi

# ── Test 4: Real Codex CLI Task ────────────────────────────────────
if [ "$RUN_TASK" = "1" ]; then
    echo ""; echo "─── Test 4: Real Codex CLI Task ───"

    if ! command -v codex &>/dev/null; then
        log_skip "Codex CLI not found"
    else
        CLI_VERSION=$(codex --version 2>/dev/null || echo "unknown")
        log_info "Codex CLI: $CLI_VERSION"

        TASK_PROMPT='Create a file /tmp/e2e-test-output.txt with the text "Hello from Codex CLI E2E test" followed by the current date. Then read it back and confirm the content is correct. This is a simple smoke test.'

        TASK_WORKSPACE="/tmp/e2e-test-workspace"
        mkdir -p "$TASK_WORKSPACE"

        mkdir -p /tmp/antigravity-task-logs
        TASK_PROXY_LOG="/tmp/antigravity-task-logs/proxy-$(date +%s).log"
        TASK_CLI_LOG="/tmp/antigravity-task-logs/cli-$(date +%s).log"
        TASK_MONITOR_LOG="/tmp/antigravity-task-logs/monitor-$(date +%s).log"

        # Set up proxy for CLI task (use the one already running on TEST_PORT)
        # Write codex profile + config pointing to our test proxy
        CONFIG_DIR="$HOME/.codex"
        CONFIG_FILE="$CONFIG_DIR/config.toml"
        CONFIG_BACKUP="$CONFIG_DIR/config.toml.task-backup"

        [ -f "$CONFIG_FILE" ] && cp "$CONFIG_FILE" "$CONFIG_BACKUP"

        # Generate model catalog
        CATALOG_PATH="$HOME/.cache/codex-proxy/models-Antigravity-Test.json"
        python3 -c "
import json, os
models = ['gemini-3.5-flash-high', 'gemini-3.5-flash-medium', 'gemini-3.5-flash-low',
          'gemini-3.1-pro-high', 'gemini-3.1-pro-low',
          'claude-sonnet-4-6', 'claude-opus-4-6-thinking', 'gpt-oss-120b-medium']
catalog = []
for m in models:
    catalog.append({'slug':m,'model':m,'display_name':m,'description':'Antigravity '+m,'hidden':False,'isDefault':m=='gemini-3.5-flash-high','shell_type':'shell_command','visibility':'list','default_reasoning_level':'medium','supported_reasoning_levels':[{'effort':'low','description':'Fast'},{'effort':'medium','description':'Balanced'},{'effort':'high','description':'Deep'}]})
os.makedirs(os.path.dirname('$CATALOG_PATH'), exist_ok=True)
json.dump(catalog, open('$CATALOG_PATH','w'), indent=2)
" || log_fail "Failed to create model catalog"

        # Write main config
        cat > "$CONFIG_FILE" <<CONFEOF
model = "gemini-3.5-flash-high"
model_provider = "Antigravity Test"
model_catalog_json = "$CATALOG_PATH"

[model_providers."Antigravity Test"]
name = "Antigravity Test"
base_url = "http://127.0.0.1:$TEST_PORT"
experimental_bearer_token = "$PROXY_API_KEY"
wire_api = "responses"
request_max_retries = 1
stream_max_retries = 0
stream_idle_timeout_ms = 600000

[projects."/home/roman/Codex-Launcher-Any-AI-Provider"]
trust_level = "trusted"
CONFEOF

        # Write profile file for Codex CLI 0.134.0+
        PROFILE_FILE="$CONFIG_DIR/Antigravity-Test.config.toml"
        cat > "$PROFILE_FILE" <<PROFEOF
model = "gemini-3.5-flash-high"
model_provider = "Antigravity Test"
model_catalog_json = "$CATALOG_PATH"
service_tier = "fast"
approvals_reviewer = "user"
PROFEOF

        log_info "Config written: profile=Antigravity-Test, port=$TEST_PORT"

        # ── Anomaly monitor (background) ──
        ANOMALY_FOUND=0
        (
            PROXY_LOG="/tmp/antigravity-test-proxy.log"
            START_TIME=$(date +%s)
            TIMEOUT_SEC=600
            PREV_LINE_COUNT=0
            STALL_COUNT=0
            LOOP_DETECTOR=""
            LOOP_COUNT=0

            while true; do
                sleep 10
                [ ! -f "$PROXY_LOG" ] && continue

                NOW=$(date +%s)
                ELAPSED=$(( NOW - START_TIME ))
                [ "$ELAPSED" -gt "$TIMEOUT_SEC" ] && {
                    echo "[MONITOR] TIMEOUT: Task exceeded ${TIMEOUT_SEC}s" >> "$TASK_MONITOR_LOG"
                    break
                }

                # Check proxy is alive
                if ! kill -0 $PROXY_PID 2>/dev/null; then
                    echo "[MONITOR] FATAL: Proxy process died" >> "$TASK_MONITOR_LOG"
                    break
                fi

                # Count lines in proxy log
                LINE_COUNT=$(wc -l < "$PROXY_LOG" 2>/dev/null || echo 0)
                NEW_LINES=$(( LINE_COUNT - PREV_LINE_COUNT ))
                PREV_LINE_COUNT=$LINE_COUNT

                # Stall detection: no new log lines for 3 consecutive checks = stalled
                if [ "$NEW_LINES" -eq 0 ]; then
                    STALL_COUNT=$(( STALL_COUNT + 1 ))
                    if [ "$STALL_COUNT" -ge 18 ]; then
                        echo "[MONITOR] STALL: No proxy activity for 180s" >> "$TASK_MONITOR_LOG"
                    fi
                else
                    STALL_COUNT=0
                fi

                # Loop detection: check if same tool call repeats
                RECENT=$(tail -50 "$PROXY_LOG" 2>/dev/null | grep "exec_command" | tail -5 | md5sum | cut -c1-8)
                if [ -n "$RECENT" ] && [ "$RECENT" = "$LOOP_DETECTOR" ]; then
                    LOOP_COUNT=$(( LOOP_COUNT + 1 ))
                    if [ "$LOOP_COUNT" -ge 6 ]; then
                        echo "[MONITOR] LOOP: Same tool calls repeating ($LOOP_COUNT times)" >> "$TASK_MONITOR_LOG"
                    fi
                else
                    LOOP_DETECTOR="$RECENT"
                    LOOP_COUNT=0
                fi

                # Check for error patterns
                ERRORS=$(tail -100 "$PROXY_LOG" 2>/dev/null | grep -ciE "error|failed|timeout|500|502|503|429" || echo 0)
                if [ "$ERRORS" -gt 10 ]; then
                    echo "[MONITOR] ERRORS: $ERRORS error lines in last 100 log lines" >> "$TASK_MONITOR_LOG"
                fi

                # Check for compaction issues
                COMPACT_LINES=$(tail -200 "$PROXY_LOG" 2>/dev/null | grep -c "compacted\|compaction\|trimming" || echo 0)
                if [ "$COMPACT_LINES" -gt 20 ]; then
                    echo "[MONITOR] COMPACTION: Excessive compaction ($COMPACT_LINES events)" >> "$TASK_MONITOR_LOG"
                fi

                # Check context item count
                HIGH_ITEM=$(tail -200 "$PROXY_LOG" 2>/dev/null | grep -oP '\[\d+\]' | grep -oP '\d+' | sort -rn | head -1 || echo 0)
                if [ -n "$HIGH_ITEM" ] && [ "$HIGH_ITEM" -gt 100 ]; then
                    echo "[MONITOR] CONTEXT: High item count detected: [$HIGH_ITEM]" >> "$TASK_MONITOR_LOG"
                fi

                # Log heartbeat
                echo "[MONITOR] ${ELAPSED}s elapsed, ${LINE_COUNT} log lines, ${NEW_LINES} new, ${ERRORS} errors" >> "$TASK_MONITOR_LOG"
            done
        ) &
        MONITOR_PID=$!

        # ── Launch Codex CLI with the task ──
        log_info "Launching Codex CLI with real task..."
        log_info "Task: Create and verify a simple test file"
        log_info "Monitor log: $TASK_MONITOR_LOG"

        cd "$TASK_WORKSPACE"

        set +e
        codex exec --profile Antigravity-Test -c "model=gemini-3.5-flash-high" \
            -c 'sandbox_permissions=["disk-full-read-access","disk-full-write-access"]' \
            "$TASK_PROMPT" \
            > "$TASK_CLI_LOG" 2>&1
        CLI_EXIT=$?
        set -e

        # Stop monitor
        kill $MONITOR_PID 2>/dev/null || true
        wait $MONITOR_PID 2>/dev/null || true

        CLI_DURATION=$(wc -l < "$TASK_CLI_LOG" 2>/dev/null || echo 0)
        log_info "CLI exited (code $CLI_EXIT, $CLI_DURATION output lines)"

        # ── Analyze results ──
        echo ""; echo "─── Test 4a: CLI Task Results ───"

        if [ "$CLI_EXIT" -eq 0 ]; then
            log_pass "CLI task completed successfully"
        else
            log_fail "CLI task failed (exit code $CLI_EXIT)"
            echo "    Last 10 lines of CLI output:"
            tail -10 "$TASK_CLI_LOG" 2>/dev/null | sed 's/^/    /'
        fi

        # Check monitor log for anomalies
        echo ""; echo "─── Test 4b: Anomaly Analysis ───"
        if [ -f "$TASK_MONITOR_LOG" ]; then
            ANOMALIES=$(grep -c "\[MONITOR\]" "$TASK_MONITOR_LOG" 2>/dev/null || echo 0)
            CRITICAL=$(grep -cE "FATAL|LOOP|TIMEOUT|STALL|ERRORS|COMPACTION|CONTEXT" "$TASK_MONITOR_LOG" 2>/dev/null || echo 0)
            log_info "Monitor: $ANOMALIES checks, $CRITICAL anomalies detected"

            if [ "$CRITICAL" -gt 0 ]; then
                echo -e "  ${RED}ANOMALIES FOUND:${NC}"
                grep -E "FATAL|LOOP|TIMEOUT|STALL|ERRORS|COMPACTION|CONTEXT" "$TASK_MONITOR_LOG" | while read line; do
                    echo -e "    ${RED}$line${NC}"
                done
                log_fail "$CRITICAL anomalies detected during task"
            else
                log_pass "No anomalies detected during task"
            fi

            [ "$VERBOSE" = "1" ] && cat "$TASK_MONITOR_LOG"
        else
            log_skip "No monitor log produced"
        fi

        # Check proxy log for issues
        echo ""; echo "─── Test 4c: Proxy Health ───"
        if [ -f "/tmp/antigravity-test-proxy.log" ]; then
            ERROR_COUNT=$(grep -ciE "error|failed|exception|traceback" /tmp/antigravity-test-proxy.log || echo 0)
            TIMEOUT_COUNT=$(grep -ci "timeout\|timed.out" /tmp/antigravity-test-proxy.log || echo 0)
            COMPACT_COUNT=$(grep -c "compacted\|compaction" /tmp/antigravity-test-proxy.log || echo 0)
            ITEM_COUNT=$(grep -oP '\[\d+\]' /tmp/antigravity-test-proxy.log | grep -oP '\d+' | sort -rn | head -1 || echo 0)

            log_info "Proxy errors: $ERROR_COUNT, timeouts: $TIMEOUT_COUNT, compactions: $COMPACT_COUNT, max context items: $ITEM_COUNT"

            [ "$ERROR_COUNT" -gt 20 ] && log_fail "High error count: $ERROR_COUNT"
            [ "$TIMEOUT_COUNT" -gt 5 ] && log_fail "Timeout count: $TIMEOUT_COUNT"
            [ "$ITEM_COUNT" -gt 100 ] && log_fail "Context items grew to: $ITEM_COUNT (compaction may be failing)"
            [ "$ITEM_COUNT" -le 100 ] && [ "$ITEM_COUNT" -gt 0 ] && log_pass "Context items stayed under 100 (max: $ITEM_COUNT)"

            # Check for repeated identical tool calls (loop detection)
            DUPE_CALLS=$(grep "exec_command" /tmp/antigravity-test-proxy.log | sed 's/.*args=//' | sort | uniq -c | sort -rn | head -1 | awk '{print $1}' || echo 0)
            if [ "$DUPE_CALLS" -gt 10 ]; then
                log_fail "Loop detected: same tool call repeated $DUPE_CALLS times"
            else
                log_pass "No tool call loops (max repeat: $DUPE_CALLS)"
            fi
        fi

        # Check if the file was actually created
        echo ""; echo "─── Test 4d: Task Output Quality ───"
        if [ -f "/tmp/e2e-test-output.txt" ]; then
            CONTENT=$(cat /tmp/e2e-test-output.txt 2>/dev/null)
            if echo "$CONTENT" | grep -q "Hello from Codex CLI E2E test"; then
                log_pass "Task output file created with correct content"
            else
                log_fail "Task output file exists but content is wrong: $CONTENT"
            fi
        else
            log_fail "Task output file /tmp/e2e-test-output.txt was NOT created"
        fi

        # Check proxy log for tool-strip events (budget cap defense)
        echo ""; echo "─── Test 4e: Anti-Loop Defense Verification ───"
        if [ -f "/tmp/antigravity-test-proxy.log" ]; then
            NULL_TOOL_LOOPS=$(grep -c "NULL-TOOL LOOP" /tmp/antigravity-test-proxy.log || echo 0)
            TOOL_STRIPPED=$(grep -c "TOOLS STRIPPED" /tmp/antigravity-test-proxy.log || echo 0)
            BUDGET_HIT=$(grep -c "HARD CAP" /tmp/antigravity-test-proxy.log || echo 0)
            READ_LOOP=$(grep -c "FILE READ LOOP" /tmp/antigravity-test-proxy.log || echo 0)
            FORCE_FINALIZE=$(grep -c "force_finalize" /tmp/antigravity-test-proxy.log || echo 0)

            log_info "Anti-loop events: null-tool=$NULL_TOOL_LOOPS stripped=$TOOL_STRIPPED budget=$BUDGET_HIT read-loop=$READ_LOOP finalize=$FORCE_FINALIZE"

            # For a simple task, none of these should fire
            if [ "$BUDGET_HIT" -gt 0 ]; then
                log_fail "Budget cap hit on simple task — model looping"
            else
                log_pass "No budget cap triggered (task completed cleanly)"
            fi

            if [ "$TOOL_STRIPPED" -gt 0 ]; then
                log_fail "Tools were stripped — model hit hard limit"
            else
                log_pass "No tool stripping needed (model behaved)"
            fi
        fi

        # Restore original config
        [ -f "$CONFIG_BACKUP" ] && mv "$CONFIG_BACKUP" "$CONFIG_FILE"
        rm -f "$PROFILE_FILE"

        log_info "Config restored"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo " Results: $PASS passed, $FAIL failed, $SKIP skipped"
echo "═══════════════════════════════════════════════════════════════"
[ -n "$BEST_EP" ] && echo -e " ${GREEN}Best direct:${NC} $BEST_MODEL @ $BEST_EP"

if [ "$FAIL" -gt 0 ]; then
    echo -e "\n${RED}FAILED — Do NOT push until all tests pass${NC}"
    for r in "${RESULTS[@]}"; do echo "$r" | grep -q "^FAIL" && echo "  $r"; done
    exit 1
else
    echo -e "\n${GREEN}ALL TESTS PASSED — Safe to push${NC}"
    exit 0
fi
