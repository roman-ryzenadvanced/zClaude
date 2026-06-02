#!/usr/bin/env python3
"""Codex Launcher X Edition — Main window with Warp-inspired modern dark UI.

This is the X Edition of the Codex Launcher GUI. It lives alongside the
original gui/launcher.py so both can coexist. Launch it with:

    python src/codex-launcher-gui-x.py

What's different from the original GUI:
  ┌──────────────────────┬──────────────────────────────────────────────┐
  │ Original GUI         │ X Edition GUI                               │
  ├──────────────────────┼──────────────────────────────────────────────┤
  │ Default OS theme     │ Catppuccin Mocha dark theme everywhere       │
  │ "Segoe UI" fonts     │ Cross-platform font detection (Noto Sans)   │
  │ Flat button rows     │ Card-based layout with visual hierarchy     │
  │ Plain text log       │ Syntax-colored log console                   │
  │ Crowded toolbar      │ Warp-style left sidebar navigation           │
  │ OS title bar         │ Custom dark title bar (Linux only)           │
  │ No hover effects     │ Animated status + hover highlights           │
  │ Sub-windows unthemed │ All sub-windows auto-themed                  │
  └──────────────────────┴──────────────────────────────────────────────┘
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from codex_launcher_lib import (
    IS_WINDOWS,
    HOME,
    CONFIG,
    CONFIG_BAK,
    CONFIG_TXN,
    ENDPOINTS_FILE,
    BGP_POOLS_FILE,
    LAUNCH_LOG,
    LOG_DIR,
    PROXY_CONFIG_DIR,
    BIN_DIR,
    PROXY,
    CLEANUP,
    PID_REGISTRY,
    PROVIDER_PRESETS,
    CHANGELOG,
    DEFAULT_CONFIG,
    OAUTH_SECRETS_PATH,
    ANTIGRAVITY_MODELS,
    safe_name,
    label_for_backend,
    normalize_model_id,
    normalize_base_url,
    _profile_slug,
    parse_model_list,
    now_utc_iso,
    apply_provider_preset,
    load_endpoints,
    save_endpoints,
    load_bgp_pools,
    save_bgp_pools,
    get_endpoint,
    build_profile_bundle,
    save_profile_bundle,
    import_profile_bundle,
    backup_config,
    restore_config,
    begin_config_transaction,
    end_config_transaction,
    recover_config_if_needed,
    write_config_for_native,
    write_config_for_translated,
    endpoint_models_url,
    endpoint_model_headers,
    fetch_models_for_endpoint,
    refresh_endpoint_models,
    run_endpoint_doctor,
    detect_codex_cli,
    detect_codex_desktop,
    launch_codex_desktop,
    is_codex_desktop_running,
    check_codex_auth,
    last_log_lines,
    kill_existing_desktop,
    safe_cleanup_owned,
    start_proxy_for,
    stop_proxy,
    start_bgp_proxy,
    get_proxy_state,
    set_proxy_state,
    detect_terminal,
    open_url,
    open_file,
    write_secure_text,
    ensure_dirs,
    create_default_endpoints,
    load_monitoring_config,
    save_monitoring_config,
    load_incident_store,
    save_incident_store,
    load_usage_stats,
    monitoring_log,
    IncidentStore,
    AIDiagnosticAgent,
    HealthWatcher,
    load_oauth_secrets,
    save_oauth_secrets,
    check_provider_latency,
)

# Re-use existing GUI submodules (endpoint dialogs, BGP, OAuth, etc.)
from gui.helpers import _fmt_tok, _fmt_dur, _status_pill, _show_doctor_results_tk
from gui.endpoint_dialogs import EditEndpointDialog, EndpointMgr
from gui.bgp_dialogs import BGPRouteDialog, BGPPoolEditDialog, BGPPoolMgr
from gui.oauth_flows import (
    google_oauth_flow,
    codebuff_oauth_flow,
    kiro_oauth_flow,
    kiro_validate_refresh_token,
    kiro_save_token,
)

# X Edition modules
from gui_x.theme import CATPPUCCIN, ThemedToplevel
from gui_x.fonts import FONT_UI, FONT_MONO, FONT_HEADING, SIZE_TITLE, SIZE_HEADING
from gui_x.fonts import SIZE_BODY, SIZE_SMALL, SIZE_TINY, SIZE_MONO, SIZE_ICON
from gui_x.sidebar import Sidebar
from gui_x.titlebar import TitleBar
from gui_x.log_console import LogConsole
from gui_x.cards import Card, StatusCard, ProviderCard, LaunchCard
from gui_x.anims import PulseIndicator, SpinnerButton, add_hover_effect

# X Edition sub-windows (themed wrappers)
from gui_x.windows import (
    XEndpointMgr,
    XAIMonitoringWindow,
    XUsageWindow,
    XBGPPoolMgr,
    XBenchmarkWindow,
    XRequestHistoryWindow,
    XSessionManagerWindow,
    XCodexUpdaterWindow,
)


class AutoScrollbar(ttk.Scrollbar):
    """A Scrollbar that shows itself only when the content exceeds the window area."""

    def set(self, lo, hi):
        # If the visible portion is >= 98% of the content, hide the scrollbar
        if float(hi) - float(lo) >= 0.98:
            self.grid_remove()
        else:
            self.grid()
        super().set(lo, hi)


class LauncherWinX:
    """Main Launcher Window — X Edition.

    Layout:
      ┌─ TitleBar ──────────────────────────────────────────────────┐
      ├──────┬───────────────────────────────────────────────────────┤
      │      │  ┌─ Status Card ──────────────────────────────────┐  │
      │  S   │  │ ✓ Codex CLI v2.1    ✓ Auth: logged in        │  │
      │  i   │  │ ✓ Codex Desktop                              │  │
      │  d   │  └───────────────────────────────────────────────┘  │
      │  e   │  ┌─ Provider Card ──────────────────────────────┐   │
      │  b   │  │ 🔵 Endpoint: [OpenAI ▾]  Model: [gpt-4o ▾]  │   │
      │  a   │  │ Sandbox/Approval toggles                     │   │
      │  r   │  └───────────────────────────────────────────────┘  │
      │      │  ┌─ Launch Card ────────────────────────────────┐   │
      │      │  │ [🚀 Launch Desktop]  [Launch CLI]           │   │
      │      │  │ [Codex Default Desktop]  [Default CLI]      │   │
      │      │  └───────────────────────────────────────────────┘  │
      │      │  ┌─ Experimental (collapsible) ─────────────────┐   │
      │      │  │ ☑ Caveman  ☑ RTK  ☐ Auto Compact  ...      │   │
      │      │  └───────────────────────────────────────────────┘  │
      │      │  ┌─ Log Console ───────────────────────────────┐   │
      │      │  │ 14:23:01 ✓ Codex CLI detected (v2.1)       │   │
      │      │  │ 14:23:02 [proxy] Proxy ready on port 61255  │   │
      │      │  └───────────────────────────────────────────────┘  │
      │      │  ┌─ Bottom Bar ─────────────────────────────────┐   │
      │      │  │ [Start Proxy] [Kill] [View Log]    [Close]  │   │
      │      │  └───────────────────────────────────────────────┘  │
      └──────┴──────────────────────────────────────────────────────┘
    """

    C = CATPPUCCIN

    def __init__(self, root):
        self._root = root
        self._proc = None
        self._endpoints_data = load_endpoints()
        self._refresh_running = False
        recover_config_if_needed()

        # ── Custom title bar (Linux only) ───────────────────────────
        self._use_custom_titlebar = not IS_WINDOWS
        if self._use_custom_titlebar:
            try:
                root.overrideredirect(True)
                # Center the window
                root.update_idletasks()
                sw = root.winfo_screenwidth()
                sh = root.winfo_screenheight()
                x = (sw - 750) // 2
                y = (sh - 1010) // 2
                root.geometry(f"750x1010+{x}+{y}")
            except Exception:
                self._use_custom_titlebar = False
                root.overrideredirect(False)

        # ── Main container ──────────────────────────────────────────
        main = tk.Frame(root, bg=self.C["base"])
        main.pack(fill="both", expand=True)

        # Title bar
        if self._use_custom_titlebar:
            version = CHANGELOG[0][0] if CHANGELOG else "0.0.0"
            self._titlebar = TitleBar(root, version=version)
            self._titlebar.pack(fill="x")

        # Horizontal layout: Sidebar + Content
        body = tk.Frame(main, bg=self.C["base"])
        body.pack(fill="both", expand=True)

        # Left sidebar
        self._sidebar = Sidebar(body, on_navigate=self._on_sidebar_nav, collapsed=True)
        self._sidebar.pack(side="left", fill="y")

        # Right content area (scrollable)
        content_outer = tk.Frame(body, bg=self.C["base"])
        content_outer.pack(side="left", fill="both", expand=True)
        content_outer.rowconfigure(0, weight=1)
        content_outer.columnconfigure(0, weight=1)

        # Canvas for scrolling
        self._canvas = tk.Canvas(content_outer, bg=self.C["base"], highlightthickness=0)
        scrollbar = AutoScrollbar(
            content_outer, orient="vertical", command=self._canvas.yview
        )
        self._content = tk.Frame(self._canvas, bg=self.C["base"], padx=16, pady=8)
        self._content.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.create_window((0, 0), window=self._content, anchor="nw")
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Bind mousewheel to canvas
        self._canvas.bind(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
        )
        self._canvas.bind(
            "<Button-4>", lambda e: self._canvas.yview_scroll(-1, "units")
        )
        self._canvas.bind("<Button-5>", lambda e: self._canvas.yview_scroll(1, "units"))

        # ── Detection ───────────────────────────────────────────────
        self._cli_info = detect_codex_cli()
        self._desktop_info = detect_codex_desktop()
        self._missing = []
        if not self._cli_info:
            self._missing.append("cli")
        if not self._desktop_info[0]:
            self._missing.append("desktop")

        # ── Build content sections ──────────────────────────────────
        self._build_status_card()
        self._build_ops_card()
        self._build_provider_card()
        self._build_launch_card()
        self._build_experimental_card()
        self._build_log_console()
        self._build_bottom_bar()

        self._update_status_card()

        # ── Auth check ──────────────────────────────────────────────
        threading.Thread(target=self._check_auth_async, daemon=True).start()

        # ── Initialize combos ───────────────────────────────────────
        self._rebuild_combo()
        self._log_dependency_status()
        self._start_watcher()

    # ══════════════════════════════════════════════════════════════════
    # Content builders
    # ══════════════════════════════════════════════════════════════════

    def _build_status_card(self):
        """Build the Status card (CLI/Desktop detection + Auth)."""
        self._status_card = StatusCard(self._content, title="Status")
        self._status_card.pack(fill="x", pady=(0, 8))

        # Auth row (added later with async update)
        self._auth_row = tk.Frame(self._status_card.content, bg=self.C["surface0"])
        self._auth_row.pack(fill="x", pady=(4, 0))

        self._auth_label = tk.Label(
            self._auth_row,
            text="Checking auth...",
            fg=self.C["dim"],
            bg=self.C["surface0"],
            font=(FONT_UI, SIZE_SMALL),
        )
        self._auth_label.pack(side="left")

        self._relogin_btn = tk.Button(
            self._auth_row,
            text="Re-login",
            bg=self.C["surface1"],
            fg=self.C["subtext"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["text"],
            relief="flat",
            bd=0,
            padx=8,
            pady=2,
            font=(FONT_UI, SIZE_TINY),
            command=self._codex_relogin,
            state="disabled",
            cursor="hand2",
        )
        self._relogin_btn.pack(side="right")

    def _update_status_card(self):
        """Update status indicators after detection."""
        # Clear existing status items
        for w in self._status_card._status_row.winfo_children():
            w.destroy()

        if self._cli_info:
            cli_path, cli_ver = self._cli_info
            self._status_card.add_status_item(
                "✓", f"Codex CLI  {cli_ver}  ({cli_path})", "green"
            )
        else:
            self._status_card.add_status_item("✗", "Codex CLI — not found", "yellow")

        if self._desktop_info[0]:
            label = "MSIX (Store)" if self._desktop_info[1] else self._desktop_info[0]
            self._status_card.add_status_item("✓", f"Codex Desktop  ({label})", "green")
        else:
            self._status_card.add_status_item(
                "✗", "Codex Desktop — not found", "yellow"
            )

    def _build_ops_card(self):
        """Build the operations bar (Refresh Models, Backup, Import)."""
        ops = tk.Frame(self._content, bg=self.C["base"])
        ops.pack(fill="x", pady=(0, 8))

        self._refresh_all_btn = tk.Button(
            ops,
            text="🔄 Refresh Models",
            bg=self.C["surface1"],
            fg=self.C["text"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["accent"],
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            font=(FONT_UI, SIZE_SMALL),
            command=self._refresh_all_models,
            cursor="hand2",
        )
        self._refresh_all_btn.pack(side="left", padx=(0, 6))
        add_hover_effect(self._refresh_all_btn, self.C["surface2"])

        tk.Button(
            ops,
            text="📦 Backup Profile",
            bg=self.C["surface1"],
            fg=self.C["text"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["accent"],
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            font=(FONT_UI, SIZE_SMALL),
            command=self._backup_profile,
            cursor="hand2",
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            ops,
            text="📥 Import Profile",
            bg=self.C["surface1"],
            fg=self.C["text"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["accent"],
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            font=(FONT_UI, SIZE_SMALL),
            command=self._import_profile,
            cursor="hand2",
        ).pack(side="left")

    def _build_provider_card(self):
        """Build the Provider card (endpoint + model selectors)."""
        self._provider_card = ProviderCard(self._content)
        self._provider_card.pack(fill="x", pady=(0, 8))

        bg = self.C["surface0"]

        # Endpoint combobox
        self._combo_ep = ttk.Combobox(
            self._provider_card.ep_combo_frame, state="readonly", width=28
        )
        self._combo_ep.pack(fill="x", pady=(2, 0))
        self._combo_ep.bind(
            "<<ComboboxSelected>>", lambda e: self._on_endpoint_changed()
        )

        # Model combobox
        self._combo_model = ttk.Combobox(
            self._provider_card.model_combo_frame, state="readonly", width=28
        )
        self._combo_model.pack(fill="x", pady=(2, 0))

        # Sandbox combobox
        self._combo_sandbox = ttk.Combobox(
            self._provider_card.sandbox_frame,
            values=["Read-only", "Workspace", "Full Access"],
            state="readonly",
            width=12,
        )
        self._combo_sandbox.set("Workspace")
        self._combo_sandbox.pack(fill="x", pady=(2, 0))

        # Approval combobox
        self._combo_approval = ttk.Combobox(
            self._provider_card.approval_frame,
            values=["Untrusted", "On Request", "Never (Full Auto)"],
            state="readonly",
            width=16,
        )
        self._combo_approval.set("On Request")
        self._combo_approval.pack(fill="x", pady=(2, 0))

    def _build_launch_card(self):
        """Build the Launch card (primary action buttons)."""
        self._launch_card = LaunchCard(self._content)
        self._launch_card.pack(fill="x", pady=(0, 8))

        bg = self.C["surface0"]

        # Primary row: Launch Desktop + Launch CLI
        self._btn_desktop = tk.Button(
            self._launch_card.primary_row,
            text="🚀 Launch Desktop",
            bg=self.C["accent"],
            fg=self.C["base"],
            activebackground=self.C["blue"],
            activeforeground=self.C["base"],
            relief="flat",
            bd=0,
            padx=16,
            pady=8,
            font=(FONT_UI, SIZE_BODY, "bold"),
            command=lambda: self._launch("desktop"),
            cursor="hand2",
        )
        if "desktop" in self._missing:
            self._btn_desktop.configure(
                state="disabled", bg=self.C["surface0"], fg=self.C["dim"]
            )
        self._btn_desktop.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._btn_cli = tk.Button(
            self._launch_card.primary_row,
            text="⚡ Launch CLI",
            bg=self.C["sapphire"],
            fg=self.C["base"],
            activebackground=self.C["teal"],
            activeforeground=self.C["base"],
            relief="flat",
            bd=0,
            padx=16,
            pady=8,
            font=(FONT_UI, SIZE_BODY, "bold"),
            command=lambda: self._launch("cli"),
            cursor="hand2",
        )
        if "cli" in self._missing:
            self._btn_cli.configure(
                state="disabled", bg=self.C["surface0"], fg=self.C["dim"]
            )
        self._btn_cli.pack(side="left", fill="x", expand=True)

        # Secondary row: Codex Default modes
        self._btn_codex_desktop = tk.Button(
            self._launch_card.secondary_row,
            text="Codex Default (Desktop)",
            bg=self.C["surface1"],
            fg=self.C["text"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["accent"],
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            font=(FONT_UI, SIZE_SMALL),
            command=lambda: self._launch_codex_default("desktop"),
            cursor="hand2",
        )
        if "desktop" in self._missing:
            self._btn_codex_desktop.configure(state="disabled", fg=self.C["dim"])
        self._btn_codex_desktop.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._btn_codex_cli = tk.Button(
            self._launch_card.secondary_row,
            text="Codex Default (CLI)",
            bg=self.C["surface1"],
            fg=self.C["text"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["accent"],
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            font=(FONT_UI, SIZE_SMALL),
            command=lambda: self._launch_codex_default("cli"),
            cursor="hand2",
        )
        if "cli" in self._missing:
            self._btn_codex_cli.configure(state="disabled", fg=self.C["dim"])
        self._btn_codex_cli.pack(side="left", fill="x", expand=True)

        # Create spinner wrappers
        self._desktop_spinner = SpinnerButton(
            self._btn_desktop, "🚀 Launch Desktop", "Launching Desktop..."
        )
        self._cli_spinner = SpinnerButton(
            self._btn_cli, "⚡ Launch CLI", "Launching CLI..."
        )

    def _build_experimental_card(self):
        """Build the collapsible Experimental toggles card."""
        self._exp_card = Card(self._content, title="Experimental")
        self._exp_card.pack(fill="x", pady=(0, 8))
        bg = self.C["surface0"]

        # Toggle row
        toggle_row = tk.Frame(self._exp_card.content, bg=bg)
        toggle_row.pack(fill="x")

        self._var_caveman = tk.BooleanVar(value=True)
        self._var_rtk = tk.BooleanVar(value=True)
        self._var_auto_compact = tk.BooleanVar(value=False)
        self._var_adaptive_compact = tk.BooleanVar(value=False)
        self._var_tool_truncation = tk.BooleanVar(value=False)

        toggles = [
            ("Caveman Mode", self._var_caveman),
            ("RTK Compression", self._var_rtk),
            ("Auto Compact", self._var_auto_compact),
            ("Adaptive Compact", self._var_adaptive_compact),
            ("Tool Truncation", self._var_tool_truncation),
        ]

        for i, (text, var) in enumerate(toggles):
            cb = tk.Checkbutton(
                toggle_row,
                text=text,
                variable=var,
                bg=bg,
                fg=self.C["text"],
                selectcolor=self.C["surface1"],
                activebackground=bg,
                activeforeground=self.C["accent"],
                font=(FONT_UI, SIZE_SMALL),
                cursor="hand2",
            )
            cb.pack(side="left", padx=(0, 12))

    def _build_log_console(self):
        """Build the syntax-colored log console."""
        self._log_console = LogConsole(self._content, height=7)
        self._log_console.pack(fill="both", expand=True, pady=(0, 8))

    def _build_bottom_bar(self):
        """Build the bottom action bar."""
        bb = tk.Frame(self._content, bg=self.C["base"])
        bb.pack(fill="x", pady=(0, 4))

        self._start_proxy_btn = tk.Button(
            bb,
            text="▶ Start Proxy",
            bg=self.C["surface1"],
            fg=self.C["green"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["green"],
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            font=(FONT_UI, SIZE_SMALL),
            command=self._start_proxy_only,
            cursor="hand2",
        )
        self._start_proxy_btn.pack(side="left")
        add_hover_effect(self._start_proxy_btn, self.C["surface2"])

        self._restart_btn = tk.Button(
            bb,
            text="↻ Restart Proxy",
            bg=self.C["surface1"],
            fg=self.C["yellow"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["yellow"],
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            font=(FONT_UI, SIZE_SMALL),
            command=self._restart_proxy,
            state="disabled",
            cursor="hand2",
        )
        self._restart_btn.pack(side="left", padx=(4, 0))

        self._kill_btn = tk.Button(
            bb,
            text="✕ Kill && Cleanup",
            bg=self.C["surface1"],
            fg=self.C["red"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["red"],
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            font=(FONT_UI, SIZE_SMALL),
            command=self._kill,
            state="disabled",
            cursor="hand2",
        )
        self._kill_btn.pack(side="left", padx=(4, 0))

        tk.Button(
            bb,
            text="📂 View Log",
            bg=self.C["surface1"],
            fg=self.C["subtext"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["text"],
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            font=(FONT_UI, SIZE_SMALL),
            command=self._open_proxy_log_dir,
            cursor="hand2",
        ).pack(side="left", padx=(4, 0))

        tk.Button(
            bb,
            text="✕ Close",
            bg=self.C["surface1"],
            fg=self.C["dim"],
            activebackground=self.C["surface2"],
            activeforeground=self.C["text"],
            relief="flat",
            bd=0,
            padx=10,
            pady=4,
            font=(FONT_UI, SIZE_SMALL),
            command=self._do_close,
            cursor="hand2",
        ).pack(side="right")

    # ══════════════════════════════════════════════════════════════════
    # Logging (delegates to LogConsole)
    # ══════════════════════════════════════════════════════════════════

    def log(self, msg):
        self._root.after(0, self._log_console.log, msg)

    # ══════════════════════════════════════════════════════════════════
    # Sidebar navigation
    # ══════════════════════════════════════════════════════════════════

    def _on_sidebar_nav(self, item_id):
        """Handle sidebar navigation clicks."""
        handlers = {
            "endpoints": self._open_mgr,
            "monitor": self._open_monitoring,
            "bgp": self._open_bgp,
            "usage": self._open_usage,
            "benchmark": self._open_benchmark,
            "history": self._open_history,
            "sessions": self._open_session_manager,
            "oauth": self._edit_oauth_secrets,
            "updater": self._open_updater,
            "changelog": self._show_changelog,
        }
        handler = handlers.get(item_id)
        if handler:
            handler()

    # ══════════════════════════════════════════════════════════════════
    # Proxy management
    # ══════════════════════════════════════════════════════════════════

    def _restart_proxy(self):
        self._kill()
        ep_name = self._combo_ep.get()
        if not ep_name:
            ep_name = load_endpoints().get("default")
        if not ep_name:
            self.log("No endpoint selected.")
            return
        ep = get_endpoint(ep_name)
        if not ep:
            self.log(f"Endpoint '{ep_name}' not found.")
            return
        ep = dict(ep)
        ep["caveman_mode"] = self._var_caveman.get()
        ep["rtk_compression"] = self._var_rtk.get()
        ep["auto_compact"] = self._var_auto_compact.get()
        ep["adaptive_compact"] = self._var_adaptive_compact.get()
        ep["tool_output_truncation"] = self._var_tool_truncation.get()
        time.sleep(0.3)
        start_proxy_for(ep, self.log)
        self.log(f"Proxy restarted for {ep_name}")

    def _start_proxy_only(self):
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
        try:
            if self._use_custom_titlebar:
                self._titlebar.set_proxy_starting()
            proxy_port = start_proxy_for(ep, self.log)
            self.log(f"Proxy started for {ep_name} on :{proxy_port}")
            if self._use_custom_titlebar:
                self._titlebar.set_proxy_status(True, f":{proxy_port}")
            self._restart_btn.configure(state="normal")
            self._kill_btn.configure(state="normal")
        except RuntimeError as e:
            self._root.after(0, lambda: messagebox.showerror("Proxy Failed", str(e)))
            if self._use_custom_titlebar:
                self._titlebar.set_proxy_status(False)

    # ══════════════════════════════════════════════════════════════════
    # Auth
    # ══════════════════════════════════════════════════════════════════

    def _check_auth_async(self):
        status, msg = check_codex_auth()
        self._root.after(0, lambda: self._update_auth_status(status, msg))

    def _update_auth_status(self, status, msg):
        if status == "logged_in":
            self._auth_label.configure(text=f"✓ Auth: {msg}", fg=self.C["green"])
            self._relogin_btn.configure(
                state="normal" if "cli" not in self._missing else "disabled"
            )
        elif status == "not_installed":
            self._auth_label.configure(
                text="Auth: N/A (CLI not installed)", fg=self.C["dim"]
            )
        else:
            self._auth_label.configure(text=f"⚠ Auth: {msg}", fg=self.C["yellow"])
            self._relogin_btn.configure(
                state="normal" if "cli" not in self._missing else "disabled"
            )

    def _codex_relogin(self):
        self.log("Opening codex login in terminal...")
        term = detect_terminal()
        if not term:
            self.log("ERROR: no terminal emulator found for re-login")
            return
        term_name, term_args, term_path = term
        cmd_parts = [term_name] + term_args + ["codex", "login"]
        if IS_WINDOWS:
            subprocess.Popen(
                cmd_parts, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            subprocess.Popen(cmd_parts, preexec_fn=os.setsid)
        self.log("Login flow started in terminal. Re-checking auth in 30s...")
        self._auth_label.configure(
            text="Auth: waiting for login...", fg=self.C["yellow"]
        )
        threading.Thread(
            target=lambda: (time.sleep(30), self._check_auth_async()), daemon=True
        ).start()

    # ══════════════════════════════════════════════════════════════════
    # Combo management
    # ══════════════════════════════════════════════════════════════════

    def _rebuild_combo(self):
        self._endpoints_data = load_endpoints()
        ep_names = [e["name"] for e in self._endpoints_data["endpoints"]]
        bgp_names = [f"🔀 {p['name']}" for p in load_bgp_pools().get("pools", [])]
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
        is_bgp = name.startswith("🔀 ")
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

    # ══════════════════════════════════════════════════════════════════
    # Latency check
    # ══════════════════════════════════════════════════════════════════

    def _check_latency(self, ep_name):
        latency_label = self._provider_card.latency_label
        latency_label.configure(text="...", fg=self.C["dim"])
        is_bgp = ep_name.startswith("🔀 ")
        if is_bgp or not ep_name:
            latency_label.configure(text=" -- ", fg=self.C["dim"])
            return
        ep = get_endpoint(ep_name)
        if not ep:
            latency_label.configure(text=" -- ", fg=self.C["dim"])
            return

        def _run():
            lat = check_provider_latency(ep)

            def _update():
                if lat is None:
                    latency_label.configure(text=" -- ", fg=self.C["dim"])
                elif lat < 1.0:
                    latency_label.configure(text=f"{lat:.2f}s", fg=self.C["green"])
                elif lat < 3.0:
                    latency_label.configure(text=f"{lat:.2f}s", fg=self.C["yellow"])
                else:
                    latency_label.configure(text=f"{lat:.2f}s", fg=self.C["red"])

            self._root.after(0, _update)

        threading.Thread(target=_run, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════
    # Window openers (themed)
    # ══════════════════════════════════════════════════════════════════

    def _on_endpoints_updated(self):
        self._rebuild_combo()

    def _open_mgr(self):
        XEndpointMgr(self._root, on_update=self._on_endpoints_updated)

    def _open_bgp(self):
        XBGPPoolMgr(self._root, on_update=self._on_endpoints_updated)

    def _open_monitoring(self):
        XAIMonitoringWindow(self._root)

    def _open_usage(self):
        XUsageWindow(self._root)

    def _open_history(self):
        XRequestHistoryWindow(self._root)

    def _open_session_manager(self):
        XSessionManagerWindow(self._root)

    def _open_benchmark(self):
        XBenchmarkWindow(self._root)

    def _open_updater(self):
        if not IS_WINDOWS:
            XCodexUpdaterWindow(self._root)

    def _open_proxy_log_dir(self):
        log_dir = str(PROXY_CONFIG_DIR)
        if IS_WINDOWS:
            req_log = PROXY_CONFIG_DIR / "requests.log"
            os.startfile(str(req_log) if req_log.exists() else log_dir)
        else:
            subprocess.Popen(["xdg-open", log_dir])

    # ══════════════════════════════════════════════════════════════════
    # OAuth secrets
    # ══════════════════════════════════════════════════════════════════

    def _edit_oauth_secrets(self):
        """Open the OAuth secrets dialog (delegates to original GUI module)."""
        # Re-use the original dialog but wrap in themed toplevel
        dlg = ThemedToplevel(self._root)
        dlg.title("OAuth Secrets & Credentials")
        dlg.geometry("620x650")
        dlg.transient(self._root)
        dlg.grab_set()
        # Delegate to a simpler themed version
        from gui.launcher import LauncherWin

        # Create a minimal adapter to reuse OAuth editing logic
        import tkinter.simpledialog

        data = load_oauth_secrets()
        if not data:
            data = {
                "antigravity": {
                    "client_id": "",
                    "client_secret": "",
                },
                "gemini_cli": {
                    "client_id": "",
                    "client_secret": "",
                },
            }
        # Build a simplified OAuth editor using the theme
        self._build_oauth_dialog(dlg, data)

    def _build_oauth_dialog(self, dlg, data):
        """Build a themed OAuth secrets dialog."""
        C = self.C
        bg = C["surface0"]

        canvas = tk.Canvas(dlg, bg=C["base"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(dlg, orient="vertical", command=canvas.yview)
        frame = tk.Frame(canvas, bg=bg, padx=16, pady=12)
        frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Google OAuth section
        tk.Label(
            frame,
            text="Google OAuth 2.0 Client Credentials",
            fg=C["accent"],
            bg=bg,
            font=(FONT_UI, SIZE_HEADING, "bold"),
        ).pack(anchor="w")
        tk.Label(
            frame,
            text=str(OAUTH_SECRETS_PATH),
            fg=C["dim"],
            bg=bg,
            font=(FONT_UI, SIZE_TINY),
        ).pack(anchor="w", pady=(0, 8))

        fields = {}
        google_token_dir = str(PROXY_CONFIG_DIR)
        for section_key, section_label, oauth_prov, token_file in [
            (
                "antigravity",
                "Antigravity (CloudCode)",
                "google-antigravity",
                "google-antigravity-oauth-token.json",
            ),
            ("gemini_cli", "Gemini CLI", "google-cli", "google-cli-oauth-token.json"),
        ]:
            tk.Label(
                frame,
                text=section_label,
                fg=C["text"],
                bg=bg,
                font=(FONT_UI, SIZE_SMALL, "bold"),
            ).pack(anchor="w", pady=(8, 2))

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
            token_color = C["green"] if has_token else C["yellow"]
            tk.Label(
                frame,
                text=token_status,
                fg=token_color,
                bg=bg,
                font=(FONT_UI, SIZE_TINY),
            ).pack(anchor="w", padx=(8, 0))

            btn_row = tk.Frame(frame, bg=bg)
            btn_row.pack(fill="x", pady=(2, 4))
            tk.Button(
                btn_row,
                text="Re-OAuth",
                bg=C["surface1"],
                fg=C["text"],
                activebackground=C["surface2"],
                relief="flat",
                bd=0,
                padx=8,
                pady=2,
                font=(FONT_UI, SIZE_TINY),
                command=lambda p=oauth_prov: google_oauth_flow(dlg, oauth_provider=p),
            ).pack(side="left", padx=(0, 4))

            for fk, fl in [
                ("client_id", "Client ID"),
                ("client_secret", "Client Secret"),
            ]:
                row = tk.Frame(frame, bg=bg)
                row.pack(fill="x", pady=1)
                tk.Label(
                    row,
                    text=fl + ":",
                    fg=C["dim"],
                    bg=bg,
                    width=14,
                    anchor="w",
                    font=(FONT_UI, SIZE_TINY),
                ).pack(side="left")
                entry = ttk.Entry(row, width=50)
                entry.insert(0, sec.get(fk, ""))
                entry.pack(side="left", fill="x", expand=True)
                if fk == "client_secret":
                    entry.configure(show="*")
                fields[(section_key, fk)] = entry

        # Codebuff section
        tk.Label(
            frame,
            text="\nFreebuff / Codebuff",
            fg=C["accent"],
            bg=bg,
            font=(FONT_UI, SIZE_HEADING, "bold"),
        ).pack(anchor="w", pady=(8, 0))
        tk.Button(
            frame,
            text="Re-OAuth (GitHub Login)",
            bg=C["surface1"],
            fg=C["text"],
            activebackground=C["surface2"],
            relief="flat",
            bd=0,
            padx=8,
            pady=2,
            font=(FONT_UI, SIZE_TINY),
            command=lambda: codebuff_oauth_flow(dlg),
        ).pack(anchor="w", pady=(4, 0))

        # Kiro section
        tk.Label(
            frame,
            text="\nKiro (AWS CodeWhisperer)",
            fg=C["accent"],
            bg=bg,
            font=(FONT_UI, SIZE_HEADING, "bold"),
        ).pack(anchor="w", pady=(8, 0))
        kiro_row = tk.Frame(frame, bg=bg)
        kiro_row.pack(fill="x", pady=(4, 0))
        tk.Button(
            kiro_row,
            text="Re-OAuth (Builder ID)",
            bg=C["surface1"],
            fg=C["text"],
            activebackground=C["surface2"],
            relief="flat",
            bd=0,
            padx=8,
            pady=2,
            font=(FONT_UI, SIZE_TINY),
            command=lambda: kiro_oauth_flow(dlg),
        ).pack(side="left", padx=(0, 4))

        # Save / Cancel
        btnf = tk.Frame(frame, bg=bg)
        btnf.pack(fill="x", pady=(16, 0))
        tk.Button(
            btnf,
            text="Cancel",
            bg=C["surface1"],
            fg=C["dim"],
            activebackground=C["surface2"],
            relief="flat",
            bd=0,
            padx=12,
            pady=4,
            font=(FONT_UI, SIZE_SMALL),
            command=dlg.destroy,
        ).pack(side="right", padx=(4, 0))
        save_btn = tk.Button(
            btnf,
            text="Save",
            bg=C["accent"],
            fg=C["base"],
            activebackground=C["blue"],
            relief="flat",
            bd=0,
            padx=12,
            pady=4,
            font=(FONT_UI, SIZE_SMALL, "bold"),
        )
        save_btn.pack(side="right")

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
            dlg.destroy()

        save_btn.configure(command=_save)

    # ══════════════════════════════════════════════════════════════════
    # Changelog
    # ══════════════════════════════════════════════════════════════════

    def _show_changelog(self):
        dlg = ThemedToplevel(self._root)
        dlg.title("Changelog")
        dlg.geometry("640x500")
        dlg.transient(self._root)

        from tkinter import scrolledtext

        txt = scrolledtext.ScrolledText(
            dlg,
            bg=self.C["base"],
            fg=self.C["text"],
            font=(FONT_MONO, SIZE_MONO),
            wrap="word",
            padx=12,
            pady=8,
        )
        txt.pack(fill="both", expand=True)

        for ver, date, items in CHANGELOG[:10]:
            txt.insert("end", f"v{ver} ({date})\n", "heading")
            for item in items:
                txt.insert("end", f"  • {item}\n", "item")
            txt.insert("end", "\n")

        txt.tag_configure(
            "heading", foreground=self.C["accent"], font=(FONT_UI, SIZE_BODY, "bold")
        )
        txt.tag_configure("item", foreground=self.C["subtext"])
        txt.configure(state="disabled")

    # ══════════════════════════════════════════════════════════════════
    # Profile operations
    # ══════════════════════════════════════════════════════════════════

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
        if not messagebox.askyesno(
            "Import",
            "Importing will replace the current endpoints and Codex config. Continue?",
        ):
            return
        try:
            import_profile_bundle(filename)
            self._rebuild_combo()
            self.log(f"Profile imported from {filename}")
        except Exception as e:
            messagebox.showerror("Import Failed", str(e))

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
            messagebox.showwarning(
                "Refresh",
                "Some providers could not auto-fetch models.\n\n" + "\n".join(failed),
            )
        self._refresh_running = False
        self._refresh_all_btn.configure(state="normal")

    def _finish_refresh_error(self, err):
        messagebox.showerror("Refresh Failed", err)
        self._refresh_running = False
        self._refresh_all_btn.configure(state="normal")

    # ══════════════════════════════════════════════════════════════════
    # Launch
    # ══════════════════════════════════════════════════════════════════

    def _set_busy(self, busy, proxy_alive=False):
        state = "disabled" if busy else "normal"
        self._btn_desktop.configure(state=state)
        self._btn_cli.configure(state=state)
        self._btn_codex_desktop.configure(state=state)
        self._btn_codex_cli.configure(state=state)
        self._kill_btn.configure(state="normal" if proxy_alive else "disabled")
        self._restart_btn.configure(state="normal" if proxy_alive else "disabled")

    def _launch(self, mode):
        ep_name = self._combo_ep.get()
        if not ep_name:
            messagebox.showwarning("Launch", "Select an endpoint first.")
            return
        is_bgp = ep_name.startswith("🔀 ")
        ep = None
        bgp_pool = None
        if is_bgp:
            bgp_name = ep_name[2:]
            for p in load_bgp_pools().get("pools", []):
                if p["name"] == bgp_name:
                    bgp_pool = p
                    break
            if not bgp_pool:
                messagebox.showerror("Launch", f"BGP pool '{bgp_name}' not found")
                return
        else:
            ep = get_endpoint(ep_name)
            if not ep:
                messagebox.showerror("Launch", f"Endpoint '{ep_name}' not found")
                return
            ep = dict(ep)

        model = self._combo_model.get()

        # Apply toggles
        if ep:
            ep["caveman_mode"] = self._var_caveman.get()
            ep["rtk_compression"] = self._var_rtk.get()
            ep["auto_compact"] = self._var_auto_compact.get()
            ep["adaptive_compact"] = self._var_adaptive_compact.get()
            ep["tool_output_truncation"] = self._var_tool_truncation.get()

        self._set_busy(True)
        if self._use_custom_titlebar:
            self._titlebar.set_proxy_starting()

        # Spinner
        spinner = self._desktop_spinner if mode == "desktop" else self._cli_spinner
        spinner.start()

        def _worker():
            try:
                if bgp_pool:
                    self._run_bgp(bgp_pool, model, mode)
                elif ep:
                    self._run(ep, model, mode)
            finally:
                self._root.after(0, lambda: (spinner.stop(), self._set_busy(False)))

        threading.Thread(target=_worker, daemon=True).start()

    def _run(self, ep, model, mode):
        sandbox = self._combo_sandbox.get()
        approval = self._combo_approval.get()
        begin_config_transaction("launch")
        try:
            if ep["backend_type"] == "native":
                write_config_for_native(ep, model)
                proxy_port = None
            else:
                proxy_port = start_proxy_for(ep, self.log)
                write_config_for_translated(ep, model, proxy_port)
            self._launch_codex(mode, sandbox, approval)
        except Exception as e:
            self._root.after(0, lambda: messagebox.showerror("Launch Error", str(e)))
        finally:
            end_config_transaction()

    def _run_bgp(self, pool, model, mode):
        sandbox = self._combo_sandbox.get()
        approval = self._combo_approval.get()
        begin_config_transaction("launch-bgp")
        try:
            port, bgp_ep = start_bgp_proxy(pool, model, self.log)
            write_config_for_translated(bgp_ep, model, port)
            self._launch_codex(mode, sandbox, approval)
        except Exception as e:
            self._root.after(
                0, lambda: messagebox.showerror("BGP Launch Error", str(e))
            )
        finally:
            end_config_transaction()

    def _launch_codex(self, mode, sandbox, approval):
        if mode == "desktop":
            self._launch_desktop(sandbox, approval)
        else:
            self._launch_cli(sandbox, approval)

    def _launch_desktop(self, sandbox, approval):
        desktop_info = detect_codex_desktop()
        if not desktop_info[0]:
            raise RuntimeError("Codex Desktop not found")
        kill_existing_desktop(self.log)
        time.sleep(1)
        proc = launch_codex_desktop(desktop_info)
        self._proc = proc
        if self._use_custom_titlebar:
            self._root.after(0, lambda: self._titlebar.set_proxy_status(True))
        self.log("Codex Desktop launched")

    def _launch_cli(self, sandbox, approval):
        term = detect_terminal()
        if not term:
            raise RuntimeError("No terminal emulator found")
        term_name, term_args, _ = term
        cmd = ["codex"]
        proc = subprocess.Popen(
            [term_name] + term_args + cmd,
            preexec_fn=os.setsid if not IS_WINDOWS else None,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0,
        )
        self._proc = proc
        self.log("Codex CLI launched")

    def _launch_codex_default(self, mode):
        sandbox = self._combo_sandbox.get()
        approval = self._combo_approval.get()
        begin_config_transaction("launch-default")
        try:
            if mode == "desktop":
                self._launch_desktop(sandbox, approval)
            else:
                self._launch_cli(sandbox, approval)
        except Exception as e:
            self._root.after(0, lambda: messagebox.showerror("Launch Error", str(e)))
        finally:
            end_config_transaction()

    # ══════════════════════════════════════════════════════════════════
    # Kill / Close
    # ══════════════════════════════════════════════════════════════════

    def _kill(self):
        stop_proxy()
        if self._proc and self._proc.poll() is None:
            try:
                if IS_WINDOWS:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                        capture_output=True,
                        timeout=10,
                    )
                else:
                    import signal

                    try:
                        pgid = os.getpgid(self._proc.pid)
                        os.killpg(pgid, signal.SIGTERM)
                        time.sleep(0.5)
                        try:
                            os.killpg(pgid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            pass
                    except (ProcessLookupError, PermissionError):
                        pass
            except Exception as e:
                self.log(f"Kill error: {e}")
            self._proc = None
        self.log("Stopped proxy and cleaned up")
        if self._use_custom_titlebar:
            self._titlebar.set_proxy_status(False)
        self._kill_btn.configure(state="disabled")
        self._restart_btn.configure(state="disabled")

    def _do_close(self):
        self._kill()
        safe_cleanup_owned(self.log)
        self._root.destroy()

    # ══════════════════════════════════════════════════════════════════
    # Watcher
    # ══════════════════════════════════════════════════════════════════

    def _start_watcher(self):
        cfg = load_monitoring_config()
        if not cfg.get("enabled"):
            return
        self._watcher = HealthWatcher(
            on_failure=lambda c: self.log(
                f"[AI Monitor] Proxy unresponsive (failures={c})"
            ),
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
            self.log(f"[AI Monitor] Killing stale processes (trigger: {trigger})")
            self._kill()
            self._root.after(0, self._restart_proxy_from_watcher)
        else:
            self.log(f"[AI Monitor] Alert: {action} (trigger: {trigger})")

    def _restart_proxy_from_watcher(self):
        try:
            ep_name = self._combo_ep.get() or load_endpoints().get("default")
            if not ep_name:
                return
            ep = get_endpoint(ep_name)
            if not ep:
                return
            start_proxy_for(dict(ep), self.log)
        except Exception as e:
            self.log(f"[AI Monitor] Proxy restart failed: {e}")

    # ══════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════

    def _log_dependency_status(self):
        if self._cli_info:
            _, ver = self._cli_info
            self.log(f"✓ Codex CLI detected ({ver})")
        else:
            self.log("✗ Codex CLI NOT found — CLI launch disabled")
        if self._desktop_info[0]:
            label = "MSIX (Store)" if self._desktop_info[1] else self._desktop_info[0]
            self.log(f"✓ Codex Desktop detected ({label})")
        else:
            self.log("✗ Codex Desktop NOT found — Desktop launch disabled")
        if self._missing:
            self.log("Install missing tools before using the launcher")
        else:
            self.log("All dependencies OK")

    def _show_install_guide(self, target):
        messagebox.showinfo(
            "Install Guide", f"Install {target} from: https://github.com/openai/codex"
        )
