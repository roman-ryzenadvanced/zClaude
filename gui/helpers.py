"""GUI helper functions — formatting, shared dialogs."""
import tkinter as tk
from tkinter import ttk

from codex_launcher_lib import _usage_theme

# Re-use identical functions from lib.utils
from codex_launcher_lib import _fmt_tok, _fmt_dur, _status_pill  # noqa: F401


def _show_doctor_results_tk(parent, ep_name, checks):
    dlg = tk.Toplevel(parent)
    dlg.title(f"Doctor: {ep_name}")
    dlg.geometry("520x420")
    dlg.transient(parent)
    dlg.grab_set()

    passed = sum(1 for _, ok, _ in checks if ok is True)
    failed = sum(1 for _, ok, _ in checks if ok is False)
    warned = sum(1 for _, ok, _ in checks if ok is None)

    hdr = tk.Label(dlg, text=f"{ep_name}   {passed} passed   {failed} failed   {warned} warnings",
                   font=("Segoe UI", 10, "bold"))
    hdr.pack(padx=12, pady=(12, 4), anchor="w")

    ttk.Separator(dlg).pack(fill="x", padx=12)

    canvas = tk.Canvas(dlg)
    scrollbar = ttk.Scrollbar(dlg, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    for name, ok, detail in checks:
        row = tk.Frame(inner)
        row.pack(fill="x", padx=12, pady=1)
        if ok is True:
            color, sym = "#27ae60", "✓"
        elif ok is False:
            color, sym = "#e74c3c", "✗"
        else:
            color, sym = "#f39c12", "○"
        tk.Label(row, text=sym, fg=color, font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Label(row, text=name, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(4, 0))
        if detail:
            tk.Label(row, text=detail, fg="#7f8c8d", font=("Segoe UI", 8)).pack(side="right")

    canvas.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=6)
    scrollbar.pack(side="right", fill="y", pady=6)

    btn_frame = tk.Frame(dlg)
    btn_frame.pack(pady=(0, 10))
    ttk.Button(btn_frame, text="Close", command=dlg.destroy).pack()
