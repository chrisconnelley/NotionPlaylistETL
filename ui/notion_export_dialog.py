import threading
import tkinter as tk
from tkinter import ttk

from logger import log
from notion import export_tracks, validate_registry
from theme import BG, SURFACE, GREEN, TEXT, TEXT_DIM
from ui.match_dialog import NotionMatchDialog


class NotionExportDialog(tk.Toplevel):
    """
    Dialog for exporting a playlist's tracks to Notion Songs and Song Artists databases.
    Shows a pre-flight check, Start Export button, live progress, and completion summary.
    """

    def __init__(self, parent_tab, sp, tracks: list):
        super().__init__(parent_tab)
        self.title("Export to Notion")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()

        self._parent_tab = parent_tab
        self._sp = sp
        self._tracks = tracks
        self._stop_event = threading.Event()

        missing = [t for t in tracks if not t.get("Artists")]
        self._missing_count = len(missing)

        self._build_ui()

        self.update_idletasks()
        px, py = parent_tab.winfo_rootx(), parent_tab.winfo_rooty()
        pw, ph = parent_tab.winfo_width(), parent_tab.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        pad = {"padx": 16, "pady": (0, 0)}

        ttk.Label(self, text=f"Exporting {len(self._tracks)} tracks to Notion",
                  font=(None, 11, "bold")).pack(padx=16, pady=(14, 6), anchor="w")

        if self._missing_count:
            warn = (f"⚠  {self._missing_count} track(s) are missing artist ID data.\n"
                    "   Please click Refresh in the playlist tab first.")
            ttk.Label(self, text=warn, foreground="#e6a817",
                      justify="left").pack(padx=16, pady=(0, 8), anchor="w")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=(0, 10))

        self._status_var = tk.StringVar(value="Ready to export.")
        ttk.Label(self, textvariable=self._status_var,
                  foreground=TEXT_DIM).pack(padx=16, pady=(0, 4), anchor="w")

        self._progress = ttk.Progressbar(self, mode="determinate",
                                         maximum=max(len(self._tracks), 1), length=340)
        self._progress.pack(padx=16, pady=(0, 10))

        self._detail_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._detail_var,
                  foreground=TEXT_DIM, font=(None, 9)).pack(padx=16, pady=(0, 4), anchor="w")

        summary_frame = ttk.Frame(self)
        summary_frame.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        summary_frame.columnconfigure(0, weight=1)
        summary_frame.rowconfigure(0, weight=1)
        self._summary_text = tk.Text(
            summary_frame, height=8, wrap="word", state="disabled",
            font=(None, 9), relief="flat", bg=SURFACE, fg=TEXT,
        )
        self._summary_text.grid(row=0, column=0, sticky="nsew")
        _ssb = ttk.Scrollbar(summary_frame, orient="vertical",
                              command=self._summary_text.yview)
        _ssb.grid(row=0, column=1, sticky="ns")
        self._summary_text.configure(yscrollcommand=_ssb.set)

        btn_bar = ttk.Frame(self)
        btn_bar.pack(fill="x", padx=16, pady=(0, 14))

        self._close_btn = ttk.Button(btn_bar, text="Cancel", command=self._on_close)
        self._close_btn.pack(side="right")

        self._start_btn = ttk.Button(btn_bar, text="Start Export",
                                     command=self._start,
                                     state="disabled" if self._missing_count else "normal")
        self._start_btn.pack(side="right", padx=(0, 6))

        self._validate_btn = ttk.Button(btn_bar, text="Validate Registry",
                                        command=self._validate_registry)
        self._validate_btn.pack(side="left")

        self._verify_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(btn_bar, text="Verify cached entries",
                        variable=self._verify_var).pack(side="left", padx=(8, 0))

    def _validate_registry(self):
        self._validate_btn.configure(state="disabled")
        self._status_var.set("Validating registry against Notion…")
        self._progress.configure(mode="indeterminate")
        self._progress.start(12)
        threading.Thread(target=self._run_validation, daemon=True).start()

    def _run_validation(self):
        try:
            def progress_cb(current, total, entry_name):
                self.after(0, self._status_var.set,
                           f"Checking {current + 1} / {total}: {entry_name[:50]}")

            song_keys   = {t.get("Spotify URL") for t in self._tracks if t.get("Spotify URL")}
            artist_keys = {a["id"] for t in self._tracks for a in t.get("Artists", [])}

            a_removed, a_kept = validate_registry("artists", progress_cb=progress_cb,
                                                  keys=artist_keys)
            s_removed, s_kept = validate_registry("songs",   progress_cb=progress_cb,
                                                  keys=song_keys)
            msg = (f"Validation complete — "
                   f"artists: {a_removed} removed, {a_kept} kept  |  "
                   f"songs: {s_removed} removed, {s_kept} kept")
            self.after(0, self._validation_done, msg)
        except Exception:
            import traceback
            self.after(0, self._validation_done,
                       f"Validation error: {traceback.format_exc().splitlines()[-1]}")

    def _validation_done(self, msg: str):
        self._progress.stop()
        self._progress.configure(mode="determinate")
        self._status_var.set(msg)
        self._validate_btn.configure(state="normal")

    def _start(self):
        self._start_btn.configure(state="disabled")
        self._close_btn.configure(text="Cancel")
        self._progress["value"] = 0
        self._status_var.set("Exporting…")
        threading.Thread(target=self._run, daemon=True).start()

    def _show_match_dialog(self, kind, item_name, candidates, result_holder, done_event):
        """Called on the main thread; shows NotionMatchDialog and signals the export thread."""
        item_type = "Artist" if kind == "artist" else "Song"
        dialog = NotionMatchDialog(self, item_type, item_name, candidates)
        self.wait_window(dialog)
        result_holder["choice"] = dialog.result
        done_event.set()

    def _run(self):
        def progress_cb(current, total, track_name):
            self.after(0, self._progress.configure, {"value": current})
            self.after(0, self._status_var.set,
                       f"Track {current} / {total}")
            self.after(0, self._detail_var.set,
                       track_name[:60] if track_name else "")

        def status_cb(spotify_url, display_status):
            self.after(0, self._parent_tab.update_notion_status, spotify_url, display_status)

        def artist_status_cb(spotify_url, display_status):
            self.after(0, self._parent_tab.update_artist_notion_status,
                       spotify_url, display_status)

        def match_cb(kind, item_name, candidates):
            """Pause the export thread, ask the user, return their choice."""
            result_holder = {}
            done_event = threading.Event()
            self.after(0, self._show_match_dialog,
                       kind, item_name, candidates, result_holder, done_event)
            done_event.wait()
            return result_holder.get("choice")  # page ID or None (create new)

        try:
            summary = export_tracks(
                self._tracks, self._sp,
                progress_cb=progress_cb,
                status_cb=status_cb,
                artist_status_cb=artist_status_cb,
                stop_event=self._stop_event,
                match_cb=match_cb,
                verify_batch=self._verify_var.get(),
            )
            self.after(0, self._show_summary, summary)
        except ValueError as exc:
            self.after(0, self._show_error, str(exc))
        except Exception:
            import traceback
            self.after(0, self._show_error, traceback.format_exc().splitlines()[-1])

    def _show_summary(self, summary):
        errors = summary.get("errors", [])
        self._status_var.set("Export complete.")
        self._detail_var.set(
            f"✓ {summary['added_songs']} song(s) added  "
            f"– {summary['existing_songs']} already in Notion  "
            f"/ {summary['skipped_songs']} skipped  "
            f"✓ {summary['added_artists']} artist(s) added  "
            f"– {summary['pre_existing_artists']} already in Notion"
            + (f"  ✗ {len(errors)} error(s)" if errors else "")
        )

        lines = []
        if summary.get("added_song_names"):
            lines.append(f"Songs added ({len(summary['added_song_names'])}):")
            lines.extend(f"  {n}" for n in summary["added_song_names"])
            lines.append("")
        if summary.get("existing_song_names"):
            lines.append(f"Songs already in Notion ({len(summary['existing_song_names'])}):")
            lines.extend(f"  {n}" for n in summary["existing_song_names"])
            lines.append("")
        if summary.get("skipped_songs"):
            lines.append(f"Songs skipped by user: {summary['skipped_songs']}")
            lines.append("")
        if summary.get("added_artist_names"):
            lines.append(f"Artists added ({len(summary['added_artist_names'])}):")
            lines.extend(f"  {n}" for n in sorted(set(summary["added_artist_names"])))
            lines.append("")
        if summary.get("existing_artist_names"):
            lines.append(f"Artists already in Notion ({len(summary['existing_artist_names'])}):")
            lines.extend(f"  {n}" for n in sorted(set(summary["existing_artist_names"])))
            lines.append("")
        if summary.get("skipped_artists"):
            lines.append(f"Artists skipped by user: {summary['skipped_artists']}")
        if errors:
            lines.append("")
            lines.append(f"Errors ({len(errors)}) — see Console tab:")
            lines.extend(f"  {e['track']}: {e['error']}" for e in errors)

        self._summary_text.configure(state="normal")
        self._summary_text.delete("1.0", "end")
        self._summary_text.insert("end", "\n".join(lines))
        self._summary_text.configure(state="disabled")

        self._close_btn.configure(text="Close")
        self._start_btn.configure(state="disabled")

    def _show_error(self, msg: str):
        self._status_var.set(f"Error: {msg}")
        self._close_btn.configure(text="Close")
        self._start_btn.configure(state="normal" if not self._missing_count else "disabled")

    def _on_close(self):
        self._stop_event.set()
        self.destroy()
