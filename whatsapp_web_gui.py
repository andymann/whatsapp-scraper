#!/usr/bin/env python3
"""
whatsapp_web_gui.py

A desktop UI (Tkinter, stdlib only) around whatsapp_web_scraper.py.

Two ways to get a logged-in WhatsApp Web session:

  1. "Launch new browser" — opens a Playwright-managed Chromium window at
     web.whatsapp.com showing a QR code to scan. Session persists on disk
     (.wweb_profile/), so this is normally a one-time step.

  2. "Connect to existing Chrome" — attaches to an already-running Chrome
     via the DevTools Protocol (CDP). IMPORTANT, and this is a real Chrome
     limitation, not a bug here: Chrome cannot expose its debugging port
     after the fact — it must be *launched* with --remote-debugging-port.
     So this mode cannot reach into a tab that's open right now unless that
     Chrome was already started with that flag. Use the "Launch debuggable
     Chrome" button, which: (a) refuses to run if Chrome is already open
     (quit it first — Chrome ignores the flag on a second launch of an
     already-running instance), then (b) relaunches Chrome pointed at your
     normal profile directory with the debug port enabled. Because it's
     your normal profile, your existing WhatsApp Web session (persisted on
     disk from ordinary use) comes back without a new QR scan.

Requires:
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from wa_common import resolve_timeframe

FROZEN = getattr(sys, "frozen", False)  # running from the PyInstaller .app

if FROZEN:
    # build_macos_app.sh copies Chromium into Contents/Resources/ms-playwright
    # (sys.executable is Contents/MacOS/<name>); point Playwright there instead
    # of ~/Library/Caches/ms-playwright.
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(Path(sys.executable).resolve().parent.parent / "Resources" / "ms-playwright"),
    )
    # The app bundle is read-only (and signed), so keep the login session
    # in the user's Application Support folder instead.
    DEFAULT_PROFILE_DIR = (
        Path.home() / "Library" / "Application Support" / "WhatsApp Scraper" / ".wweb_profile"
    )
else:
    DEFAULT_PROFILE_DIR = Path(__file__).with_name(".wweb_profile")
DEFAULT_CDP_ENDPOINT = "http://localhost:9222"
DEFAULT_CDP_PORT = 9222

TIMEFRAME_PRESETS = {
    "Last hour": {"hours": 1},
    "Last 24 hours": {"hours": 24},
    "Last 7 days": {"days": 7},
    "Custom": {},
}


# --------------------------------------------------------------------------- #
# Platform helpers for "launch debuggable Chrome"
# --------------------------------------------------------------------------- #

def find_chrome_binary() -> Optional[str]:
    if sys.platform == "darwin":
        candidate = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        return candidate if Path(candidate).exists() else None
    if sys.platform.startswith("win"):
        for base in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                     r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
            if Path(base).exists():
                return base
        return None
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
        found = subprocess.run(["which", name], capture_output=True, text=True)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    return None


def default_chrome_profile_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    if sys.platform.startswith("win"):
        return Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    return Path.home() / ".config" / "google-chrome"


def chrome_is_running() -> Optional[bool]:
    """Best-effort check; returns None if we can't tell."""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True)
        elif sys.platform.startswith("win"):
            result = subprocess.run(["tasklist"], capture_output=True, text=True)
            return "chrome.exe" in result.stdout
        else:
            result = subprocess.run(["pgrep", "-x", "chrome"], capture_output=True)
        return result.returncode == 0
    except Exception:
        return None


