import tkinter as tk
from tkinter import ttk

from theme import BG, SURFACE, GREEN, TEXT


class PlaylistBrowser(ttk.Frame):
    def __init__(self, parent: ttk.Notebook, on_open):
        super().__init__(parent)
        self._on_open = on_open
        self._playlists: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        ttk.Label(top, text="Your Playlists",
                  font=(None, 10, "bold")).pack(side="left")
        self.refresh_btn = ttk.Button(top, text="Refresh",
                                      command=self._on_refresh, state="disabled")
        self.refresh_btn.pack(side="right")

        list_outer = ttk.Frame(self)
        list_outer.grid(row=1, column=0, sticky="nsew", padx=8, pady=6)
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
            row=2, column=0, sticky="w", padx=8, pady=(0, 2))

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.progress.start(12)

    def load(self, playlists: list[dict], from_cache: bool = False):
        self._playlists = playlists
        self.listbox.delete(0, "end")
        for p in playlists:
            self.listbox.insert("end", p["name"])
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
        if sel and self._playlists:
            self._on_open(self._playlists[sel[0]])
