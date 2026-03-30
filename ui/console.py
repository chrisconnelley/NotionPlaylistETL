import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from logger import log, log_queue
from notion import snapshot_schema
from theme import BG, SURFACE, TEXT, TEXT_DIM


class SettingsTab(ttk.Frame):
    """Settings tab with schema verification, database reset, and live console logs."""

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
        self.rowconfigure(0, weight=0)  # Settings section
        self.rowconfigure(1, weight=1)  # Console section

        # ── Settings Section ──────────────────────────────────────────
        settings_frame = ttk.Frame(self)
        settings_frame.grid(row=0, column=0, sticky="ew", padx=6, pady=6)

        ttk.Label(settings_frame, text="Settings",
                  font=(None, 11, "bold")).pack(anchor="w")
        ttk.Separator(settings_frame, orient="horizontal").pack(fill="x", pady=(4, 8))

        btn_frame = ttk.Frame(settings_frame)
        btn_frame.pack(anchor="w")
        ttk.Button(btn_frame, text="Verify Schema",
                   command=self._verify_schema).pack(side="left", padx=(0, 4))
        ttk.Button(btn_frame, text="Reset Notion Databases",
                   command=self._reset_notion_databases).pack(side="left")

        # ── Console Section ───────────────────────────────────────────
        console_label = ttk.Label(self, text="Console Output",
                                  font=(None, 10, "bold"))
        console_label.grid(row=1, column=0, sticky="nw", padx=6, pady=(4, 2))

        text_frame = ttk.Frame(self)
        text_frame.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 0))
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.text = tk.Text(text_frame, state="disabled", font=("Courier New", 9),
                            wrap="none", bg=SURFACE, fg=TEXT,
                            insertbackground=TEXT, height=12)
        self.text.grid(row=0, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=vsb.set)

        hsb = ttk.Scrollbar(text_frame, orient="horizontal", command=self.text.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.text.configure(xscrollcommand=hsb.set)

        bar = ttk.Frame(self)
        bar.grid(row=3, column=0, sticky="ew", padx=6, pady=(2, 6))
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

    def _verify_schema(self):
        """Run schema verification in background and log results."""
        self._append("[INFO] Starting schema verification…")
        threading.Thread(target=self._run_schema_verification, daemon=True).start()

    def _run_schema_verification(self):
        """Fetch and snapshot schemas from Notion."""
        try:
            self._append("[INFO] Fetching Notion database list and schemas…")
            saved = snapshot_schema()
            self._append(f"[INFO] ✓ Schema verification complete!")
            self._append(f"[INFO] Saved {len(saved)} database schema(s):")
            for name in saved:
                self._append(f"[INFO]   ✓ {name}")
        except Exception as exc:
            self._append(f"[ERROR] Schema verification failed: {exc}")
            import traceback
            for line in traceback.format_exc().splitlines():
                self._append(f"[ERROR] {line}")

    def _reset_notion_databases(self):
        """Reset Notion databases with confirmation."""
        result = messagebox.askyesno(
            "Reset Notion Databases",
            "This will:\n"
            "• Delete all 4 Notion databases (Songs, Artists, Playlists, Playlist Songs)\n"
            "• Delete the MusicTunnel page\n"
            "• Reset notion_config.py\n\n"
            "You will need to run setup again to recreate them.\n\n"
            "Are you sure?"
        )
        if not result:
            log.info("Reset cancelled by user")
            return

        self._append("[INFO] Resetting Notion databases…")
        threading.Thread(target=self._run_reset_notion_databases, daemon=True).start()

    def _run_reset_notion_databases(self):
        """Delete databases and MusicTunnel page in Notion."""
        try:
            from config import NOTION_API_KEY
            from notion.setup import get_notion_client

            if not NOTION_API_KEY:
                self._append("[ERROR] NOTION_API_KEY not set")
                return

            client = get_notion_client()

            # Get current DB IDs from config
            from notion_config import (
                NOTION_SONGS_DB_ID,
                NOTION_ARTISTS_DB_ID,
                NOTION_PLAYLISTS_DB_ID,
                NOTION_PLAYLIST_SONGS_DB_ID,
            )

            db_ids = [
                ("Songs", NOTION_SONGS_DB_ID),
                ("Song Artists", NOTION_ARTISTS_DB_ID),
                ("Playlists", NOTION_PLAYLISTS_DB_ID),
                ("Playlist Songs", NOTION_PLAYLIST_SONGS_DB_ID),
            ]

            # Delete databases
            for name, db_id in db_ids:
                try:
                    client.blocks.delete(db_id)
                    self._append(f"[INFO] ✓ Deleted database: {name}")
                except Exception as e:
                    self._append(f"[WARNING] Could not delete {name}: {e}")

            # Find and delete MusicTunnel page
            # We need to find it by searching for pages, then delete it
            try:
                results = client.search(
                    query="MusicTunnel",
                    filter={"property": "object", "value": "page"},
                )
                for item in results.get("results", []):
                    if item.get("object") == "page":
                        title = ""
                        if "properties" in item:
                            title_prop = item["properties"].get("title", {})
                            if isinstance(title_prop, dict) and "title" in title_prop:
                                title_items = title_prop["title"]
                                if isinstance(title_items, list) and title_items:
                                    title = title_items[0].get("plain_text", "")

                        if title == "MusicTunnel":
                            page_id = item["id"]
                            client.blocks.delete(page_id)
                            self._append(f"[INFO] ✓ Deleted MusicTunnel page")
                            break
            except Exception as e:
                self._append(f"[WARNING] Could not delete MusicTunnel page: {e}")

            # Reset notion_config.py
            self._reset_notion_config()

            self._append("[INFO] ✓ Notion databases reset complete!")
            self._append("[INFO] Click 'OK' below to reload settings, or restart the app to set up again.")

            # Schedule reload on main thread
            self.after(100, self._notify_reset_complete)

        except Exception as exc:
            self._append(f"[ERROR] Reset failed: {exc}")
            import traceback
            for line in traceback.format_exc().splitlines():
                self._append(f"[ERROR] {line}")

    def _reset_notion_config(self):
        """Reset notion_config.py to dummy values (preserve parent page ID)."""
        import os
        from config import BASE_DIR
        from notion_config import NOTION_PARENT_PAGE_ID

        config_path = os.path.join(BASE_DIR, "notion_config.py")

        # Preserve the parent page ID
        parent_id_line = f'NOTION_PARENT_PAGE_ID = "{NOTION_PARENT_PAGE_ID}"' if NOTION_PARENT_PAGE_ID else 'NOTION_PARENT_PAGE_ID = None'

        content = f'''"""
Notion database IDs — regenerated on first setup, then persisted.

This file is generated by the setup flow when connecting to a new Teamspace.
See notion/setup.py for the setup process.
"""

# Database IDs for the current Notion Teamspace
NOTION_SONGS_DB_ID = "missing"
NOTION_ARTISTS_DB_ID = "missing"
NOTION_PLAYLISTS_DB_ID = "missing"
NOTION_PLAYLIST_SONGS_DB_ID = "missing"

# Parent page ID where MusicTunnel and databases are created
# If set, will be used by default for setup (no need to prompt)
{parent_id_line}

# Database name → schema filename mapping
DB_NAMES = {{
    "Songs": "songs.json",
    "Song Artists": "song_artists.json",
    "Playlists": "playlists.json",
    "Playlist Songs": "playlist_songs.json",
}}
'''
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content)
        self._append("[INFO] ✓ Reset notion_config.py (preserved parent page ID)")

    def _notify_reset_complete(self):
        """Notify that reset is complete and trigger check."""
        messagebox.showinfo(
            "Reset Complete",
            "Notion databases have been reset.\n\n"
            "The app will now check for missing databases and offer to set them up."
        )
        # Trigger the check_notion_setup callback if available
        if hasattr(self, "_on_reset_complete_callback"):
            self._on_reset_complete_callback()

    def set_reset_callback(self, callback):
        """Set callback to be called when reset is complete."""
        self._on_reset_complete_callback = callback
