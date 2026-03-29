import tkinter as tk
from tkinter import ttk

from theme import BG, SURFACE, GREEN, TEXT, TEXT_DIM


class PlaylistBrowser(ttk.Frame):
    def __init__(self, parent: ttk.Notebook, on_open):
        super().__init__(parent)
        self._on_open = on_open
        self._playlists: list[dict] = []
        self._filtered: list[dict] = []  # playlists currently shown in listbox
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        ttk.Label(top, text="Your Playlists",
                  font=(None, 10, "bold")).pack(side="left")
        self.refresh_btn = ttk.Button(top, text="Refresh",
                                      command=self._on_refresh, state="disabled")
        self.refresh_btn.pack(side="right")

        filter_bar = ttk.Frame(self)
        filter_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(6, 0))
        filter_bar.columnconfigure(1, weight=1)
        ttk.Label(filter_bar, text="Filter:", foreground=TEXT_DIM).grid(
            row=0, column=0, padx=(0, 4))
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", self._on_filter_change)
        filter_entry = ttk.Entry(filter_bar, textvariable=self._filter_var)
        filter_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(filter_bar, text="✕", width=2,
                   command=self._clear_filter).grid(row=0, column=2, padx=(4, 0))

        list_outer = ttk.Frame(self)
        list_outer.grid(row=2, column=0, sticky="nsew", padx=8, pady=6)
        list_outer.columnconfigure(0, weight=1)
        list_outer.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(list_outer, selectmode="single",
                                  activestyle="dotbox", font=(None, 10), height=18,
                                  bg=SURFACE, fg=TEXT,
                                  selectbackground=GREEN, selectforeground=TEXT,
                                  highlightbackground=BG, highlightcolor=GREEN,
                                  borderwidth=0)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        self.listbox.bind("<Double-Button-1>", self._open_selected)
        self.listbox.bind("<Return>", self._open_selected)

        sb = ttk.Scrollbar(list_outer, orient="vertical",
                            command=self.listbox.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=sb.set)

        self.status_var = tk.StringVar(value="Connecting to Spotify…")
        ttk.Label(self, textvariable=self.status_var).grid(
            row=3, column=0, sticky="w", padx=8, pady=(0, 2))

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.progress.start(12)

    def load(self, playlists: list[dict], from_cache: bool = False):
        self._playlists = playlists
        self._apply_filter()
        n = len(playlists)
        if from_cache:
            self.status_var.set(
                f"{n} playlist{'s' if n != 1 else ''} (cached) — refreshing…"
            )
        else:
            self.progress.stop()
            self.refresh_btn.configure(state="normal")
            self.status_var.set(
                f"{n} playlist{'s' if n != 1 else ''}. Double-click to open."
            )

    def _on_filter_change(self, *_):
        self._apply_filter()

    def _apply_filter(self):
        term = self._filter_var.get().strip().lower()
        self._filtered = [p for p in self._playlists
                          if term in p["name"].lower()] if term else list(self._playlists)
        self.listbox.delete(0, "end")
        for p in self._filtered:
            self.listbox.insert("end", p["name"])

    def _clear_filter(self):
        self._filter_var.set("")

    def show_error(self, msg: str):
        self.progress.stop()
        self.refresh_btn.configure(state="normal")
        if not self._playlists:
            self.status_var.set(f"Error: {msg}")
        else:
            self.status_var.set(f"Refresh failed — showing cached list. ({msg})")

    def _on_refresh(self):
        self.refresh_btn.configure(state="disabled")
        self.progress.start(12)
        self.status_var.set("Refreshing…")
        self.listbox.delete(0, "end")
        self.event_generate("<<Refresh>>")

    def _open_selected(self, _event=None):
        sel = self.listbox.curselection()
        if sel and self._filtered:
            self._on_open(self._filtered[sel[0]])
