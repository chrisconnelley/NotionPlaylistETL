import queue
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from logger import log, log_queue
from theme import SURFACE, TEXT, TEXT_DIM


class ConsoleTab(ttk.Frame):
    """Displays log messages in real time. Polls log_queue via after()."""

    _LEVEL_TAGS = {
        "DEBUG":   (TEXT_DIM,  None),
        "INFO":    (TEXT,      None),
        "WARNING": ("#e6a817", None),
        "ERROR":   ("#e85d4a", None),
        "CRITICAL":(TEXT,      "#e85d4a"),
    }

    def __init__(self, parent: ttk.Notebook):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.text = tk.Text(self, state="disabled", font=("Courier New", 9),
                            wrap="none", bg=SURFACE, fg=TEXT,
                            insertbackground=TEXT)
        self.text.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=vsb.set)

        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.text.configure(xscrollcommand=hsb.set)

        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=(2, 6))
        ttk.Button(bar, text="Clear", command=self._clear).pack(side="right")
        ttk.Button(bar, text="Copy All", command=self._copy_all).pack(side="right", padx=(0, 4))
        ttk.Button(bar, text="Save Log…", command=self._save_log).pack(side="right", padx=(0, 4))
        self._auto_scroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Auto-scroll",
                        variable=self._auto_scroll).pack(side="right", padx=(0, 8))

        for level, (fg, bg) in self._LEVEL_TAGS.items():
            self.text.tag_configure(level, foreground=fg, background=bg)

        self._poll()

    def _poll(self):
        try:
            while True:
                msg = log_queue.get_nowait()
                self._append(msg)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _append(self, msg: str):
        level = "INFO"
        for lvl in self._LEVEL_TAGS:
            if f"[{lvl}]" in msg:
                level = lvl
                break

        self.text.configure(state="normal")
        self.text.insert("end", msg + "\n", level)
        self.text.configure(state="disabled")
        if self._auto_scroll.get():
            self.text.see("end")

    def _save_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"etl_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
        if not path:
            return
        content = self.text.get("1.0", "end")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        messagebox.showinfo("Log saved", f"Log saved to:\n{path}")

    def _copy_all(self):
        content = self.text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(content)
        messagebox.showinfo("Copied", "Log copied to clipboard.")

    def _clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
