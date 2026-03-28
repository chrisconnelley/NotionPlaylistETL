import threading
import tkinter as tk
from tkinter import messagebox, ttk

from logger import log
from notion import fetch_databases
from theme import BG, SURFACE, GREEN, TEXT, TEXT_DIM, ELEVATED


class NotionDatabasePicker(tk.Toplevel):
    """Modal dialog that fetches available Notion databases and lets the user pick one."""

    def __init__(self, parent, on_select):
        super().__init__(parent)
        self.title("Select Notion Database")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()  # modal

        self._on_select = on_select
        self._databases: list[dict] = []

        self._build_ui()
        threading.Thread(target=self._load_databases, daemon=True).start()

        # Centre over parent
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")

    def _build_ui(self):
        ttk.Label(self, text="Choose a Notion database to export into:",
                  foreground=TEXT_DIM).pack(padx=16, pady=(14, 6), anchor="w")

        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=16)

        self.listbox = tk.Listbox(list_frame, width=48, height=10,
                                  bg=SURFACE, fg=TEXT,
                                  selectbackground=GREEN, selectforeground=TEXT,
                                  highlightbackground=BG, highlightcolor=GREEN,
                                  borderwidth=0, font=(None, 10))
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", self._confirm)

        sb = ttk.Scrollbar(list_frame, orient="vertical",
                           command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=sb.set)

        self.status_var = tk.StringVar(value="Loading databases…")
        ttk.Label(self, textvariable=self.status_var,
                  foreground=TEXT_DIM).pack(padx=16, pady=(6, 4), anchor="w")

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=16, pady=(0, 10))
        self.progress.start(12)

        btn_bar = ttk.Frame(self)
        btn_bar.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(btn_bar, text="Cancel",
                   command=self.destroy).pack(side="right")
        self.select_btn = ttk.Button(btn_bar, text="Select",
                                     command=self._confirm, state="disabled")
        self.select_btn.pack(side="right", padx=(0, 6))

    def _load_databases(self):
        try:
            databases = fetch_databases()
        except Exception as exc:
            log.error("Failed to fetch Notion databases: %s", exc)
            self.after(0, self._show_error, str(exc))
            return

        self._databases = databases
        self.after(0, self._populate, databases)

    def _populate(self, databases: list[dict]):
        self.progress.stop()
        self.progress.pack_forget()

        if not databases:
            self.status_var.set("No databases found. Share a database with your integration.")
            return

        for db in databases:
            self.listbox.insert("end", db["name"])

        self.status_var.set(f"{len(databases)} database{'s' if len(databases) != 1 else ''} found.")
        self.select_btn.configure(state="normal")

    def _show_error(self, msg: str):
        self.progress.stop()
        self.status_var.set(f"Error: {msg}")

    def _confirm(self, _event=None):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("No selection", "Please select a database.",
                                   parent=self)
            return
        chosen = self._databases[sel[0]]
        log.info("Notion database selected: %r (%s)", chosen["name"], chosen["id"])
        self.destroy()
        self._on_select(chosen)
