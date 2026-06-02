"""Endpoint CRUD dialogs — EditEndpointDialog + EndpointMgr."""
import http.server
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import urllib.error
import urllib.parse
import urllib.request

from gui.oauth_flows import (
    google_oauth_flow, codebuff_oauth_flow, kiro_oauth_flow,
)
from codex_launcher_lib import (
    PROXY_CONFIG_DIR, OAUTH_SECRETS_PATH,
    PROVIDER_PRESETS, ANTIGRAVITY_MODELS, UA,
    safe_name, label_for_backend, normalize_model_id, normalize_base_url,
    parse_model_list,
    load_endpoints, save_endpoints, get_endpoint,
    fetch_models_for_endpoint, run_endpoint_doctor,
    load_oauth_secrets, open_url,
)
from gui.helpers import _show_doctor_results_tk


class EditEndpointDialog:
    def __init__(self, parent, existing_name=None):
        self.result = False
        self._existing_name = existing_name
        self._parent_mgr = parent

        if existing_name:
            self._data = dict(get_endpoint(existing_name) or {})
        else:
            self._data = {
                "name": "", "backend_type": "openai-compat",
                "base_url": "", "api_key": "", "default_model": "",
                "models": [], "provider_preset": "Custom",
            }

        self._dlg = tk.Toplevel(parent)
        title = "Edit Endpoint" if existing_name else "Add Endpoint"
        self._dlg.title(title)
        self._dlg.geometry("680x720")
        self._dlg.transient(parent)
        self._dlg.grab_set()

        main = ttk.Frame(self._dlg, padding=12)
        main.pack(fill="both", expand=True)

        grid = ttk.Frame(main)
        grid.pack(fill="x")

        row_idx = [0]

        def add_field(label, widget_factory):
            ttk.Label(grid, text=label).grid(row=row_idx[0], column=0, sticky="e", padx=(0, 6), pady=2)
            w = widget_factory()
            w.grid(row=row_idx[0], column=1, sticky="ew", pady=2)
            row_idx[0] += 1
            return w

        self._entry_name = add_field("Name:", lambda: ttk.Entry(grid))
        self._entry_name.insert(0, self._data.get("name", ""))

        presets_list = list(PROVIDER_PRESETS.keys())
        if "Custom" in presets_list:
            presets_list.remove("Custom")
            presets_list.sort(key=str.lower)
            presets_list.insert(0, "Custom")
        else:
            presets_list.sort(key=str.lower)

        self._combo_preset = ttk.Combobox(grid, values=presets_list, state="readonly")
        preset = self._data.get("provider_preset", "Custom")
        self._combo_preset.set(preset)
        add_field("Preset:", lambda: self._combo_preset)
        self._combo_preset.bind("<<ComboboxSelected>>", lambda e: self._apply_selected_preset(initial=False))

        backend_types = [
            ("openai-compat", "OpenAI-compatible (needs proxy)"),
            ("anthropic", "Anthropic (needs proxy)"),
            ("command-code", "Command Code (needs proxy)"),
            ("freebuff", "Freebuff - Free DeepSeek/Kimi (needs proxy)"),
            ("gemini-oauth-cli", "Gemini CLI OAuth (needs proxy)"),
            ("gemini-oauth-antigravity", "Antigravity OAuth (needs proxy)"),
            ("kiro-oauth", "Kiro AWS CodeWhisperer (needs proxy)"),
            ("native", "Native OpenAI (no proxy)"),
        ]
        self._combo_type = ttk.Combobox(grid, values=[f"{v} - {l}" for v, l in backend_types], state="readonly")
        bt = self._data.get("backend_type", "openai-compat")
        bt_display = next((f"{v} - {l}" for v, l in backend_types if v == bt), backend_types[0][0] + " - " + backend_types[0][1])
        self._combo_type.set(bt_display)
        add_field("Type:", lambda: self._combo_type)
        self._bt_map = {f"{v} - {l}": v for v, l in backend_types}

        self._entry_url = add_field("Base URL:", lambda: ttk.Entry(grid))
        self._entry_url.insert(0, self._data.get("base_url", ""))

        key_frame = ttk.Frame(grid)
        self._entry_key = ttk.Entry(key_frame, show="*")
        self._entry_key.pack(side="left", fill="x", expand=True)
        self._entry_key.insert(0, self._data.get("api_key", ""))
        self._reveal_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(key_frame, text="Show", variable=self._reveal_var,
                        command=lambda: self._entry_key.configure(show="" if self._reveal_var.get() else "*")).pack(side="left", padx=(4, 0))
        self._oauth_btn = ttk.Button(key_frame, text="OAuth Login", command=self._do_oauth_login)
        self._oauth_btn.pack(side="left", padx=(4, 0))
        add_field("API Key:", lambda: key_frame)

        self._entry_cc_ver = add_field("CC Version:", lambda: ttk.Entry(grid))
        self._entry_cc_ver.insert(0, self._data.get("cc_version", ""))

        reason_frame = ttk.Frame(grid)
        self._reason_var = tk.BooleanVar(value=self._data.get("reasoning_enabled", True))
        self._reason_cb = ttk.Checkbutton(reason_frame, text="Reasoning ON", variable=self._reason_var,
                                          command=self._on_reasoning_toggled)
        self._reason_cb.pack(side="left")
        self._combo_effort = ttk.Combobox(reason_frame, values=["none", "minimal", "low", "medium", "high", "max"],
                                          state="readonly", width=10)
        self._combo_effort.set(self._data.get("reasoning_effort", "medium"))
        self._combo_effort.pack(side="left", padx=(8, 0))
        ttk.Label(reason_frame, text="Effort").pack(side="left", padx=(4, 0))
        add_field("Reasoning:", lambda: reason_frame)
        self._on_reasoning_toggled()

        enhancer_frame = ttk.Frame(grid)
        self._enhancer_var = tk.BooleanVar(value=self._data.get("prompt_enhancer", False))
        self._enhancer_cb = ttk.Checkbutton(enhancer_frame, text="Prompt Enhancer", variable=self._enhancer_var, command=self._on_enhancer_toggled)
        self._enhancer_cb.pack(side="left")
        self._enhancer_status_lbl = ttk.Label(enhancer_frame, text="", foreground="gray")
        self._enhancer_status_lbl.pack(side="left", padx=(6, 0))
        self._enhancer_mode = ttk.Combobox(enhancer_frame, values=["offline", "ai-powered"], state="readonly", width=10)
        self._enhancer_mode.set(self._data.get("prompt_enhancer_mode", "offline"))
        self._enhancer_mode.pack(side="left", padx=(8, 0))
        add_field("Prompt Enhancer:", lambda: enhancer_frame)
        self._on_enhancer_toggled()

        self._entry_enhancer_model = ttk.Entry(grid)
        self._entry_enhancer_model.insert(0, self._data.get("prompt_enhancer_model", ""))
        add_field("Enhancer Model:", lambda: self._entry_enhancer_model)

        self._entry_enhancer_url = ttk.Entry(grid)
        self._entry_enhancer_url.insert(0, self._data.get("prompt_enhancer_url", ""))
        add_field("Enhancer URL:", lambda: self._entry_enhancer_url)

        self._entry_enhancer_key = ttk.Entry(grid, show="*")
        self._entry_enhancer_key.insert(0, self._data.get("prompt_enhancer_key", ""))
        add_field("Enhancer Key:", lambda: self._entry_enhancer_key)

        grid.columnconfigure(1, weight=1)

        ttk.Label(main, text="Models:").pack(anchor="w", pady=(8, 2))

        model_input_frame = ttk.Frame(main)
        model_input_frame.pack(fill="x")
        self._entry_model = ttk.Entry(model_input_frame)
        self._entry_model.pack(side="left", fill="x", expand=True)
        ttk.Button(model_input_frame, text="Add", command=self._add_model).pack(side="left", padx=(4, 0))
        ttk.Button(model_input_frame, text="Bulk Add", command=self._add_models_from_text).pack(side="left", padx=(4, 0))
        ttk.Button(model_input_frame, text="Fetch from API", command=self._fetch_models).pack(side="left", padx=(4, 0))
        ttk.Button(model_input_frame, text="Sync from Preset", command=lambda: self._apply_selected_preset_force()).pack(side="left", padx=(4, 0))
        ttk.Button(model_input_frame, text="Test Endpoint", command=self._diagnose_endpoint).pack(side="left", padx=(4, 0))

        ttk.Label(main, text="Bulk add (one per line or comma-separated):").pack(anchor="w", pady=(4, 0))
        self._bulk_text = tk.Text(main, height=3, wrap="word")
        self._bulk_text.pack(fill="x", pady=(2, 4))

        list_frame = ttk.Frame(main)
        list_frame.pack(fill="both", expand=True)
        self._model_listbox = tk.Listbox(list_frame, height=6)
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self._model_listbox.yview)
        self._model_listbox.configure(yscrollcommand=sb.set)
        self._model_listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._model_listbox.bind("<Double-Button-1>", lambda e: self._remove_selected_model())
        # Clear All button
        list_btn_frame = ttk.Frame(main)
        list_btn_frame.pack(fill="x", pady=(2, 0))
        ttk.Button(list_btn_frame, text="Clear All", command=self._clear_all_models).pack(side="left")
        ttk.Button(list_btn_frame, text="Sort A-Z", command=self._sort_models).pack(side="left", padx=(4, 0))
        for m in self._data.get("models", []):
            self._model_listbox.insert("end", m)

        default_frame = ttk.Frame(main)
        default_frame.pack(fill="x", pady=(4, 0))
        ttk.Label(default_frame, text="Default Model:").pack(side="left")
        self._combo_default = ttk.Combobox(default_frame, state="readonly")
        self._combo_default.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._refresh_default_combo()
        dm = self._data.get("default_model", "")
        if dm:
            self._combo_default.set(dm)

        self._apply_selected_preset(initial=True)

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_frame, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(btn_frame, text="Save", command=self._save).pack(side="right", padx=(8, 0))

    def _on_reasoning_toggled(self):
        state = "readonly" if self._reason_var.get() else "disabled"
        self._combo_effort.configure(state=state)

    def _on_enhancer_toggled(self):
        if self._enhancer_var.get():
            self._enhancer_status_lbl.configure(text="ON", foreground="#2ea043")
        else:
            self._enhancer_status_lbl.configure(text="OFF", foreground="#888888")

    def _apply_selected_preset(self, initial=False):
        preset_name = self._combo_preset.get() or "Custom"
        preset = PROVIDER_PRESETS.get(preset_name, {})
        is_oauth = bool(preset.get("oauth_provider"))
        self._oauth_btn.configure(state="normal" if is_oauth else "disabled")

        if not initial or self._existing_name is None:
            # Auto-fill name if it is a new endpoint and currently empty or matching a preset
            if self._existing_name is None:
                current_name = self._entry_name.get().strip()
                is_preset_name = current_name in PROVIDER_PRESETS or current_name == ""
                if is_preset_name and preset_name != "Custom":
                    self._entry_name.delete(0, "end")
                    self._entry_name.insert(0, preset_name)

            bt = preset.get("backend_type", "openai-compat")
            bt_display = next((k for k, v in self._bt_map.items() if v == bt), list(self._bt_map.keys())[0])
            self._combo_type.set(bt_display)
            self._entry_url.delete(0, "end")
            self._entry_url.insert(0, preset.get("base_url", ""))
            cc_ver = preset.get("cc_version", "")
            if cc_ver and not self._entry_cc_ver.get().strip():
                self._entry_cc_ver.delete(0, "end")
                self._entry_cc_ver.insert(0, cc_ver)
            if preset.get("models") and (not initial or self._model_listbox.size() == 0):
                self._model_listbox.delete(0, "end")
                for mid in preset["models"]:
                    self._model_listbox.insert("end", mid)
                self._refresh_default_combo()
                if preset["models"]:
                    self._combo_default.set(preset["models"][0])

    def _apply_selected_preset_force(self):
        preset_name = self._combo_preset.get() or "Custom"
        preset = PROVIDER_PRESETS.get(preset_name, {})

        # Force sync updates name for new endpoints
        if self._existing_name is None and preset_name != "Custom":
            self._entry_name.delete(0, "end")
            self._entry_name.insert(0, preset_name)

        bt = preset.get("backend_type", "openai-compat")
        bt_display = next((k for k, v in self._bt_map.items() if v == bt), list(self._bt_map.keys())[0])
        self._combo_type.set(bt_display)
        self._entry_url.delete(0, "end")
        self._entry_url.insert(0, preset.get("base_url", ""))
        cc_ver = preset.get("cc_version", "")
        if cc_ver:
            self._entry_cc_ver.delete(0, "end")
            self._entry_cc_ver.insert(0, cc_ver)
        if preset.get("models"):
            self._model_listbox.delete(0, "end")
            for mid in preset["models"]:
                self._model_listbox.insert("end", mid)
            self._refresh_default_combo()
            if preset["models"]:
                self._combo_default.set(preset["models"][0])

    def _add_model(self):
        m = normalize_model_id(self._entry_model.get())
        if m:
            self._model_listbox.insert("end", m)
            self._refresh_default_combo()
            self._entry_model.delete(0, "end")

    def _add_models_from_text(self):
        text = self._bulk_text.get("1.0", "end")
        models = parse_model_list(text)
        existing = set(self._model_listbox.get(i) for i in range(self._model_listbox.size()))
        for mid in models:
            if mid not in existing:
                self._model_listbox.insert("end", mid)
        self._bulk_text.delete("1.0", "end")
        self._refresh_default_combo()

    def _remove_selected_model(self):
        sel = self._model_listbox.curselection()
        if sel:
            self._model_listbox.delete(sel[0])
            self._refresh_default_combo()

    def _clear_all_models(self):
        """Remove all models from the list."""
        if self._model_listbox.size() == 0:
            return
        if messagebox.askyesno("Clear Models", "Remove all models from the list?", parent=self._dlg):
            self._model_listbox.delete(0, "end")
            self._refresh_default_combo()

    def _sort_models(self):
        """Sort all models alphabetically."""
        all_models = list(self._model_listbox.get(i) for i in range(self._model_listbox.size()))
        if len(all_models) < 2:
            return
        all_models.sort(key=str.lower)
        self._model_listbox.delete(0, "end")
        for mid in all_models:
            self._model_listbox.insert("end", mid)
        self._refresh_default_combo()

    def _refresh_default_combo(self):
        models = list(self._model_listbox.get(i) for i in range(self._model_listbox.size()))
        current = self._combo_default.get()
        self._combo_default["values"] = models
        if current in models:
            self._combo_default.set(current)
        elif models:
            self._combo_default.set(models[0])
        else:
            self._combo_default.set("")

    def _fetch_models(self):
        ep = self._make_endpoint_snapshot()
        ids, err = fetch_models_for_endpoint(ep)
        if ids:
            existing = set(self._model_listbox.get(i) for i in range(self._model_listbox.size()))
            for mid in ids:
                if mid not in existing:
                    self._model_listbox.insert("end", mid)
            # Sort all models alphabetically
            all_models = list(self._model_listbox.get(i) for i in range(self._model_listbox.size()))
            all_models.sort(key=str.lower)
            self._model_listbox.delete(0, "end")
            for mid in all_models:
                self._model_listbox.insert("end", mid)
            self._refresh_default_combo()
        else:
            messagebox.showerror("Fetch Models", f"Failed:\n{err}", parent=self._dlg)

    def _diagnose_endpoint(self):
        ep = self._make_endpoint_snapshot()
        wait = tk.Toplevel(self._dlg)
        wait.title("Running Doctor...")
        wait.geometry("280x80")
        wait.transient(self._dlg)
        wait.grab_set()
        tk.Label(wait, text="Running endpoint diagnostics...").pack(expand=True)

        def _run():
            checks = run_endpoint_doctor(ep)
            self._dlg.after(0, lambda: (wait.destroy(), _show_doctor_results_tk(self._dlg, ep.get("default_model", "endpoint"), checks)))

        threading.Thread(target=_run, daemon=True).start()

    def _make_endpoint_snapshot(self):
        bt_display = self._combo_type.get()
        bt = self._bt_map.get(bt_display, "openai-compat")
        return {
            "base_url": self._entry_url.get().strip(),
            "api_key": self._entry_key.get().strip(),
            "backend_type": bt,
            "default_model": self._combo_default.get() or "",
        }

    def _do_oauth_login(self):
        preset_name = self._combo_preset.get() or "Custom"
        preset = PROVIDER_PRESETS.get(preset_name, {})
        provider = preset.get("oauth_provider", "")
        
        def _on_token(access_token):
            self._entry_key.delete(0, "end")
            self._entry_key.insert(0, access_token)

        if provider == "codebuff":
            codebuff_oauth_flow(self._dlg, on_token=_on_token)
        elif (provider or "").startswith("google"):
            google_oauth_flow(self._dlg, oauth_provider=provider, on_token=_on_token)
        elif provider == "kiro":
            kiro_oauth_flow(self._dlg, on_token=_on_token)

    def _cancel(self):
        self._dlg.destroy()

    def _save(self):
        name = self._entry_name.get().strip()
        if not name:
            messagebox.showerror("Error", "Name is required", parent=self._dlg)
            return
        bt_display = self._combo_type.get()
        bt = self._bt_map.get(bt_display, "openai-compat")
        url = self._entry_url.get().strip()
        key = self._entry_key.get().strip()
        models = list(self._model_listbox.get(i) for i in range(self._model_listbox.size()))

        if not models:
            ep_snap = self._make_endpoint_snapshot()
            ids, err = fetch_models_for_endpoint(ep_snap)
            if ids:
                for mid in ids:
                    self._model_listbox.insert("end", mid)
                self._refresh_default_combo()
                models = list(self._model_listbox.get(i) for i in range(self._model_listbox.size()))
            else:
                r = messagebox.askyesno("No Models", f"Auto-fetch failed ({err}).\n\nAdd models manually now?", parent=self._dlg)
                if r:
                    self._entry_model.focus_set()
                    return
                self._dlg.destroy()
                return

        if not models:
            messagebox.showerror("Error", "At least one model is required", parent=self._dlg)
            return

        default = self._combo_default.get() or models[0]
        data = load_endpoints()

        if self._existing_name and self._existing_name != name:
            data["endpoints"] = [e for e in data["endpoints"] if e["name"] != self._existing_name]

        existing = [e for e in data["endpoints"] if e["name"] == name]
        if existing:
            data["endpoints"] = [e for e in data["endpoints"] if e["name"] != name]

        new_ep = {
            "name": name, "backend_type": bt, "base_url": normalize_base_url(url),
            "api_key": key, "default_model": default, "models": models,
            "provider_preset": self._combo_preset.get() or "Custom",
            "reasoning_enabled": self._reason_var.get(),
            "reasoning_effort": self._combo_effort.get() or "medium",
            "prompt_enhancer": self._enhancer_var.get(),
            "prompt_enhancer_mode": self._enhancer_mode.get() or "offline",
        }
        cc_ver = self._entry_cc_ver.get().strip()
        if cc_ver:
            new_ep["cc_version"] = cc_ver
        enh_model = self._entry_enhancer_model.get().strip()
        enh_url = self._entry_enhancer_url.get().strip()
        enh_key = self._entry_enhancer_key.get().strip()
        if enh_model:
            new_ep["prompt_enhancer_model"] = enh_model
        if enh_url:
            new_ep["prompt_enhancer_url"] = enh_url
        if enh_key:
            new_ep["prompt_enhancer_key"] = enh_key
        preset_name = self._combo_preset.get() or "Custom"
        preset = PROVIDER_PRESETS.get(preset_name, {})
        if preset.get("oauth_provider"):
            new_ep["oauth_provider"] = preset["oauth_provider"]

        found = False
        for i, e in enumerate(data["endpoints"]):
            if e["name"] == name:
                data["endpoints"][i] = new_ep
                found = True
                break
        if not found:
            data["endpoints"].append(new_ep)
            if data.get("default") is None:
                data["default"] = name

        save_endpoints(data)
        self._hot_reload_proxy_key(new_ep)
        self.result = True
        self._dlg.destroy()

    def _hot_reload_proxy_key(self, ep):
        try:
            ep_name = ep.get("name", "")
            proxy_port = None
            for cfg_file in PROXY_CONFIG_DIR.glob("proxy-*.json"):
                try:
                    with open(str(cfg_file)) as f:
                        pcfg = json.load(f)
                    if safe_name(ep_name).lower() in str(cfg_file).lower():
                        proxy_port = pcfg.get("port")
                        pcfg["api_key"] = ep.get("api_key", "")
                        with open(str(cfg_file), "w") as f:
                            json.dump(pcfg, f, indent=2)
                        break
                except Exception:
                    continue
            if proxy_port:
                try:
                    url = f"http://127.0.0.1:{proxy_port}/admin/reload"
                    resp = urllib.request.urlopen(url, timeout=3)
                    result = json.loads(resp.read())
                    reloaded = result.get("reloaded", False)
                    preview = result.get("api_key_preview", "?")
                    print(f"[hot-reload] key {'updated' if reloaded else 'unchanged'}: {preview}", file=sys.stderr)
                    if reloaded:
                        verify_url = f"http://127.0.0.1:{proxy_port}/admin/verify-key"
                        vresp = urllib.request.urlopen(verify_url, timeout=10)
                        vresult = json.loads(vresp.read())
                        valid = vresult.get("valid", False)
                        if valid:
                            print(f"[hot-reload] key verified OK ({vresult.get('models', '?')} models)", file=sys.stderr)
                        else:
                            print(f"[hot-reload] WARNING: key verification failed: {vresult.get('error', 'unknown')}", file=sys.stderr)
                except Exception:
                    pass
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# EndpointMgr
# ═══════════════════════════════════════════════════════════════════════

