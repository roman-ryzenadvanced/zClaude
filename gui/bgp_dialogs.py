"""BGP pool management dialogs — route editor, pool editor, pool manager."""
import tkinter as tk
from tkinter import ttk, messagebox

from codex_launcher_lib import (
    load_endpoints, save_endpoints, load_bgp_pools, save_bgp_pools,
    normalize_base_url,
)


class BGPRouteDialog:
    def __init__(self, parent, endpoints, existing=None):
        self.result = None
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("BGP Route")
        self._dlg.geometry("440x300")
        self._dlg.transient(parent)
        self._dlg.grab_set()

        main = ttk.Frame(self._dlg, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Route Name:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)
        self._entry_name = ttk.Entry(main)
        self._entry_name.grid(row=0, column=1, sticky="ew", pady=2)
        if existing:
            self._entry_name.insert(0, existing.get("name", ""))

        ttk.Label(main, text="Endpoint:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=2)
        ep_names = [e["name"] for e in endpoints]
        self._combo_ep = ttk.Combobox(main, values=ep_names, state="readonly")
        self._combo_ep.grid(row=1, column=1, sticky="ew", pady=2)
        if existing and existing.get("endpoint_name") in ep_names:
            self._combo_ep.set(existing["endpoint_name"])
        elif ep_names:
            self._combo_ep.set(ep_names[0])

        ttk.Label(main, text="URL:").grid(row=2, column=0, sticky="e", padx=(0, 6), pady=2)
        self._entry_url = ttk.Entry(main)
        self._entry_url.grid(row=2, column=1, sticky="ew", pady=2)

        ttk.Label(main, text="API Key:").grid(row=3, column=0, sticky="e", padx=(0, 6), pady=2)
        self._entry_key = ttk.Entry(main, show="*")
        self._entry_key.grid(row=3, column=1, sticky="ew", pady=2)

        ttk.Label(main, text="Model:").grid(row=4, column=0, sticky="e", padx=(0, 6), pady=2)
        self._combo_model = ttk.Combobox(main, state="readonly")
        self._combo_model.grid(row=4, column=1, sticky="ew", pady=2)

        main.columnconfigure(1, weight=1)

        self._endpoints = endpoints
        self._combo_ep.bind("<<ComboboxSelected>>", lambda e: self._on_ep_changed())
        self._on_ep_changed()

        if existing:
            self._entry_url.delete(0, "end")
            self._entry_url.insert(0, existing.get("target_url", ""))
            self._entry_key.delete(0, "end")
            self._entry_key.insert(0, existing.get("api_key", ""))
            if existing.get("model"):
                self._combo_model.set(existing["model"])

        btn_frame = ttk.Frame(main)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn_frame, text="Cancel", command=self._dlg.destroy).pack(side="right")
        ttk.Button(btn_frame, text="OK", command=self._ok).pack(side="right", padx=(8, 0))

        self._dlg.wait_window()

    def _on_ep_changed(self):
        ep_name = self._combo_ep.get()
        ep = None
        for e in self._endpoints:
            if e["name"] == ep_name:
                ep = e
                break
        if ep:
            self._entry_url.delete(0, "end")
            self._entry_url.insert(0, normalize_base_url(ep.get("base_url", "")))
            self._entry_key.delete(0, "end")
            self._entry_key.insert(0, ep.get("api_key", ""))
            models = ep.get("models", [])
            self._combo_model["values"] = models
            if ep.get("default_model") and ep["default_model"] in models:
                self._combo_model.set(ep["default_model"])
            elif models:
                self._combo_model.set(models[0])

    def _ok(self):
        ep_name = self._combo_ep.get()
        ep = None
        for e in self._endpoints:
            if e["name"] == ep_name:
                ep = e
                break
        self.result = {
            "name": self._entry_name.get().strip() or ep_name,
            "endpoint_name": ep_name,
            "target_url": self._entry_url.get().strip(),
            "api_key": self._entry_key.get().strip(),
            "model": self._combo_model.get() or "",
            "priority": 99,
        }
        if ep:
            self.result["reasoning_enabled"] = ep.get("reasoning_enabled", True)
            self.result["reasoning_effort"] = ep.get("reasoning_effort", "medium")
            self.result["oauth_provider"] = ep.get("oauth_provider", "")
        self._dlg.destroy()


