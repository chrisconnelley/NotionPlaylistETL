"""
Check if Notion databases are configured and accessible.
Offer to set up new databases in a new Teamspace.
"""

import importlib
import sys

from logger import log
from notion.setup import check_database_exists


def _reload_config():
    """Reload config modules to pick up changes to notion_config.py."""
    log.debug("Starting config reload...")

    # Reload in dependency order
    modules_to_reload = [
        "notion_config",
        "config",
        "notion._songs",
        "notion._artists",
        "notion._playlists",
        "notion._playlist_songs",
    ]

    for module_name in modules_to_reload:
        if module_name in sys.modules:
            try:
                importlib.reload(sys.modules[module_name])
                log.debug(f"Reloaded module: {module_name}")

                # After reloading config, log the values
                if module_name == "config":
                    from config import NOTION_PLAYLISTS_DB_ID
                    log.debug(f"  After reload, NOTION_PLAYLISTS_DB_ID = {NOTION_PLAYLISTS_DB_ID[:8] if NOTION_PLAYLISTS_DB_ID != 'missing' else 'missing'}...")

            except Exception as e:
                log.warning(f"Could not reload {module_name}: {e}")

    log.debug("Config reload complete")


def get_db_ids():
    """Get current database IDs, reloading config if needed."""
    _reload_config()

    from config import (
        NOTION_API_KEY,
        NOTION_SONGS_DB_ID,
        NOTION_ARTISTS_DB_ID,
        NOTION_PLAYLISTS_DB_ID,
        NOTION_PLAYLIST_SONGS_DB_ID,
    )

    db_ids = {
        "api_key": NOTION_API_KEY,
        "ids": {
            "Songs": NOTION_SONGS_DB_ID,
            "Song Artists": NOTION_ARTISTS_DB_ID,
            "Playlists": NOTION_PLAYLISTS_DB_ID,
            "Playlist Songs": NOTION_PLAYLIST_SONGS_DB_ID,
        }
    }

    log.debug(f"Loaded database IDs: Songs={NOTION_SONGS_DB_ID[:8]}..., Artists={NOTION_ARTISTS_DB_ID[:8]}..., "
              f"Playlists={NOTION_PLAYLISTS_DB_ID[:8]}..., PlaylistSongs={NOTION_PLAYLIST_SONGS_DB_ID[:8]}...")

    return db_ids


def check_notion_setup() -> tuple[bool, list[str]]:
    """
    Check if Notion databases are accessible.

    Returns:
        (all_exist, missing_dbs)
        - all_exist: True if all databases are accessible
        - missing_dbs: List of database names that don't exist
    """
    db_info = get_db_ids()
    api_key = db_info["api_key"]
    db_config = db_info["ids"]

    if not api_key:
        log.error("NOTION_API_KEY not set in .env")
        return False, ["All databases (API key missing)"]

    missing = []
    for name, db_id in db_config.items():
        # Skip checking "missing" placeholder IDs
        if db_id == "missing":
            missing.append(name)
            continue

        if not check_database_exists(db_id):
            missing.append(name)
            log.warning(f"Database not found: {name} (ID: {db_id})")

    all_exist = len(missing) == 0
    return all_exist, missing


def get_setup_instructions() -> str:
    """Get user-friendly instructions for setting up a new Teamspace."""
    return """
You appear to be using a new Notion Teamspace.

The configured database IDs are not accessible. I can set up new databases
in your Teamspace using the saved schemas.

To proceed, I will need:
1. A parent page ID (any existing page in your Notion workspace)
2. Then I'll create a "MusicTunnel" page inside it
3. Create all four databases inside MusicTunnel
4. Update notion_config.py with the new database IDs

Ready to set up? Click OK to continue, or Cancel to configure manually.
"""
