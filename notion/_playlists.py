import time
import traceback

from logger import log
from notion._api import _notion_post, _notion_request
from notion._helpers import (
    SKIP, _apostrophe_variants, _page_title, _chunks,
    _normalize_spotify_url, _make_registry_entry, _merge_candidates,
)


def _get_playlists_db_id():
    """Get Playlists database ID dynamically (always gets current value from config)."""
    from config import NOTION_PLAYLISTS_DB_ID
    return NOTION_PLAYLISTS_DB_ID


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


def _batch_lookup_playlists(playlist_ids: list, registry: dict, db_id: str) -> None:
    """Batch-check Notion for playlists by Spotify ID. Updates registry in-place for found items."""
    if not playlist_ids:
        return

    log.info("Pre-flight: checking %d unregistered playlist ID(s) in Notion", len(playlist_ids))

    # Verify database ID
    if db_id == "missing":
        log.error("Pre-flight: database ID is 'missing' — databases not configured")
        return

    try:
        found_count = 0
        for chunk in _chunks(playlist_ids, 50):
            time.sleep(0.35)
            result = _notion_post(f"databases/{db_id}/query", {
                "filter": {"or": [
                    {"property": "Spotify URL", "url": {"equals": f"https://open.spotify.com/playlist/{pid}"}}
                    for pid in chunk
                ]},
                "page_size": len(chunk),
            })
            for page in result.get("results", []):
                url = page.get("properties", {}).get("Spotify URL", {}).get("url")
                if url:
                    playlist_id = url.split("/")[-1]
                    if playlist_id and playlist_id not in registry:
                        name = _page_title(page)
                        registry[playlist_id] = _make_registry_entry(
                            page["id"], name, "found_existing")
                        found_count += 1
                        log.info("Pre-flight: matched playlist %r by Spotify URL", name)
        log.info("Pre-flight: batch playlist lookup complete — found %d/%d", found_count, len(playlist_ids))
    except Exception:
        log.warning("Pre-flight playlist batch lookup failed:\n%s", traceback.format_exc())


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
                spotify_url = page.get("properties", {}).get("Spotify URL", {}).get("url")
                candidates.append({
                    "id": page["id"],
                    "name": page_name,
                    "spotify_url": spotify_url,
                })
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
    """Return (notion_page_id, status): 'pre_existing', 'added', or 'skipped'.
    Always checks Notion first. Registry tracks pre-flight URL matches (auto-accepted).
    Spotify URL matches don't require user confirmation (100% definitive).
    """
    spotify_id = playlist["id"]
    spotify_url = f"https://open.spotify.com/playlist/{spotify_id}"

    # Fast path: if pre-flight batch found this playlist by Spotify URL, auto-accept (no dialog)
    if spotify_id in registry:
        log.info("Playlist %r auto-matched by Spotify URL (definitive)", playlist["name"])
        return registry[spotify_id]["notion_page_id"], "pre_existing"

    notion_id = None

    # Step 1: match by Spotify URL (definitive, always check Notion first)
    time.sleep(0.35)
    result = _find_playlist_by_spotify_url(spotify_url, db_id)
    if result:
        match_id, match_name = result
        log.info("Playlist %r found in Notion by Spotify URL", playlist["name"])
        notion_id = match_id
    else:
        # Combined name-based search: exact + similar (all at once)
        time.sleep(0.35)
        exact = []
        result = _find_playlist_by_name(playlist["name"], db_id)
        if result:
            match_id, match_name = result
            # Check if it has a different Spotify URL (if so, exclude it)
            notion_page = _notion_request("GET", f"pages/{match_id}")
            notion_spotify_url = notion_page.get("properties", {}).get("Spotify URL", {}).get("url")
            if not notion_spotify_url or _normalize_spotify_url(notion_spotify_url) == _normalize_spotify_url(spotify_url):
                exact = [{"id": match_id, "name": match_name, "spotify_url": notion_spotify_url}]

        time.sleep(0.35)
        similar = _search_similar_playlists(playlist["name"], db_id)
        candidates = _merge_candidates(exact, similar, spotify_url)

        if match_cb:
            display_with_url = playlist["name"]
            if any(c.get("spotify_url") for c in candidates):
                display_with_url += f"  […{spotify_url[-4:]}]"
            choice = match_cb("playlist", display_with_url, candidates)
            if choice == SKIP:
                return None, "skipped"
            if choice:
                notion_id = choice
                try:
                    _backfill_playlist_spotify_url(choice, playlist)
                except Exception:
                    log.warning("Could not backfill playlist %r:\n%s",
                               playlist["name"], traceback.format_exc())
        elif candidates:
            # Programmatic mode: auto-accept first match
            notion_id = candidates[0]["id"]

    if notion_id:
        log.debug("Registering playlist %r as pre_existing", playlist["name"])
        registry[spotify_id] = _make_registry_entry(
            notion_id, playlist["name"], "found_existing", db_id=db_id)
        return notion_id, "pre_existing"

    time.sleep(0.35)
    notion_id = _create_playlist_in_notion(playlist, db_id)
    registry[spotify_id] = _make_registry_entry(
        notion_id, playlist["name"], "added", db_id=db_id)
    log.info("Created Notion playlist: %r", playlist["name"])
    return notion_id, "added"


def export_playlist(playlist: dict, db_id: str, match_cb=None) -> dict:
    """
    Ensure the playlist exists in the given Notion DB.
    Returns {"status": str, "page_id": str|None, "name": str}
    """
    registry = {}  # Write-only registry for tracking created playlists

    # Pre-flight batch lookup: check Notion for playlist by Spotify ID
    log.info("Pre-flight: checking playlist %r in Notion", playlist["name"])
    _batch_lookup_playlists([playlist["id"]], registry, db_id)

    page_id, status = _ensure_playlist(playlist, registry, match_cb=match_cb, db_id=db_id)
    log.info("Playlist export: %r → %s", playlist["name"], status)
    return {"status": status, "page_id": page_id, "name": playlist["name"]}