class EndpointMgr:
    def __init__(self, parent, on_update=None):
        self._parent = parent
        self._on_update = on_update

        self._dlg = tk.Toplevel(parent)
        self._dlg.title("Manage Endpoints")
        self._dlg.geometry("600x400")
        self._dlg.transient(parent)

        main = ttk.Frame(self._dlg, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Endpoints", font=("Segoe UI", 11, "bold")).pack(anchor="w")

        tree_frame = ttk.Frame(main)
        tree_frame.pack(fill="both", expand=True, pady=(4, 0))
        cols = ("name", "provider", "backend", "default_model")
        self._tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        for col, heading, width in [("name", "Name", 140), ("provider", "Provider", 160),
                                     ("backend", "Type", 140), ("default_model", "Default Model", 140)]:
            self._tree.heading(col, text=heading)
            self._tree.column(col, width=width, minwidth=80)
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_frame, text="Add", command=self._add).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Edit", command=self._edit).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Delete", command=self._delete).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Set Default", command=self._set_default).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Doctor", command=self._doctor_selected).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Doctor All", command=self._doctor_all).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Close", command=self._dlg.destroy).pack(side="right")

        self._rebuild()

    def _rebuild(self):
        for item in self._tree.get_children():
            self._tree.delete(item)
        data = load_endpoints()
        for ep in data["endpoints"]:
            provider = ep.get("provider_preset", "Custom")
            bt = label_for_backend(ep["backend_type"])
            self._tree.insert("", "end", values=(ep["name"], provider, bt, ep.get("default_model", "")))

    def _selected_name(self):
        sel = self._tree.selection()
        if not sel:
            return None
        return self._tree.item(sel[0])["values"][0]

    def _add(self):
        d = EditEndpointDialog(self._dlg, None)
        self._dlg.wait_window(d._dlg)
        if d.result:
            self._rebuild()
            if self._on_update:
                self._on_update()

    def _edit(self):
        name = self._selected_name()
        if not name:
            return
        d = EditEndpointDialog(self._dlg, name)
        self._dlg.wait_window(d._dlg)
        if d.result:
            self._rebuild()
            if self._on_update:
                self._on_update()

    def _delete(self):
        name = self._selected_name()
        if not name:
            return
        if not messagebox.askyesno("Delete", f'Delete endpoint "{name}"?', parent=self._dlg):
            return
        data = load_endpoints()
        data["endpoints"] = [e for e in data["endpoints"] if e["name"] != name]
        if data.get("default") == name:
            data["default"] = data["endpoints"][0]["name"] if data["endpoints"] else None
        save_endpoints(data)
        self._rebuild()
        if self._on_update:
            self._on_update()

    def _set_default(self):
        name = self._selected_name()
        if not name:
            return
        data = load_endpoints()
        data["default"] = name
        save_endpoints(data)
        self._rebuild()
        if self._on_update:
            self._on_update()

    def _doctor_selected(self):
        name = self._selected_name()
        if not name:
            return
        ep = get_endpoint(name)
        if not ep:
            return
        wait = tk.Toplevel(self._dlg)
        wait.title(f"Doctor: {name}...")
        wait.geometry("280x80")
        wait.transient(self._dlg)
        wait.grab_set()
        tk.Label(wait, text=f"Running diagnostics for {name}...").pack(expand=True)

        def _run():
            checks = run_endpoint_doctor(ep)
            self._dlg.after(0, lambda: (wait.destroy(), _show_doctor_results_tk(self._dlg, name, checks)))

        threading.Thread(target=_run, daemon=True).start()

    def _doctor_all(self):
        data = load_endpoints()
        endpoints = data.get("endpoints", [])
        if not endpoints:
            messagebox.showinfo("Doctor All", "No endpoints configured.", parent=self._dlg)
            return

        wait = tk.Toplevel(self._dlg)
        wait.title("Doctor All...")
        wait.geometry("320x80")
        wait.transient(self._dlg)
        wait.grab_set()
        tk.Label(wait, text=f"Testing {len(endpoints)} endpoints...").pack(expand=True)

        all_results = {}

        def _run():
            for ep in endpoints:
                try:
                    all_results[ep["name"]] = run_endpoint_doctor(ep)
                except Exception as e:
                    all_results[ep["name"]] = [("Doctor run", False, str(e)[:100])]

            def _show():
                wait.destroy()
                dlg = tk.Toplevel(self._dlg)
                dlg.title("Doctor All Results")
                dlg.geometry("580x480")
                dlg.transient(self._dlg)

                canvas = tk.Canvas(dlg)
                scrollbar = ttk.Scrollbar(dlg, orient="vertical", command=canvas.yview)
                inner = tk.Frame(canvas)
                inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
                canvas.create_window((0, 0), window=inner, anchor="nw")
                canvas.configure(yscrollcommand=scrollbar.set)

                for ep_name, checks in all_results.items():
                    passed = sum(1 for _, ok, _ in checks if ok is True)
                    failed = sum(1 for _, ok, _ in checks if ok is False)
                    color = "#e74c3c" if failed else "#27ae60"
                    status = f"{failed} failed" if failed else f"{passed} passed"
                    tk.Label(inner, text=f"{ep_name}   {status}", fg=color,
                             font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(8, 2))
                    for name, ok, detail in checks:
                        if ok is True:
                            sym, sc = "✓", "#27ae60"
                        elif ok is False:
                            sym, sc = "✗", "#e74c3c"
                        else:
                            sym, sc = "○", "#f39c12"
                        row = tk.Frame(inner)
                        row.pack(anchor="w", padx=24, pady=0)
                        tk.Label(row, text=sym, fg=sc, font=("Segoe UI", 9, "bold")).pack(side="left")
                        txt = name
                        if detail:
                            txt += f"  {detail}"
                        tk.Label(row, text=txt, fg="#7f8c8d", font=("Segoe UI", 8)).pack(side="left")
                    ttk.Separator(inner).pack(fill="x", padx=12, pady=4)

                canvas.pack(side="left", fill="both", expand=True, padx=(12, 0))
                scrollbar.pack(side="right", fill="y")
                ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=8)

            self._dlg.after(0, _show)

        threading.Thread(target=_run, daemon=True).start()