class BGPPoolEditDialog:
    def __init__(self, parent, existing_name=None):
        self.result = False
        self._existing_name = existing_name
        self._parent_mgr = parent

        self._dlg = tk.Toplevel(parent._dlg if hasattr(parent, "_dlg") else parent)
        title = "Edit BGP Pool" if existing_name else "Create BGP Pool"
        self._dlg.title(title)
        self._dlg.geometry("620x500")
        self._dlg.transient(parent._dlg if hasattr(parent, "_dlg") else parent)
        self._dlg.grab_set()

        data = load_bgp_pools()
        pool = None
        if existing_name:
            for p in data.get("pools", []):
                if p["name"] == existing_name:
                    pool = p
                    break
        if not pool:
            pool = {"name": "", "strategy": "failover", "routes": []}

        main = ttk.Frame(self._dlg, padding=12)
        main.pack(fill="both", expand=True)

        grid = ttk.Frame(main)
        grid.pack(fill="x")
        ttk.Label(grid, text="Pool Name:").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)
        self._entry_name = ttk.Entry(grid)
        self._entry_name.grid(row=0, column=1, sticky="ew", pady=2)
        self._entry_name.insert(0, pool["name"])

        ttk.Label(grid, text="Strategy:").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=2)
        self._combo_strategy = ttk.Combobox(grid, values=["failover", "race"], state="readonly")
        self._combo_strategy.grid(row=1, column=1, sticky="ew", pady=2)
        self._combo_strategy.set(pool.get("strategy", "failover"))
        grid.columnconfigure(1, weight=1)

        ttk.Label(main, text="Routes (double-click to remove):", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 2))

        tree_frame = ttk.Frame(main)
        tree_frame.pack(fill="both", expand=True)
        cols = ("name", "endpoint", "url", "model", "priority")
        self._route_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        for col, heading, w in [("name", "Route Name", 100), ("endpoint", "Endpoint", 120),
                                 ("url", "URL", 160), ("model", "Model", 120), ("priority", "Priority", 60)]:
            self._route_tree.heading(col, text=heading)
            self._route_tree.column(col, width=w, minwidth=50)
        rsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._route_tree.yview)
        self._route_tree.configure(yscrollcommand=rsb.set)
        self._route_tree.pack(side="left", fill="both", expand=True)
        rsb.pack(side="right", fill="y")

        self._routes = []
        for r in pool.get("routes", []):
            self._routes.append(dict(r))
            self._route_tree.insert("", "end", values=(
                r.get("name", ""), r.get("endpoint_name", ""),
                r.get("target_url", ""), r.get("model", ""), r.get("priority", 99)))

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(6, 0))
        ttk.Button(btn_frame, text="Add Route", command=self._add_route).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Edit Route", command=self._edit_route).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Remove Route", command=self._remove_route).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Up", command=lambda: self._move_route(-1)).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Down", command=lambda: self._move_route(1)).pack(side="left", padx=(0, 4))

        save_frame = ttk.Frame(main)
        save_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(save_frame, text="Cancel", command=self._dlg.destroy).pack(side="right")
        ttk.Button(save_frame, text="Save", command=self._save).pack(side="right", padx=(8, 0))

    def _add_route(self):
        endpoints = load_endpoints().get("endpoints", [])
        if not endpoints:
            messagebox.showinfo("Info", "No endpoints configured. Add endpoints first.", parent=self._dlg)
            return
        d = BGPRouteDialog(self._dlg, endpoints, None)
        if d.result:
            r = d.result
            self._routes.append(r)
            self._route_tree.insert("", "end", values=(
                r.get("name", ""), r.get("endpoint_name", ""),
                r.get("target_url", ""), r.get("model", ""), r.get("priority", 99)))

    def _edit_route(self):
        sel = self._route_tree.selection()
        if not sel:
            return
        idx = self._route_tree.index(sel[0])
        endpoints = load_endpoints().get("endpoints", [])
        d = BGPRouteDialog(self._dlg, endpoints, self._routes[idx])
        if d.result:
            r = d.result
            self._routes[idx] = r
            self._route_tree.item(sel[0], values=(
                r.get("name", ""), r.get("endpoint_name", ""),
                r.get("target_url", ""), r.get("model", ""), r.get("priority", 99)))

    def _remove_route(self):
        sel = self._route_tree.selection()
        if not sel:
            return
        idx = self._route_tree.index(sel[0])
        self._route_tree.delete(sel[0])
        del self._routes[idx]

    def _move_route(self, direction):
        sel = self._route_tree.selection()
        if not sel:
            return
        idx = self._route_tree.index(sel[0])
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self._routes):
            return
        route = self._routes.pop(idx)
        self._routes.insert(new_idx, route)
        self._rebuild_routes_tree(new_idx)

    def _rebuild_routes_tree(self, select_idx=None):
        for item in self._route_tree.get_children():
            self._route_tree.delete(item)
        for r in self._routes:
            self._route_tree.insert("", "end", values=(
                r.get("name", ""), r.get("endpoint_name", ""),
                r.get("target_url", ""), r.get("model", ""), r.get("priority", 99)))
        if select_idx is not None:
            children = self._route_tree.get_children()
            if select_idx < len(children):
                self._route_tree.selection_set(children[select_idx])

    def _save(self):
        name = self._entry_name.get().strip()
        if not name:
            return
        strategy = self._combo_strategy.get() or "failover"
        routes = []
        for i, r in enumerate(self._routes):
            if not r.get("target_url"):
                continue
            routes.append({
                "name": r.get("name") or f"Route {i+1}",
                "endpoint_name": r.get("endpoint_name", ""),
                "target_url": r.get("target_url", ""),
                "api_key": r.get("api_key", ""),
                "model": r.get("model", ""),
                "priority": i + 1,
                "reasoning_enabled": True,
                "reasoning_effort": "medium",
            })
        data = load_bgp_pools()
        if self._existing_name:
            data["pools"] = [p for p in data["pools"] if p["name"] != self._existing_name]
        data["pools"].append({"name": name, "strategy": strategy, "routes": routes})
        save_bgp_pools(data)
        self.result = True
        self._dlg.destroy()


