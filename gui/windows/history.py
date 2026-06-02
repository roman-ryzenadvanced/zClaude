"""Request history window — browse request snapshots."""
import json
import tkinter as tk
from tkinter import ttk, messagebox

from codex_launcher_lib import PROXY_CONFIG_DIR, _usage_theme


class RequestHistoryWindow:
    def __init__(self, parent):
        U = _usage_theme()
        self._U = U
        self._snap_dir = PROXY_CONFIG_DIR / "requests"
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("Request History")
        self._dlg.geometry("720x500")
        self._dlg.transient(parent)
        self._dlg.configure(bg=U["base"])

        style = ttk.Style()
        style.configure("Hist.Treeview", background=U["surface0"], foreground=U["text"],
                        fieldbackground=U["surface0"], borderwidth=0, font=("Segoe UI", 9))
        style.configure("Hist.Treeview.Heading", background=U["surface1"], foreground=U["text"],
                        font=("Segoe UI", 9, "bold"))
        style.map("Hist.Treeview", background=[("selected", U["surface2"])])

        main = tk.Frame(self._dlg, bg=U["base"], padx=10, pady=10)
        main.pack(fill="both", expand=True)

        hdr = tk.Frame(main, bg=U["base"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="📋", fg=U["accent"], bg=U["base"], font=("Segoe UI", 12)).pack(side="left")
        tk.Label(hdr, text="Request History", fg=U["text"], bg=U["base"],
                 font=("Segoe UI", 12, "bold")).pack(side="left", padx=(4, 0))

        self._count_lbl = tk.Label(hdr, text="", fg=U["dim"], bg=U["base"], font=("Segoe UI", 8))
        self._count_lbl.pack(side="left", padx=(8, 0))

        refresh_btn = tk.Button(hdr, text="Refresh", fg=U["text"], bg=U["surface0"],
                                activebackground=U["surface1"], relief="flat", bd=0,
                                command=self._load, padx=12, pady=2)
        refresh_btn.pack(side="right", padx=(4, 0))
        clear_btn = tk.Button(hdr, text="Clear All", fg=U["red"], bg=U["surface0"],
                              activebackground=U["surface1"], relief="flat", bd=0,
                              command=self._clear_all, padx=12, pady=2)
        clear_btn.pack(side="right")

        paned = ttk.PanedWindow(main, orient="vertical")
        paned.pack(fill="both", expand=True, pady=(6, 0))

        top_frame = tk.Frame(paned, bg=U["base"])
        cols = ("time", "model", "status", "duration", "id", "error")
        self._tree = ttk.Treeview(top_frame, columns=cols, show="headings", height=10,
                                  style="Hist.Treeview")
        for col, heading, w in [("time", "Time", 140), ("model", "Model", 140), ("status", "Status", 80),
                                 ("duration", "Duration", 70), ("id", "ID", 180), ("error", "Error", 120)]:
            self._tree.heading(col, text=heading)
            self._tree.column(col, width=w, minwidth=50)
        self._tree.tag_configure("ok", foreground=U["green"])
        self._tree.tag_configure("warn", foreground=U["yellow"])
        self._tree.tag_configure("error", foreground=U["red"])
        self._tree.tag_configure("unknown", foreground=U["dim"])
        tree_sb = ttk.Scrollbar(top_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        tree_sb.pack(side="right", fill="y")
        paned.add(top_frame, weight=1)

        bottom_frame = tk.Frame(paned, bg=U["base"])
        self._detail = tk.Text(bottom_frame, height=10, wrap="word", font=("Consolas", 9),
                               bg=U["surface0"], fg=U["text"], insertbackground=U["text"],
                               selectbackground=U["surface2"], relief="flat", bd=0, padx=8, pady=6)
        detail_sb = ttk.Scrollbar(bottom_frame, orient="vertical", command=self._detail.yview)
        self._detail.configure(yscrollcommand=detail_sb.set)
        self._detail.pack(side="left", fill="both", expand=True)
        detail_sb.pack(side="right", fill="y")
        paned.add(bottom_frame, weight=1)

        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        self._snapshots = []
        self._load()

    def _load(self):
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._snapshots = []
        if not self._snap_dir.exists():
            self._count_lbl.configure(text="")
            return
        files = sorted(self._snap_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files[:200]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                meta = data.get("_meta", {})
                self._snapshots.append(data)
                ts = meta.get("ts_iso", "")[:19].replace("T", " ")
                model = meta.get("model", "?")
                status = meta.get("status", "unknown")
                dur = f"{meta['duration_s']:.1f}s" if meta.get("duration_s") is not None else "-"
                rid = meta.get("request_id", "")[:28]
                err = (meta.get("error") or "")[:60]
                tag = "ok" if status in ("ok", "success", "completed") else \
                      "error" if status in ("error", "failed", "timeout") else \
                      "warn" if status in ("warn", "partial") else "unknown"
                self._tree.insert("", "end", values=(ts, model, status, dur, rid, err), tags=(tag,))
            except Exception:
                pass
        self._count_lbl.configure(text=f"({len(self._snapshots)} requests)")

    def _on_select(self, event):
        sel = self._tree.selection()
        if not sel:
            return
        idx = self._tree.index(sel[0])
        if idx < len(self._snapshots):
            data = self._snapshots[idx]
            self._detail.delete("1.0", "end")
            self._detail.insert("end", json.dumps(data, indent=2, ensure_ascii=False)[:50000])

    def _clear_all(self):
        if not messagebox.askyesno("Clear All", "Delete all request snapshots?", parent=self._dlg):
            return
        if self._snap_dir.exists():
            for f in self._snap_dir.glob("*.json"):
                try:
                    f.unlink()
                except Exception:
                    pass
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._snapshots = []
        self._detail.delete("1.0", "end")
        self._count_lbl.configure(text="")
