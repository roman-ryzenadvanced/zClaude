"""AI-powered monitoring — self-healing watchdog with 3-tier response."""
import collections
import copy
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from lib.constants import (
    IS_WINDOWS, PROXY_CONFIG_DIR,
    MONITORING_FILE, INCIDENT_STORE_FILE, MONITORING_LOG,
)

# ═══════════════════════════════════════════════════════════════════════
# AI Monitoring — Self-Healing Watchdog
# ═══════════════════════════════════════════════════════════════════════

_TIER1_RULES = [
    ("proxy_health_fail",      "restart_proxy",         30),
    ("proxy_port_conflict",    "kill_stale_restart",    60),
    ("upstream_429",           "wait_retry",             0),
    ("upstream_502_503",       "retry_backoff",         30),
    ("upstream_500_repeat",    "switch_provider",       60),
    ("upstream_timeout",       "retry_increase_timeout",30),
    ("upstream_401_403",       "alert_bad_key",          0),
    ("stream_broken_pipe",     "restart_proxy",         30),
    ("stream_reset",           "restart_proxy",         30),
    ("parsed_tool_calls_0_x3", "clear_schema_cache",   300),
    ("sanitizer_suspicious_5x","alert_model_issue",      0),
    ("stuck_recovery_x5",      "suggest_switch_model",   0),
    ("codex_process_dead",     "alert_restart",           0),
    ("schema_corrupt",         "delete_provider_caps",    0),
]

_FAILURE_SIGNALS = {
    "parsed_tool_calls=0":      ("C1", "parser_empty"),
    "[STUCK-RECOVERY]":         ("C3", "stuck_recovery"),
    "suspicious cmd":           ("C4", "sanitizer_flag"),
    "empty cmd recovered":      ("C6", "empty_cmd"),
    "HTTP 429":                 ("B1", "rate_limited"),
    "HTTP 500":                 ("B2", "server_error"),
    "HTTP 502":                 ("B2", "server_error"),
    "HTTP 503":                 ("B2", "server_error"),
    "HTTP 401":                 ("B3", "auth_failure"),
    "HTTP 403":                 ("B4", "forbidden"),
    "Connection refused":       ("A1", "proxy_dead"),
    "Address already in use":   ("A2", "port_conflict"),
    "Broken pipe":              ("B7", "broken_pipe"),
    "Connection reset":         ("B6", "connection_reset"),
    "timed out":                ("B5", "timeout"),
    "SELF-REVIVE CRASH":        ("A5", "proxy_crash"),
    "stream error":             ("B6", "stream_error"),
    "content_type.*array":      ("E1", "schema_corrupt"),
}

_DIAGNOSTIC_SYSTEM_PROMPT = (
    'You are a diagnostic agent for "Codex Launcher" — a desktop app that runs a local '
    'translation proxy between OpenAI Codex CLI/Desktop and AI providers.\n\n'
    'Analyze the incident and respond with ONLY a JSON object:\n'
    '{"action": "...", "reason": "...", "confidence": 0.0-1.0}\n\n'
    'Available actions: restart_proxy, kill_stale_processes, clear_schema_cache, '
    'switch_provider, increase_timeout, regenerate_config, cleanup_stale, '
    'alert_user, ignore, retry_now\n\n'
    'Rules:\n'
    '- upstream 401/403 with auth error -> alert_user\n'
    '- proxy dead -> restart_proxy\n'
    '- same error 5+ times -> switch_provider or alert_user\n'
    '- schema/content_type error -> clear_schema_cache\n'
    '- "Address already in use" -> kill_stale_processes then restart_proxy\n'
    '- timeout on slow upstream -> increase_timeout\n'
    '- single transient 429/502/503 -> ignore\n'
    '- "stream disconnected" + proxy healthy -> ignore\n'
    '- no extra text, no markdown, just the JSON object'
)


