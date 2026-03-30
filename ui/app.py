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
from notion.check_setup import check_notion_setup, get_setup_instructions
from spotify import get_spotify_client, fetch_user_playlists
from theme import apply_theme, make_icon
from ui.browser import PlaylistBrowser
from ui.console import SettingsTab
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
        self._check_notion_setup()
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
            content = self._settings.text.get("1.0", "end")
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

        self._settings = SettingsTab(self.notebook)
        self.notebook.add(self._settings, text="Settings")
        self._settings.set_reset_callback(self._on_notion_reset_complete)

    # ------------------------------------------------------------------
    # Notion setup check
    # ------------------------------------------------------------------

    def _on_notion_reset_complete(self):
        """Called when Notion database reset is complete."""
        log.info("Notion reset complete — checking for missing databases…")
        # Reload config to pick up the "missing" database IDs
        from notion.check_setup import _reload_config
        _reload_config()
        # Now check for missing databases and offer setup
        self._check_notion_setup()

    def _check_notion_setup(self):
        """Check if Notion databases are configured. Offer setup if not."""
        all_exist, missing = check_notion_setup()
        if all_exist:
            log.info("✓ Notion databases are accessible")
            return

        if not missing:
            # API key not set
            log.info("NOTION_API_KEY not set in .env — Notion features disabled")
            return

        # Some databases are missing — offer to set them up
        log.warning(f"Missing Notion databases: {', '.join(missing)}")
        instructions = get_setup_instructions()
        result = messagebox.askokcancel(
            "New Notion Teamspace Detected",
            instructions
        )
        if result:
            self._offer_notion_setup()

    def _offer_notion_setup(self):
        """Set up Notion databases in a new Teamspace."""
        from tkinter import simpledialog
        from notion.setup import setup_databases_in_page, update_config_file
        from notion_config import NOTION_PARENT_PAGE_ID

        # Use stored parent page ID if available
        parent_page_id = NOTION_PARENT_PAGE_ID

        if not parent_page_id:
            # Ask for parent page ID where the setup should create things
            instructions = (
                "To set up the databases, I need a parent page ID in your Notion workspace.\n\n"
                "Steps:\n"
                "1. Open any page in your Notion workspace\n"
                "2. Copy the page ID from the URL:\n"
                "   https://www.notion.so/COPY_THIS_PART?...\n\n"
                "3. Paste it here (just the ID, no spaces):"
            )
            parent_page_id = simpledialog.askstring(
                "Parent Page Required",
                instructions,
            )

            if parent_page_id is None:
                log.info("Setup cancelled by user")
                return

            parent_page_id = parent_page_id.strip()
            if not parent_page_id:
                log.info("No parent page ID provided")
                messagebox.showwarning(
                    "Page ID Required",
                    "You must provide a parent page ID to proceed with setup."
                )
                return
        else:
            log.info(f"Using stored parent page ID for setup")

        log.info(f"Setting up Notion databases in new Teamspace (parent: {parent_page_id[:8]}...)")
        try:
            id_mapping = setup_databases_in_page(parent_page_id)
            update_config_file(id_mapping, parent_page_id)
            log.info("✓ Setup complete! Databases created and config updated.")
            messagebox.showinfo(
                "Setup Complete",
                "✓ Notion databases created successfully!\n\n"
                "A new 'MusicTunnel' page has been created with all four databases.\n"
                "notion_config.py has been updated."
            )
            # Reload settings to recognize the new databases
            self.after(500, self._on_notion_setup_complete)
        except Exception as e:
            log.error(f"Setup failed: {e}")
            messagebox.showerror(
                "Setup Failed",
                f"Could not create databases:\n{e}\n\n"
                "See the Settings tab for details."
            )

    def _on_notion_setup_complete(self):
        """Called when Notion setup is complete — reload and verify."""
        log.info("Notion setup complete! Reloading database connections…")
        from notion.check_setup import _reload_config
        _reload_config()
        # Check if databases are now accessible
        all_exist, missing = check_notion_setup()
        if all_exist:
            messagebox.showinfo(
                "Setup Complete",
                "✓ Notion databases are now ready to use!"
            )
            log.info("✓ All Notion databases verified and accessible")
        else:
            messagebox.showwarning(
                "Setup Verification",
                f"Setup completed, but could not verify all databases.\n"
                f"Missing: {', '.join(missing)}\n\n"
                f"Please check the Settings tab for details."
            )

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
