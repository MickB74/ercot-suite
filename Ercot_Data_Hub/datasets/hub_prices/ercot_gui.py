#!/usr/bin/env python3
"""
ERCOT Hub Price Downloader -- the button app.

A tiny window with a big "Update Now" button. Click it and it pulls the latest
15-minute ERCOT hub prices straight from the ERCOT Public API into the data/
folder (Parquet + CSV). First launch walks you through entering your free ERCOT
API credentials.

Run it by double-clicking "Update ERCOT Prices.command", or:
    .venv/bin/python ercot_gui.py
"""

from __future__ import annotations

import queue
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import ercot_api as core


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("ERCOT Hub Price Downloader")
        root.geometry("720x560")
        root.minsize(640, 480)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self._refresh_status()
        self.root.after(120, self._drain_log)

        # On launch, if data is missing or stale (>7 days), nudge the user.
        self.root.after(400, self._startup_check)

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        header = ttk.Frame(self.root)
        header.pack(fill="x", **pad)
        ttk.Label(header, text="ERCOT Hub Prices  ·  15-minute Real-Time",
                  font=("Helvetica", 16, "bold")).pack(anchor="w")
        ttk.Label(header, text="Pulled directly from the ERCOT Public API (NP6-905-CD), all trading hubs.",
                  foreground="#666").pack(anchor="w")

        # Status box
        self.status_var = tk.StringVar(value="Checking local data...")
        status = ttk.LabelFrame(self.root, text="Local data status")
        status.pack(fill="x", **pad)
        ttk.Label(status, textvariable=self.status_var, justify="left",
                  font=("Menlo", 11)).pack(anchor="w", padx=10, pady=8)

        # Buttons
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)

        self.update_btn = tk.Button(
            btns, text="⬇  Update Now", command=self.on_update,
            font=("Helvetica", 15, "bold"), bg="#1f7a3d", fg="white",
            activebackground="#176030", activeforeground="white",
            height=2, relief="raised", bd=0,
        )
        self.update_btn.pack(side="left", fill="x", expand=True)

        side = ttk.Frame(btns)
        side.pack(side="left", padx=(10, 0))
        ttk.Button(side, text="Set Credentials", command=self.on_credentials).pack(fill="x", pady=2)
        ttk.Button(side, text="Open Data Folder", command=self.on_open_folder).pack(fill="x", pady=2)
        self.auto_btn = ttk.Button(side, text="Enable Weekly Auto-Update", command=self.on_enable_weekly)
        self.auto_btn.pack(fill="x", pady=2)

        # Log
        logframe = ttk.LabelFrame(self.root, text="Progress")
        logframe.pack(fill="both", expand=True, **pad)
        self.log = scrolledtext.ScrolledText(logframe, height=12, font=("Menlo", 10),
                                             state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

    # ---- helpers ----------------------------------------------------------

    def _log_line(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain_log(self):
        try:
            while True:
                self._log_line(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(120, self._drain_log)

    def _refresh_status(self):
        info = core.store_summary()
        if not info.get("exists"):
            txt = "No data downloaded yet.\nClick “Update Now” to fetch the full history."
        else:
            dsu = info.get("days_since_update")
            freshness = "never" if dsu is None else f"{dsu:.1f} days ago"
            txt = (
                f"Rows:           {info['rows']:,} fifteen-minute intervals\n"
                f"Date range:     {info['start']}  →  {info['end']}\n"
                f"Hubs:           {len(info['hubs'])}  ({', '.join(info['hubs'])})\n"
                f"Last updated:   {freshness}"
            )
        if not core.have_credentials():
            txt += "\n\n⚠  No ERCOT API credentials set — click “Set Credentials”."
        self.status_var.set(txt)

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.update_btn.configure(state=state,
                                  text="⏳  Working…" if busy else "⬇  Update Now")

    # ---- actions ----------------------------------------------------------

    def _startup_check(self):
        if not core.have_credentials():
            self.on_credentials(first_run=True)
            return
        if core.is_stale():
            self._log_line("Data is missing or more than a week old — starting an update.")
            self.on_update()

    def on_update(self):
        if self.worker and self.worker.is_alive():
            return
        if not core.have_credentials():
            messagebox.showwarning("Credentials needed",
                                   "Please set your ERCOT API credentials first.")
            self.on_credentials()
            return
        self._set_busy(True)
        self._log_line("=" * 50)

        def run():
            try:
                core.update(progress_callback=self.log_queue.put)
                self.log_queue.put("✅ Update complete.")
                ok = True
            except Exception as e:
                self.log_queue.put(f"❌ {e}")
                ok = False
            self.root.after(0, lambda: self._finish(ok))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _finish(self, ok: bool):
        self._set_busy(False)
        self._refresh_status()
        if not ok:
            messagebox.showerror("Update failed",
                                 "Something went wrong — see the Progress log for details.")

    def on_credentials(self, first_run: bool = False):
        CredentialsDialog(self.root, on_save=self._after_credentials, first_run=first_run)

    def _after_credentials(self):
        self._refresh_status()
        if core.have_credentials() and core.is_stale():
            self.on_update()

    def on_open_folder(self):
        core.DATA_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(core.DATA_DIR)])

    def on_enable_weekly(self):
        script = core.PROJECT_DIR / "install_weekly_autoupdate.command"
        if not script.exists():
            messagebox.showerror("Not found", f"Missing {script.name}")
            return
        try:
            subprocess.run(["bash", str(script)], check=True,
                           capture_output=True, text=True)
            messagebox.showinfo(
                "Weekly auto-update enabled",
                "Done. The app will quietly refresh ERCOT prices once a week "
                "(and catch up on login if your Mac was asleep).")
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Could not enable", e.stderr or str(e))


class CredentialsDialog(tk.Toplevel):
    def __init__(self, parent, on_save, first_run=False):
        super().__init__(parent)
        self.on_save = on_save
        self.title("ERCOT API Credentials")
        self.geometry("520x340")
        self.transient(parent)
        self.grab_set()

        cfg = core.load_config()
        intro = ("Welcome! To pull data straight from ERCOT you need a free "
                 "ERCOT Public API account.\n\n" if first_run else "")
        ttk.Label(self, text=intro +
                  "1. Sign up at  apiexplorer.ercot.com\n"
                  "2. Copy your Primary subscription key from your profile\n"
                  "3. Enter your login below — stored locally only.",
                  justify="left", wraplength=480).pack(anchor="w", padx=14, pady=10)

        form = ttk.Frame(self)
        form.pack(fill="x", padx=14)
        self.user = self._row(form, "Username / email", cfg.get("username", ""))
        self.pw = self._row(form, "Password", cfg.get("password", ""), show="•")
        self.key = self._row(form, "Subscription key", cfg.get("subscription_key", ""), show="•")

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=14, pady=14)
        ttk.Button(btns, text="Test & Save", command=self.save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=8)
        self.msg = ttk.Label(self, text="", foreground="#b00")
        self.msg.pack(anchor="w", padx=14)

    def _row(self, parent, label, value, show=""):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=18).pack(side="left")
        var = tk.StringVar(value=value)
        ttk.Entry(row, textvariable=var, show=show).pack(side="left", fill="x", expand=True)
        return var

    def save(self):
        cfg = core.load_config()
        cfg.update({
            "username": self.user.get().strip(),
            "password": self.pw.get().strip(),
            "subscription_key": self.key.get().strip(),
        })
        if not core.have_credentials(cfg):
            self.msg.configure(text="All three fields are required.")
            return
        core.save_config(cfg)
        self.msg.configure(text="Testing login…", foreground="#666")
        self.update_idletasks()
        try:
            core.get_access_token(cfg)
        except Exception as e:
            self.msg.configure(text=f"Login failed: {e}", foreground="#b00")
            return
        self.destroy()
        self.on_save()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
