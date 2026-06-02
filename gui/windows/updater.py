"""Codex Desktop updater window — auto-update and rebuild from source."""
import json
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
import urllib.request
from pathlib import Path

from codex_launcher_lib import IS_WINDOWS, detect_codex_desktop, detect_codex_cli, open_file


_UPSTREAM_REPO = "ilysenko/codex-desktop-linux"
_UPDATER_BIN = shutil.which("codex-update-manager") or ""
_UPDATER_SERVICE_LOG = Path.home() / ".local/state/codex-update-manager/service.log"


def _get_updater_status():
    if not _UPDATER_BIN:
        return None
    try:
        out = subprocess.run(
            [_UPDATER_BIN, "status", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return json.loads(out.stdout.strip())
    except Exception:
        pass
    return None


def _get_installed_desktop_version():
    if IS_WINDOWS:
        info = detect_codex_desktop()
        if info and info[0]:
            return info[0]
        return None
    try:
        out = subprocess.run(
            ["dpkg-query", "-W", "-f", "${Version}", "codex-desktop"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _get_upstream_info():
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{_UPSTREAM_REPO}/commits?per_page=1",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "codex-launcher"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        commits = json.loads(resp.read())
        if commits:
            c = commits[0]
            return {
                "sha": c["sha"][:12],
                "date": c["commit"]["committer"]["date"][:10],
                "message": c["commit"]["message"].split("\n")[0][:80],
            }
    except Exception:
        pass
    return None


def _is_updater_service_active():
    if IS_WINDOWS:
        return False
    try:
        out = subprocess.run(
            ["systemctl", "--user", "is-active", "codex-update-manager.service"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() == "active"
    except Exception:
        return False


class CodexUpdaterWindow:
    def __init__(self, parent):
        self._parent = parent
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("Codex Desktop Updater")
        self._dlg.geometry("600x620")
        self._dlg.transient(parent)

        main = ttk.Frame(self._dlg, padding=12)
        main.pack(fill="both", expand=True)

        hdr = ttk.Frame(main)
        hdr.pack(fill="x")
        ttk.Label(hdr, text="Codex Desktop Updater",
                  font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Label(hdr, text="Auto-update from github.com/ilysenko/codex-desktop-linux",
                  foreground="gray", font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))

        if IS_WINDOWS:
            info_frame = ttk.LabelFrame(main, text="Status", padding=8)
            info_frame.pack(fill="x", pady=(8, 0))
            ttk.Label(info_frame,
                      text="Auto-updater is Linux-only (uses dpkg/codex-update-manager).\n"
                           "On Windows, use the official Codex Desktop installer\n"
                           "or download updates from https://codex.desktop.openai.com",
                      foreground="#d29922").pack(anchor="w")

            upstream_frame = ttk.LabelFrame(main, text="Upstream (GitHub)", padding=8)
            upstream_frame.pack(fill="x", pady=(8, 0))
            self._upstream_lbl = ttk.Label(upstream_frame, text="Checking...", foreground="gray")
            self._upstream_lbl.pack(anchor="w")

            self._log_text = scrolledtext.ScrolledText(main, height=10, state="disabled",
                                                        wrap="word", font=("Consolas", 9))
            self._log_text.pack(fill="both", expand=True, pady=(8, 0))

            ttk.Button(main, text="Close",
                       command=self._dlg.destroy).pack(side="right", pady=(8, 0))
            self._log("Updater initialized (Windows mode)")
            threading.Thread(target=self._refresh_status, daemon=True).start()
            return

        info_frame = ttk.LabelFrame(main, text="Current Installation", padding=8)
        info_frame.pack(fill="x", pady=(8, 0))

        info_grid = ttk.Frame(info_frame)
        info_grid.pack(fill="x")
        info_grid.columnconfigure(1, weight=1)

        self._installed_lbl = ttk.Label(info_grid, text="Checking...", foreground="gray")
        self._upstream_lbl = ttk.Label(info_grid, text="Checking...", foreground="gray")
        self._service_lbl = ttk.Label(info_grid, text="Checking...", foreground="gray")
        self._candidate_lbl = ttk.Label(info_grid, text="--", foreground="gray")
        self._cli_lbl = ttk.Label(info_grid, text="Checking...", foreground="gray")

        for row_idx, (text, lbl) in enumerate([
            ("Installed:", self._installed_lbl),
            ("Upstream:", self._upstream_lbl),
            ("Service:", self._service_lbl),
            ("Candidate:", self._candidate_lbl),
            ("CLI:", self._cli_lbl),
        ]):
            ttk.Label(info_grid, text=text).grid(row=row_idx, column=0, sticky="e", padx=(0, 6), pady=1)
            lbl.grid(row=row_idx, column=1, sticky="w", pady=1)

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(8, 0))
        self._check_btn = ttk.Button(btn_frame, text="Check for Updates", command=self._check_updates)
        self._check_btn.pack(side="left", padx=(0, 4))
        self._install_btn = ttk.Button(btn_frame, text="Install Update", command=self._install_update, state="disabled")
        self._install_btn.pack(side="left", padx=(0, 4))
        self._rollback_btn = ttk.Button(btn_frame, text="Rollback", command=self._rollback, state="disabled")
        self._rollback_btn.pack(side="left", padx=(0, 4))

        ttk.Label(main, text="Auto-updater: only detects new upstream Codex.dmg from OpenAI.\n"
                             "For latest community patches, use Rebuild from Source below.",
                  foreground="gray", font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

        svc_frame = ttk.Frame(main)
        svc_frame.pack(fill="x", pady=(4, 0))
        ttk.Button(svc_frame, text="Start Service",
                   command=lambda: self._svc_cmd("start")).pack(side="left", padx=(0, 4))
        ttk.Button(svc_frame, text="Stop Service",
                   command=lambda: self._svc_cmd("stop")).pack(side="left", padx=(0, 4))
        ttk.Button(svc_frame, text="Enable Autostart",
                   command=lambda: self._svc_cmd("enable")).pack(side="left", padx=(0, 4))

        rebuild_frame = ttk.LabelFrame(main, text="Rebuild from Source (Recommended)", padding=4)
        rebuild_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(rebuild_frame,
                  text="For latest community fixes from ilysenko/codex-desktop-linux,\n"
                       "use Clone/Pull then Build & Install to rebuild a fresh .deb from source.",
                  foreground="gray", font=("Segoe UI", 8)).pack(anchor="w")
        rebuild_btns = ttk.Frame(rebuild_frame)
        rebuild_btns.pack(fill="x", pady=(4, 0))
        self._clone_btn = ttk.Button(rebuild_btns, text="Clone / Pull Repo", command=self._clone_or_pull)
        self._clone_btn.pack(side="left", padx=(0, 4))
        self._build_btn = ttk.Button(rebuild_btns, text="Build & Install .deb",
                                     command=self._build_and_install, state="disabled")
        self._build_btn.pack(side="left", padx=(0, 4))
        self._rebuild_dir = Path.home() / ".cache/codex-launcher/codex-desktop-linux"
        ttk.Label(rebuild_frame, text=f"Build dir: {self._rebuild_dir}",
                  foreground="gray", font=("Segoe UI", 8)).pack(anchor="w")

        self._log_text = scrolledtext.ScrolledText(main, height=8, state="disabled",
                                                    wrap="word", font=("Consolas", 9))
        self._log_text.pack(fill="both", expand=True, pady=(4, 0))

        bb = ttk.Frame(main)
        bb.pack(fill="x", pady=(4, 0))
        ttk.Button(bb, text="Clear Log", command=self._clear_log).pack(side="left")
        ttk.Button(bb, text="View Service Log", command=self._view_service_log).pack(side="left", padx=(8, 0))
        ttk.Button(bb, text="Close", command=self._dlg.destroy).pack(side="right")

        self._log("Updater initialized")
        threading.Thread(target=self._refresh_status, daemon=True).start()

    def _log(self, msg):
        def _append():
            self._log_text.configure(state="normal")
            self._log_text.insert("end", msg + "\n")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")
        try:
            self._dlg.after(0, _append)
        except Exception:
            pass

    def _clear_log(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _refresh_status(self):
        installed = _get_installed_desktop_version()
        upstream = _get_upstream_info()
        status = _get_updater_status()
        svc_active = _is_updater_service_active()
        cli_info = detect_codex_cli()

        def _update():
            if IS_WINDOWS:
                if installed:
                    self._installed_lbl.configure(text=installed, foreground="#2ea043")
                else:
                    self._installed_lbl.configure(text="Not detected", foreground="#d29922")
                if upstream:
                    self._upstream_lbl.configure(
                        text=f"{upstream['date']}  ({upstream['sha']}) {upstream['message']}",
                        foreground="#2ea043")
                else:
                    self._upstream_lbl.configure(text="Could not fetch", foreground="#d29922")
                return

            if installed:
                self._installed_lbl.configure(text=installed, foreground="#2ea043")
            else:
                self._installed_lbl.configure(text="Not installed via dpkg", foreground="#d29922")

            if upstream:
                self._upstream_lbl.configure(
                    text=f"{upstream['date']}  ({upstream['sha']}) {upstream['message']}",
                    foreground="#2ea043")
            else:
                self._upstream_lbl.configure(text="Could not fetch", foreground="#d29922")

            if svc_active:
                self._service_lbl.configure(text="active", foreground="#2ea043")
            else:
                self._service_lbl.configure(text="inactive", foreground="#d29922")

            if status:
                cand = status.get("candidate_version")
                if cand:
                    self._candidate_lbl.configure(text=cand, foreground="#58a6ff")
                    self._install_btn.configure(state="normal")
                else:
                    self._candidate_lbl.configure(text="No update pending", foreground="gray")

                cli_ver = status.get("cli_installed_version", "")
                cli_latest = status.get("cli_latest_version", "")
                cli_status = status.get("cli_status", "")
                if cli_ver:
                    color = "#2ea043" if cli_status == "up_to_date" else "#d29922"
                    extra = " (up to date)" if cli_status == "up_to_date" else f" -> {cli_latest}"
                    self._cli_lbl.configure(text=f"{cli_ver}{extra}", foreground=color)

                has_rollback = bool(status.get("last_known_good_version"))
                self._rollback_btn.configure(state="normal" if has_rollback else "disabled")
            else:
                if not _UPDATER_BIN:
                    self._candidate_lbl.configure(text="codex-update-manager not found", foreground="#d29922")
                else:
                    self._candidate_lbl.configure(text="Status unavailable", foreground="#d29922")

            if hasattr(self, '_rebuild_dir') and self._rebuild_dir.exists():
                self._build_btn.configure(state="normal")

        self._dlg.after(0, _update)
        self._log(f"Status: installed={installed} svc={'active' if svc_active else 'inactive'}")

    def _check_updates(self):
        self._check_btn.configure(state="disabled")
        self._log("Checking for updates...")

        def _run():
            try:
                out = subprocess.run(
                    [_UPDATER_BIN, "check-now"],
                    capture_output=True, text=True, timeout=120,
                )
                self._log(f"check-now: rc={out.returncode}")
                if out.stdout:
                    self._log(out.stdout.strip())
                if out.stderr:
                    self._log(out.stderr.strip())
            except Exception as e:
                self._log(f"Error: {e}")
            finally:
                self._dlg.after(0, lambda: self._check_btn.configure(state="normal"))
                self._refresh_status()

        threading.Thread(target=_run, daemon=True).start()

    def _install_update(self):
        self._install_btn.configure(state="disabled")
        self._log("Installing update (may prompt for sudo)...")

        def _run():
            try:
                desktop_running = False
                if not IS_WINDOWS:
                    try:
                        out = subprocess.run(
                            ["pgrep", "-f", "/opt/codex-desktop/electron"],
                            capture_output=True, text=True, timeout=5,
                        )
                        desktop_running = out.returncode == 0
                    except Exception:
                        pass
                    if desktop_running:
                        self._log("Codex Desktop is running. Closing it to proceed with update...")
                        subprocess.run(["pkill", "-f", "/opt/codex-desktop/electron"], timeout=10)
                        time.sleep(3)
                        self._log("Desktop closed.")

                out = subprocess.run(
                    [_UPDATER_BIN, "install-ready"],
                    capture_output=True, text=True, timeout=300,
                )
                self._log(f"install-ready: rc={out.returncode}")
                combined = (out.stdout or "") + (out.stderr or "")
                if out.stdout:
                    self._log(out.stdout.strip())
                if out.stderr:
                    self._log(out.stderr.strip())
                if out.returncode == 0 and "successfully" in combined.lower():
                    self._log("Update installed successfully!")
                elif "No Codex Desktop update is ready" in combined:
                    self._log("No update is ready. Run 'Check for Updates' first, or use Clone/Pull + Build & Install.")
                    self._dlg.after(0, lambda: self._install_btn.configure(state="disabled"))
                elif "Close it to install" in combined:
                    self._log("Desktop was still running. Close Desktop manually and try again.")
                    self._dlg.after(0, lambda: self._install_btn.configure(state="normal"))
                elif out.returncode == 0:
                    self._log("install-ready returned OK but no confirmation. Output: " + combined[:200])
                    self._dlg.after(0, lambda: self._install_btn.configure(state="disabled"))
                else:
                    self._log("Update may not have completed. Check the log above.")
                    self._dlg.after(0, lambda: self._install_btn.configure(state="normal"))
            except Exception as e:
                self._log(f"Error: {e}")
            finally:
                self._refresh_status()

        threading.Thread(target=_run, daemon=True).start()

    def _rollback(self):
        self._rollback_btn.configure(state="disabled")
        self._log("Rolling back to previous version...")

        def _run():
            try:
                out = subprocess.run(
                    [_UPDATER_BIN, "rollback"],
                    capture_output=True, text=True, timeout=300,
                )
                self._log(f"rollback: rc={out.returncode}")
                if out.stdout:
                    self._log(out.stdout.strip())
                if out.stderr:
                    self._log(out.stderr.strip())
            except Exception as e:
                self._log(f"Error: {e}")
            finally:
                self._refresh_status()

        threading.Thread(target=_run, daemon=True).start()

    def _svc_cmd(self, action):
        cmd_map = {
            "start": ["systemctl", "--user", "start", "codex-update-manager.service"],
            "stop": ["systemctl", "--user", "stop", "codex-update-manager.service"],
            "enable": ["systemctl", "--user", "enable", "--now", "codex-update-manager.service"],
        }
        cmd = cmd_map.get(action)
        if not cmd:
            return
        self._log(f"Running: {' '.join(cmd)}")

        def _run():
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                self._log(f"{action}: rc={out.returncode}")
                if out.stderr:
                    self._log(out.stderr.strip())
            except Exception as e:
                self._log(f"Error: {e}")
            finally:
                self._refresh_status()

        threading.Thread(target=_run, daemon=True).start()

    def _clone_or_pull(self):
        self._clone_btn.configure(state="disabled")
        self._log(f"Clone/pull {_UPSTREAM_REPO}...")

        def _run():
            try:
                self._rebuild_dir.parent.mkdir(parents=True, exist_ok=True)
                if self._rebuild_dir.exists():
                    self._log("Pulling latest changes...")
                    out = subprocess.run(
                        ["git", "pull", "--ff-only"],
                        capture_output=True, text=True, timeout=60,
                        cwd=str(self._rebuild_dir),
                    )
                else:
                    self._log("Cloning repository...")
                    out = subprocess.run(
                        ["git", "clone", "--depth=1",
                         f"https://github.com/{_UPSTREAM_REPO}.git", str(self._rebuild_dir)],
                        capture_output=True, text=True, timeout=120,
                    )
                self._log(f"git: rc={out.returncode}")
                if out.stdout:
                    self._log(out.stdout.strip()[:200])
                if out.stderr:
                    self._log(out.stderr.strip()[:200])
                if out.returncode == 0:
                    self._log("Repository ready.")
                    self._dlg.after(0, lambda: self._build_btn.configure(state="normal"))
            except Exception as e:
                self._log(f"Error: {e}")
            finally:
                self._dlg.after(0, lambda: self._clone_btn.configure(state="normal"))

        threading.Thread(target=_run, daemon=True).start()

    def _build_and_install(self):
        self._build_btn.configure(state="disabled")
        self._log("Building Codex Desktop from source (this may take several minutes)...")

        def _run():
            try:
                self._log("Installing build dependencies...")
                out = subprocess.run(
                    ["bash", "-c", "bash scripts/install-deps.sh"],
                    capture_output=True, text=True, timeout=300,
                    cwd=str(self._rebuild_dir),
                )
                self._log(f"install-deps: rc={out.returncode}")
                if out.stderr:
                    self._log(out.stderr.strip()[-300:])

                self._log("Building app from upstream DMG...")
                out = subprocess.run(
                    ["make", "build-app-fresh"],
                    capture_output=True, text=True, timeout=600,
                    cwd=str(self._rebuild_dir),
                )
                self._log(f"build-app-fresh: rc={out.returncode}")
                if out.stderr:
                    self._log(out.stderr.strip()[-300:])

                if out.returncode != 0:
                    self._log("Build failed. Check log above.")
                    return

                self._log("Building .deb package...")
                out = subprocess.run(
                    ["make", "deb"],
                    capture_output=True, text=True, timeout=120,
                    cwd=str(self._rebuild_dir),
                )
                self._log(f"deb: rc={out.returncode}")
                if out.stderr:
                    self._log(out.stderr.strip()[-300:])

                if out.returncode != 0:
                    self._log("Deb build failed.")
                    return

                deb_files = list((self._rebuild_dir / "dist").glob("codex-desktop_*.deb"))
                if not deb_files:
                    self._log("No .deb found in dist/")
                    return

                deb_path = deb_files[-1]
                self._log(f"Installing {deb_path.name}...")
                out = subprocess.run(
                    ["pkexec", "dpkg", "-i", str(deb_path)],
                    capture_output=True, text=True, timeout=120,
                )
                self._log(f"dpkg -i: rc={out.returncode}")
                if out.stdout:
                    self._log(out.stdout.strip()[:300])
                if out.stderr:
                    self._log(out.stderr.strip()[:300])

                if out.returncode == 0:
                    self._log("Codex Desktop updated successfully!")
                else:
                    self._log("Installation failed. Try: sudo dpkg -i " + str(deb_path))
            except Exception as e:
                self._log(f"Error: {e}")
            finally:
                self._refresh_status()

        threading.Thread(target=_run, daemon=True).start()

    def _view_service_log(self):
        if _UPDATER_SERVICE_LOG.exists():
            open_file(str(_UPDATER_SERVICE_LOG))
        else:
            self._log(f"Service log not found at {_UPDATER_SERVICE_LOG}")
