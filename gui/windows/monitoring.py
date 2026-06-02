"""AI Monitoring configuration window."""
import tkinter as tk
from tkinter import ttk

from codex_launcher_lib import (
    PROXY_CONFIG_DIR,
    load_monitoring_config, save_monitoring_config,
    load_incident_store, save_incident_store,
    open_file,
)


class AIMonitoringWindow:
    def __init__(self, parent):
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("AI Monitoring")
        self._dlg.geometry("580x520")
        self._dlg.transient(parent)

        self._cfg = load_monitoring_config()
        self._store = load_incident_store()

        main = ttk.Frame(self._dlg, padding=12)
        main.pack(fill="both", expand=True)

        hdr = ttk.Frame(main)
        hdr.pack(fill="x")
        ttk.Label(hdr, text="AI Monitoring", font=("Segoe UI", 11, "bold")).pack(side="left")
        self._toggle_var = tk.BooleanVar(value=self._cfg.get("enabled", False))
        ttk.Checkbutton(hdr, text="Enabled", variable=self._toggle_var,
                        command=self._on_toggle).pack(side="right")

        frame = ttk.LabelFrame(main, text="Diagnostic Agent", padding=8)
        frame.pack(fill="x", pady=(8, 0))

        grid = ttk.Frame(frame)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="Provider URL:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)
        self._url_entry = ttk.Entry(grid)
        self._url_entry.grid(row=0, column=1, sticky="ew", pady=2)
        self._url_entry.insert(0, self._cfg.get("provider_url", ""))

        ttk.Label(grid, text="Model:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=2)
        self._model_entry = ttk.Entry(grid)
        self._model_entry.grid(row=1, column=1, sticky="ew", pady=2)
        self._model_entry.insert(0, self._cfg.get("model", ""))

        ttk.Label(grid, text="API Key:").grid(row=2, column=0, sticky="e", padx=(0, 6), pady=2)
        key_frame = ttk.Frame(grid)
        key_frame.grid(row=2, column=1, sticky="ew", pady=2)
        self._key_entry = ttk.Entry(key_frame, show="*")
        self._key_entry.pack(side="left", fill="x", expand=True)
        self._key_entry.insert(0, self._cfg.get("api_key", ""))
        self._reveal_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(key_frame, text="Show", variable=self._reveal_key,
                        command=lambda: self._key_entry.configure(show="" if self._reveal_key.get() else "*")).pack(side="left", padx=(4, 0))

        ttk.Label(grid, text="Health Check:").grid(row=3, column=0, sticky="e", padx=(0, 6), pady=2)
        spin_frame = ttk.Frame(grid)
        spin_frame.grid(row=3, column=1, sticky="w", pady=2)
        self._interval_spin = ttk.Spinbox(spin_frame, from_=2, to=30, width=5)
        self._interval_spin.set(self._cfg.get("health_check_interval_s", 5))
        self._interval_spin.pack(side="left")
        ttk.Label(spin_frame, text="seconds").pack(side="left", padx=(4, 0))

        opts_frame = ttk.Frame(frame)
        opts_frame.pack(fill="x", pady=(4, 0))
        self._auto_restart_var = tk.BooleanVar(value=self._cfg.get("auto_restart_proxy", True))
        ttk.Checkbutton(opts_frame, text="Auto-restart proxy on crash",
                        variable=self._auto_restart_var).pack(side="left")
        self._auto_switch_var = tk.BooleanVar(value=self._cfg.get("auto_switch_provider", False))
        ttk.Checkbutton(opts_frame, text="Auto-switch provider on repeated failure",
                        variable=self._auto_switch_var).pack(side="left", padx=(12, 0))

        ttk.Button(frame, text="Save Configuration", command=self._on_save).pack(pady=(8, 0))

        stats = self._store.get("stats", {"ai_calls": 0, "tokens_used": 0})
        stats_text = (f"AI diagnostic calls: {stats.get('ai_calls', 0)}  |  "
                      f"Tokens used: {stats.get('tokens_used', 0):,}  |  "
                      f"Known patterns: {len(self._store.get('incidents', {}))}")
        ttk.Label(main, text=stats_text, font=("Segoe UI", 8)).pack(anchor="w", pady=(8, 0))

        inc_frame = ttk.LabelFrame(main, text="Recent Incidents", padding=4)
        inc_frame.pack(fill="both", expand=True, pady=(4, 0))
        self._inc_text = tk.Text(inc_frame, height=8, wrap="word", state="disabled")
        inc_sb = ttk.Scrollbar(inc_frame, orient="vertical", command=self._inc_text.yview)
        self._inc_text.configure(yscrollcommand=inc_sb.set)
        self._inc_text.pack(side="left", fill="both", expand=True)
        inc_sb.pack(side="right", fill="y")
        self._refresh_incidents()

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_frame, text="View Monitoring Log",
                   command=lambda: open_file(str(PROXY_CONFIG_DIR / "monitoring.log"))).pack(side="left")
        ttk.Button(btn_frame, text="Clear Incident Store", command=self._on_clear_store).pack(side="left", padx=(8, 0))
        ttk.Button(btn_frame, text="Close", command=self._dlg.destroy).pack(side="right")

    def _on_toggle(self):
        self._cfg["enabled"] = self._toggle_var.get()
        save_monitoring_config(self._cfg)

    def _on_save(self):
        self._cfg["provider_url"] = self._url_entry.get().strip()
        self._cfg["model"] = self._model_entry.get().strip()
        self._cfg["api_key"] = self._key_entry.get().strip()
        try:
            self._cfg["health_check_interval_s"] = int(self._interval_spin.get())
        except ValueError:
            pass
        self._cfg["auto_restart_proxy"] = self._auto_restart_var.get()
        self._cfg["auto_switch_provider"] = self._auto_switch_var.get()
        save_monitoring_config(self._cfg)
        self._inc_text.configure(state="normal")
        self._inc_text.delete("1.0", "end")
        self._inc_text.insert("end", "Configuration saved.\n")
        self._inc_text.configure(state="disabled")

    def _on_clear_store(self):
        save_incident_store({"version": 1, "incidents": {}, "stats": {"ai_calls": 0, "tokens_used": 0}})
        self._store = {"version": 1, "incidents": {}, "stats": {"ai_calls": 0, "tokens_used": 0}}
        self._refresh_incidents()

    def _refresh_incidents(self):
        lines = []
        for pattern, inc in sorted(self._store.get("incidents", {}).items(),
                                    key=lambda x: x[1].get("last_seen", ""), reverse=True):
            sc = inc.get("success_count", 0)
            fc = inc.get("fail_count", 0)
            rate = sc / max(sc + fc, 1)
            lines.append(
                f"[{inc.get('last_seen', '?')[:16]}] {pattern}\n"
                f"  fix={inc.get('fix', '?')}  success_rate={rate:.0%}  seen={inc.get('occurrences', 0)}x\n"
            )
        if not lines:
            lines.append("No incidents recorded yet.\n\nEnable AI Monitoring and use Codex to populate the store.\n")
        self._inc_text.configure(state="normal")
        self._inc_text.delete("1.0", "end")
        self._inc_text.insert("end", "\n".join(lines))
        self._inc_text.configure(state="disabled")
