"""Session manager window — multi-provider session browser."""
import os as _os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

_src = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src not in sys.path:
    sys.path.insert(0, _src)

from codex_launcher_lib import _usage_theme


class SessionManagerWindow:
    """Multi-provider session browser (Codex, Claude Code, Gemini CLI)."""

    def __init__(self, parent):
        U = _usage_theme()
        self._U = U
        self._sessions = []
        self._filtered = []

        self._dlg = tk.Toplevel(parent)
        self._dlg.title("Session Manager")
        self._dlg.geometry("960x600")
        self._dlg.transient(parent)
        self._dlg.configure(bg=U["base"])

        main = tk.Frame(self._dlg, bg=U["base"])
        main.pack(fill="both", expand=True, padx=12, pady=8)

        # ── Header ────────────────────────────────────────────────
        hdr = tk.Frame(main, bg=U["base"])
        hdr.pack(fill="x")
        tk.Label(hdr, text="📂", fg=U["accent"], bg=U["base"],
                 font=("Segoe UI", 12)).pack(side="left")
        tk.Label(hdr, text="Session Manager", fg=U["text"], bg=U["base"],
                 font=("Segoe UI", 12, "bold")).pack(side="left", padx=(4, 0))
        self._count_lbl = tk.Label(hdr, text="", fg=U["dim"], bg=U["base"],
                                   font=("Segoe UI", 8))
        self._count_lbl.pack(side="left", padx=(8, 0))

        delete_btn = tk.Button(hdr, text="Delete", fg=U["red"], bg=U["surface0"],
                              activebackground=U["surface1"], relief="flat", bd=0,
                              command=self._delete_selected, padx=12, pady=2)
        delete_btn.pack(side="right", padx=(4, 0))
        refresh_btn = tk.Button(hdr, text="Refresh", fg=U["text"], bg=U["surface0"],
                               activebackground=U["surface1"], relief="flat", bd=0,
                               command=self._load, padx=12, pady=2)
        refresh_btn.pack(side="right")

        # ── Filter bar ────────────────────────────────────────────
        filter_frame = tk.Frame(main, bg=U["base"])
        filter_frame.pack(fill="x", pady=(6, 0))

        self._provider_var = tk.StringVar(value="all")
        for text, val in [("All", "all"), ("Codex", "codex"),
                          ("Claude", "claude"), ("Gemini", "gemini")]:
            tk.Radiobutton(filter_frame, text=text, variable=self._provider_var,
                           value=val, command=self._apply_filter,
                           bg=U["base"], fg=U["text"], selectcolor=U["surface1"],
                           activebackground=U["base"], activeforeground=U["accent"],
                           font=("Segoe UI", 9), indicatoron=0, padx=8, pady=2,
                           relief="flat", bd=1).pack(side="left", padx=(0, 2))

        search_lbl = tk.Label(filter_frame, text="🔍", fg=U["dim"], bg=U["base"],
                              font=("Segoe UI", 9))
        search_lbl.pack(side="left", padx=(12, 2))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        search_entry = tk.Entry(filter_frame, textvariable=self._search_var,
                                bg=U["surface1"], fg=U["text"], insertbackground=U["text"],
                                font=("Segoe UI", 9), relief="flat", bd=0, width=25)
        search_entry.pack(side="left", fill="x", expand=True, ipady=3)

        # ── Split pane (horizontal: list | conversation) ──────────
        paned = ttk.PanedWindow(main, orient="horizontal")
        paned.pack(fill="both", expand=True, pady=(6, 0))

        # Left: session list
        left_frame = tk.Frame(paned, bg=U["base"])
        paned.add(left_frame, weight=2)

        style = ttk.Style()
        style.configure("Sess.Treeview", background=U["surface0"], foreground=U["text"],
                        fieldbackground=U["surface0"], borderwidth=0, font=("Segoe UI", 9))
        style.configure("Sess.Treeview.Heading", background=U["surface1"],
                        foreground=U["text"], font=("Segoe UI", 9, "bold"))
        style.map("Sess.Treeview", background=[("selected", U["surface2"])])

        cols = ("provider", "title", "model", "date")
        self._tree = ttk.Treeview(left_frame, columns=cols, show="headings",
                                  height=20, style="Sess.Treeview")
        for col, heading, w in [("provider", "Provider", 60), ("title", "Title", 180),
                                ("model", "Model", 120), ("date", "Date", 110)]:
            self._tree.heading(col, text=heading)
            self._tree.column(col, width=w, minwidth=50)

        self._tree.tag_configure("codex", foreground=U["green"])
        self._tree.tag_configure("claude", foreground=U["lavender"])
        self._tree.tag_configure("gemini", foreground=U["sapphire"])

        tree_sb = ttk.Scrollbar(left_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        tree_sb.pack(side="right", fill="y")
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        # Right: conversation view
        right_frame = tk.Frame(paned, bg=U["base"])
        paned.add(right_frame, weight=3)

        self._conv = tk.Text(right_frame, wrap="word", bg=U["surface0"], fg=U["text"],
                             font=("Consolas", 9), relief="flat", bd=0, padx=8, pady=8,
                             state="disabled", cursor="arrow")
        self._conv.tag_configure("user", foreground=U["green"])
        self._conv.tag_configure("assistant", foreground=U["text"])
        self._conv.tag_configure("tool", foreground=U["lavender"])
        self._conv.tag_configure("system", foreground=U["dim"])
        self._conv.tag_configure("role_label", foreground=U["accent"],
                                font=("Segoe UI", 8, "bold"))
        self._conv.tag_configure("separator", foreground=U["surface2"])

        conv_sb = ttk.Scrollbar(right_frame, orient="vertical", command=self._conv.yview)
        self._conv.configure(yscrollcommand=conv_sb.set)
        self._conv.pack(side="left", fill="both", expand=True)
        conv_sb.pack(side="right", fill="y")

        # ── Footer: resume command ────────────────────────────────
        footer = tk.Frame(main, bg=U["base"])
        footer.pack(fill="x", pady=(6, 0))

        self._resume_lbl = tk.Label(footer, text="", fg=U["dim"], bg=U["base"],
                                    font=("Consolas", 9), anchor="w")
        self._resume_lbl.pack(side="left", fill="x", expand=True)

        copy_btn = tk.Button(footer, text="📋 Copy Resume Cmd", fg=U["text"],
                             bg=U["surface0"], activebackground=U["surface1"],
                             relief="flat", bd=0, command=self._copy_resume, padx=12, pady=2)
        copy_btn.pack(side="right")

        # ── Load data ─────────────────────────────────────────────
        self._load()

    # ── Data loading ──────────────────────────────────────────────

    def _load(self):
        """Scan all providers and populate tree."""
        self._sessions = []
        self._filtered = []
        self._tree.delete(*self._tree.get_children())
        self._conv.configure(state="normal")
        self._conv.delete("1.0", "end")
        self._conv.configure(state="disabled")
        self._resume_lbl.configure(text="Scanning sessions...")

        def _scan():
            from session_manager import scan_all
            sessions = scan_all(limit=200)
            self._dlg.after(0, lambda: self._populate(sessions))

        import threading
        threading.Thread(target=_scan, daemon=True).start()

    def _populate(self, sessions):
        self._sessions = sessions
        self._apply_filter()
        self._count_lbl.configure(text=f"{len(self._sessions)} sessions")

    def _apply_filter(self):
        """Filter sessions by provider and search text."""
        provider = self._provider_var.get()
        search = self._search_var.get().lower().strip()

        self._filtered = []
        for s in self._sessions:
            if provider != "all" and s.provider != provider:
                continue
            if search:
                searchable = f"{s.title} {s.model} {s.project_dir} {s.session_id}".lower()
                if search not in searchable:
                    continue
            self._filtered.append(s)

        self._tree.delete(*self._tree.get_children())
        for s in self._filtered:
            date_str = self._format_time(s.last_active) if s.last_active else ""
            self._tree.insert("", "end", values=(s.provider, s.title, s.model, date_str),
                              tags=(s.provider,))

    def _on_select(self, event):
        """Load and display conversation for selected session."""
        sel = self._tree.selection()
        if not sel:
            return
        idx = self._tree.index(sel[0])
        if idx >= len(self._filtered):
            return
        meta = self._filtered[idx]

        self._resume_lbl.configure(text=meta.resume_cmd if meta.resume_cmd else "(no resume command)")

        def _load_msgs():
            from session_manager import load_messages
            msgs = load_messages(meta)
            self._dlg.after(0, lambda: self._show_messages(meta, msgs))

        import threading
        threading.Thread(target=_load_msgs, daemon=True).start()

    def _show_messages(self, meta, msgs):
        """Display messages in the conversation view."""
        self._conv.configure(state="normal")
        self._conv.delete("1.0", "end")

        if not msgs:
            self._conv.insert("end", f"No messages found in this session.\n\n"
                              f"Provider: {meta.provider}\n"
                              f"Session: {meta.session_id}\n"
                              f"File: {meta.file_path}\n", "system")
        else:
            for msg in msgs:
                role_label = {"user": "USER", "assistant": "ASSISTANT",
                              "tool": "TOOL", "system": "SYSTEM"}.get(msg.role, msg.role.upper())
                self._conv.insert("end", f"[{role_label}]\n", "role_label")
                content = msg.content[:5000] if msg.content else "(empty)"
                self._conv.insert("end", f"{content}\n", msg.role)
                self._conv.insert("end", "─" * 60 + "\n", "separator")

        self._conv.configure(state="disabled")

    # ── Actions ───────────────────────────────────────────────────

    def _copy_resume(self):
        """Copy resume command to clipboard."""
        sel = self._tree.selection()
        if not sel:
            return
        idx = self._tree.index(sel[0])
        if idx >= len(self._filtered):
            return
        meta = self._filtered[idx]
        cmd = meta.resume_cmd
        if not cmd:
            return
        self._dlg.clipboard_clear()
        self._dlg.clipboard_append(cmd)
        self._resume_lbl.configure(text=f"✓ Copied: {cmd}")

    def _delete_selected(self):
        """Delete selected session with confirmation."""
        sel = self._tree.selection()
        if not sel:
            return
        idx = self._tree.index(sel[0])
        if idx >= len(self._filtered):
            return
        meta = self._filtered[idx]
        if not messagebox.askyesno("Delete Session",
                f"Delete this {meta.provider} session?\n\n"
                f"Title: {meta.title}\n"
                f"File: {meta.file_path}\n\n"
                "This cannot be undone."):
            return
        from session_manager import delete_session
        if delete_session(meta):
            self._load()
        else:
            messagebox.showerror("Delete Failed", "Could not delete session file.")

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _format_time(ts):
        """Format unix timestamp to human-readable relative time."""
        import time
        diff = time.time() - ts
        if diff < 60:
            return "just now"
        if diff < 3600:
            return f"{int(diff / 60)}m ago"
        if diff < 86400:
            return f"{int(diff / 3600)}h ago"
        if diff < 604800:
            return f"{int(diff / 86400)}d ago"
        return time.strftime("%Y-%m-%d", time.localtime(ts))
