import time
import traceback
from datetime import datetime, timezone

from logger import log
from notion._api import _notion_post, _notion_request
from notion._helpers import SKIP, _apostrophe_variants, _page_title
from notion._registry import load_registry, save_registry


def _find_playlist_by_spotify_url(spotify_url: str, db_id: str) -> "tuple[str, str] | None":
    result = _notion_post(f"databases/{db_id}/query", {
        "filter": {"property": "Spotify URL", "url": {"equals": spotify_url}},
        "page_size": 1,
    })
    pages = result.get("results", [])
    if not pages:
        return None
    return pages[0]["id"], _page_title(pages[0])


def _find_playlist_by_name(name: str, db_id: str) -> "tuple[str, str] | None":
    for variant in _apostrophe_variants(name):
        result = _notion_post(f"databases/{db_id}/query", {
            "filter": {"property": "Name", "title": {"equals": variant}},
            "page_size": 1,
        })
        pages = result.get("results", [])
        if pages:
            return pages[0]["id"], _page_title(pages[0])
    return None


def _search_similar_playlists(name: str, db_id: str) -> list:
    seen_ids: set = set()
    candidates: list = []
    search_terms = list(_apostrophe_variants(name))
    first_word = name.split()[0]
    for v in _apostrophe_variants(first_word):
        if v not in search_terms:
            search_terms.append(v)

    for search_term in search_terms:
        result = _notion_post(f"databases/{db_id}/query", {
            "filter": {"property": "Name", "title": {"contains": search_term}},
            "page_size": 10,
        })
        for page in result.get("results", []):
            if page["id"] in seen_ids:
                continue
            page_name = _page_title(page)
            if page_name:
                candidates.append({"id": page["id"], "name": page_name})
                seen_ids.add(page["id"])
        if candidates:
            return candidates
    return []


def _backfill_playlist_spotify_url(notion_page_id: str, playlist: dict) -> None:
    spotify_url = f"https://open.spotify.com/playlist/{playlist['id']}"
    properties: dict = {"Spotify URL": {"url": spotify_url}}
    if playlist.get("cover_url"):
        properties["Playlist Cover"] = {
            "files": [{"name": "cover", "type": "external",
                       "external": {"url": playlist["cover_url"]}}]
        }
    _notion_request("PATCH", f"pages/{notion_page_id}", json={"properties": properties})
    log.info("Backfilled Spotify metadata on existing playlist: %r", playlist["name"])


def _create_playlist_in_notion(playlist: dict, db_id: str) -> str:
    spotify_url = f"https://open.spotify.com/playlist/{playlist['id']}"
    properties: dict = {
        "Name": {"title": [{"text": {"content": playlist["name"]}}]},
        "Spotify URL": {"url": spotify_url},
    }
    if playlist.get("cover_url"):
        properties["Playlist Cover"] = {
            "files": [{"name": "cover", "type": "external",
                       "external": {"url": playlist["cover_url"]}}]
        }
    result = _notion_post("pages", {
        "parent": {"database_id": db_id},
        "properties": properties,
    })
    return result["id"]


def _ensure_playlist(playlist: dict, registry: dict,
                     match_cb=None, db_id: str = None) -> "tuple[str, str]":
    """Return (notion_page_id, status): 'pre_existing', 'added', or 'skipped'."""
    spotify_id = playlist["id"]
    spotify_url = f"https://open.spotify.com/playlist/{spotify_id}"
    now = datetime.now(timezone.utc).isoformat()

    if spotify_id in registry:
        return registry[spotify_id]["notion_page_id"], "pre_existing"

    notion_id = None

    # Step 1: match by Spotify URL
    time.sleep(0.35)
    result = _find_playlist_by_spotify_url(spotify_url, db_id)
    if result:
        match_id, match_name = result
        if match_cb:
            choice = match_cb("playlist", playlist["name"],
                              [{"id": match_id, "name": match_name}])
            if choice == SKIP:
                return None, "skipped"
            notion_id = choice
        else:
            notion_id = match_id

    # Step 2: exact name match
    if not notion_id:
        time.sleep(0.35)
        result = _find_playlist_by_name(playlist["name"], db_id)
        if result:
            match_id, match_name = result
            try:
                _backfill_playlist_spotify_url(match_id, playlist)
            except Exception:
                log.warning("Could not backfill playlist %r:\n%s",
                            playlist["name"], traceback.format_exc())
            if match_cb:
                choice = match_cb("playlist", playlist["name"],
                                  [{"id": match_id, "name": match_name}])
                if choice == SKIP:
                    return None, "skipped"
                notion_id = choice
            else:
                notion_id = match_id

    # Step 3: interactive similarity search
    if not notion_id and match_cb:
        time.sleep(0.35)
        candidates = _search_similar_playlists(playlist["name"], db_id)
        choice = match_cb("playlist", playlist["name"], candidates)
        if choice == SKIP:
            return None, "skipped"
        if choice:
            try:
                _backfill_playlist_spotify_url(choice, playlist)
            except Exception:
                log.warning("Could not backfill playlist %r:\n%s",
                            playlist["name"], traceback.format_exc())
            notion_id = choice

    if notion_id:
        log.info("Matched Notion playlist: %r", playlist["name"])
        registry[spotify_id] = {
            "notion_page_id": notion_id,
            "name": playlist["name"],
            "db_id": db_id,
            "status": "pre_existing",
            "first_seen": now, "last_synced": now,
            "history": [{"action": "found_existing", "timestamp": now}],
        }
        return notion_id, "pre_existing"

    time.sleep(0.35)
    notion_id = _create_playlist_in_notion(playlist, db_id)
    registry[spotify_id] = {
        "notion_page_id": notion_id,
        "name": playlist["name"],
        "db_id": db_id,
        "status": "added",
        "first_seen": now, "last_synced": now,
        "history": [{"action": "added", "timestamp": now}],
    }
    log.info("Created Notion playlist: %r", playlist["name"])
    return notion_id, "added"


def export_playlist(playlist: dict, db_id: str, match_cb=None) -> dict:
    """
    Ensure the playlist exists in the given Notion DB.
    Returns {"status": str, "page_id": str|None, "name": str}
    """
    registry = load_registry("playlists")
    page_id, status = _ensure_playlist(playlist, registry, match_cb=match_cb, db_id=db_id)
    save_registry("playlists", registry)
    log.info("Playlist export: %r → %s", playlist["name"], status)
    return {"status": status, "page_id": page_id, "name": playlist["name"]}
