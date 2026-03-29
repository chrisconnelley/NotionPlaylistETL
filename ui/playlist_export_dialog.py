import threading
import tkinter as tk
from tkinter import ttk

from config import NOTION_JESSIE_PLAYLISTS_DB_ID, NOTION_TERI_PLAYLISTS_DB_ID
from logger import log
from notion import export_playlist, export_playlist_songs, SKIP
from theme import BG, SURFACE, TEXT, TEXT_DIM
from ui.match_dialog import NotionMatchDialog

_DB_OPTIONS = {
    "Jessie & Chris's Playlists": NOTION_JESSIE_PLAYLISTS_DB_ID,
    "Teri & Chris's Playlists":   NOTION_TERI_PLAYLISTS_DB_ID,
}


class PlaylistExportDialog(tk.Toplevel):
    """
    Two-phase dialog for exporting a Spotify playlist to Notion.

    Phase 1 — matches/creates the playlist record in the chosen DB.
    Phase 2 — creates individual playlist song records, with lyrics blocks,
               linking back to the existing Song and Song Artist pages.
    """

    def __init__(self, parent_tab, sp, playlist: dict, tracks: list):
        super().__init__(parent_tab)
        self.title("Export Playlist to Notion")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()

        self._parent_tab  = parent_tab
        self._sp          = sp
        self._playlist    = playlist
        self._tracks      = tracks
        self._stop_event  = threading.Event()
        self._playlist_db_id: str | None = None   # set after phase 1

        self._build_ui()

        self.update_idletasks()
        px, py = parent_tab.winfo_rootx(), parent_tab.winfo_rooty()
        pw, ph = parent_tab.winfo_width(), parent_tab.winfo_height()
        w, h   = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        ttk.Label(self, text="Export Playlist to Notion",
                  font=(None, 11, "bold")).pack(padx=16, pady=(14, 4), anchor="w")
        ttk.Label(self, text=self._playlist["name"],
                  foreground=TEXT_DIM).pack(padx=16, pady=(0, 10), anchor="w")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=(0, 10))

        # Destination dropdown
        db_frame = ttk.Frame(self)
        db_frame.pack(fill="x", padx=16, pady=(0, 10))
        ttk.Label(db_frame, text="Destination:").pack(side="left")
        self._db_var = tk.StringVar(value=list(_DB_OPTIONS)[0])
        self._db_combo = ttk.Combobox(db_frame, textvariable=self._db_var,
                                      values=list(_DB_OPTIONS), state="readonly",
                                      width=30)
        self._db_combo.pack(side="left", padx=(8, 0))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=(0, 10))

        # ── Phase 1 ────────────────────────────────────────────────────
        ttk.Label(self, text="Phase 1 — Playlist",
                  font=(None, 9, "bold")).pack(padx=16, anchor="w")

        self._pl_status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self._pl_status_var,
                  foreground=TEXT_DIM).pack(padx=16, pady=(2, 0), anchor="w")

        self._pl_result_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._pl_result_var,
                  font=(None, 10, "bold")).pack(padx=16, pady=(2, 10), anchor="w")

        # ── Phase 2 (built now, revealed after phase 1) ─────────────────
        self._phase2_frame = ttk.Frame(self)
        # packed later by _reveal_phase2()

        n = len(self._tracks)
        ttk.Separator(self._phase2_frame,
                      orient="horizontal").pack(fill="x", pady=(0, 10))
        ttk.Label(self._phase2_frame,
                  text=f"Phase 2 — Songs  ({n} track{'s' if n != 1 else ''})",
                  font=(None, 9, "bold")).pack(padx=16, anchor="w")

        # Lyrics warning (shown only when some lyrics are still None)
        self._lyrics_warn_var = tk.StringVar(value="")
        self._lyrics_warn_lbl = ttk.Label(
            self._phase2_frame, textvariable=self._lyrics_warn_var,
            foreground="#e6a817", justify="left",
        )
        self._lyrics_warn_lbl.pack(padx=16, pady=(4, 0), anchor="w")

        self._songs_progress = ttk.Progressbar(
            self._phase2_frame, mode="determinate",
            maximum=max(n, 1), length=340,
        )
        self._songs_progress.pack(padx=16, pady=(6, 0))

        self._songs_status_var = tk.StringVar(value="")
        ttk.Label(self._phase2_frame, textvariable=self._songs_status_var,
                  foreground=TEXT_DIM).pack(padx=16, pady=(4, 4), anchor="w")

        # Scrollable summary
        summary_outer = ttk.Frame(self._phase2_frame)
        summary_outer.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        summary_outer.columnconfigure(0, weight=1)
        summary_outer.rowconfigure(0, weight=1)
        self._summary_text = tk.Text(
            summary_outer, height=6, wrap="word", state="disabled",
            font=(None, 9), relief="flat", bg=SURFACE, fg=TEXT,
        )
        self._summary_text.grid(row=0, column=0, sticky="nsew")
        _ssb = ttk.Scrollbar(summary_outer, orient="vertical",
                             command=self._summary_text.yview)
        _ssb.grid(row=0, column=1, sticky="ns")
        self._summary_text.configure(yscrollcommand=_ssb.set)

        # ── Button bar ─────────────────────────────────────────────────
        btn_bar = ttk.Frame(self)
        btn_bar.pack(fill="x", padx=16, pady=(0, 14))

        self._close_btn = ttk.Button(btn_bar, text="Cancel",
                                     command=self._on_close)
        self._close_btn.pack(side="right")

        self._songs_btn = ttk.Button(btn_bar, text="Export Songs",
                                     command=self._start_songs,
                                     state="disabled")
        self._songs_btn.pack(side="right", padx=(0, 6))

        self._start_btn = ttk.Button(btn_bar, text="Start Export",
                                     command=self._start_playlist)
        self._start_btn.pack(side="right", padx=(0, 6))

    # ------------------------------------------------------------------
    # Phase 1 — playlist
    # ------------------------------------------------------------------

    def _show_match_dialog(self, kind, item_name, candidates, result_holder, done_event):
        dialog = NotionMatchDialog(self, "Playlist", item_name, candidates)
        self.wait_window(dialog)
        result_holder["choice"] = dialog.result
        done_event.set()

    def _start_playlist(self):
        self._start_btn.configure(state="disabled")
        self._db_combo.configure(state="disabled")
        self._pl_status_var.set("Exporting playlist…")
        self._pl_result_var.set("")
        threading.Thread(target=self._run_playlist, daemon=True).start()

    def _run_playlist(self):
        db_id = _DB_OPTIONS[self._db_var.get()]

        playlist = dict(self._playlist)
        if self._sp:
            try:
                imgs = self._sp.playlist_cover_image(playlist["id"])
                if imgs:
                    playlist["cover_url"] = imgs[0]["url"]
            except Exception:
                import traceback
                log.debug("Could not fetch cover for %r: %s",
                          playlist["name"], traceback.format_exc().splitlines()[-1])

        def match_cb(kind, item_name, candidates):
            result_holder = {}
            done_event = threading.Event()
            self.after(0, self._show_match_dialog,
                       kind, item_name, candidates, result_holder, done_event)
            done_event.wait()
            return result_holder.get("choice")

        try:
            result = export_playlist(playlist, db_id, match_cb=match_cb)
            self._playlist_db_id = db_id
            self.after(0, self._playlist_done, result)
        except Exception:
            import traceback
            self.after(0, self._playlist_error, traceback.format_exc().splitlines()[-1])

    def _playlist_done(self, result: dict):
        status = result["status"]
        name   = result["name"]
        if status == "added":
            self._pl_result_var.set(f"✓ Created in Notion: {name}")
        elif status == "pre_existing":
            self._pl_result_var.set(f"✓ Already in Notion: {name}")
        else:
            self._pl_result_var.set("— Playlist skipped.")
        self._pl_status_var.set("Done.")

        if status != "skipped":
            self._reveal_phase2()

    def _playlist_error(self, msg: str):
        self._pl_status_var.set(f"Error: {msg}")
        self._start_btn.configure(state="normal")
        self._db_combo.configure(state="readonly")

    # ------------------------------------------------------------------
    # Phase 2 — playlist songs
    # ------------------------------------------------------------------

    def _reveal_phase2(self):
        """Show the songs section and enable the Export Songs button."""
        self._phase2_frame.pack(fill="both", expand=True, padx=0, pady=0,
                                before=self.nametowidget(
                                    self._close_btn.winfo_parent()))

        missing_lyrics = sum(1 for t in self._tracks if t.get("Lyrics") is None)
        if missing_lyrics:
            self._lyrics_warn_var.set(
                f"⚠  {missing_lyrics} track(s) still loading lyrics — "
                "those songs will be created with an empty Lyrics block."
            )

        self._songs_btn.configure(state="normal")
        self.resizable(False, True)
        self.update_idletasks()

    def _start_songs(self):
        self._songs_btn.configure(state="disabled")
        self._close_btn.configure(text="Cancel")
        self._songs_progress["value"] = 0
        self._songs_status_var.set("Starting…")
        self._stop_event.clear()
        threading.Thread(target=self._run_songs, daemon=True).start()

    def _run_songs(self):
        def progress_cb(current, total, track_name):
            self.after(0, self._songs_progress.configure, {"value": current})
            self.after(0, self._songs_status_var.set,
                       f"Track {current} / {total}" +
                       (f"  —  {track_name[:50]}" if track_name else ""))

        def match_cb(kind, item_name, candidates):
            result_holder = {}
            done_event = threading.Event()
            self.after(0, self._show_match_dialog,
                       kind, item_name, candidates, result_holder, done_event)
            done_event.wait()
            return result_holder.get("choice")

        try:
            summary = export_playlist_songs(
                self._tracks,
                self._playlist["id"],
                self._playlist_db_id,
                sp=self._sp,
                match_cb=match_cb,
                progress_cb=progress_cb,
                stop_event=self._stop_event,
            )
            self.after(0, self._songs_done, summary)
        except Exception:
            import traceback
            self.after(0, self._songs_error, traceback.format_exc().splitlines()[-1])

    def _songs_done(self, summary: dict):
        self._songs_status_var.set(
            f"✓ {summary['added']} added  "
            f"— {summary['pre_existing']} already in Notion  "
            f"/ {summary['skipped']} skipped"
            + (f"  ✗ {len(summary['errors'])} error(s)" if summary["errors"] else "")
        )

        lines = []
        if summary["added_names"]:
            lines.append(f"Added ({len(summary['added_names'])}):")
            lines.extend(f"  {n}" for n in summary["added_names"])
            lines.append("")
        if summary["skipped_names"]:
            lines.append(f"Skipped ({len(summary['skipped_names'])}):")
            lines.extend(f"  {n}" for n in summary["skipped_names"])
            lines.append("")
        if summary["errors"]:
            lines.append(f"Errors ({len(summary['errors'])}) — see Console tab:")
            lines.extend(f"  {e['track']}: {e['error']}" for e in summary["errors"])

        self._summary_text.configure(state="normal")
        self._summary_text.delete("1.0", "end")
        self._summary_text.insert("end", "\n".join(lines))
        self._summary_text.configure(state="disabled")

        self._close_btn.configure(text="Close")

    def _songs_error(self, msg: str):
        self._songs_status_var.set(f"Error: {msg}")
        self._songs_btn.configure(state="normal")
        self._close_btn.configure(text="Cancel")

    # ------------------------------------------------------------------

    def _on_close(self):
        self._stop_event.set()
        self.destroy()
