#!/usr/bin/env python3
"""Codex Launcher GUI (tkinter) — manage endpoints, launch Desktop or CLI with any provider.

Windows-native tkinter GUI mirroring all features of the GTK version.
Imports process management, config engine, proxy lifecycle from codex_launcher_lib.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import base64
import hashlib
import secrets
import http.server
import collections
from pathlib import Path

from codex_launcher_lib import (
    IS_WINDOWS, HOME, CONFIG, CONFIG_BAK, CONFIG_TXN,
    ENDPOINTS_FILE, BGP_POOLS_FILE, LAUNCH_LOG, LOG_DIR,
    PROXY_CONFIG_DIR, BIN_DIR, PROXY, CLEANUP, PID_REGISTRY,
    PROVIDER_PRESETS, CHANGELOG, DEFAULT_CONFIG, OAUTH_SECRETS_PATH,
    ANTIGRAVITY_MODELS,
    safe_name, label_for_backend, normalize_model_id, normalize_base_url,
    _profile_slug,
    parse_model_list, now_utc_iso, apply_provider_preset,
    load_endpoints, save_endpoints, load_bgp_pools, save_bgp_pools,
    get_endpoint, build_profile_bundle, save_profile_bundle, import_profile_bundle,
    backup_config, restore_config, begin_config_transaction, end_config_transaction,
    recover_config_if_needed, write_config_for_native, write_config_for_translated,
    endpoint_models_url, endpoint_model_headers, fetch_models_for_endpoint,
    refresh_endpoint_models, run_endpoint_doctor,
    detect_codex_cli, detect_codex_desktop, launch_codex_desktop, is_codex_desktop_running, check_codex_auth,
    last_log_lines, kill_existing_desktop, safe_cleanup_owned,
    start_proxy_for, stop_proxy, start_bgp_proxy, get_proxy_state, set_proxy_state,
    detect_terminal, open_url, open_file, write_secure_text,
    ensure_dirs, create_default_endpoints,
    load_monitoring_config, save_monitoring_config,
    load_incident_store, save_incident_store, load_usage_stats,
    monitoring_log,
    IncidentStore, AIDiagnosticAgent, HealthWatcher,
    load_oauth_secrets, save_oauth_secrets,
    _usage_theme, UA,
    check_provider_latency,
)


# ═══════════════════════════════════════════════════════════════════════
# Extracted modules
# ═══════════════════════════════════════════════════════════════════════

from gui.helpers import _fmt_tok, _fmt_dur, _status_pill, _show_doctor_results_tk
from gui.endpoint_dialogs import EditEndpointDialog, EndpointMgr
from gui.bgp_dialogs import BGPRouteDialog, BGPPoolEditDialog, BGPPoolMgr
from gui.windows.monitoring import AIMonitoringWindow
from gui.windows.usage import UsageWindow
from gui.windows.sessions import SessionManagerWindow
from gui.windows.history import RequestHistoryWindow
from gui.windows.benchmark import BenchmarkWindow
from gui.windows.updater import CodexUpdaterWindow


# ═══════════════════════════════════════════════════════════════════════
# Main Launcher Window
# ═══════════════════════════════════════════════════════════════════════

from gui.oauth_flows import (
    google_oauth_flow, codebuff_oauth_flow, kiro_oauth_flow,
)

class LauncherWin:
    def __init__(self, root):
        self._root = root
        self._proc = None
        self._endpoints_data = load_endpoints()
        self._refresh_running = False
        recover_config_if_needed()

        main = ttk.Frame(root, padding=16)
        main.pack(fill="both", expand=True)
        main.pack_propagate(False)


        # Title
        hdr = ttk.Frame(main)
        hdr.pack(fill="x")
        ttk.Label(hdr, text=f"Codex Launcher v{CHANGELOG[0][0]}", font=("Segoe UI", 13, "bold")).pack(side="left")

        # Toolbar — two rows to fit all buttons
        tb1 = ttk.Frame(main)
        tb1.pack(fill="x", pady=(6, 0))
        ttk.Button(tb1, text="Endpoints...", command=self._open_mgr).pack(side="left")
        ttk.Button(tb1, text="AI Monitor", command=self._open_monitoring).pack(side="left", padx=(6, 0))
        ttk.Button(tb1, text="AI BGP", command=self._open_bgp).pack(side="left", padx=(6, 0))
        ttk.Button(tb1, text="Usage", command=self._open_usage).pack(side="left", padx=(6, 0))
        ttk.Button(tb1, text="Benchmark", command=self._open_benchmark).pack(side="left", padx=(6, 0))
        hist_btn = ttk.Menubutton(tb1, text="History ▾")
        hist_menu = tk.Menu(hist_btn, tearoff=0)
        hist_menu.add_command(label="Request History", command=self._open_history)
        hist_menu.add_command(label="Session Manager", command=self._open_session_manager)
        hist_btn.configure(menu=hist_menu)
        hist_btn.pack(side="left", padx=(6, 0))
        ttk.Button(tb1, text="OAuth Secrets", command=self._edit_oauth_secrets).pack(side="left", padx=(6, 0))
        ttk.Button(tb1, text="Changelog", command=self._show_changelog).pack(side="right")
        if not IS_WINDOWS:
            ttk.Button(tb1, text="Update Desktop", command=self._open_updater).pack(side="right", padx=(0, 6))

        # Detection status — one row per item so long paths don't truncate
        self._cli_info = detect_codex_cli()
        self._desktop_info = detect_codex_desktop()

        cli_row = ttk.Frame(main)
        cli_row.pack(fill="x", pady=(4, 0))
        if self._cli_info:
            cli_path, cli_ver = self._cli_info
            ttk.Label(cli_row, text=f"✓ Codex CLI  {cli_ver}", foreground="#2ea043").pack(side="left")
            ttk.Label(cli_row, text=f"  ({cli_path})", foreground="gray").pack(side="left")
        else:
            ttk.Label(cli_row, text="✗ Codex CLI -- not found", foreground="#d29922").pack(side="left")
            ttk.Button(cli_row, text="Install", command=lambda: self._show_install_guide("cli")).pack(side="left", padx=(6, 0))

        desk_row = ttk.Frame(main)
        desk_row.pack(fill="x", pady=(2, 0))
        if self._desktop_info[0]:
            label = "MSIX (Store)" if self._desktop_info[1] else self._desktop_info[0]
            ttk.Label(desk_row, text="✓ Codex Desktop", foreground="#2ea043").pack(side="left")
            ttk.Label(desk_row, text=f"  ({label})", foreground="gray").pack(side="left")
        else:
            ttk.Label(desk_row, text="✗ Codex Desktop -- not found", foreground="#d29922").pack(side="left")
            ttk.Button(desk_row, text="Install", command=lambda: self._show_install_guide("desktop")).pack(side="left", padx=(6, 0))

        self._missing = []
        if not self._cli_info:
            self._missing.append("cli")
        if not self._desktop_info[0]:
            self._missing.append("desktop")

        # Auth status
        auth_frame = ttk.Frame(main)
        auth_frame.pack(fill="x", pady=(6, 0))
        self._auth_label = ttk.Label(auth_frame, text="Checking auth...")
        self._auth_label.pack(side="left")
        self._relogin_btn = ttk.Button(auth_frame, text="Re-login", command=self._codex_relogin, state="disabled")
        self._relogin_btn.pack(side="right")
        threading.Thread(target=self._check_auth_async, daemon=True).start()

        # Ops bar
        ops_frame = ttk.Frame(main)
        ops_frame.pack(fill="x", pady=(6, 0))
        self._refresh_all_btn = ttk.Button(ops_frame, text="Refresh Models", command=self._refresh_all_models)
        self._refresh_all_btn.pack(side="left")
        ttk.Button(ops_frame, text="Backup Profile", command=self._backup_profile).pack(side="left", padx=(8, 0))
        ttk.Button(ops_frame, text="Import Profile", command=self._import_profile).pack(side="left", padx=(8, 0))

        # Endpoint + Model selectors
        sel_frame = ttk.Frame(main)
        sel_frame.pack(fill="x", pady=(6, 0))
        ttk.Label(sel_frame, text="Endpoint:").pack(side="left")
        self._combo_ep = ttk.Combobox(sel_frame, state="readonly", width=24)
        self._combo_ep.pack(side="left", padx=(4, 0))
        self._combo_ep.bind("<<ComboboxSelected>>", lambda e: self._on_endpoint_changed())
        self._latency_label = ttk.Label(sel_frame, text=" -- ", font=("Segoe UI", 9, "bold"), foreground="gray")
        self._latency_label.pack(side="left", padx=(6, 0))
        ttk.Label(sel_frame, text="Model:").pack(side="left", padx=(12, 0))
        self._combo_model = ttk.Combobox(sel_frame, state="readonly", width=24)
        self._combo_model.pack(side="left", padx=(4, 0))

        # Sandbox / Approval mode selectors
        mode_frame = ttk.Frame(main)
        mode_frame.pack(fill="x", pady=(6, 0))
        ttk.Label(mode_frame, text="Sandbox:").pack(side="left")
        self._combo_sandbox = ttk.Combobox(mode_frame, values=["Read-only", "Workspace", "Full Access"],
                                           state="readonly", width=12)
        self._combo_sandbox.set("Workspace")
        self._combo_sandbox.pack(side="left", padx=(4, 0))
        ttk.Label(mode_frame, text="Approval:").pack(side="left", padx=(12, 0))
        self._combo_approval = ttk.Combobox(mode_frame, values=["Untrusted", "On Request", "Never (Full Auto)"],
                                            state="readonly", width=16)
        self._combo_approval.set("On Request")
        self._combo_approval.pack(side="left", padx=(4, 0))

        # Experimental mode toggles
        exp_frame = ttk.Frame(main)
        exp_frame.pack(fill="x", pady=(2, 0))
        self._var_caveman = tk.BooleanVar(value=True)
        ttk.Checkbutton(exp_frame, text="Caveman Mode", variable=self._var_caveman).pack(side="left", padx=(0, 8))
        self._var_rtk = tk.BooleanVar(value=True)
        ttk.Checkbutton(exp_frame, text="RTK Compression", variable=self._var_rtk).pack(side="left", padx=(0, 8))
        self._var_auto_compact = tk.BooleanVar(value=False)
        ttk.Checkbutton(exp_frame, text="Auto Compact", variable=self._var_auto_compact).pack(side="left", padx=(0, 8))
        self._var_adaptive_compact = tk.BooleanVar(value=False)
        ttk.Checkbutton(exp_frame, text="Adaptive Compact", variable=self._var_adaptive_compact).pack(side="left", padx=(0, 8))
        self._var_tool_truncation = tk.BooleanVar(value=False)
        ttk.Checkbutton(exp_frame, text="Tool Truncation", variable=self._var_tool_truncation).pack(side="left", padx=(0, 8))

        # Launch buttons
        btn_frame1 = ttk.Frame(main)
        btn_frame1.pack(fill="x", pady=(8, 0))
        self._btn_desktop = ttk.Button(btn_frame1, text="Launch Desktop", command=lambda: self._launch("desktop"))
        if "desktop" in self._missing:
            self._btn_desktop.configure(state="disabled")
        self._btn_desktop.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._btn_cli = ttk.Button(btn_frame1, text="Launch CLI", command=lambda: self._launch("cli"))
        if "cli" in self._missing:
            self._btn_cli.configure(state="disabled")
        self._btn_cli.pack(side="left", fill="x", expand=True)

        btn_frame2 = ttk.Frame(main)
        btn_frame2.pack(fill="x", pady=(4, 0))
        self._btn_codex_desktop = ttk.Button(btn_frame2, text="Codex Default (Desktop)",
                                              command=lambda: self._launch_codex_default("desktop"))
        if "desktop" in self._missing:
            self._btn_codex_desktop.configure(state="disabled")
        self._btn_codex_desktop.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._btn_codex_cli = ttk.Button(btn_frame2, text="Codex Default (CLI)",
                                          command=lambda: self._launch_codex_default("cli"))
        if "cli" in self._missing:
            self._btn_codex_cli.configure(state="disabled")
        self._btn_codex_cli.pack(side="left", fill="x", expand=True)

        # Log area
        self._log_text = scrolledtext.ScrolledText(main, height=10, state="disabled", wrap="word",
                                                     font=("Consolas", 9))
        self._log_text.pack(fill="both", expand=True, pady=(8, 0))

        # Bottom bar
        bb = ttk.Frame(main)
        bb.pack(fill="x", pady=(6, 0))
        ttk.Button(bb, text="Clear Log", command=self._clear_log).pack(side="left")
        self._start_proxy_btn = ttk.Button(bb, text="Start Proxy", command=self._start_proxy_only, state="normal")
        self._start_proxy_btn.pack(side="left", padx=(4, 0))
        self._restart_btn = ttk.Button(bb, text="Restart Proxy", command=self._restart_proxy, state="disabled")
        self._restart_btn.pack(side="left", padx=(4, 0))
        ttk.Button(bb, text="AI Assistant", command=self._open_assistant).pack(side="left", padx=(4, 0))
        self._kill_btn = ttk.Button(bb, text="Kill && Cleanup", command=self._kill, state="disabled")
        self._kill_btn.pack(side="left", fill="x", expand=True, padx=(8, 0))
        ttk.Button(bb, text="View Log", command=self._open_proxy_log_dir).pack(side="left")
        ttk.Button(bb, text="Close", command=self._do_close).pack(side="left", padx=(8, 0))

        self._rebuild_combo()
        self._log_dependency_status()
        self._start_watcher()

    # ── Logging ──────────────────────────────────────────────────────

    def log(self, msg):
        self._root.after(0, self._append_log, msg)

    def _append_log(self, msg):
        self._log_text.configure(state="normal")
        self._log_text.insert("end", msg + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _clear_log(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _restart_proxy(self):
        self._kill()
        # Use the currently selected endpoint from the combo box, not the saved default
        ep_name = self._combo_ep.get()
        if not ep_name:
            # Fallback to default if nothing selected in combo
            ep_name = load_endpoints().get("default")
        if not ep_name:
            self.log("No endpoint selected.")
            return
        ep = get_endpoint(ep_name)
        if not ep:
            self.log(f"Endpoint '{ep_name}' not found.")
            return
        ep = dict(ep)
        # Apply current GUI toggle states (same as _start_proxy_only)
        ep["caveman_mode"] = self._var_caveman.get()
        ep["rtk_compression"] = self._var_rtk.get()
        ep["auto_compact"] = self._var_auto_compact.get()
        ep["adaptive_compact"] = self._var_adaptive_compact.get()
        ep["tool_output_truncation"] = self._var_tool_truncation.get()
        time.sleep(0.3)
        start_proxy_for(ep, self.log)
        self.log(f"Proxy restarted for {ep_name}")

    def _start_proxy_only(self):
        """Start the proxy without touching Codex — useful when Codex is already running."""
        ep_name = self._combo_ep.get()
        if not ep_name:
            self.log("ERROR: no endpoint selected")
            return
        ep = get_endpoint(ep_name)
        if not ep:
            self.log(f"ERROR: endpoint '{ep_name}' not found")
            return
        ep = dict(ep)
        ep["caveman_mode"] = self._var_caveman.get()
        ep["rtk_compression"] = self._var_rtk.get()
        ep["auto_compact"] = self._var_auto_compact.get()
        ep["adaptive_compact"] = self._var_adaptive_compact.get()
        ep["tool_output_truncation"] = self._var_tool_truncation.get()
        if ep["backend_type"] == "native":
            self.log("Native endpoint — no proxy needed.")
            return
        self._set_busy(True)
        try:
            proxy_port = start_proxy_for(ep, self.log)
            self.log(f"Proxy started for {ep_name} on :{proxy_port}")
            self._set_busy(False, proxy_alive=True)
        except RuntimeError as e:
            self._root.after(0, lambda: messagebox.showerror("Proxy Failed", str(e)))
            self._set_busy(False)

    def _log_dependency_status(self):
        if self._cli_info:
            _, ver = self._cli_info
            self.log(f"✓ Codex CLI detected ({ver})")
        else:
            self.log("✗ Codex CLI NOT found -- CLI launch disabled.")
        if self._desktop_info[0]:
            label = "MSIX (Store)" if self._desktop_info[1] else self._desktop_info[0]
            self.log(f"✓ Codex Desktop detected ({label})")
        else:
            self.log("✗ Codex Desktop NOT found -- Desktop launch disabled.")
        if self._missing:
            self.log("Install missing tools before using the launcher.")
        else:
            self.log("All dependencies OK.")

    # ── Auth ─────────────────────────────────────────────────────────

    def _check_auth_async(self):
        status, msg = check_codex_auth()
        self._root.after(0, lambda: self._update_auth_status(status, msg))

    def _update_auth_status(self, status, msg):
        if status == "logged_in":
            self._auth_label.configure(text=f"✓ Auth: {msg}", foreground="#2ea043")
            self._relogin_btn.configure(state="normal" if "cli" not in self._missing else "disabled")
        elif status == "not_installed":
            self._auth_label.configure(text="Auth: N/A (CLI not installed)", foreground="#888")
        else:
            self._auth_label.configure(text=f"⚠ Auth: {msg}", foreground="#d29922")
            self._relogin_btn.configure(state="normal" if "cli" not in self._missing else "disabled")

    def _codex_relogin(self):
        self.log("Opening codex login in terminal...")
        term = detect_terminal()
        if not term:
            self.log("ERROR: no terminal emulator found for re-login")
            return
        term_name, term_args, term_path = term
        cmd_parts = [term_name] + term_args + ["codex", "login"]
        if IS_WINDOWS:
            subprocess.Popen(cmd_parts, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(cmd_parts, preexec_fn=os.setsid)
        self.log("Login flow started in terminal. Re-checking auth in 30s...")
        self._auth_label.configure(text="Auth: waiting for login...")
        threading.Thread(target=lambda: (time.sleep(30), self._check_auth_async()), daemon=True).start()

    # ── Combo management ─────────────────────────────────────────────

    def _rebuild_combo(self):
        self._endpoints_data = load_endpoints()
        ep_names = [e["name"] for e in self._endpoints_data["endpoints"]]
        bgp_names = [f"\U0001F500 {p['name']}" for p in load_bgp_pools().get("pools", [])]
        all_names = ep_names + bgp_names
        self._combo_ep["values"] = all_names
        if all_names:
            default = self._endpoints_data.get("default")
            if default and default in ep_names:
                self._combo_ep.set(default)
            else:
                self._combo_ep.set(all_names[0])
        self._on_endpoint_changed()

    def _on_endpoint_changed(self):
        name = self._combo_ep.get()
        is_bgp = name.startswith("\U0001F500 ")
        bgp_name = name[2:] if is_bgp else None
        ep = get_endpoint(name) if name and not is_bgp else None
        models = []
        if is_bgp:
            for p in load_bgp_pools().get("pools", []):
                if p["name"] == bgp_name:
                    seen = set()
                    for r in p.get("routes", []):
                        m = r.get("model", "")
                        if m and m not in seen:
                            models.append(m)
                            seen.add(m)
                    break
        elif ep:
            models = ep.get("models", [])
        self._combo_model["values"] = models
        if ep and ep.get("default_model") in models:
            self._combo_model.set(ep["default_model"])
        elif models:
            self._combo_model.set(models[0])
        else:
            self._combo_model.set("")
        self._check_latency(name)

    # ── Latency check ────────────────────────────────────────────────

    def _check_latency(self, ep_name):
        self._latency_label.configure(text="...", foreground="gray")
        is_bgp = ep_name.startswith("\U0001F500 ")
        if is_bgp or not ep_name:
            self._latency_label.configure(text=" -- ", foreground="gray")
            return
        ep = get_endpoint(ep_name)
        if not ep:
            self._latency_label.configure(text=" -- ", foreground="gray")
            return

        def _run():
            lat = check_provider_latency(ep)
            def _update():
                if lat is None:
                    self._latency_label.configure(text=" -- ", foreground="gray")
                elif lat < 1.0:
                    self._latency_label.configure(text=f"{lat:.2f}s", foreground="#2ea043")
                elif lat < 3.0:
                    self._latency_label.configure(text=f"{lat:.2f}s", foreground="#d29922")
                else:
                    self._latency_label.configure(text=f"{lat:.2f}s", foreground="#e74c3c")
            self._root.after(0, _update)

        threading.Thread(target=_run, daemon=True).start()

    # ── Window openers ───────────────────────────────────────────────

    def _on_endpoints_updated(self):
        self._rebuild_combo()

    def _open_mgr(self):
        EndpointMgr(self._root, on_update=self._on_endpoints_updated)

    def _open_bgp(self):
        BGPPoolMgr(self._root, on_update=self._on_endpoints_updated)

    def _google_reoauth(self, provider, parent_dlg=None):
        google_oauth_flow(parent_dlg or self._root, oauth_provider=provider)

    def _kiro_reoauth(self, parent_dlg=None):
        kiro_oauth_flow(parent_dlg or self._root)

    def _codebuff_reoauth_standalone(self, parent_dlg=None):
        codebuff_oauth_flow(parent_dlg or self._root)

    def _open_monitoring(self):
        AIMonitoringWindow(self._root)

    def _open_usage(self):
        UsageWindow(self._root)

    def _open_history(self):
        RequestHistoryWindow(self._root)

    def _open_session_manager(self):
        SessionManagerWindow(self._root)

    def _open_benchmark(self):
        BenchmarkWindow(self._root)

    def _open_updater(self):
        CodexUpdaterWindow(self._root)

    def _open_proxy_log_dir(self):
        log_dir = str(PROXY_CONFIG_DIR)
        req_log = PROXY_CONFIG_DIR / "requests.log"
        if IS_WINDOWS:
            if req_log.exists():
                os.startfile(str(req_log))
            else:
                os.startfile(log_dir)
        else:
            import subprocess as _sp
            _sp.Popen(["xdg-open", log_dir])

    def _open_assistant(self):
        assist_path = str(Path(__file__).resolve().parent / "flet-codex-assist.py")
        if Path(assist_path).exists():
            subprocess.Popen([sys.executable, assist_path], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0)

    def _kiro_import_standalone(self, parent_dlg=None):
        """Import Kiro refresh token from OAuth secrets dialog."""
        dlg = tk.Toplevel(parent_dlg or self._root)
        dlg.title("Kiro — Import Token")
        dlg.geometry("520x180")
        dlg.transient(parent_dlg or self._root)
        dlg.grab_set()
        tk.Label(dlg, text="Paste Kiro refresh token (starts with 'aor'):", font=("Segoe UI", 10, "bold")).pack(padx=16, pady=(12, 4), anchor="w")
        entry = ttk.Entry(dlg, width=60, show="*")
        entry.pack(padx=16, fill="x", pady=(4, 4))
        status_var = tk.StringVar(value="")
        tk.Label(dlg, textvariable=status_var, wraplength=480).pack(padx=16, anchor="w")

        def _do():
            raw = entry.get().strip()
            if not raw or not raw.startswith("aor"):
                status_var.set("Token must start with 'aor'")
                return
            status_var.set("Validating...")
            def _thread():
                try:
                    result = kiro_validate_refresh_token(raw)
                    kiro_save_token(result["access_token"], result["refresh_token"],
                                    expires_in=result["expires_in"], email=result["email"],
                                    provider_kind="kiro-imported",
                                    profile_arn=result.get("profile_arn"))
                    dlg.after(0, lambda: status_var.set(f"OK! Logged in as {result['email'] or 'unknown'}"))
                    dlg.after(2000, dlg.destroy)
                except Exception as e:
                    dlg.after(0, lambda: status_var.set(f"Failed: {str(e)[:200]}"))
            threading.Thread(target=_thread, daemon=True).start()
        ttk.Button(dlg, text="Import", command=_do).pack(padx=16, pady=(8, 0), anchor="w")

    def _edit_oauth_secrets(self):
        import tkinter.simpledialog
        data = load_oauth_secrets()
        if not data:
            data = {"antigravity": {"client_id": "", "client_secret": ""},
                    "gemini_cli": {"client_id": "", "client_secret": ""}}

        dlg = tk.Toplevel(self._root)
        dlg.title("OAuth Secrets & Credentials")
        dlg.geometry("620x650")
        dlg.transient(self._root)
        dlg.grab_set()

        canvas = tk.Canvas(dlg)
        scrollbar = ttk.Scrollbar(dlg, orient="vertical", command=canvas.yview)
        frame = ttk.Frame(canvas, padding=16)
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(frame, text="Google OAuth 2.0 Client Credentials", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, text=str(OAUTH_SECRETS_PATH), foreground="gray").pack(anchor="w", pady=(0, 8))

        fields = {}
        nf = ttk.Frame(frame)
        nf.pack(fill="x")
        row = 0
        google_token_dir = str(PROXY_CONFIG_DIR)
        for section_key, section_label, oauth_prov, token_file in [
            ("antigravity", "Antigravity (CloudCode)", "google-antigravity", "google-antigravity-oauth-token.json"),
            ("gemini_cli", "Gemini CLI", "google-cli", "google-cli-oauth-token.json"),
        ]:
            ttk.Label(nf, text=f"\n{section_label}", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, columnspan=4, sticky="w", pady=(8, 2))
            row += 1
            sec = data.get(section_key, {})
            token_path = os.path.join(google_token_dir, token_file)
            has_token = False
            try:
                with open(token_path) as tf:
                    td = json.load(tf)
                has_token = bool(td.get("refresh_token") or td.get("access_token"))
            except Exception:
                pass
            token_status = "Token: valid" if has_token else "Token: missing"
            token_color = "#2ea043" if has_token else "#d29922"
            ttk.Label(nf, text=token_status, foreground=token_color).grid(row=row, column=0, sticky="w", padx=(8, 4), pady=2)
            import_btn = ttk.Button(nf, text="Import JSON",
                                     command=lambda sk=section_key: self._import_oauth_json(fields, sk))
            import_btn.grid(row=row, column=2, padx=(4, 0), pady=2, sticky="e")
            reauth_btn = ttk.Button(nf, text="Re-OAuth",
                                     command=lambda p=oauth_prov: self._google_reoauth(p, dlg))
            reauth_btn.grid(row=row, column=3, padx=(4, 0), pady=2, sticky="e")
            row += 1
            for fk, fl in [("client_id", "Client ID"), ("client_secret", "Client Secret")]:
                ttk.Label(nf, text=fl + ":").grid(row=row, column=0, sticky="w", padx=(8, 4), pady=2)
                entry = ttk.Entry(nf, width=55)
                entry.insert(0, sec.get(fk, ""))
                entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=2)
                if fk == "client_secret":
                    entry.configure(show="*")
                fields[(section_key, fk)] = entry
                row += 1

        nf.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Import client_secret_*.json from Google Cloud Console → Credentials", foreground="gray").pack(anchor="w")

        ttk.Separator(frame).pack(fill="x", pady=(12, 8))

        ttk.Label(frame, text="Freebuff / Codebuff Credentials", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, text=str(HOME / ".config" / "manicode" / "credentials.json"), foreground="gray").pack(anchor="w", pady=(0, 8))

        cb_creds_path = str(HOME / ".config" / "manicode" / "credentials.json")
        cb_fields = {}
        try:
            with open(cb_creds_path) as f:
                cb_data = json.load(f)
        except Exception:
            cb_data = {}
        cb_default = cb_data.get("default", {})

        cb_info = f"Email: {cb_default.get('email', 'not logged in')}"
        cb_name = cb_default.get("name", "")
        if cb_name:
            cb_info = f"{cb_name} — {cb_info}"
        has_cb_token = bool(cb_default.get("authToken", ""))
        status_text = "Logged in" if has_cb_token else "Not logged in"
        status_color = "#2ea043" if has_cb_token else "#d29922"
        ttk.Label(frame, text=cb_info).pack(anchor="w")
        ttk.Label(frame, text=f"Status: {status_text}", foreground=status_color, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))

        cb_nf = ttk.Frame(frame)
        cb_nf.pack(fill="x")
        cb_row = [0]
        for fk, fl in [("authToken", "Auth Token"), ("fingerprintId", "Fingerprint ID")]:
            ttk.Label(cb_nf, text=fl + ":").grid(row=cb_row[0], column=0, sticky="w", padx=(8, 4), pady=2)
            entry = ttk.Entry(cb_nf, width=55, show="*")
            entry.insert(0, cb_default.get(fk, ""))
            entry.grid(row=cb_row[0], column=1, sticky="ew", pady=2)
            cb_fields[fk] = entry
            cb_row[0] += 1
        cb_nf.columnconfigure(1, weight=1)

        ttk.Button(frame, text="Re-OAuth (GitHub Login)",
                    command=lambda: self._codebuff_reoauth_standalone(dlg)).pack(anchor="w", pady=(4, 0))

        cb_accounts = cb_data.get("accounts", [])
        if cb_accounts:
            ttk.Label(frame, text=f"Additional accounts: {len(cb_accounts)} (edit credentials.json manually)", foreground="gray").pack(anchor="w")

        # ── Section 3: Kiro (AWS CodeWhisperer) Credentials ──
        ttk.Separator(frame).pack(fill="x", pady=(12, 8))
        ttk.Label(frame, text="Kiro (AWS CodeWhisperer)", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        kiro_token_path = str(PROXY_CONFIG_DIR / "kiro-oauth-token.json")
        ttk.Label(frame, text=kiro_token_path, foreground="gray").pack(anchor="w", pady=(0, 4))

        kiro_email = ""
        kiro_status = "Not logged in"
        kiro_color = "#d29922"
        try:
            with open(kiro_token_path) as f:
                kiro_td = json.load(f)
            kiro_email = kiro_td.get("email", "")
            kiro_expires = kiro_td.get("expires_at", 0)
            if kiro_td.get("access_token"):
                if kiro_expires and time.time() < kiro_expires:
                    kiro_status = f"Valid (expires in {int(kiro_expires - time.time())}s)"
                    kiro_color = "#2ea043"
                else:
                    kiro_status = "Expired — will refresh on use"
                    kiro_color = "#d29922"
            if kiro_email:
                ttk.Label(frame, text=f"Email: {kiro_email}").pack(anchor="w")
        except Exception:
            pass
        ttk.Label(frame, text=f"Status: {kiro_status}", foreground=kiro_color, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))

        kiro_btn_frame = ttk.Frame(frame)
        kiro_btn_frame.pack(fill="x", pady=(0, 4))
        ttk.Button(kiro_btn_frame, text="Re-OAuth (Builder ID)", command=lambda: self._kiro_reoauth(dlg)).pack(side="left", padx=(0, 4))
        ttk.Button(kiro_btn_frame, text="Import Token", command=lambda: self._kiro_import_standalone(dlg)).pack(side="left")

        btnf = ttk.Frame(frame)
        btnf.pack(fill="x", pady=(12, 0))
        ttk.Button(btnf, text="Cancel", command=dlg.destroy).pack(side="right", padx=(4, 0))
        save_btn = ttk.Button(btnf, text="Save")
        save_btn.pack(side="right", padx=(4, 0))

        def _save():
            for (sk, fk), entry in fields.items():
                if sk not in data:
                    data[sk] = {}
                data[sk][fk] = entry.get().strip()
            try:
                save_oauth_secrets(data)
            except Exception as e:
                messagebox.showerror("Save failed", str(e), parent=dlg)
                return
            cb_updated = dict(cb_default)
            for fk, entry in cb_fields.items():
                val = entry.get().strip()
                if val:
                    cb_updated[fk] = val
            if cb_updated:
                cb_data["default"] = cb_updated
                try:
                    os.makedirs(os.path.dirname(cb_creds_path), exist_ok=True)
                    with open(cb_creds_path, "w") as f:
                        json.dump(cb_data, f, indent=2)
                except Exception as e:
                    messagebox.showerror("Save failed", str(e), parent=dlg)
                    return
            dlg.destroy()

        save_btn.configure(command=_save)

    def _import_oauth_json(self, fields, section_key):
        path = filedialog.askopenfilename(
            title="Import Google OAuth Client Secret JSON",
            filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            creds = raw.get("installed") or raw.get("web") or raw
            cid = creds.get("client_id", "")
            csec = creds.get("client_secret", "")
            if not cid or not csec:
                raise ValueError("JSON does not contain client_id and client_secret")
            if (section_key, "client_id") in fields:
                fields[(section_key, "client_id")].delete(0, "end")
                fields[(section_key, "client_id")].insert(0, cid)
            if (section_key, "client_secret") in fields:
                fields[(section_key, "client_secret")].delete(0, "end")
                fields[(section_key, "client_secret")].insert(0, csec)
        except Exception as e:
            messagebox.showerror("Import failed", str(e))

    # ── Watcher ──────────────────────────────────────────────────────

    def _start_watcher(self):
        cfg = load_monitoring_config()
        if not cfg.get("enabled"):
            return
        self._watcher = HealthWatcher(
            on_failure=lambda c: self.log(f"[AI Monitor] Proxy unresponsive (failures={c})"),
            on_recovery=lambda: self.log("[AI Monitor] Proxy recovered"),
            on_signal=lambda fid, cat, line: None,
            on_action=self._on_watcher_action,
        )
        self._watcher.start()
        self.log("AI Monitoring: watchdog started")

    def _on_watcher_action(self, action, trigger):
        cfg = load_monitoring_config()
        if action == "restart_proxy" and cfg.get("auto_restart_proxy"):
            self.log(f"[AI Monitor] Auto-restarting proxy (trigger: {trigger})")
            self._root.after(0, self._restart_proxy_from_watcher)
        elif action in ("clear_schema_cache", "delete_provider_caps"):
            try:
                cap_file = PROXY_CONFIG_DIR / "provider-caps.json"
                if cap_file.exists():
                    cap_file.unlink()
                    self.log("[AI Monitor] Cleared corrupt schema cache")
            except Exception as e:
                self.log(f"[AI Monitor] Failed to clear cache: {e}")
        elif action == "kill_stale_restart":
            self.log(f"[AI Monitor] Killing stale processes + restarting (trigger: {trigger})")
            self._kill()
            self._root.after(0, self._restart_proxy_from_watcher)
        else:
            self.log(f"[AI Monitor] Alert: {action} (trigger: {trigger})")

    def _restart_proxy_from_watcher(self):
        try:
            # Use the currently selected endpoint from combo, fallback to default
            ep_name = self._combo_ep.get() or load_endpoints().get("default")
            if not ep_name:
                return
            ep = get_endpoint(ep_name)
            if not ep:
                return
            start_proxy_for(dict(ep), self.log)
        except Exception as e:
            self.log(f"[AI Monitor] Proxy restart failed: {e}")

    # ── Profile operations ───────────────────────────────────────────

    def _backup_profile(self):
        filename = filedialog.asksaveasfilename(
            title="Backup Codex Profile",
            defaultextension=".json",
            initialfile=f"codex-profile-{time.strftime('%Y%m%d-%H%M%S')}.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            save_profile_bundle(filename)
            self.log(f"Profile backed up to {filename}")
        except Exception as e:
            messagebox.showerror("Backup Failed", str(e))

    def _refresh_all_models(self):
        if self._refresh_running:
            return
        self._refresh_running = True
        self._refresh_all_btn.configure(state="disabled")
        self.log("Refreshing models for all providers...")
        threading.Thread(target=self._refresh_all_models_worker, daemon=True).start()

    def _refresh_all_models_worker(self):
        try:
            data = load_endpoints()
            updated = 0
            failed = []
            for idx, ep in enumerate(list(data["endpoints"])):
                refreshed, err = refresh_endpoint_models(ep)
                if refreshed:
                    data["endpoints"][idx] = refreshed
                    updated += 1
                else:
                    failed.append(f"{ep['name']}: {err}")
            if updated:
                save_endpoints(data)
            self._root.after(0, lambda: self._finish_refresh(updated, failed))
        except Exception as e:
            self._root.after(0, lambda: self._finish_refresh_error(str(e)))

    def _finish_refresh(self, updated, failed):
        if updated:
            self._rebuild_combo()
            self.log(f"Refreshed models for {updated} provider(s)")
        if failed:
            messagebox.showwarning("Refresh", "Some providers could not auto-fetch models.\n\n" +
                                   "\n".join(failed))
        elif updated:
            messagebox.showinfo("Refresh", f"Refreshed models for {updated} provider(s).")
        else:
            messagebox.showinfo("Refresh", "No providers were refreshed.")
        self._refresh_running = False
        self._refresh_all_btn.configure(state="normal")

    def _finish_refresh_error(self, err):
        messagebox.showerror("Refresh Failed", err)
        self._refresh_running = False
        self._refresh_all_btn.configure(state="normal")

    def _import_profile(self):
        if self._proc and self._proc.poll() is None:
            messagebox.showwarning("Import", "Stop Codex before importing a profile.")
            return
        filename = filedialog.askopenfilename(
            title="Import Codex Profile",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return
        if not messagebox.askyesno("Import",
                "Importing will replace the current endpoints and Codex config. Continue?"):
            return
        try:
            import_profile_bundle(filename)
            self._rebuild_combo()
            self.log(f"Profile imported from {filename}")
            messagebox.showinfo("Import", "Profile imported successfully.")
        except Exception as e:
            messagebox.showerror("Import Failed", str(e))

    # ── Dialogs ──────────────────────────────────────────────────────

    def _show_changelog(self):
        dlg = tk.Toplevel(self._root)
        dlg.title("Changelog")
        dlg.geometry("540x480")
        dlg.transient(self._root)
        text = scrolledtext.ScrolledText(dlg, wrap="word", font=("Segoe UI", 9))
        text.pack(fill="both", expand=True, padx=12, pady=12)
        for ver, date, items in CHANGELOG:
            text.insert("end", f"v{ver}  ({date})\n")
            for item in items:
                text.insert("end", f"  • {item}\n")
            text.insert("end", "\n")
        text.configure(state="disabled")
        ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=(0, 10))

    def _show_install_guide(self, which):
        if which == "cli":
            guide = ("Codex CLI is required to use CLI launch features.\n\n"
                     "Install with npm:\n  npm install -g @openai/codex\n\n"
                     "Or download from:\n  https://github.com/openai/codex\n\n"
                     "After installing, restart the launcher.")
        else:
            guide = ("Codex Desktop is required to use Desktop launch features.\n\n"
                     "Download from:\n  https://codex.desktop.openai.com\n\n"
                     "After installing, restart the launcher.")
        messagebox.showinfo(f"Install Codex {which.title()}", guide)

    # ── Launch ───────────────────────────────────────────────────────

    def _set_busy(self, busy, proxy_alive=False):
        has_cli = "cli" not in self._missing
        has_desk = "desktop" not in self._missing
        def _update():
            self._btn_desktop.configure(state="disabled" if busy or not has_desk else "normal")
            self._btn_cli.configure(state="disabled" if busy or not has_cli else "normal")
            self._btn_codex_desktop.configure(state="disabled" if busy or not has_desk else "normal")
            self._btn_codex_cli.configure(state="disabled" if busy or not has_cli else "normal")
            self._kill_btn.configure(state="normal" if busy or proxy_alive else "disabled")
            self._restart_btn.configure(state="normal" if busy or proxy_alive else "disabled")
            self._start_proxy_btn.configure(state="disabled" if busy or proxy_alive else "normal")
        self._root.after(0, _update)

    def _launch(self, target):
        name = self._combo_ep.get()
        if not name:
            self.log("ERROR: no endpoint selected")
            return
        model = self._combo_model.get()
        if not model:
            self.log("ERROR: no model selected")
            return

        is_bgp = name.startswith("\U0001F500 ")
        if is_bgp:
            pool_name = name[2:]
            pool = None
            for p in load_bgp_pools().get("pools", []):
                if p["name"] == pool_name:
                    pool = p
                    break
            if not pool:
                self.log(f"ERROR: BGP pool '{pool_name}' not found")
                return
            self._set_busy(True)
            target_name = "Desktop" if target == "desktop" else "CLI"
            self.log(f"=== BGP: {pool_name} / {model} -> {target_name} ===")
            threading.Thread(target=self._run_bgp, args=(pool, model, target), daemon=True).start()
            return

        ep = get_endpoint(name)
        if not ep:
            self.log("ERROR: endpoint not found")
            return
        self._set_busy(True)
        target_name = "Desktop" if target == "desktop" else "CLI"
        self.log(f"=== {ep['name']} / {model} -> {target_name} ===")
        threading.Thread(target=self._run, args=(ep, model, target), daemon=True).start()

    def _launch_codex_default(self, target):
        if "cli" not in self._missing:
            status, msg = check_codex_auth()
            if status != "logged_in":
                if not messagebox.askyesno("Auth Warning",
                        f"Codex auth check: {msg}\n\n"
                        "Launch may fail without valid authentication.\nContinue anyway?"):
                    self._set_busy(False)
                    return
        self._set_busy(True)
        target_name = "Desktop" if target == "desktop" else "CLI"
        self.log(f"=== Codex Default (OAuth) -> {target_name} ===")
        threading.Thread(target=self._run_codex_default, args=(target,), daemon=True).start()

    def _run(self, ep, model, target):
        keep_session_alive = False
        try:
            self.log("Cleaning up stale processes...")
            safe_cleanup_owned(self.log)
            recover_config_if_needed(self.log)

            ep["caveman_mode"] = self._var_caveman.get()
            ep["rtk_compression"] = self._var_rtk.get()
            ep["auto_compact"] = self._var_auto_compact.get()
            ep["adaptive_compact"] = self._var_adaptive_compact.get()
            ep["tool_output_truncation"] = self._var_tool_truncation.get()
            needs_proxy = ep["backend_type"] != "native"
            if needs_proxy:
                self.log("Starting translation proxy...")
                try:
                    proxy_port = start_proxy_for(ep, self.log)
                except RuntimeError as e:
                    self._root.after(0, lambda: messagebox.showerror("Proxy Failed", str(e)))
                    return
                self.log(f"Configuring Codex for {ep['name']} (proxied on :{proxy_port})...")
                begin_config_transaction(f"launch:{ep['name']}")
                write_config_for_translated(ep, model, proxy_port)
            else:
                self.log(f"Configuring Codex for {ep['name']} (native)...")
                begin_config_transaction(f"launch:{ep['name']}")
                write_config_for_native(ep, model)

            if target == "desktop":
                if needs_proxy:
                    kill_existing_desktop(self.log)
                keep_session_alive = self._launch_desktop(ep, model)
            else:
                self._launch_cli(ep, model)
        except Exception as e:
            self.log(f"ERROR: {e}")
        finally:
            if keep_session_alive:
                self.log("Warm-start handoff detected; keeping proxy/config active for running Desktop.")
                self._set_busy(False, proxy_alive=True)
                self.log("Ready. Use Kill && Cleanup when finished.")
            else:
                stop_proxy()
                restore_config()
                end_config_transaction()
                self._set_busy(False)
                self.log("Ready.")

    def _run_bgp(self, pool, model, target):
        keep_session_alive = False
        try:
            self.log("Cleaning up stale processes...")
            safe_cleanup_owned(self.log)
            recover_config_if_needed(self.log)

            self.log(f"Starting BGP proxy with {len(pool.get('routes', []))} routes...")
            port, bgp_ep = start_bgp_proxy(pool, model, self.log)

            begin_config_transaction(f"launch:bgp:{pool['name']}")
            write_config_for_translated(bgp_ep, model, port)

            if target == "desktop":
                kill_existing_desktop(self.log)
                keep_session_alive = self._launch_desktop(bgp_ep, model)
            else:
                self._launch_cli(bgp_ep, model)
        except Exception as e:
            self.log(f"ERROR: {e}")
        finally:
            if keep_session_alive:
                self.log("Warm-start handoff detected; keeping proxy/config active.")
                self._set_busy(False)
                self.log("Ready. Use Kill && Cleanup when finished.")
            else:
                stop_proxy()
                restore_config()
                end_config_transaction()
                self._set_busy(False)
                self.log("Ready.")

    def _run_codex_default(self, target):
        try:
            self.log("Cleaning up stale processes...")
            safe_cleanup_owned(self.log)
            stop_proxy()
            recover_config_if_needed(self.log)
            self.log("Resetting config to Codex defaults (OAuth)...")
            begin_config_transaction("launch:default")
            if CONFIG.exists():
                CONFIG.unlink()
            if target == "desktop":
                self._launch_desktop_direct()
            else:
                self._launch_cli_default()
        except Exception as e:
            self.log(f"ERROR: {e}")
        finally:
            restore_config()
            end_config_transaction()
            self._set_busy(False)
            self.log("Ready.")

    def _launch_desktop(self, ep, model):
        if not self._desktop_info[0]:
            self.log("ERROR: Codex Desktop not found")
            return False

        _, is_msix = self._desktop_info
        self._proc = launch_codex_desktop(self._desktop_info)
        if not self._proc:
            self.log("ERROR: Failed to launch Codex Desktop")
            return False

        pid = self._proc.pid
        self.log(f"Desktop started (PID {pid})")
        self.log(f"Log: {LAUNCH_LOG}")

        # MSIX: cmd.exe exits immediately, monitor via tasklist instead
        if is_msix and IS_WINDOWS:
            time.sleep(3)
            if not is_codex_desktop_running():
                self.log("ERROR: Codex Desktop did not start")
                self._proc = None
                return False
            self.log("Codex Desktop is running (MSIX)")
            self._proc = None
            return True

        t0 = time.time()
        stall_warned = False
        while self._proc and self._proc.poll() is None:
            time.sleep(1.5)
            el = time.time() - t0
            if el > 20 and not stall_warned:
                self.log("Still starting after 20s -- possible stall. Click Kill if window doesn't appear.")
                self.log(f"--- last log lines ---\n{last_log_lines()}")
                stall_warned = True

        if self._proc:
            rc = self._proc.poll()
            el = time.time() - t0
            self.log(f"Desktop exited (code {rc}) after {el:.0f}s")
            if el < 12:
                self.log("TIP: Quick exit -- may be warm-start handoff (normal) or crash.")
                last_lines = last_log_lines()
                self.log(f"--- last log lines ---\n{last_lines}")
                if rc == 0 and "warm-start" in last_lines.lower():
                    self._proc = None
                    return True
            self._proc = None
        return False

    def _sandbox_flag(self):
        mapping = {"Read-only": "read-only", "Workspace": "workspace", "Full Access": "full-access"}
        return mapping.get(self._combo_sandbox.get(), "workspace")

    def _approval_flag(self):
        mapping = {"Untrusted": "suggest", "On Request": "auto-edit", "Never (Full Auto)": "full-auto"}
        return mapping.get(self._combo_approval.get(), "auto-edit")

    def _launch_cli(self, ep, model):
        self.log(f"Launching Codex CLI with {ep['name']}...")
        term = detect_terminal()
        if not term:
            self.log("ERROR: no terminal found")
            return

        term_name, term_args, _ = term
        cmd_parts = [term_name] + term_args
        if ep["backend_type"] == "native":
            cmd_parts.extend(["codex", "-c", f"model={model}",
                              "-s", self._sandbox_flag(), "-a", self._approval_flag()])
        else:
            cmd_parts.extend(["codex", "--profile", _profile_slug(ep["name"]), "-c", f"model={model}",
                              "-s", self._sandbox_flag(), "-a", self._approval_flag()])

        self.log(f"Running: {' '.join(cmd_parts)}")
        if IS_WINDOWS:
            self._proc = subprocess.Popen(cmd_parts, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self._proc = subprocess.Popen(cmd_parts, preexec_fn=os.setsid)
        pid = self._proc.pid
        self.log(f"CLI started in terminal (PID {pid})")

        while self._proc and self._proc.poll() is None:
            time.sleep(1.5)
        if self._proc:
            rc = self._proc.poll()
            self.log(f"CLI exited (code {rc})")
            self._proc = None

    def _launch_desktop_direct(self):
        self.log("Launching Codex Desktop (default OAuth)...")
        if not self._desktop_info[0]:
            self.log("ERROR: Codex Desktop not found")
            return
        self._proc = launch_codex_desktop(self._desktop_info)
        if not self._proc:
            self.log("ERROR: Failed to launch Codex Desktop")
            return
        pid = self._proc.pid
        self.log(f"Desktop started (PID {pid})")

        t0 = time.time()
        stall_warned = False
        while self._proc and self._proc.poll() is None:
            time.sleep(1.5)
            el = time.time() - t0
            if el > 20 and not stall_warned:
                self.log("Still starting after 20s -- possible stall.")
                self.log(f"--- last log lines ---\n{last_log_lines()}")
                stall_warned = True
        if self._proc:
            rc = self._proc.poll()
            el = time.time() - t0
            self.log(f"Desktop exited (code {rc}) after {el:.0f}s")
            self._proc = None

    def _launch_cli_default(self):
        self.log("Launching Codex CLI (default OAuth)...")
        term = detect_terminal()
        if not term:
            self.log("ERROR: no terminal found")
            return
        term_name, term_args, _ = term
        cmd_parts = [term_name] + term_args + ["codex",
                  "-s", self._sandbox_flag(), "-a", self._approval_flag()]
        self.log(f"Running: {' '.join(cmd_parts)}")
        if IS_WINDOWS:
            self._proc = subprocess.Popen(cmd_parts, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self._proc = subprocess.Popen(cmd_parts, preexec_fn=os.setsid)
        pid = self._proc.pid
        self.log(f"CLI started in terminal (PID {pid})")
        while self._proc and self._proc.poll() is None:
            time.sleep(1.5)
        if self._proc:
            rc = self._proc.poll()
            self.log(f"CLI exited (code {rc})")
            self._proc = None

    # ── Kill ─────────────────────────────────────────────────────────

    def _kill(self):
        self.log("=== Killing ===")
        if self._proc and self._proc.poll() is None:
            try:
                if IS_WINDOWS:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                                   capture_output=True, timeout=10)
                else:
                    import signal as sig
                    pgid = os.getpgid(self._proc.pid)
                    os.killpg(pgid, sig.SIGTERM)
                    time.sleep(1)
                    if self._proc.poll() is None:
                        os.killpg(pgid, sig.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            self._proc = None
        stop_proxy()
        safe_cleanup_owned(self.log)
        restore_config()
        end_config_transaction()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if LAUNCH_LOG.exists():
            try:
                LAUNCH_LOG.unlink()
            except Exception:
                pass
        self.log("Cleanup complete")
        self._set_busy(False)
        self.log("Ready.")

    def _do_close(self):
        if self._proc and self._proc.poll() is None:
            if not messagebox.askyesno("Confirm", "Codex is still running. Kill it?"):
                return
            self._kill()
        stop_proxy()
        self._root.destroy()



