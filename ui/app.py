import os
import threading
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import spotipy

from cache import load_playlist_cache, save_playlist_cache
from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, BASE_DIR
from logger import log
from notion._songs import _load_all_songs_cache
from spotify import get_spotify_client, fetch_user_playlists
from theme import apply_theme, make_icon
from ui.browser import PlaylistBrowser
from ui.console import ConsoleTab
from ui.playlist_tab import PlaylistTab


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MusicTunnel")
        self.minsize(780, 560)
        apply_theme(self)
        self._icon = make_icon()
        if self._icon:
            self.iconphoto(True, self._icon)
        self._sp: spotipy.Spotify | None = None
        self._open_tabs: dict[str, str] = {}
        self._build_ui()
        self._connect()
        # Load Notion songs cache in background (one-time load during app session)
        threading.Thread(target=_load_all_songs_cache, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        log.info("Application closing — saving log.")
        log_path = os.path.join(
            BASE_DIR, f"etl_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        try:
            content = self._console.text.get("1.0", "end")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as exc:
            print(f"Warning: could not save log on exit: {exc}")
        self.destroy()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        self.browser = PlaylistBrowser(self.notebook, self._open_playlist)
        self.notebook.add(self.browser, text="Playlists")
        self.browser.bind("<<Refresh>>", lambda _: self._connect())

        self._console = ConsoleTab(self.notebook)
        self.notebook.add(self._console, text="Console")

    # ------------------------------------------------------------------
    # Spotify connection
    # ------------------------------------------------------------------

    def _connect(self):
        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            log.error("Missing credentials: SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET "
                      "not set in .env")
            messagebox.showerror(
                "Missing credentials",
                "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in your .env file.",
            )
            return
        cached = load_playlist_cache()
        if cached:
            self.browser.load(cached, from_cache=True)
        log.info("Connecting to Spotify…")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            sp = get_spotify_client()
            playlists = fetch_user_playlists(sp)
        except Exception:
            msg = traceback.format_exc()
            log.error("Connection failed:\n%s", msg)
            self.after(0, self.browser.show_error,
                       "Connection failed — see Console tab for details.")
            return
        self._sp = sp
        save_playlist_cache(playlists)
        self.after(0, self.browser.load, playlists)
        self.after(0, self._notify_tabs_spotify_ready, sp)

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------

    def _open_playlist(self, playlist: dict):
        pid = playlist["id"]
        if pid in self._open_tabs:
            try:
                self.notebook.select(self._open_tabs[pid])
                return
            except tk.TclError:
                del self._open_tabs[pid]

        name = playlist["name"]
        label = (name[:20] + "…") if len(name) > 20 else name
        tab = PlaylistTab(
            self.notebook, self._sp, playlist,
            close_cb=lambda p=pid: self._close_tab(p),
        )
        self.notebook.add(tab, text=label)
        self._open_tabs[pid] = str(tab)
        self.notebook.select(str(tab))

    def _notify_tabs_spotify_ready(self, sp):
        for widget_path in self._open_tabs.values():
            try:
                widget = self.nametowidget(widget_path)
                if isinstance(widget, PlaylistTab):
                    widget.set_spotify_client(sp)
            except tk.TclError:
                pass

    def _close_tab(self, playlist_id: str):
        widget_path = self._open_tabs.pop(playlist_id, None)
        if widget_path is None:
            return
        try:
            widget = self.nametowidget(widget_path)
            self.notebook.forget(widget)
            if isinstance(widget, PlaylistTab):
                widget.close()
        except tk.TclError:
            pass