def load_monitoring_config():
    if MONITORING_FILE.exists():
        try:
            return json.loads(MONITORING_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[lib] failed to load monitoring config: {exc}", file=sys.stderr)
    return {
        "enabled": False,
        "provider_url": "",
        "model": "",
        "api_key": "",
        "health_check_interval_s": 5,
        "auto_restart_proxy": True,
        "auto_switch_provider": False,
    }


def save_monitoring_config(cfg):
    MONITORING_FILE.parent.mkdir(parents=True, exist_ok=True)
    MONITORING_FILE.write_text(json.dumps(cfg, indent=2))


def load_incident_store():
    if INCIDENT_STORE_FILE.exists():
        try:
            return json.loads(INCIDENT_STORE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[lib] failed to load incident store: {exc}", file=sys.stderr)
    return {"version": 1, "incidents": {}, "stats": {"ai_calls": 0, "tokens_used": 0}}


def save_incident_store(store):
    INCIDENT_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    INCIDENT_STORE_FILE.write_text(json.dumps(store, indent=2))


def monitoring_log(msg):
    try:
        with open(str(MONITORING_LOG), "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception as exc:
        print(f"[lib] monitoring_log write failed: {exc}", file=sys.stderr)


class IncidentStore:
    def __init__(self):
        self._store = load_incident_store()
        self._dirty = False
        self._lock = threading.Lock()

    def lookup(self, pattern):
        with self._lock:
            inc = self._store.get("incidents", {}).get(pattern)
        if inc and inc.get("success_count", 0) > 0:
            rate = inc["success_count"] / max(inc["success_count"] + inc.get("fail_count", 0), 1)
            if rate > 0.5:
                return inc
        return None

    def record(self, pattern, fix, success=True):
        with self._lock:
            new_store = copy.deepcopy(self._store)
            incs = new_store.setdefault("incidents", {})
            inc = incs.setdefault(pattern, {
                "fix": fix, "success_count": 0, "fail_count": 0,
                "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "occurrences": 0,
            })
            inc = dict(inc)
            inc["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            inc["occurrences"] = inc.get("occurrences", 0) + 1
            if success:
                inc["success_count"] = inc.get("success_count", 0) + 1
            else:
                inc["fail_count"] = inc.get("fail_count", 0) + 1
            incs[pattern] = inc
            self._store = new_store
            self._dirty = True

    def record_ai_call(self, tokens=0):
        with self._lock:
            new_store = copy.deepcopy(self._store)
            stats = dict(new_store.get("stats", {"ai_calls": 0, "tokens_used": 0}))
            stats["ai_calls"] = stats.get("ai_calls", 0) + 1
            stats["tokens_used"] = stats.get("tokens_used", 0) + tokens
            new_store["stats"] = stats
            self._store = new_store
            self._dirty = True

    def flush(self):
        with self._lock:
            if self._dirty:
                save_incident_store(self._store)
                self._dirty = False

    @property
    def stats(self):
        with self._lock:
            return dict(self._store.get("stats", {"ai_calls": 0, "tokens_used": 0}))


class AIDiagnosticAgent:
    def __init__(self, provider_url, model, api_key):
        self.provider_url = provider_url
        self.model = model
        self.api_key = api_key
        self.incident_store = IncidentStore()

    def diagnose(self, context):
        pattern = self._extract_pattern(context)
        known = self.incident_store.lookup(pattern)
        if known:
            monitoring_log(f"Tier 2 HIT: pattern={pattern} fix={known['fix']}")
            return {"action": known["fix"], "reason": "known_pattern", "confidence": 0.9, "tier": 2}
        action = self._call_model(context)
        if action:
            self.incident_store.record(pattern, action.get("action", "unknown"))
            self.incident_store.flush()
        return action

    def _extract_pattern(self, context):
        parts = []
        for k in sorted(context.get("signals", [])):
            parts.append(k)
        if context.get("http_code"):
            parts.append(f"http_{context['http_code']}")
        return "+".join(parts[:3]) or "unknown"

    def _call_model(self, context):
        prompt = (
            f"INCIDENT REPORT:\n"
            f"Time: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            f"Proxy health: {context.get('proxy_alive', 'unknown')}\n"
            f"Upstream: {context.get('upstream_url', 'unknown')}\n"
            f"Model: {context.get('model', 'unknown')}\n"
            f"Last HTTP code: {context.get('http_code', 'n/a')}\n"
            f"Recent signals: {context.get('signals', [])}\n"
            f"Recent log tail:\n{context.get('log_tail', '')[:1500]}\n"
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _DIAGNOSTIC_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 200,
            "temperature": 0.1,
        }
        try:
            req = urllib.request.Request(
                self.provider_url,
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read())
            text = result["choices"][0]["message"]["content"].strip()
            self.incident_store.record_ai_call(tokens=800)
            # Robust schema validation and parsing
            action = {}
            try:
                action = json.loads(text)
                if not isinstance(action, dict):
                    action = {}
            except Exception:
                # If JSON parsing failed, try using regex to extract JSON block
                m = re.search(r'\{.*\}', text, re.DOTALL)
                if m:
                    try:
                        action = json.loads(m.group(0))
                    except Exception:
                        pass

            valid_actions = {
                "restart_proxy", "kill_stale_processes", "clear_schema_cache",
                "switch_provider", "increase_timeout", "regenerate_config",
                "cleanup_stale", "alert_user", "ignore", "retry_now"
            }
            validated_action = action.get("action")
            if validated_action not in valid_actions:
                validated_action = "alert_user"
            
            reason = action.get("reason", "unknown")
            if not isinstance(reason, str):
                reason = str(reason)
                
            try:
                confidence = float(action.get("confidence", 0.5))
            except Exception:
                confidence = 0.5

            action = {
                "action": validated_action,
                "reason": reason,
                "confidence": confidence,
                "tier": 3
            }
            monitoring_log(f"Tier 3 AI: action={action.get('action')} reason={action.get('reason')}")
            return action
        except Exception as e:
            monitoring_log(f"Tier 3 AI FAILED: {e}")
            return {"action": "alert_user", "reason": f"ai_diag_failed: {e}", "confidence": 0.0, "tier": 3}


class HealthWatcher(threading.Thread):
    def __init__(self, on_failure, on_recovery, on_signal, on_action):
        super().__init__(daemon=True)
        self.cfg = load_monitoring_config()
        self.on_failure = on_failure
        self.on_recovery = on_recovery
        self.on_signal = on_signal
        self.on_action = on_action
        self.failures = 0
        self.running = False
        self._signal_counts = collections.defaultdict(int)
        self._last_actions = {}
        self._restart_count = 0
        self._last_restart_time = 0

    def run(self):
        self.running = True
        self.incident_store = IncidentStore()
        self._log_analyzer = _LogAnalyzerThread(self._on_log_signal)
        self._log_analyzer.start()
        while self.running:
            self.cfg = load_monitoring_config()
            if not self.cfg.get("enabled"):
                time.sleep(5)
                continue
            port = self._get_proxy_port()
            if port:
                healthy = self._check_health(port)
                if healthy:
                    if self.failures > 0:
                        self.failures = 0
                        self.on_recovery()
                else:
                    self.failures += 1
                    if self.failures >= 3:
                        self._handle_failure("proxy_health_fail")
            self.incident_store.flush()
            interval = self.cfg.get("health_check_interval_s", 5)
            time.sleep(interval)

    def stop(self):
        self.running = False
        if hasattr(self, '_log_analyzer'):
            self._log_analyzer.running = False

    def _get_proxy_port(self):
        try:
            cfg_path = PROXY_CONFIG_DIR / "proxy-config.json"
            if cfg_path.exists():
                d = json.loads(cfg_path.read_text(encoding="utf-8"))
                return d.get("port")
        except Exception as exc:
            print(f"[lib] _get_proxy_port: {exc}", file=sys.stderr)
        return None

    def _check_health(self, port):
        try:
            req = urllib.request.Request(f"http://localhost:{port}/health")
            resp = urllib.request.urlopen(req, timeout=5)
            return resp.status == 200
        except Exception:
            return False

    def _on_log_signal(self, fault_id, category, line):
        self._signal_counts[category] += 1
        self.on_signal(fault_id, category, line[:200])
        count = self._signal_counts[category]
        if category in ("proxy_dead", "port_conflict") and count >= 2:
            self._handle_failure(category)
        elif category in ("server_error", "timeout") and count >= 3:
            self._handle_failure(category + "_repeat")
        elif category in ("sanitizer_flag",) and count >= 5:
            self._handle_failure("sanitizer_suspicious_5x")
        elif category in ("stuck_recovery",) and count >= 5:
            self._handle_failure("stuck_recovery_x5")
        elif category in ("parser_empty",) and count >= 3:
            self._handle_failure("parsed_tool_calls_0_x3")
        elif category in ("schema_corrupt",):
            self._handle_failure("schema_corrupt")

    def _handle_failure(self, trigger):
        now = time.time()
        for rule_trigger, action, cooldown in _TIER1_RULES:
            if rule_trigger == trigger:
                last_t = self._last_actions.get(action, 0)
                if now - last_t < cooldown:
                    return
                self._last_actions[action] = now
                monitoring_log(f"Tier 1: trigger={trigger} action={action}")
                self.on_action(action, trigger)
                self.incident_store.record(trigger, action, success=True)
                return
        self._try_tier2_3(trigger)

    def _try_tier2_3(self, trigger):
        cfg = self.cfg
        if not cfg.get("provider_url") or not cfg.get("model") or not cfg.get("api_key"):
            monitoring_log(f"No AI configured for Tier 2/3 — alerting user for trigger={trigger}")
            self.on_action("alert_user", trigger)
            return
        agent = AIDiagnosticAgent(cfg["provider_url"], cfg["model"], cfg["api_key"])
        context = {
            "signals": [trigger],
            "proxy_alive": self.failures == 0,
            "log_tail": self._get_recent_log(),
        }
        result = agent.diagnose(context)
        if result:
            action = result.get("action", "alert_user")
            monitoring_log(f"Tier {result.get('tier', '?')}: action={action}")
            self.on_action(action, trigger)

    def _get_recent_log(self):
        lines = []
        for log_name in ["cc-debug.log", "proxy.log"]:
            log_path = PROXY_CONFIG_DIR / log_name
            try:
                text = log_path.read_text(encoding="utf-8")
                lines.extend(text.splitlines()[-20:])
            except Exception:
                pass
        return "\n".join(lines[-30:])


class _LogAnalyzerThread(threading.Thread):
    def __init__(self, on_signal):
        super().__init__(daemon=True)
        self.on_signal = on_signal
        self.running = False

    def run(self):
        self.running = True
        log_paths = [
            str(PROXY_CONFIG_DIR / "cc-debug.log"),
            str(PROXY_CONFIG_DIR / "proxy.log"),
        ]
        fhs = {}
        for p in log_paths:
            try:
                f = open(p, "r")
                f.seek(0, 2)
                fhs[p] = f
            except Exception:
                pass
        while self.running:
            activity = False
            for p, fh in list(fhs.items()):
                try:
                    line = fh.readline()
                    if line:
                        activity = True
                        for pattern, (fault_id, category) in _FAILURE_SIGNALS.items():
                            if re.search(pattern, line):
                                self.on_signal(fault_id, category, line.strip())
                                break
                except Exception:
                    pass
            if not activity:
                time.sleep(0.5)

