import os
import subprocess

# macOS system Tk warning suppression for cleaner demo output.
os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

import tkinter as tk
from tkinter import messagebox, ttk


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCUSTFILE = os.path.join(BASE_DIR, "locustfile.py")
LOCUST_BIN = os.path.join(BASE_DIR, ".venv", "bin", "locust")
LOG_PATH = os.path.join(BASE_DIR, "locust-gui.log")


class LocustControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Locust Bot Control")
        self.root.geometry("760x560")

        self.process = None
        self.log_file = None

        self._build_ui()
        self._tick()

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Target Host").grid(row=0, column=0, sticky="w")
        self.host_var = tk.StringVar(value="http://127.0.0.1:5000")
        ttk.Entry(frm, textvariable=self.host_var, width=50).grid(row=0, column=1, columnspan=3, sticky="ew", padx=6)

        ttk.Label(frm, text="Users").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.users_var = tk.StringVar(value="60")
        ttk.Entry(frm, textvariable=self.users_var, width=12).grid(row=1, column=1, sticky="w", padx=6, pady=(8, 0))

        ttk.Label(frm, text="Spawn/sec").grid(row=1, column=2, sticky="w", pady=(8, 0))
        self.spawn_var = tk.StringVar(value="10")
        ttk.Entry(frm, textvariable=self.spawn_var, width=12).grid(row=1, column=3, sticky="w", padx=6, pady=(8, 0))

        ttk.Label(frm, text="Duration").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.duration_var = tk.StringVar(value="2m")
        ttk.Entry(frm, textvariable=self.duration_var, width=12).grid(row=2, column=1, sticky="w", padx=6, pady=(8, 0))

        ttk.Label(frm, text="Status:").grid(row=3, column=0, sticky="w", pady=(12, 0))
        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(frm, textvariable=self.status_var).grid(row=3, column=1, columnspan=3, sticky="w", padx=6, pady=(12, 0))

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=4, column=0, columnspan=4, sticky="w", pady=(12, 8))
        ttk.Button(btn_row, text="Start Bots", command=self.start_bots).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Stop Bots", command=self.stop_bots).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Refresh Log", command=self.refresh_log).pack(side="left")

        ttk.Label(frm, text="Locust Output").grid(row=5, column=0, sticky="w")
        self.log_box = tk.Text(frm, height=22, wrap="word")
        self.log_box.grid(row=6, column=0, columnspan=4, sticky="nsew", pady=(4, 0))

        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(3, weight=1)
        frm.rowconfigure(6, weight=1)

    def _locust_command(self):
        locust_exec = LOCUST_BIN if os.path.exists(LOCUST_BIN) else "locust"
        return [
            locust_exec,
            "-f",
            LOCUSTFILE,
            "--host",
            self.host_var.get().strip(),
            "--headless",
            "-u",
            self.users_var.get().strip(),
            "-r",
            self.spawn_var.get().strip(),
            "-t",
            self.duration_var.get().strip(),
            "--only-summary",
        ]

    def start_bots(self):
        if self.process and self.process.poll() is None:
            messagebox.showinfo("Locust", "Locust is already running.")
            return
        if not os.path.exists(LOCUSTFILE):
            messagebox.showerror("Locust", f"Missing file: {LOCUSTFILE}")
            return
        try:
            self.log_file = open(LOG_PATH, "w", encoding="utf-8")
            self.process = subprocess.Popen(
                self._locust_command(),
                cwd=BASE_DIR,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
            )
            self.status_var.set(f"Running (pid {self.process.pid})")
            self.refresh_log()
        except Exception as exc:
            messagebox.showerror("Locust Start Failed", str(exc))

    def stop_bots(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.status_var.set("Stopping...")
        else:
            self.status_var.set("Stopped")

    def refresh_log(self):
        if not os.path.exists(LOG_PATH):
            text = "No log output yet."
        else:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            text = "".join(lines[-120:]) if lines else "No log output yet."
        self.log_box.delete("1.0", tk.END)
        self.log_box.insert(tk.END, text)
        self.log_box.see(tk.END)

    def _tick(self):
        if self.process and self.process.poll() is None:
            self.status_var.set(f"Running (pid {self.process.pid})")
        elif self.process:
            self.status_var.set(f"Stopped (exit {self.process.returncode})")
        else:
            self.status_var.set("Stopped")
        self.refresh_log()
        self.root.after(1500, self._tick)


if __name__ == "__main__":
    root = tk.Tk()
    app = LocustControlApp(root)
    root.mainloop()
