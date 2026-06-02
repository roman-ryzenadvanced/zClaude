#!/usr/bin/env python3
"""X Edition Sidebar — Warp-style left navigation rail.

WHY: The original GUI had a toolbar with 8+ buttons crammed into two rows.
As features grew, it got more and more crowded. And buttons with text labels
take a lot of horizontal space.

Warp uses an elegant left sidebar with icon buttons that:
  - Takes up minimal space (just icons when collapsed)
  - Scales to any number of features
  - Shows which section is active
  - Expands to show labels when you want them

Think of it like a bookshelf vs. a pile of books on the floor:
the sidebar organizes navigation into a clean, vertical structure
instead of a horizontal mess.

Features:
  - Vertical icon + label buttons
  - Active state with accent left border
  - Collapsible (icons-only vs. icons + labels)
  - Tooltip on hover
  - Bottom section for settings/help
"""
import tkinter as tk
from gui_x.theme import CATPPUCCIN
from gui_x.fonts import FONT_UI, SIZE_BODY, SIZE_SMALL, SIZE_TINY


# Navigation items: (id, icon, label)
NAV_ITEMS = [
    ("endpoints", "🔌", "Endpoints"),
    ("monitor",   "👁", "AI Monitor"),
    ("bgp",       "🔀", "AI BGP"),
    ("usage",     "📊", "Usage"),
    ("benchmark", "⚡", "Benchmark"),
    ("history",   "📜", "History"),
    ("sessions",  "💬", "Sessions"),
    ("oauth",     "🔐", "OAuth"),
]

BOTTOM_ITEMS = [
    ("updater",   "🔄", "Update"),
    ("changelog", "📋", "Changelog"),
]


class Sidebar(tk.Frame):
    """Warp-style left navigation sidebar.

    Args:
        parent:    Parent widget
        on_navigate: Callback when a nav item is clicked: on_navigate(item_id)
        collapsed: Start in collapsed mode (icons only)
    """

    C = CATPPUCCIN

    def __init__(self, parent, on_navigate=None, collapsed=False, **kwargs):
        bg = kwargs.pop("bg", self.C["surface0"])
        super().__init__(parent, bg=bg, width=48, **kwargs)
        self.pack_propagate(False)

        self._on_navigate = on_navigate
        self._collapsed = collapsed
        self._active_id = None
        self._buttons = {}

        # ── Top section: Navigation items ───────────────────────────
        self._top_frame = tk.Frame(self, bg=bg)
        self._top_frame.pack(fill="x", padx=4, pady=(8, 4))

        for item_id, icon, label in NAV_ITEMS:
            self._create_nav_button(self._top_frame, item_id, icon, label)

        # ── Separator ───────────────────────────────────────────────
        sep = tk.Frame(self, bg=self.C["surface2"], height=1)
        sep.pack(fill="x", padx=8, pady=8)

        # ── Bottom section ──────────────────────────────────────────
        self._bottom_frame = tk.Frame(self, bg=bg)
        self._bottom_frame.pack(side="bottom", fill="x", padx=4, pady=(4, 8))

        for item_id, icon, label in BOTTOM_ITEMS:
            self._create_nav_button(self._bottom_frame, item_id, icon, label)

        # ── Toggle button (collapse/expand) ─────────────────────────
        self._toggle_frame = tk.Frame(self._bottom_frame, bg=bg)
        self._toggle_frame.pack(fill="x", pady=(8, 0))
        self._toggle_btn = tk.Button(
            self._toggle_frame, text="≫" if self._collapsed else "≪",
            bg=bg, fg=self.C["dim"], activebackground=self.C["surface1"],
            activeforeground=self.C["accent"], relief="flat", bd=0,
            font=(FONT_UI, SIZE_BODY), command=self.toggle,
            cursor="hand2")
        self._toggle_btn.pack(fill="x")

        # Set initial width
        self._update_width()

    def _create_nav_button(self, parent, item_id, icon, label):
        """Create a single navigation button with icon + optional label."""
        bg = self.C["surface0"]

        btn_frame = tk.Frame(parent, bg=bg, cursor="hand2")
        btn_frame.pack(fill="x", pady=1)

        # Left accent bar (shows when active)
        accent_bar = tk.Frame(btn_frame, bg=bg, width=3)
        accent_bar.pack(side="left", fill="y", pady=2)
        accent_bar.pack_propagate(False)

        # Icon
        icon_label = tk.Label(btn_frame, text=icon, bg=bg, fg=self.C["subtext"],
                               font=(FONT_UI, SIZE_BODY), width=2, anchor="center")
        icon_label.pack(side="left", padx=(2, 0))

        # Label (hidden when collapsed)
        text_label = tk.Label(btn_frame, text=label, bg=bg, fg=self.C["dim"],
                               font=(FONT_UI, SIZE_SMALL), anchor="w")
        if not self._collapsed:
            text_label.pack(side="left", padx=(4, 0), fill="x", expand=True)

        # Hover bindings
        for widget in (btn_frame, icon_label, text_label):
            widget.bind("<Enter>", lambda e, bf=btn_frame, il=icon_label,
                        tl=text_label, ab=accent_bar: self._on_hover(bf, il, tl, ab, True))
            widget.bind("<Leave>", lambda e, bf=btn_frame, il=icon_label,
                        tl=text_label, ab=accent_bar: self._on_hover(bf, il, tl, ab, False))
            widget.bind("<Button-1>", lambda e, iid=item_id: self._on_click(iid))

        # Tooltip for collapsed mode
        ToolTip([btn_frame, icon_label, text_label], label, lambda: self._collapsed)

        self._buttons[item_id] = {
            "frame": btn_frame,
            "icon": icon_label,
            "label": text_label,
            "accent": accent_bar,
        }

    def _on_hover(self, frame, icon, label, accent, entering):
        """Handle mouse hover — highlight the button."""
        if self._active_id and frame == self._buttons[self._active_id]["frame"]:
            return  # Don't change active item on hover
        if entering:
            frame.configure(bg=self.C["surface1"])
            icon.configure(bg=self.C["surface1"], fg=self.C["text"])
            label.configure(bg=self.C["surface1"])
        else:
            frame.configure(bg=self.C["surface0"])
            icon.configure(bg=self.C["surface0"], fg=self.C["subtext"])
            label.configure(bg=self.C["surface0"])

    def _on_click(self, item_id):
        """Handle button click — set active state and call callback."""
        self.set_active(item_id)
        if self._on_navigate:
            self._on_navigate(item_id)

    def set_active(self, item_id):
        """Set the active navigation item (highlighted state)."""
        # Deactivate previous
        if self._active_id and self._active_id in self._buttons:
            prev = self._buttons[self._active_id]
            for w in (prev["frame"], prev["icon"], prev["label"]):
                w.configure(bg=self.C["surface0"])
            prev["icon"].configure(fg=self.C["subtext"])
            prev["label"].configure(fg=self.C["dim"])
            prev["accent"].configure(bg=self.C["surface0"])

        # Activate new
        self._active_id = item_id
        if item_id in self._buttons:
            curr = self._buttons[item_id]
            for w in (curr["frame"], curr["icon"], curr["label"]):
                w.configure(bg=self.C["surface1"])
            curr["icon"].configure(fg=self.C["accent"])
            curr["label"].configure(fg=self.C["text"])
            curr["accent"].configure(bg=self.C["accent"])

    def toggle(self):
        """Toggle between collapsed (icons only) and expanded (icons + labels)."""
        self._collapsed = not self._collapsed
        self._toggle_btn.configure(text="≫" if self._collapsed else "≪")

        for item_id, btn in self._buttons.items():
            if self._collapsed:
                btn["label"].pack_forget()
            else:
                btn["label"].pack(side="left", padx=(4, 0), fill="x", expand=True)

        self._update_width()

    def _update_width(self):
        """Update sidebar width based on collapsed state."""
        width = 48 if self._collapsed else 160
        self.configure(width=width)


