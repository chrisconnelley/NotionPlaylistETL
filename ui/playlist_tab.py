import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import spotipy

from cache import load_tracks_cache, save_tracks_cache
from export import export_to_csv, default_filename
from lyrics import fetch_lyrics
from logger import log
from spotify import fetch_all_tracks
from theme import SURFACE, TEXT
from ui.notion_dialog import NotionDatabasePicker


class PlaylistTab(ttk.Frame):
    _COLS = ("num", "artist", "title", "year", "lyrics_preview")
    _NAMES = ("#", "Artist", "Title", "Year", "Lyrics")
    _WIDTHS = (40, 160, 210, 55, 260)
    _STRETCH = {"lyrics_preview"}

    def __init__(self, parent: ttk.Notebook, sp: spotipy.Spotify,
                 playlist: dict, close_cb):
        super().__init__(parent)
        self._sp = sp
        self._playlist = playlist
        self._close_cb = close_cb
        self._tracks: list[dict] = []
        self._stop_lyrics = threading.Event()
        self._build_ui()
        log.info("Opening playlist tab: %r", playlist["name"])
        threading.Thread(target=self._load_tracks, daemon=True).start()

    def close(self):
        self._stop_lyrics.set()
        self.destroy()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=3)
        self.rowconfigure(1, weight=1)

        tree_outer = ttk.Frame(self)
        tree_outer.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 0))
        tree_outer.columnconfigure(0, weight=1)
        tree_outer.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tree_outer, columns=self._COLS, show="headings", selectmode="browse"
        )
        for col, name, width in zip(self._COLS, self._NAMES, self._WIDTHS):
            self.tree.heading(col, text=name,
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=width, minwidth=30,
                             stretch=(col in self._STRETCH))
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)

        vsb = ttk.Scrollbar(tree_outer, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vsb.set)

        hsb = ttk.Scrollbar(tree_outer, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=hsb.set)

        lyrics_frame = ttk.LabelFrame(self, text="Lyrics")
        lyrics_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        lyrics_frame.columnconfigure(0, weight=1)
        lyrics_frame.rowconfigure(0, weight=1)

        self.lyrics_text = tk.Text(
            lyrics_frame, wrap="word", state="disabled",
            font=(None, 9), relief="flat",
            bg=SURFACE, fg=TEXT, insertbackground=TEXT,
        )
        self.lyrics_text.grid(row=0, column=0, sticky="nsew")
        lsb = ttk.Scrollbar(lyrics_frame, orient="vertical",
                             command=self.lyrics_text.yview)
        lsb.grid(row=0, column=1, sticky="ns")
        self.lyrics_text.configure(yscrollcommand=lsb.set)

        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, sticky="ew", padx=6, pady=(0, 6))

        self.status_var = tk.StringVar(value="Loading tracks…")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        ttk.Button(bar, text="Close Tab", command=self._close_cb).pack(side="right")
        ttk.Button(bar, text="Export to Notion",
                   command=self._export_to_notion).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="Export to CSV",
                   command=self._export).pack(side="right", padx=(0, 6))
        self.refresh_btn = ttk.Button(bar, text="Refresh",
                                      command=self._refresh, state="disabled")
        self.refresh_btn.pack(side="right", padx=(0, 6))

        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=100)
        self.progress.pack(side="right", padx=(0, 8))
        self.progress.start(12)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_tracks(self):
        cached = load_tracks_cache(self._playlist["id"])
        if cached is not None:
            log.info("Loaded %d tracks from cache for %r",
                     len(cached), self._playlist["name"])
            for t in cached:
                t["Lyrics"] = None
            self._tracks = cached
            self.after(0, self._populate_tree)
            threading.Thread(target=self._load_lyrics_bg, daemon=True).start()
            return
        self._fetch_from_spotify()

    def _refresh(self):
        self._stop_lyrics.set()
        self._stop_lyrics = threading.Event()
        self.refresh_btn.configure(state="disabled")
        self.progress.start(12)
        self.status_var.set("Refreshing from Spotify…")
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._tracks = []
        threading.Thread(target=self._fetch_from_spotify, daemon=True).start()

    def _fetch_from_spotify(self):
        try:
            meta = self._sp.playlist(self._playlist["id"],
                                     fields="name,tracks.total")
            tracks_meta = meta.get("tracks") or meta.get("items") or {}
            total = tracks_meta.get("total", 0)
            self._playlist["total"] = total
            log.debug("Playlist %r has %d tracks", self._playlist["name"], total)
        except spotipy.SpotifyException as exc:
            log.warning("Could not fetch track total for %r: HTTP %s",
                        self._playlist["name"], exc.http_status)
            if exc.http_status == 403:
                self.after(0, self._set_error,
                           "Access denied (403) — this playlist may be private "
                           "or owned by another user.")
                return
            total = 0
        except Exception:
            log.warning("Could not fetch track total for %r:\n%s",
                        self._playlist["name"], traceback.format_exc())
            total = 0

        def progress_cb(fetched):
            label = f"Loading… {fetched} / {total}" if total else f"Loading… {fetched}"
            self.after(0, self.status_var.set, label)

        try:
            tracks = fetch_all_tracks(self._sp, self._playlist["id"],
                                      progress_cb=progress_cb)
        except spotipy.SpotifyException as exc:
            log.error("Spotify error fetching tracks for %r: HTTP %s — %s",
                      self._playlist["name"], exc.http_status, exc.msg)
            if exc.http_status == 403:
                self.after(0, self._set_error,
                           "Access denied (403) — this playlist may be private "
                           "or owned by another user.")
            elif exc.http_status == 404:
                self.after(0, self._set_error,
                           "Playlist not found (404) — it may have been deleted.")
            else:
                self.after(0, self._set_error,
                           f"Spotify error {exc.http_status} — see Console tab.")
            return
        except Exception:
            log.error("Failed to fetch tracks for %r:\n%s",
                      self._playlist["name"], traceback.format_exc())
            self.after(0, self._set_error,
                       "Failed to load tracks — see Console tab for details.")
            return

        save_tracks_cache(self._playlist["id"], tracks)
        self._tracks = tracks
        self.after(0, self._populate_tree)
        threading.Thread(target=self._load_lyrics_bg, daemon=True).start()

    def _populate_tree(self):
        for i, t in enumerate(self._tracks):
            self.tree.insert("", "end", iid=str(i), values=(
                i + 1, t["Artist(s)"], t["Track Name"], t["Year"], "…",
            ))
        n = len(self._tracks)
        self.status_var.set(f"{n} track{'s' if n != 1 else ''} — fetching lyrics…")
        self.refresh_btn.configure(state="normal")

    def _load_lyrics_bg(self):
        for idx, track in enumerate(self._tracks):
            if self._stop_lyrics.is_set():
                log.debug("Lyrics fetch cancelled for %r", self._playlist["name"])
                return
            lyrics = fetch_lyrics(track["Artist(s)"], track["Track Name"])
            track["Lyrics"] = lyrics
            preview = lyrics.split("\n")[0][:60] if lyrics else "—"
            self.after(0, self._update_lyrics_cell, str(idx), preview)

        n = len(self._tracks)
        self.after(0, self.status_var.set,
                   f"{n} track{'s' if n != 1 else ''} — lyrics loaded.")
        self.after(0, self.progress.stop)
        log.info("Finished loading lyrics for %r", self._playlist["name"])

    def _update_lyrics_cell(self, iid: str, preview: str):
        if not self.tree.exists(iid):
            return
        vals = list(self.tree.item(iid, "values"))
        vals[4] = preview
        self.tree.item(iid, values=vals)
        if self.tree.selection() == (iid,):
            self._render_lyrics(int(iid))

    def _set_error(self, msg: str):
        self.progress.stop()
        self.status_var.set(f"Error: {msg}")

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _on_row_select(self, _event=None):
        sel = self.tree.selection()
        if sel:
            self._render_lyrics(int(sel[0]))

    def _render_lyrics(self, idx: int):
        if idx >= len(self._tracks):
            return
        t = self._tracks[idx]
        lyrics = t.get("Lyrics")

        self.lyrics_text.configure(state="normal")
        self.lyrics_text.delete("1.0", "end")

        if lyrics is None:
            body = "Loading lyrics…"
        elif lyrics == "":
            body = "Lyrics not found."
        else:
            header = f"{t['Track Name']}  —  {t['Artist(s)']}  ({t['Year']})\n"
            body = header + ("─" * 48) + "\n\n" + lyrics

        self.lyrics_text.insert("end", body)
        self.lyrics_text.configure(state="disabled")

    def _sort_by(self, col: str):
        data = [(self.tree.set(iid, col), iid)
                for iid in self.tree.get_children("")]
        try:
            data.sort(key=lambda x: int(x[0]))
        except ValueError:
            data.sort(key=lambda x: x[0].lower())
        for i, (_, iid) in enumerate(data):
            self.tree.move(iid, "", i)

    def _export_to_notion(self):
        if not self._tracks:
            messagebox.showwarning("No tracks", "Tracks are still loading.")
            return
        NotionDatabasePicker(self, on_select=self._on_notion_database_selected)

    def _on_notion_database_selected(self, database: dict):
        log.info("Exporting %d tracks to Notion database %r (%s)",
                 len(self._tracks), database["name"], database["id"])
        messagebox.showinfo("Coming soon",
                            f"Export to \"{database['name']}\" will be wired up next.")

    def _export(self):
        if not self._tracks:
            messagebox.showwarning("No tracks", "Tracks are still loading.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=default_filename(self._playlist["name"]),
        )
        if not path:
            return
        try:
            export_to_csv(self._tracks, path)
            messagebox.showinfo("Exported",
                                f"Saved {len(self._tracks)} tracks to:\n{path}")
        except Exception:
            msg = traceback.format_exc()
            log.error("Export failed:\n%s", msg)
            messagebox.showerror("Export failed",
                                 "Export failed — see Console tab for details.")
