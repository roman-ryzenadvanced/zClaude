"""Multi-lane benchmark window — A/B/C endpoint comparison."""
import json
import threading
import time
import tkinter as tk
from tkinter import ttk
import urllib.request

from codex_launcher_lib import normalize_base_url, UA, get_endpoint, load_endpoints


class BenchmarkWindow:
    _BENCH_PROMPT = "In exactly 3 bullet points, explain why the sky is blue."
    _BENCH_TOOLS = [{"type": "function", "function": {"name": "get_weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]

    def __init__(self, parent):
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("Model Benchmark")
        self._dlg.geometry("820x560")
        self._dlg.transient(parent)
        self._running = False
        self._ep_data = load_endpoints()

        main = ttk.Frame(self._dlg, padding=10)
        main.pack(fill="both", expand=True)

        hdr = ttk.Frame(main)
        hdr.pack(fill="x")
        ttk.Label(hdr, text="Multi-Provider Benchmark", font=("Segoe UI", 11, "bold")).pack(side="left")
        self._run_btn = ttk.Button(hdr, text="Run Benchmark", command=self._run)
        self._run_btn.pack(side="right")

        lanes_frame = ttk.Frame(main)
        lanes_frame.pack(fill="x", pady=(8, 0))

        self._lanes = []
        self._c_var = tk.BooleanVar(value=False)
        for i, lane_label in enumerate(["A", "B", "C"]):
            if i == 2:
                lf = ttk.LabelFrame(lanes_frame, text="Lane C (optional)")
                cb = ttk.Checkbutton(lanes_frame, text="Enable Lane C", variable=self._c_var,
                                     command=lambda: lf.configure() if not self._c_var.get() else None)
            else:
                lf = ttk.LabelFrame(lanes_frame, text=f"Lane {lane_label}")
            lf.pack(side="left", fill="both", expand=True, padx=(0, 4 if i < 2 else 0))

            ep_frame = ttk.Frame(lf, padding=4)
            ep_frame.pack(fill="x")
            ttk.Label(ep_frame, text="Endpoint:").pack(side="left")
            ep_combo = ttk.Combobox(ep_frame, values=[e["name"] for e in self._ep_data.get("endpoints", [])], state="readonly")
            ep_combo.pack(side="left", fill="x", expand=True, padx=(4, 0))

            m_frame = ttk.Frame(lf, padding=4)
            m_frame.pack(fill="x")
            ttk.Label(m_frame, text="Model:").pack(side="left")
            m_combo = ttk.Combobox(m_frame, state="readonly")
            m_combo.pack(side="left", fill="x", expand=True, padx=(4, 0))

            ep_combo.bind("<<ComboboxSelected>>", lambda e, mc=m_combo: self._update_lane_models(ep_combo, mc))
            self._lanes.append({"ep": ep_combo, "model": m_combo})

        default_name = self._ep_data.get("default")
        eps = self._ep_data.get("endpoints", [])
        if default_name:
            self._lanes[0]["ep"].set(default_name)
        if len(eps) > 1:
            self._lanes[1]["ep"].set(eps[1]["name"])
        elif eps:
            self._lanes[1]["ep"].set(eps[0]["name"])
        if len(eps) > 2:
            self._lanes[2]["ep"].set(eps[2]["name"])
        elif len(eps) > 1:
            self._lanes[2]["ep"].set(eps[1]["name"])

        tests_frame = ttk.Frame(main)
        tests_frame.pack(fill="x", pady=(8, 0))
        self._test_ttft = tk.BooleanVar(value=True)
        self._test_total = tk.BooleanVar(value=True)
        self._test_tools = tk.BooleanVar(value=True)
        self._test_tps = tk.BooleanVar(value=True)
        ttk.Checkbutton(tests_frame, text="Time to First Token", variable=self._test_ttft).pack(side="left")
        ttk.Checkbutton(tests_frame, text="Total Latency", variable=self._test_total).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(tests_frame, text="Tool Call", variable=self._test_tools).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(tests_frame, text="Tokens/sec", variable=self._test_tps).pack(side="left", padx=(8, 0))

        results_frame = ttk.Frame(main)
        results_frame.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("test", "a", "b", "c", "winner")
        self._results_tree = ttk.Treeview(results_frame, columns=cols, show="headings", height=6)
        for col, heading in [("test", "Test"), ("a", "Lane A"), ("b", "Lane B"), ("c", "Lane C"), ("winner", "Winner")]:
            self._results_tree.heading(col, text=heading)
            self._results_tree.column(col, width=150, minwidth=80)
        rsb = ttk.Scrollbar(results_frame, orient="vertical", command=self._results_tree.yview)
        self._results_tree.configure(yscrollcommand=rsb.set)
        self._results_tree.pack(side="left", fill="both", expand=True)
        rsb.pack(side="right", fill="y")

        self._status_var = tk.StringVar(value="Select endpoints and models per lane, then Run Benchmark.")
        ttk.Label(main, textvariable=self._status_var).pack(anchor="w", pady=(4, 0))

    def _update_lane_models(self, ep_combo, model_combo):
        name = ep_combo.get()
        if not name:
            return
        ep = get_endpoint(name)
        models = (ep or {}).get("models", [])
        model_combo["values"] = models
        if models:
            model_combo.set(models[0])

    def _collect_lanes(self):
        active = []
        for i, lane in enumerate(self._lanes):
            if i == 2 and not self._c_var.get():
                continue
            ep_name = lane["ep"].get()
            model = lane["model"].get()
            if not ep_name or not model:
                continue
            ep = get_endpoint(ep_name)
            if not ep:
                continue
            active.append({"ep": ep, "model": model, "label": f"{ep_name}/{model}"})
        return active

    def _bench_single(self, ep, model, stream, with_tools=False):
        url = normalize_base_url(ep.get("base_url", ""))
        key = (ep.get("api_key") or "").strip()
        bt = ep.get("backend_type", "openai-compat")
        if bt == "anthropic":
            test_url = f"{url}/v1/messages"
            headers = {"User-Agent": UA, "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            body = {"model": model, "max_tokens": 100, "stream": stream,
                    "messages": [{"role": "user", "content": self._BENCH_PROMPT}]}
            if with_tools:
                body["tools"] = self._BENCH_TOOLS
                body["messages"] = [{"role": "user", "content": "Use get_weather for Paris"}]
            data = json.dumps(body).encode()
        else:
            test_url = f"{url}/chat/completions"
            headers = {"User-Agent": UA, "Authorization": f"Bearer {key}", "content-type": "application/json"}
            body = {"model": model, "max_tokens": 100, "stream": stream,
                    "messages": [{"role": "user", "content": self._BENCH_PROMPT}]}
            if with_tools:
                body["tools"] = self._BENCH_TOOLS
                body["messages"] = [{"role": "user", "content": "Use get_weather for Paris"}]
            data = json.dumps(body).encode()

        req = urllib.request.Request(test_url, data=data, headers=headers, method="POST")
        t0 = time.time()
        ttft = None
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            if stream:
                first_chunk_time = None
                chunks = []
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                        ttft = first_chunk_time - t0
                    chunks.append(chunk)
                total = time.time() - t0
                result_text = b"".join(chunks).decode(errors="replace")[:300]
            else:
                raw = resp.read()
                total = time.time() - t0
                result_text = raw.decode(errors="replace")[:300]
                payload = json.loads(raw)
                choices = payload.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    if with_tools:
                        tcs = msg.get("tool_calls", [])
                        has_tools = len(tcs) > 0
                        return {"ttft": ttft or total, "total": total,
                                "detail": f"tools={has_tools}, tok={payload.get('usage', {}).get('total_tokens', '?')}"}
                    content = msg.get("content", "")[:50]
                    return {"ttft": ttft or total, "total": total,
                            "detail": f"{content[:40]}... tok={payload.get('usage', {}).get('total_tokens', '?')}"}
            return {"ttft": ttft or total, "total": total, "detail": result_text[:60]}
        except Exception as e:
            total = time.time() - t0
            return {"ttft": ttft or total, "total": total, "detail": f"Error: {str(e)[:40]}"}

    def _bench_tps(self, ep, model):
        url = normalize_base_url(ep.get("base_url", ""))
        key = (ep.get("api_key") or "").strip()
        bt = ep.get("backend_type", "openai-compat")
        prompt = "Write a detailed paragraph about artificial intelligence in at least 150 words."
        max_tok = 512
        if bt == "anthropic":
            test_url = f"{url}/v1/messages"
            headers = {"User-Agent": UA, "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        else:
            test_url = f"{url}/chat/completions"
            headers = {"User-Agent": UA, "Authorization": f"Bearer {key}", "content-type": "application/json"}
        body = json.dumps({"model": model, "max_tokens": max_tok, "stream": True,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(test_url, data=body, headers=headers, method="POST")
        t0 = time.time()
        first_token_t = None
        token_count = 0
        try:
            resp = urllib.request.urlopen(req, timeout=90)
            buf = b""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                if first_token_t is None:
                    first_token_t = time.time()
                buf += chunk
            total = time.time() - t0
            text = buf.decode(errors="replace")
            for line in text.split("\n"):
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        d = json.loads(line[6:])
                        content = d.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content:
                            token_count += max(1, len(content) / 4)
                    except Exception:
                        pass
            if token_count == 0:
                token_count = max(1, len(text) / 4)
            gen_time = (time.time() - first_token_t) if first_token_t else total
            tps = token_count / gen_time if gen_time > 0 else 0
            return {"tps": tps, "tokens": int(token_count), "gen_time": gen_time, "total": total,
                    "detail": f"{int(token_count)} tok / {gen_time:.1f}s"}
        except Exception as e:
            total = time.time() - t0
            return {"tps": 0, "tokens": 0, "gen_time": total, "total": total, "detail": f"Error: {str(e)[:40]}"}

    def _run(self):
        if self._running:
            return
        lanes = self._collect_lanes()
        if len(lanes) < 2:
            self._status_var.set("Need at least 2 lanes with endpoint + model selected.")
            return
        self._running = True
        self._run_btn.configure(state="disabled")
        for item in self._results_tree.get_children():
            self._results_tree.delete(item)
        self._status_var.set("Running benchmark...")
        threading.Thread(target=self._run_bench, args=(lanes,), daemon=True).start()

    def _run_bench(self, lanes):
        results = []
        tests = []
        if self._test_ttft.get():
            tests.append(("TTFT (stream)", True, False))
        if self._test_total.get():
            tests.append(("Total latency", False, False))
        if self._test_tools.get():
            tests.append(("Tool call", False, True))
        run_tps = self._test_tps.get()

        for test_name, stream, tools in tests:
            lane_results = []
            for lane in lanes:
                label = lane["label"]
                self._dlg.after(0, lambda l=label: self._status_var.set(f"Running {test_name}: {l}..."))
                r = self._bench_single(lane["ep"], lane["model"], stream, tools)
                lane_results.append((label, r))

            metric = "ttft" if stream else "total"
            values = [(lr[0], lr[1][metric]) for lr in lane_results]
            sorted_v = sorted(values, key=lambda x: x[1])
            best_val = sorted_v[0][1]
            second_val = sorted_v[1][1] if len(sorted_v) > 1 else best_val + 1
            if best_val < second_val * 0.85:
                winner = sorted_v[0][0]
            else:
                winner = "Tie"

            cols = []
            for lr in lane_results:
                v = lr[1][metric]
                cols.append(f"{v:.2f}s ({lr[1]['detail'][:30]})")
            while len(cols) < 3:
                cols.append("--")
            cols.append(winner)
            results.append(tuple([test_name] + cols))

        if run_tps:
            lane_tps = []
            for lane in lanes:
                label = lane["label"]
                self._dlg.after(0, lambda l=label: self._status_var.set(f"Tokens/sec: {l}..."))
                r = self._bench_tps(lane["ep"], lane["model"])
                lane_tps.append((label, r))

            tps_vals = [(lt[0], lt[1]["tps"]) for lt in lane_tps]
            sorted_tps = sorted(tps_vals, key=lambda x: x[1], reverse=True)
            best_tps = sorted_tps[0][1]
            second_tps = sorted_tps[1][1] if len(sorted_tps) > 1 else 0
            if best_tps > 0 and second_tps > 0 and best_tps > second_tps * 1.15:
                winner_tps = sorted_tps[0][0]
            else:
                winner_tps = "Tie"

            cols_tps = []
            for lt in lane_tps:
                tps = lt[1]["tps"]
                cols_tps.append(f"{tps:.1f} t/s ({lt[1]['detail'][:25]})")
            while len(cols_tps) < 3:
                cols_tps.append("--")
            cols_tps.append(winner_tps)
            results.append(tuple(["Tokens/sec"] + cols_tps))

        def _show():
            for row in results:
                self._results_tree.insert("", "end", values=row)
            self._status_var.set("Benchmark complete.")
            self._running = False
            self._run_btn.configure(state="normal")

        self._dlg.after(0, _show)