class ToolTip:
    """A shared Tkinter tooltip that shows up on hover over multiple widgets of a button."""
    def __init__(self, widgets, text, is_enabled_cb=None):
        self.widgets = widgets if isinstance(widgets, (list, tuple)) else [widgets]
        self.text = text
        self.is_enabled_cb = is_enabled_cb
        self.tip_window = None
        self.id = None
        
        for widget in self.widgets:
            widget.bind("<Enter>", self.enter, add="+")
            widget.bind("<Leave>", self.leave, add="+")
            widget.bind("<ButtonPress>", self.leave, add="+")

    def enter(self, event=None):
        if self.is_enabled_cb and not self.is_enabled_cb():
            return
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        # Small delay to see if we've entered another widget of the same button
        self.widgets[0].after(50, self._check_and_hide)

    def _check_and_hide(self):
        try:
            x, y = self.widgets[0].winfo_pointerxy()
            widget_under = self.widgets[0].winfo_containing(x, y)
            if widget_under in self.widgets:
                return
        except Exception:
            pass
        self.hide_tip()

    def schedule(self):
        self.unschedule()
        self.id = self.widgets[0].after(400, self.show_tip)

    def unschedule(self):
        if self.id:
            self.widgets[0].after_cancel(self.id)
            self.id = None

    def show_tip(self):
        if self.tip_window or not self.text:
            return
        primary = self.widgets[0]
        x = primary.winfo_rootx() + 38
        y = primary.winfo_rooty() + 8
        self.tip_window = tw = tk.Toplevel(primary)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        bg = CATPPUCCIN["mantle"]
        fg = CATPPUCCIN["text"]
        border_color = CATPPUCCIN["surface2"]
        
        frame = tk.Frame(tw, bg=bg, highlightbackground=border_color, highlightthickness=1)
        frame.pack()
        label = tk.Label(frame, text=self.text, justify="left",
                         bg=bg, fg=fg,
                         font=(FONT_UI, SIZE_TINY),
                         padx=6, pady=3)
        label.pack(ipadx=1)

    def hide_tip(self):
        tw = self.tip_window
        self.tip_window = None
        if tw:
            tw.destroy()