def launch_debuggable_chrome(port: int = DEFAULT_CDP_PORT) -> str:
    binary = find_chrome_binary()
    if binary is None:
        raise RuntimeError(
            "Could not find a Chrome install automatically. Launch it yourself with:\n"
            f"  <path to chrome> --remote-debugging-port={port}"
        )
    subprocess.Popen(
        [binary, f"--remote-debugging-port={port}", "https://web.whatsapp.com"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return f"http://localhost:{port}"


# --------------------------------------------------------------------------- #
# Background worker — owns the single Playwright/browser lifecycle
# --------------------------------------------------------------------------- #

class Worker(threading.Thread):
    def __init__(self, result_queue: queue.Queue):
        super().__init__(daemon=True)
        self.cmd_queue: queue.Queue = queue.Queue()
        self.result_queue = result_queue
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def submit(self, cmd: str, **payload) -> None:
        self.cmd_queue.put((cmd, payload))

    def run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.result_queue.put((
                "error",
                "Playwright is required:\n    pip install playwright\n    playwright install chromium",
            ))
            return

        self.playwright = sync_playwright().start()
        try:
            while True:
                cmd, payload = self.cmd_queue.get()
                if cmd == "shutdown":
                    break
                try:
                    getattr(self, f"_cmd_{cmd}")(**payload)
                except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
                    self.result_queue.put(("error", self._friendly_error(exc)))
        finally:
            self._cleanup()

    def _log(self, msg: str) -> None:
        self.result_queue.put(("log", msg))

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        text = str(exc)
        if "Executable doesn't exist" in text and "playwright install" in text:
            return (
                "Playwright's browser isn't downloaded yet. Run this once in a terminal, "
                "then try again:\n\n    playwright install chromium\n\n"
                f"(Original error: {text.splitlines()[0]})"
            )
        return text

    def _cmd_connect_new(self, profile_dir: str, headless: bool, login_timeout: int) -> None:
        import whatsapp_web_scraper as wws

        self._log("Launching browser...")
        self.context, self.page = wws.launch_new_browser_context(
            self.playwright, Path(profile_dir), headless
        )
        self.page.goto("https://web.whatsapp.com")
        self._log("Waiting for login (scan the QR code if shown)...")
        wws.wait_for_login(self.page, login_timeout, log=self._log)
        self.result_queue.put(("connected", None))

    def _cmd_connect_cdp(self, endpoint: str) -> None:
        import whatsapp_web_scraper as wws

        self._log(f"Attaching to {endpoint} ...")
        self.browser, self.context, self.page = wws.attach_to_existing_browser(
            self.playwright, endpoint
        )
        if wws.is_logged_in(self.page, timeout_ms=4000):
            self.result_queue.put(("connected", None))
        else:
            self._log(
                "Attached, but that tab isn't logged in yet. Scan the QR code "
                "in it, then click Fetch (no need to reconnect)."
            )
            self.result_queue.put(("connected_pending", None))

    def _cmd_fetch(self, chats_limit: int, start, end) -> None:
        import whatsapp_web_scraper as wws

        if self.page is None:
            self.result_queue.put(("error", "Not connected yet."))
            return
        if not wws.is_logged_in(self.page, timeout_ms=3000):
            self.result_queue.put(("error", "Not logged in yet — finish scanning the QR code first."))
            return

        records = wws.scrape_from_page(self.page, chats_limit, start, end, log=self._log)
        self.result_queue.put(("records", records))

    def _cleanup(self) -> None:
        for obj in (self.context, self.browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("WhatsApp Web Message Viewer")
        self.geometry("920x640")
        self.minsize(760, 480)

        self.records: list = []
        self.result_queue: queue.Queue = queue.Queue()
        self.worker = Worker(self.result_queue)
        self.worker.start()
        self._auto_fetch_job: Optional[str] = None

        self._build_ui()
        self.after(150, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout ------------------------------------------------------------ #

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        conn = ttk.LabelFrame(self, text="1. Connect")
        conn.pack(fill="x", **pad)

        self.mode_var = tk.StringVar(value="new")
        ttk.Radiobutton(conn, text="Launch new browser (scan QR)", variable=self.mode_var,
                        value="new", command=self._update_mode).grid(row=0, column=0, sticky="w", **pad)
        ttk.Radiobutton(conn, text="Connect to existing Chrome (CDP)", variable=self.mode_var,
                        value="cdp", command=self._update_mode).grid(row=1, column=0, sticky="w", **pad)

        self.cdp_entry = ttk.Entry(conn, width=28)
        self.cdp_entry.insert(0, DEFAULT_CDP_ENDPOINT)
        self.cdp_entry.grid(row=1, column=1, sticky="w", **pad)

        self.launch_chrome_btn = ttk.Button(
            conn, text="Launch debuggable Chrome...", command=self._on_launch_debuggable_chrome
        )
        self.launch_chrome_btn.grid(row=1, column=2, sticky="w", **pad)

        self.connect_btn = ttk.Button(conn, text="Connect", command=self._on_connect)
        self.connect_btn.grid(row=0, column=2, rowspan=1, sticky="w", **pad)

        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(conn, textvariable=self.status_var, foreground="#555").grid(
            row=2, column=0, columnspan=3, sticky="w", **pad
        )

        self._update_mode()

        query = ttk.LabelFrame(self, text="2. Query")
        query.pack(fill="x", **pad)

        ttk.Label(query, text="Timeframe:").grid(row=0, column=0, sticky="w", **pad)
        self.preset_var = tk.StringVar(value="Last 24 hours")
        preset_combo = ttk.Combobox(
            query, textvariable=self.preset_var, values=list(TIMEFRAME_PRESETS), state="readonly", width=16
        )
        preset_combo.grid(row=0, column=1, sticky="w", **pad)
        preset_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_custom_fields())

        ttk.Label(query, text="Since:").grid(row=0, column=2, sticky="w", **pad)
        self.since_entry = ttk.Entry(query, width=20)
        self.since_entry.grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(query, text="Until:").grid(row=0, column=4, sticky="w", **pad)
        self.until_entry = ttk.Entry(query, width=20)
        self.until_entry.grid(row=0, column=5, sticky="w", **pad)

        ttk.Label(query, text="Chats:").grid(row=1, column=0, sticky="w", **pad)
        self.limit_var = tk.IntVar(value=10)
        ttk.Spinbox(query, from_=1, to=50, textvariable=self.limit_var, width=5).grid(
            row=1, column=1, sticky="w", **pad
        )

        self.fetch_btn = ttk.Button(query, text="Fetch messages", command=self._on_fetch, state="disabled")
        self.fetch_btn.grid(row=1, column=4, sticky="e", **pad)

        self.auto_fetch_var = tk.BooleanVar(value=False)
        self.auto_fetch_check = ttk.Checkbutton(
            query, text="Auto-fetch every 30s", variable=self.auto_fetch_var,
            command=self._on_toggle_auto_fetch,
        )
        self.auto_fetch_check.grid(row=1, column=5, sticky="e", **pad)

        self._update_custom_fields()

        results = ttk.LabelFrame(self, text="3. Messages")
        results.pack(fill="both", expand=True, **pad)

        self.tree = ttk.Treeview(results, columns=("sender", "message"), show="headings")
        self.tree.heading("sender", text="Sender")
        self.tree.heading("message", text="Message")
        self.tree.column("sender", width=160, anchor="w")
        self.tree.column("message", width=600, anchor="w")
        vsb = ttk.Scrollbar(results, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="x", **pad)
        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill="x")
        ttk.Button(log_toolbar, text="Save log...", command=self._save_log).pack(side="right", padx=4, pady=2)
        self.log_text = tk.Text(log_frame, height=8, state="disabled", wrap="word")
        log_vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_vsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_vsb.pack(side="right", fill="y")

    def _update_mode(self) -> None:
        cdp_mode = self.mode_var.get() == "cdp"
        state = "normal" if cdp_mode else "disabled"
        self.cdp_entry.configure(state=state)
        self.launch_chrome_btn.configure(state=state)

    def _update_custom_fields(self) -> None:
        custom = self.preset_var.get() == "Custom"
        state = "normal" if custom else "disabled"
        self.since_entry.configure(state=state)
        self.until_entry.configure(state=state)

    # -- actions ------------------------------------------------------------ #

    def _on_connect(self) -> None:
        self.status_var.set("Connecting...")
        self.connect_btn.configure(state="disabled")
        if self.mode_var.get() == "new":
            self.worker.submit(
                "connect_new", profile_dir=str(DEFAULT_PROFILE_DIR), headless=False, login_timeout=120
            )
        else:
            self.worker.submit("connect_cdp", endpoint=self.cdp_entry.get().strip())

    def _on_launch_debuggable_chrome(self) -> None:
        running = chrome_is_running()
        if running:
            messagebox.showwarning(
                "Quit Chrome first",
                "Chrome is already running. Chrome ignores --remote-debugging-port on a "
                "second launch of an already-running instance — quit Chrome completely "
                "(Cmd+Q / close all windows), then click this button again.\n\n"
                "Your existing WhatsApp Web login is stored in your Chrome profile and "
                "will still be there after the relaunch — no new QR scan needed.",
            )
            return
        try:
            endpoint = launch_debuggable_chrome(DEFAULT_CDP_PORT)
        except Exception as exc:
            messagebox.showerror("Could not launch Chrome", str(exc))
            return
        self.cdp_entry.delete(0, "end")
        self.cdp_entry.insert(0, endpoint)
        self._log(f"Launched Chrome with debugging on {endpoint}. Click Connect once it's loaded.")

    def _resolve_timeframe(self):
        preset = TIMEFRAME_PRESETS[self.preset_var.get()]
        if self.preset_var.get() == "Custom":
            since = self.since_entry.get().strip() or None
            until = self.until_entry.get().strip() or None
            return resolve_timeframe(since, until, None, None, None)
        return resolve_timeframe(None, None, preset.get("days"), preset.get("hours"), preset.get("minutes"))

    def _on_fetch(self) -> None:
        try:
            start, end = self._resolve_timeframe()
        except ValueError as exc:
            messagebox.showerror("Invalid timeframe", str(exc))
            return

        self.fetch_btn.configure(state="disabled")
        self._log(f"Fetching messages from {start.isoformat()} to {end.isoformat()}...")
        self.worker.submit(
            "fetch",
            chats_limit=int(self.limit_var.get()),
            start=start,
            end=end,
        )

    def _on_toggle_auto_fetch(self) -> None:
        if self.auto_fetch_var.get():
            self._log("Auto-fetch enabled (every 30s).")
            self._auto_fetch_tick()
        else:
            self._log("Auto-fetch disabled.")
            if self._auto_fetch_job is not None:
                self.after_cancel(self._auto_fetch_job)
                self._auto_fetch_job = None

    def _auto_fetch_tick(self) -> None:
        if not self.auto_fetch_var.get():
            self._auto_fetch_job = None
            return

        if self.worker.page is None:
            self._log("Auto-fetch: not connected yet, skipping this tick.")
        elif str(self.fetch_btn["state"]) == "disabled":
            pass  # a fetch is already in progress — skip this tick
        else:
            self._on_fetch()

        self._auto_fetch_job = self.after(30000, self._auto_fetch_tick)

    def _save_log(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if not path:
            return
        Path(path).write_text(self.log_text.get("1.0", "end"))

    # -- queue polling / logging -------------------------------------------- #

    def _log(self, msg: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _populate_table(self, records) -> None:
        self.tree.delete(*self.tree.get_children())
        for record in records:
            message = record.message.replace("\n", " ⏎ ")
            self.tree.insert("", "end", values=(record.sender, message))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "connected":
                    self.status_var.set("Connected ✓ (logged in)")
                    self.connect_btn.configure(state="normal")
                    self.fetch_btn.configure(state="normal")
                elif kind == "connected_pending":
                    self.status_var.set("Tab open — waiting for QR scan")
                    self.connect_btn.configure(state="normal")
                    self.fetch_btn.configure(state="normal")
                elif kind == "records":
                    self.records = payload
                    self._populate_table(payload)
                    self._log(f"Fetched {len(payload)} message(s).")
                    self.fetch_btn.configure(state="normal")
                elif kind == "error":
                    self._log(f"Error: {payload}")
                    messagebox.showerror("Error", payload)
                    self.connect_btn.configure(state="normal")
                    self.fetch_btn.configure(state="normal")
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    def _on_close(self) -> None:
        self.worker.submit("shutdown")
        self.destroy()


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
