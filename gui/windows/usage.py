"""Usage dashboard — per-provider statistics with themed cards."""
import tkinter as tk
from tkinter import ttk

from codex_launcher_lib import load_usage_stats, _usage_theme
from gui.helpers import _fmt_tok, _fmt_dur, _status_pill


class UsageWindow:
    def __init__(self, parent):
        self._U = _usage_theme()
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("Usage Dashboard")
        self._dlg.geometry("720x640")
        self._dlg.transient(parent)
        self._dlg.configure(bg=self._U["base"])

        self._build_header()
        self._build_summary_strip()
        ttk.Separator(self._dlg).pack(fill="x", padx=16)

        self._cards_frame = tk.Frame(self._dlg, bg=self._U["base"])
        canvas = tk.Canvas(self._cards_frame, bg=self._U["base"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self._cards_frame, orient="vertical", command=canvas.yview)
        self._cards_inner = tk.Frame(canvas, bg=self._U["base"])
        self._cards_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._cards_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(16, 0))
        scrollbar.pack(side="right", fill="y")
        self._cards_frame.pack(fill="both", expand=True, pady=(8, 0))

        self._refresh()

    def _build_header(self):
        U = self._U
        hdr = tk.Frame(self._dlg, bg=U["base"])
        hdr.pack(fill="x", padx=16, pady=(12, 6))
        tk.Label(hdr, text="⚡", fg=U["accent"], bg=U["base"], font=("Segoe UI", 14)).pack(side="left")
        tk.Label(hdr, text="Usage Dashboard", fg=U["text"], bg=U["base"],
                 font=("Segoe UI", 14, "bold")).pack(side="left", padx=(4, 0))
        self._status_dots = tk.Label(hdr, text="", fg=U["text"], bg=U["base"], font=("Segoe UI", 9))
        self._status_dots.pack(side="left", padx=(8, 0))
        self._updated_lbl = tk.Label(hdr, text="Never", fg=U["dim"], bg=U["base"], font=("Segoe UI", 8))
        self._updated_lbl.pack(side="right")
        refresh_btn = tk.Button(hdr, text="Refresh", fg=U["text"], bg=U["surface0"],
                                activebackground=U["surface1"], relief="flat", bd=0,
                                command=self._refresh, padx=12, pady=2)
        refresh_btn.pack(side="right", padx=(8, 0))

    def _build_summary_strip(self):
        U = self._U
        strip = tk.Frame(self._dlg, bg=U["surface0"], padx=12, pady=8)
        strip.pack(fill="x", padx=16, pady=(0, 6))
        self._kpi_labels = {}
        for key, label, icon in [("providers", "Providers", "\U0001F4CA"),
                                  ("requests", "Requests", "⚡"),
                                  ("tokens", "Tokens", "\U0001F9E0"),
                                  ("latency", "Avg Latency", "⏱")]:
            box = tk.Frame(strip, bg=U["surface0"])
            box.pack(side="left", padx=(0, 20))
            tk.Label(box, text=f"{icon} {label}", fg=U["dim"], bg=U["surface0"],
                     font=("Segoe UI", 8), anchor="w").pack(anchor="w")
            val = tk.Label(box, text="-", fg=U["text"], bg=U["surface0"],
                           font=("Segoe UI", 9, "bold"), anchor="w")
            val.pack(anchor="w")
            self._kpi_labels[key] = val

    def _refresh(self):
        for w in self._cards_inner.winfo_children():
            w.destroy()
        stats = load_usage_stats()
        updated = stats.get("updated")
        if updated:
            self._updated_lbl.configure(text=updated)
        providers = stats.get("providers", {})
        if not providers:
            tk.Label(self._cards_inner, text="No usage data yet.\nLaunch a session to start tracking.",
                     fg=self._U["dim"], bg=self._U["base"], font=("Segoe UI", 11)).pack(pady=60)
            return

        total_req = total_tok_in = total_tok_out = 0
        total_dur = 0.0
        n_ok = n_warn = n_err = 0

        sorted_providers = sorted(providers.items(), key=lambda x: x[1].get("total_requests", 0), reverse=True)
        for prov_name, prov_data in sorted_providers:
            t = prov_data.get("total_requests", 0)
            total_req += t
            total_tok_in += prov_data.get("total_tokens_in", 0)
            total_tok_out += prov_data.get("total_tokens_out", 0)
            total_dur += prov_data.get("total_duration_s", 0.0)
            fail = prov_data.get("failures", 0)
            fail_pct = fail / t if t > 0 else 0
            if fail_pct > 0.15:
                n_err += 1
            elif fail_pct > 0.05:
                n_warn += 1
            else:
                n_ok += 1

        self._kpi_labels["providers"].configure(text=str(len(providers)))
        self._kpi_labels["requests"].configure(text=f"{total_req:,}")
        tok_sum = total_tok_in + total_tok_out
        tok_str = f"{_fmt_tok(tok_sum)} in:{_fmt_tok(total_tok_in)} out:{_fmt_tok(total_tok_out)}" if tok_sum else "N/A"
        self._kpi_labels["tokens"].configure(text=tok_str)
        avg_lat = total_dur / total_req if total_req > 0 else 0
        self._kpi_labels["latency"].configure(text=_fmt_dur(avg_lat))

        dots = ""
        if n_ok:
            dots += f"●{n_ok} "
        if n_warn:
            dots += f"◐{n_warn} "
        if n_err:
            dots += f"✗{n_err}"
        self._status_dots.configure(text=dots)

        for prov_name, prov_data in sorted_providers:
            self._build_card(prov_name, prov_data)

    def _build_card(self, name, data):
        U = self._U
        total = data.get("total_requests", 0)
        ok = data.get("successes", 0)
        fail = data.get("failures", 0)
        success_rate = ok / total if total > 0 else 1.0
        fail_pct = fail / total if total > 0 else 0
        status_text, status_color = _status_pill(success_rate, fail_pct)

        card = tk.Frame(self._cards_inner, bg=U["surface0"], padx=14, pady=10,
                        highlightbackground=status_color, highlightthickness=1)
        card.pack(fill="x", pady=(0, 6))

        top = tk.Frame(card, bg=U["surface0"])
        top.pack(fill="x")
        tk.Label(top, text="●", fg=status_color, bg=U["surface0"], font=("Segoe UI", 10)).pack(side="left")
        short = name.replace("https://", "").replace("http://", "").split("/")[0]
        tk.Label(top, text=short, fg=U["text"], bg=U["surface0"],
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(4, 0))
        tk.Label(top, text=f" {status_text} ", fg=U["base"], bg=status_color,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(4, 0))
        tk.Label(top, text=f"{total} req", fg=U["subtext"], bg=U["surface0"],
                 font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))
        last_used = data.get("last_used", "")
        if last_used:
            tk.Label(top, text=last_used, fg=U["dim"], bg=U["surface0"],
                     font=("Segoe UI", 7)).pack(side="right")

        gauge = tk.Frame(card, bg=U["surface0"])
        gauge.pack(fill="x", pady=(4, 0))
        bar_frame = tk.Frame(gauge, bg=U["surface1"], height=12)
        bar_frame.pack(fill="x", side="left", expand=True)
        bar_frame.pack_propagate(False)
        fill_pct = int(success_rate * 100)
        fill_frame = tk.Frame(bar_frame, bg=status_color, height=12)
        fill_frame.place(relwidth=success_rate, relheight=1.0)
        tk.Label(gauge, text=f"{fill_pct}%", fg=U["subtext"], bg=U["surface0"],
                 font=("Segoe UI", 8)).pack(side="left", padx=(4, 0))
        if fail > 0:
            tk.Label(gauge, text=f"{fail} fail", fg=U["red"], bg=U["surface0"],
                     font=("Segoe UI", 8)).pack(side="right")

        metrics = tk.Frame(card, bg=U["surface0"])
        metrics.pack(fill="x", pady=(4, 0))
        t_in = data.get("total_tokens_in", 0)
        t_out = data.get("total_tokens_out", 0)
        dur = data.get("total_duration_s", 0.0)
        avg_dur = dur / total if total > 0 else 0
        for label, value, color in [("Tokens In", _fmt_tok(t_in), U["sapphire"]),
                                     ("Tokens Out", _fmt_tok(t_out), U["peach"]),
                                     ("Avg Latency", _fmt_dur(avg_dur), U["sky"]),
                                     ("Duration", _fmt_dur(dur), U["lavender"])]:
            box = tk.Frame(metrics, bg=U["surface0"])
            box.pack(side="left", padx=(0, 16))
            tk.Label(box, text=label, fg=U["dim"], bg=U["surface0"], font=("Segoe UI", 7)).pack(anchor="w")
            tk.Label(box, text=value, fg=color, bg=U["surface0"],
                     font=("Segoe UI", 9, "bold")).pack(anchor="w")

        models = data.get("models", {})
        if models:
            self._build_models_section(card, models, total, U)

        last_err = data.get("last_error")
        if last_err:
            err_frame = tk.Frame(card, bg=U["surface0"])
            err_frame.pack(fill="x", pady=(4, 0))
            tk.Label(err_frame, text=f"⚠ {last_err}", fg=U["red"], bg=U["surface0"],
                     font=("Segoe UI", 7)).pack(anchor="w")

    def _build_models_section(self, parent, models, total_req, U):
        sep_m = tk.Frame(parent, bg=U["lavender"], height=1)
        sep_m.pack(fill="x", pady=(4, 2))

        header = tk.Frame(parent, bg=U["surface0"])
        header.pack(fill="x")
        tk.Label(header, text="🤖", fg=U["lavender"], bg=U["surface0"],
                 font=("Segoe UI", 7)).pack(side="left")
        tk.Label(header, text="Models", fg=U["lavender"], bg=U["surface0"],
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(4, 0))

        sorted_models = sorted(models.items(), key=lambda x: x[1].get("requests", 0), reverse=True)

        if total_req > 0:
            comp_bar = tk.Frame(parent, bg=U["surface1"], height=8)
            comp_bar.pack(fill="x", pady=(2, 0))
            comp_bar.pack_propagate(False)
            for i, (mname, mdata) in enumerate(sorted_models):
                m_req = mdata.get("requests", 0)
                pct = m_req / total_req
                if pct < 0.01:
                    continue
                color = U["model_palette"][i % len(U["model_palette"])]
                seg = tk.Frame(comp_bar, bg=color, width=max(int(pct * 400), 4), height=8)
                seg.pack(side="left", fill="y")

        models_box = tk.Frame(parent, bg=U["surface0"])
        models_box.pack(fill="x", pady=(2, 0))

        for i, (mname, mdata) in enumerate(sorted_models[:6]):
            color = U["model_palette"][i % len(U["model_palette"])]
            row = tk.Frame(models_box, bg=U["surface0"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text="●", fg=color, bg=U["surface0"],
                     font=("Segoe UI", 7)).pack(side="left")
            tk.Label(row, text=mname, fg=U["subtext"], bg=U["surface0"],
                     font=("Segoe UI", 7), width=24, anchor="w").pack(side="left")

            m_req = mdata.get("requests", 0)
            pct = m_req / total_req * 100 if total_req > 0 else 0
            frac = m_req / total_req if total_req > 0 else 0

            bar_frame = tk.Frame(row, bg=U["surface1"], height=6, width=80)
            bar_frame.pack(side="left", padx=(4, 0))
            bar_frame.pack_propagate(False)
            fill_frame = tk.Frame(bar_frame, bg=color, height=6)
            fill_frame.place(relwidth=frac, relheight=1.0)

            tk.Label(row, text=f"{pct:.0f}% ({m_req})", fg=U["dim"], bg=U["surface0"],
                     font=("Segoe UI", 7)).pack(side="left", padx=(4, 0))

            m_in = mdata.get("tokens_in", 0)
            m_out = mdata.get("tokens_out", 0)
            if m_in or m_out:
                tk.Label(row, text=f"in:{_fmt_tok(m_in)} out:{_fmt_tok(m_out)}", fg=U["dim"],
                         bg=U["surface0"], font=("Segoe UI", 7)).pack(side="right")