class BGPPoolMgr:
    def __init__(self, parent, on_update=None):
        self._parent = parent
        self._on_update = on_update

        self._dlg = tk.Toplevel(parent)
        self._dlg.title("AI BGP -- Pool Manager")
        self._dlg.geometry("660x440")
        self._dlg.transient(parent)

        main = ttk.Frame(self._dlg, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="AI BGP Pools  --  multi-provider routing with automatic failover",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")

        tree_frame = ttk.Frame(main)
        tree_frame.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("name", "routes", "strategy")
        self._tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=10)
        for col, heading, w in [("name", "Pool Name", 180), ("routes", "Routes", 280), ("strategy", "Strategy", 100)]:
            self._tree.heading(col, text=heading)
            self._tree.column(col, width=w, minwidth=60)
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_frame, text="Create Pool", command=self._add_pool).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Edit Pool", command=self._edit_pool).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Delete Pool", command=self._del_pool).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Close", command=self._dlg.destroy).pack(side="right")

        self._rebuild()

    def _rebuild(self):
        for item in self._tree.get_children():
            self._tree.delete(item)
        for pool in load_bgp_pools().get("pools", []):
            routes_str = " -> ".join(f'{r.get("name","?")}/{r.get("model","?")}' for r in pool.get("routes", []))
            self._tree.insert("", "end", values=(pool["name"], routes_str, pool.get("strategy", "failover")))

    def _selected_name(self):
        sel = self._tree.selection()
        if not sel:
            return None
        return self._tree.item(sel[0])["values"][0]

    def _add_pool(self):
        d = BGPPoolEditDialog(self, None)
        self._dlg.wait_window(d._dlg)
        if d.result:
            self._rebuild()
            if self._on_update:
                self._on_update()

    def _edit_pool(self):
        name = self._selected_name()
        if not name:
            return
        d = BGPPoolEditDialog(self, name)
        self._dlg.wait_window(d._dlg)
        if d.result:
            self._rebuild()
            if self._on_update:
                self._on_update()

    def _del_pool(self):
        name = self._selected_name()
        if not name:
            return
        if not messagebox.askyesno("Delete", f'Delete BGP pool "{name}"?', parent=self._dlg):
            return
        data = load_bgp_pools()
        data["pools"] = [p for p in data["pools"] if p["name"] != name]
        save_bgp_pools(data)
        self._rebuild()
        if self._on_update:
            self._on_update()
