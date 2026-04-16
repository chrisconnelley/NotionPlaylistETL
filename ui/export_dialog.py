import threading
import tkinter as tk
from tkinter import ttk

from logger import log
from notion import export_tracks, export_playlist, export_playlist_songs, undo_export
from settings import load_settings, save_settings
from theme import BG, SURFACE, TEXT, TEXT_DIM
from ui.match_dialog import NotionMatchDialog


class ExportDialog(tk.Toplevel):
    """
    Unified export dialog: Song Artists → Songs → Playlist Record → Playlist Songs.
    All four phases run automatically in sequence after the user clicks Start Export.
    """

    def __init__(self, parent_tab, sp, playlist: dict, tracks: list):
        super().__init__(parent_tab)
        self.title("Export to Notion")
        self.resizable(False, False)
        self.configure(bg=BG)

        self._parent_tab = parent_tab
        self._sp = sp
        self._playlist = playlist
        self._tracks = tracks
        self._stop_event = threading.Event()

        missing = [t for t in tracks if not t.get("Artists")]
        self._missing_count = len(missing)

        self._countdown_id = None
        self._settings_data = load_settings()

        self._build_ui()

        self.update_idletasks()
        px, py = parent_tab.winfo_rootx(), parent_tab.winfo_rooty()
        pw, ph = parent_tab.winfo_width(), parent_tab.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if self._auto_start.get() and not self._missing_count:
            self._begin_countdown(5)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        n = len(self._tracks)

        ttk.Label(self, text="Export to Notion",
                  font=(None, 11, "bold")).pack(padx=16, pady=(14, 2), anchor="w")
        ttk.Label(self, text=self._playlist["name"],
                  foreground=TEXT_DIM).pack(padx=16, pady=(0, 8), anchor="w")

        if self._missing_count:
            warn = (f"⚠  {self._missing_count} track(s) are missing artist ID data.\n"
                    "   Please click Refresh in the playlist tab first.")
            ttk.Label(self, text=warn, foreground="#e6a817",
                      justify="left").pack(padx=16, pady=(0, 6), anchor="w")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=(0, 10))

        # Phase 1 — Playlist Record
        ttk.Label(self, text="Phase 1 — Playlist Record",
                  font=(None, 9, "bold")).pack(padx=16, anchor="w")
        self._p1_status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self._p1_status,
                  foreground=TEXT_DIM).pack(padx=16, pady=(2, 10), anchor="w")

        # Phase 2 — Songs & Artists
        ttk.Label(self, text=f"Phase 2 — Songs & Artists  ({n} track{'s' if n != 1 else ''})",
                  font=(None, 9, "bold")).pack(padx=16, anchor="w")
        self._p2_status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._p2_status,
                  foreground=TEXT_DIM).pack(padx=16, pady=(2, 4), anchor="w")
        self._p2_bar = ttk.Progressbar(self, mode="determinate",
                                        maximum=max(n, 1), length=360)
        self._p2_bar.pack(padx=16, pady=(0, 10))

        # Phase 3 — Playlist Songs
        ttk.Label(self, text=f"Phase 3 — Playlist Songs  ({n} track{'s' if n != 1 else ''})",
                  font=(None, 9, "bold")).pack(padx=16, anchor="w")
        self._p3_status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._p3_status,
                  foreground=TEXT_DIM).pack(padx=16, pady=(2, 4), anchor="w")
        self._p3_bar = ttk.Progressbar(self, mode="determinate",
                                        maximum=max(n, 1), length=360)
        self._p3_bar.pack(padx=16, pady=(0, 10))

        # Summary
        summary_frame = ttk.Frame(self)
        summary_frame.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        summary_frame.columnconfigure(0, weight=1)
        summary_frame.rowconfigure(0, weight=1)
        self._summary_text = tk.Text(
            summary_frame, height=6, wrap="word", state="disabled",
            font=(None, 9), relief="flat", bg=SURFACE, fg=TEXT,
        )
        self._summary_text.grid(row=0, column=0, sticky="nsew")
        _ssb = ttk.Scrollbar(summary_frame, orient="vertical",
                              command=self._summary_text.yview)
        _ssb.grid(row=0, column=1, sticky="ns")
        self._summary_text.configure(yscrollcommand=_ssb.set)

        # Button bar
        self._btn_bar = ttk.Frame(self)
        self._btn_bar.pack(fill="x", padx=16, pady=(0, 14))

        self._auto_start = tk.BooleanVar(value=self._settings_data.get("auto_start", False))
        ttk.Checkbutton(self._btn_bar, text="Auto-start",
                        variable=self._auto_start,
                        command=self._on_auto_start_toggled).pack(side="left")

        self._close_on_complete = tk.BooleanVar(value=self._settings_data.get("close_on_complete", False))
        ttk.Checkbutton(self._btn_bar, text="Close on complete",
                        variable=self._close_on_complete,
                        command=self._save_prefs).pack(side="left", padx=(8, 0))

        self._close_btn = ttk.Button(self._btn_bar, text="Cancel", command=self._on_close)
        self._close_btn.pack(side="right")

        self._start_btn = ttk.Button(self._btn_bar, text="Start Export",
                                     command=self._start,
                                     state="disabled" if self._missing_count else "normal")
        self._start_btn.pack(side="right", padx=(0, 6))

    # ------------------------------------------------------------------
    # Match dialog (called on main thread; blocks the export thread)
    # ------------------------------------------------------------------

    def _show_match_dialog(self, kind, item_name, candidates, result_holder, done_event):
        item_type = {"artist": "Artist", "song": "Song", "playlist": "Playlist"}.get(kind, kind)
        dialog = NotionMatchDialog(self, item_type, item_name, candidates)
        self.wait_window(dialog)
        result_holder["choice"] = dialog.result
        done_event.set()

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _save_prefs(self):
        s = load_settings()
        s["auto_start"] = self._auto_start.get()
        s["close_on_complete"] = self._close_on_complete.get()
        save_settings(s)

    def _on_auto_start_toggled(self):
        self._save_prefs()
        if self._auto_start.get():
            if not self._missing_count:
                self._begin_countdown(5)
        else:
            self._cancel_countdown()

    # ------------------------------------------------------------------
    # Auto-start countdown
    # ------------------------------------------------------------------

    def _begin_countdown(self, remaining: int):
        if remaining <= 0:
            self._countdown_id = None
            self._start()
            return
        self._start_btn.configure(text=f"Starting in {remaining}\u2026")
        self._countdown_id = self.after(1000, self._begin_countdown, remaining - 1)

    def _cancel_countdown(self):
        if self._countdown_id is not None:
            self.after_cancel(self._countdown_id)
            self._countdown_id = None
            self._start_btn.configure(text="Start Export")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _start(self):
        self._cancel_countdown()
        self._start_btn.configure(state="disabled", text="Start Export")
        self._close_btn.configure(text="Cancel")
        self._stop_event.clear()
        threading.Thread(target=self._run, daemon=True).start()

    def _ask_auto_create(self, unmatched_songs, unmatched_artists,
                         playlist_unmatched, total_count, result_holder, done_event):
        """Show a confirmation dialog for auto-creating unmatched items."""
        from tkinter import messagebox
        parts = []
        if unmatched_songs:
            parts.append(f"{unmatched_songs} song(s)")
        if unmatched_artists:
            parts.append(f"{unmatched_artists} artist(s)")
        if playlist_unmatched:
            parts.append("1 playlist")
        unmatched_desc = ", ".join(parts)
        answer = messagebox.askyesno(
            "Auto-Create New Records",
            f"All {total_count} tracks have Spotify URLs and all existing "
            f"Notion records are linked to Spotify.\n\n"
            f"After checking Notion, {unmatched_desc} were not found "
            f"by Spotify URL.\n\n"
            f"Create new records for all unmatched items?\n"
            f"(Skips manual name-matching dialogs)",
            parent=self,
        )
        result_holder["auto_create"] = answer
        done_event.set()

    def _run(self):
        def match_cb(kind, item_name, candidates):
            result_holder = {}
            done_event = threading.Event()
            self.after(0, self._show_match_dialog,
                       kind, item_name, candidates, result_holder, done_event)
            done_event.wait()
            return result_holder.get("choice")

        # Get database ID dynamically (always current value)
        from config import NOTION_PLAYLISTS_DB_ID
        db_id = NOTION_PLAYLISTS_DB_ID

        # Check if auto-create is possible (all tracks have Spotify URLs)
        all_have_urls = all(t.get("Spotify URL") for t in self._tracks)
        auto_create = False

        # ── Phase 1: Playlist Record ──────────────────────────────────
        self.after(0, self._p1_status.set, "Checking Notion for existing records…")

        # Fetch cover image
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

        # ── Auto-create pre-flight (all in Phase 1) ──────────────────
        if all_have_urls:
            from notion._songs import _batch_lookup_songs, _load_all_songs_cache
            from notion._artists import _batch_lookup_artists
            from notion._playlists import _batch_lookup_playlists, _all_playlists_have_urls

            _load_all_songs_cache()

            # Pre-flight: count unmatched songs
            preview_songs_reg = {}
            track_urls = [t["Spotify URL"] for t in self._tracks]
            _batch_lookup_songs(track_urls, preview_songs_reg)
            unmatched_songs = len(track_urls) - len(preview_songs_reg)

            # Pre-flight: count unmatched artists
            preview_artists_reg = {}
            artist_ids = list({a["id"] for t in self._tracks for a in t.get("Artists", [])})
            _batch_lookup_artists(artist_ids, preview_artists_reg)
            unmatched_artists = len(artist_ids) - len(preview_artists_reg)

            # Pre-flight: check playlist
            preview_pl_reg = {}
            _batch_lookup_playlists([playlist["id"]], preview_pl_reg, db_id)
            playlist_unmatched = playlist["id"] not in preview_pl_reg

            # Only offer auto-create if all existing Notion playlists have URLs
            all_playlists_linked = _all_playlists_have_urls(db_id)

            unmatched_total = unmatched_songs + unmatched_artists
            if playlist_unmatched and all_playlists_linked:
                unmatched_total += 1

            if unmatched_total > 0 and all_playlists_linked:
                result_holder = {}
                done_event = threading.Event()
                self.after(0, self._ask_auto_create,
                           unmatched_songs, unmatched_artists,
                           playlist_unmatched, len(self._tracks),
                           result_holder, done_event)
                done_event.wait()
                auto_create = result_holder.get("auto_create", False)

                if auto_create:
                    log.info("Auto-create enabled: %d unmatched song(s), %d unmatched artist(s)%s",
                             unmatched_songs, unmatched_artists,
                             ", 1 unmatched playlist" if playlist_unmatched else "")

        # ── Export playlist record ────────────────────────────────────
        self.after(0, self._p1_status.set, "Creating/verifying playlist…")

        try:
            pl_result = export_playlist(playlist, db_id, match_cb=match_cb,
                                        auto_create=auto_create)
        except Exception:
            import traceback
            self.after(0, self._show_error, traceback.format_exc().splitlines()[-1])
            return

        if pl_result["status"] == "added":
            p1_msg = f"✓ Created: {pl_result['name']}"
        elif pl_result["status"] == "pre_existing":
            p1_msg = f"✓ Already in Notion: {pl_result['name']}"
        else:
            p1_msg = "— Playlist skipped by user."
            self.after(0, self._p1_status.set, p1_msg)
            self.after(0, self._p2_status.set, "— Skipped (playlist not created)")
            self.after(0, self._p3_status.set, "— Skipped (playlist not created)")
            self.after(0, self._close_btn.configure, {"text": "Close"})
            return
        self.after(0, self._p1_status.set, p1_msg)

        if self._stop_event.is_set():
            self.after(0, self._close_btn.configure, {"text": "Close"})
            return

        # ── Phase 2: Songs & Artists ─────────────────────────────────
        self.after(0, self._p2_status.set, "Exporting…")

        def p2_progress(current, total, track_name):
            self.after(0, self._p2_bar.configure, {"value": current})
            self.after(0, self._p2_status.set,
                       f"Track {current} / {total}" +
                       (f"  —  {track_name[:50]}" if track_name else ""))

        try:
            tracks_summary = export_tracks(
                self._tracks, self._sp,
                progress_cb=p2_progress,
                stop_event=self._stop_event,
                match_cb=match_cb,
                auto_create=auto_create,
            )
        except ValueError as exc:
            self.after(0, self._show_error, str(exc))
            return
        except Exception:
            import traceback
            self.after(0, self._show_error, traceback.format_exc().splitlines()[-1])
            return

        if self._stop_event.is_set():
            self.after(0, self._p2_status.set, "Cancelled.")
            self.after(0, self._close_btn.configure, {"text": "Close"})
            return

        p2_done = (f"✓ {tracks_summary['added_songs']} added  "
                   f"— {tracks_summary['existing_songs']} already in Notion"
                   + (f"  ✗ {len(tracks_summary['errors'])} error(s)"
                      if tracks_summary["errors"] else ""))
        self.after(0, self._p2_status.set, p2_done)

        # ── Phase 3: Playlist Songs ───────────────────────────────────
        self.after(0, self._p3_status.set, "Exporting playlist songs…")

        def p3_progress(current, total, track_name):
            self.after(0, self._p3_bar.configure, {"value": current})
            self.after(0, self._p3_status.set,
                       f"Track {current} / {total}" +
                       (f"  —  {track_name[:50]}" if track_name else ""))

        try:
            songs_summary = export_playlist_songs(
                self._tracks,
                self._playlist["id"],
                db_id,
                sp=self._sp,
                match_cb=match_cb,
                progress_cb=p3_progress,
                stop_event=self._stop_event,
                auto_create=auto_create,
            )
        except Exception:
            import traceback
            self.after(0, self._show_error, traceback.format_exc().splitlines()[-1])
            return

        self._undo_manifest = {
            "playlist_songs": songs_summary.get("created_page_ids", []),
            "songs":          tracks_summary.get("created_song_page_ids", []),
            "artists":        tracks_summary.get("created_artist_page_ids", []),
            "playlist":       pl_result.get("created_page_ids", []),
        }

        self.after(0, self._show_summary, tracks_summary, pl_result, songs_summary)

    def _show_summary(self, tracks_summary, pl_result, songs_summary):
        parts = [f"✓ {songs_summary['added']} added"]
        if songs_summary.get("repaired"):
            parts.append(f"✓ {songs_summary['repaired']} repaired")
        parts.append(f"— {songs_summary['pre_existing']} already in Notion")
        if songs_summary.get("skipped"):
            parts.append(f"/ {songs_summary['skipped']} skipped")
        if songs_summary.get("errors"):
            parts.append(f"✗ {len(songs_summary['errors'])} error(s)")
        self._p3_status.set("  ".join(parts))

        lines = []
        if tracks_summary.get("added_song_names"):
            lines.append(f"Songs added ({len(tracks_summary['added_song_names'])}):")
            lines.extend(f"  {n}" for n in tracks_summary["added_song_names"])
            lines.append("")
        if tracks_summary.get("existing_song_names"):
            lines.append(f"Songs already in Notion ({len(tracks_summary['existing_song_names'])}):")
            lines.extend(f"  {n}" for n in tracks_summary["existing_song_names"])
            lines.append("")
        if tracks_summary.get("added_artist_names"):
            lines.append(f"Artists added ({len(tracks_summary['added_artist_names'])}):")
            lines.extend(f"  {n}" for n in sorted(set(tracks_summary["added_artist_names"])))
            lines.append("")
        if songs_summary.get("repaired_names"):
            lines.append(f"Playlist songs repaired ({len(songs_summary['repaired_names'])}):")
            lines.extend(f"  {n}" for n in songs_summary["repaired_names"])
            lines.append("")
        if songs_summary.get("added_names"):
            lines.append(f"Playlist songs added ({len(songs_summary['added_names'])}):")
            lines.extend(f"  {n}" for n in songs_summary["added_names"])
            lines.append("")
        if songs_summary.get("skipped_names"):
            lines.append(f"Skipped ({len(songs_summary['skipped_names'])}):")
            lines.extend(f"  {n}" for n in songs_summary["skipped_names"])
            lines.append("")
        all_errors = tracks_summary.get("errors", []) + songs_summary.get("errors", [])
        if all_errors:
            lines.append(f"Errors ({len(all_errors)}) — see Settings tab:")
            lines.extend(f"  {e['track']}: {e['error']}" for e in all_errors)

        summary_str = "\n".join(lines) if lines else "No details."
        log.info("Export summary for '%s':\n%s", self._playlist["name"], summary_str)

        self._summary_text.configure(state="normal")
        self._summary_text.delete("1.0", "end")
        self._summary_text.insert("end", summary_str)
        self._summary_text.configure(state="disabled")

        self._close_btn.configure(text="Close")

        total_created = sum(len(v) for v in self._undo_manifest.values())
        if total_created > 0:
            self._undo_btn = ttk.Button(
                self._btn_bar,
                text=f"Undo Export ({total_created})",
                command=self._start_undo,
            )
            self._undo_btn.pack(side="right", padx=(0, 6))

        if self._close_on_complete.get() and total_created == 0:
            self.after(1500, self._close_with_tab)

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def _start_undo(self):
        self._undo_btn.configure(state="disabled")
        self._close_btn.configure(state="disabled")
        total = sum(len(v) for v in self._undo_manifest.values())
        self._p3_status.set("Undoing export…")
        self._p3_bar.configure(value=0, maximum=max(total, 1))
        self._stop_event.clear()
        threading.Thread(target=self._run_undo, daemon=True).start()

    def _run_undo(self):
        def undo_progress(current, total, category):
            self.after(0, self._p3_bar.configure, {"value": current})
            self.after(0, self._p3_status.set,
                       f"Undoing {current}/{total}  —  {category}")

        result = undo_export(self._undo_manifest,
                             progress_cb=undo_progress,
                             stop_event=self._stop_event)

        def finish():
            msg = f"Undo complete: {result['archived']} archived"
            if result["failed"]:
                msg += f", {result['failed']} failed"
            self._p3_status.set(msg)
            self._undo_btn.configure(text="Undone", state="disabled")
            self._close_btn.configure(state="normal", text="Close")
            self._undo_manifest = {}

        self.after(0, finish)

    def _show_error(self, msg: str):
        self._p1_status.set(f"Error: {msg}")
        self._close_btn.configure(text="Close")
        self._start_btn.configure(text="Start Export",
                                  state="normal" if not self._missing_count else "disabled")

    def _close_with_tab(self):
        self._on_close()
        self._parent_tab._close_cb()

    def _on_close(self):
        self._stop_event.set()
        self.destroy()
