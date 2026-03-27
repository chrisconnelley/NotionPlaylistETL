import csv
import json
import logging
import os
import queue
import re
import threading
import tkinter as tk
import traceback
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import requests
import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SCOPE = "playlist-read-private playlist-read-collaborative"
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "playlist_cache.json")


# ---------------------------------------------------------------------------
# Logging — writes to stderr AND an in-app queue consumed by the Console tab
# ---------------------------------------------------------------------------

_log_queue: queue.Queue = queue.Queue()


class _QueueHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _log_queue.put(self.format(record))


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("etl")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%H:%M:%S")
    sh = logging.StreamHandler()          # stderr / VS Code terminal
    sh.setFormatter(fmt)
    qh = _QueueHandler()
    qh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(qh)
    return logger


log = _setup_logging()


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def get_spotify_client() -> spotipy.Spotify:
    log.debug("Creating SpotifyOAuth client (redirect=%s)", REDIRECT_URI)
    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def fetch_user_playlists(sp: spotipy.Spotify) -> list[dict]:
    playlists = []
    results = sp.current_user_playlists(limit=50)
    while results:
        for item in results["items"]:
            if item is None:
                log.warning("Skipping None item in playlists response")
                continue
            owner = item.get("owner") or {}
            playlists.append({
                "id": item["id"],
                "name": item.get("name", "<unnamed>"),
                "total": None,  # fetched lazily when the playlist is opened
                "owner": owner.get("display_name") or owner.get("id", "unknown"),
            })
        results = sp.next(results) if results.get("next") else None
    log.info("Fetched %d playlists", len(playlists))
    return playlists


def fetch_all_tracks(sp: spotipy.Spotify, playlist_id: str,
                     progress_cb=None) -> list[dict]:
    tracks = []
    log.debug("Fetching tracks for playlist %s", playlist_id)
    results = sp.playlist_items(
        playlist_id,
        fields="items(added_at,added_by.id,"
               "track(name,id,duration_ms,external_urls,artists(name),album(name,release_date)),"
               "item(name,id,duration_ms,external_urls,artists(name),album(name,release_date)))"
               ",next",
        limit=100,
    )
    while results:
        for item in results["items"]:
            track = item.get("track") or item.get("item")
            if not track:
                log.debug("Skipping item — no 'track'/'item' key. Present keys: %s",
                          list(item.keys()) if item else "None")
                continue
            release_date = track["album"].get("release_date", "")
            year = release_date[:4] if release_date else ""
            duration_ms = track.get("duration_ms") or 0
            duration_sec = duration_ms // 1000
            duration_fmt = f"{duration_sec // 60}:{duration_sec % 60:02d}"
            tracks.append({
                "Track Name": track["name"],
                "Artist(s)": ", ".join(a["name"] for a in track["artists"]),
                "Album": track["album"]["name"],
                "Release Date": release_date,
                "Year": year,
                "Duration": duration_fmt,
                "Spotify URL": track["external_urls"].get("spotify", ""),
                "Added At": item.get("added_at", ""),
                "Added By": item.get("added_by", {}).get("id", ""),
                "Lyrics": None,
            })
        if progress_cb:
            progress_cb(len(tracks))
        results = sp.next(results) if results.get("next") else None
    log.info("Fetched %d tracks for playlist %s", len(tracks), playlist_id)
    return tracks


def fetch_lyrics(artist: str, title: str) -> str:
    clean_title = re.sub(r"\s*[\(\[].*?[\)\]]", "", title).strip()
    first_artist = artist.split(",")[0].strip()
    url = (
        f"https://api.lyrics.ovh/v1/"
        f"{requests.utils.quote(first_artist)}/"
        f"{requests.utils.quote(clean_title)}"
    )
    try:
        resp = requests.get(url, timeout=6)
        if resp.status_code == 200:
            lyrics = resp.json().get("lyrics", "")
            if lyrics:
                log.debug("Lyrics found: %r by %r", clean_title, first_artist)
            else:
                log.debug("No lyrics in response: %r by %r", clean_title, first_artist)
            return lyrics
        log.debug("Lyrics HTTP %d: %r by %r", resp.status_code, clean_title, first_artist)
    except Exception:
        log.debug("Lyrics fetch error for %r by %r:\n%s",
                  clean_title, first_artist, traceback.format_exc())
    return ""


def export_to_csv(tracks: list[dict], output_path: str) -> None:
    fieldnames = ["Track Name", "Artist(s)", "Album", "Release Date",
                  "Duration", "Spotify URL", "Added At", "Added By"]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in tracks:
            writer.writerow({k: t.get(k, "") for k in fieldnames})
    log.info("Exported %d tracks to %s", len(tracks), output_path)


def default_filename(playlist_name: str) -> str:
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", playlist_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe_name}_{timestamp}.csv"


def load_playlist_cache() -> list[dict] | None:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        log.info("Loaded %d playlists from cache (%s)", len(data), CACHE_PATH)
        return data
    except FileNotFoundError:
        log.debug("No playlist cache found at %s", CACHE_PATH)
    except Exception:
        log.warning("Could not read playlist cache:\n%s", traceback.format_exc())
    return None


def save_playlist_cache(playlists: list[dict]) -> None:
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(playlists, f, ensure_ascii=False, indent=2)
        log.debug("Saved %d playlists to cache", len(playlists))
    except Exception:
        log.warning("Could not save playlist cache:\n%s", traceback.format_exc())


# ---------------------------------------------------------------------------
# Console tab
# ---------------------------------------------------------------------------

class ConsoleTab(ttk.Frame):
    """Displays log messages in real time. Polls _log_queue via after()."""

    _LEVEL_TAGS = {
        "DEBUG":   ("gray",    None),
        "INFO":    (None,      None),
        "WARNING": ("orange",  None),
        "ERROR":   ("red",     None),
        "CRITICAL":("white",   "red"),
    }

    def __init__(self, parent: ttk.Notebook):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.text = tk.Text(self, state="disabled", font=("Courier New", 9),
                            wrap="none", bg="#1e1e1e", fg="#d4d4d4",
                            insertbackground="white")
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
                msg = _log_queue.get_nowait()
                self._append(msg)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _append(self, msg: str):
        # Detect level from formatted message for colouring
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


# ---------------------------------------------------------------------------
# Playlist Tab
# ---------------------------------------------------------------------------

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
        ttk.Button(bar, text="Export to CSV",
                   command=self._export).pack(side="right", padx=(0, 6))

        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=100)
        self.progress.pack(side="right", padx=(0, 8))
        self.progress.start(12)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_tracks(self):
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


# ---------------------------------------------------------------------------
# Playlist Browser
# ---------------------------------------------------------------------------

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
                                  activestyle="dotbox", font=(None, 10), height=18)
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
        # Keep any cached list visible; just update the status
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


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Notion Playlist ETL")
        self.minsize(780, 560)
        self._sp: spotipy.Spotify | None = None
        self._open_tabs: dict[str, str] = {}
        self._build_ui()
        self._connect()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        log.info("Application closing — saving log.")
        log_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"etl_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
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

    # ------------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------------

    def _open_playlist(self, playlist: dict):
        if self._sp is None:
            self.browser.status_var.set("Still connecting to Spotify — please wait…")
            return
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    App().mainloop()
