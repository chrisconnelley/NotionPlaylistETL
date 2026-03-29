import time
import traceback
from datetime import datetime, timezone

from config import NOTION_ARTISTS_DB_ID
from logger import log
from notion._api import _notion_post, _notion_request
from notion._helpers import SKIP, _apostrophe_variants, _page_title


def _find_artist_in_notion(spotify_artist_id: str) -> "tuple[str, str] | None":
    result = _notion_post(f"databases/{NOTION_ARTISTS_DB_ID}/query", {
        "filter": {
            "property": "Spotify Artist ID",
            "rich_text": {"equals": spotify_artist_id},
        },
        "page_size": 1,
    })
    pages = result.get("results", [])
    if not pages:
        return None
    return pages[0]["id"], _page_title(pages[0])


def _find_artist_by_name_in_notion(name: str) -> "tuple[str, str] | None":
    result = _notion_post(f"databases/{NOTION_ARTISTS_DB_ID}/query", {
        "filter": {"property": "Name", "title": {"equals": name}},
        "page_size": 1,
    })
    pages = result.get("results", [])
    if not pages:
        return None
    return pages[0]["id"], _page_title(pages[0])


def _search_similar_artists_in_notion(name: str) -> list:
    seen_ids: set = set()
    candidates: list = []
    base_terms = [name, name.split()[0]]
    search_terms = []
    for t in base_terms:
        for v in _apostrophe_variants(t):
            if v not in search_terms:
                search_terms.append(v)

    for search_term in search_terms:
        result = _notion_post(f"databases/{NOTION_ARTISTS_DB_ID}/query", {
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


def _backfill_artist_spotify_id(notion_page_id: str, artist_info: dict) -> None:
    properties: dict = {
        "Spotify Artist ID": {"rich_text": [{"text": {"content": artist_info["id"]}}]},
    }
    if artist_info.get("spotify_url"):
        properties["Spotify URL"] = {"url": artist_info["spotify_url"]}
    if artist_info.get("genres"):
        properties["Genres"] = {"multi_select": [{"name": g} for g in artist_info["genres"][:10]]}
    if artist_info.get("popularity") is not None:
        properties["Popularity"] = {"number": artist_info["popularity"]}
    if artist_info.get("followers") is not None:
        properties["Followers"] = {"number": artist_info["followers"]}
    if artist_info.get("image_url"):
        properties["Image URL"] = {"url": artist_info["image_url"]}
    _notion_request("PATCH", f"pages/{notion_page_id}", json={"properties": properties})
    log.info("Backfilled Spotify metadata on existing artist: %r", artist_info["name"])


def _create_artist_in_notion(artist_info: dict) -> str:
    properties = {
        "Name": {"title": [{"text": {"content": artist_info["name"]}}]},
        "Spotify Artist ID": {"rich_text": [{"text": {"content": artist_info["id"]}}]},
    }
    if artist_info.get("spotify_url"):
        properties["Spotify URL"] = {"url": artist_info["spotify_url"]}
    if artist_info.get("genres"):
        properties["Genres"] = {"multi_select": [{"name": g} for g in artist_info["genres"][:10]]}
    if artist_info.get("popularity") is not None:
        properties["Popularity"] = {"number": artist_info["popularity"]}
    if artist_info.get("followers") is not None:
        properties["Followers"] = {"number": artist_info["followers"]}
    if artist_info.get("image_url"):
        properties["Image URL"] = {"url": artist_info["image_url"]}
    result = _notion_post("pages", {
        "parent": {"database_id": NOTION_ARTISTS_DB_ID},
        "properties": properties,
    })
    return result["id"]


def _ensure_artist(artist_info: dict, registry: dict,
                   match_cb=None) -> "tuple[str, str]":
    """
    Return (notion_page_id, status) where status is 'pre_existing', 'added', or 'skipped'.
    match_cb(kind, item_name, candidates) -> notion_page_id | None
    """
    spotify_id = artist_info["id"]
    now = datetime.now(timezone.utc).isoformat()

    if spotify_id in registry:
        return registry[spotify_id]["notion_page_id"], "pre_existing"

    notion_id = None

    # Step 1: match by Spotify Artist ID
    time.sleep(0.35)
    result = _find_artist_in_notion(spotify_id)
    if result:
        match_id, match_name = result
        if match_cb:
            choice = match_cb("artist", artist_info["name"],
                              [{"id": match_id, "name": match_name}])
            if choice == SKIP:
                return None, "skipped"
            notion_id = choice
        else:
            notion_id = match_id

    # Step 2: exact name match
    if not notion_id:
        time.sleep(0.35)
        result = _find_artist_by_name_in_notion(artist_info["name"])
        if result:
            match_id, match_name = result
            if match_cb:
                choice = match_cb("artist", artist_info["name"],
                                  [{"id": match_id, "name": match_name}])
                if choice == SKIP:
                    return None, "skipped"
                notion_id = choice
            else:
                notion_id = match_id
            if notion_id == match_id:
                try:
                    _backfill_artist_spotify_id(match_id, artist_info)
                except Exception:
                    log.warning("Could not backfill artist %r:\n%s",
                                artist_info["name"], traceback.format_exc())

    # Step 3: interactive similar-name search
    if not notion_id and match_cb:
        time.sleep(0.35)
        candidates = _search_similar_artists_in_notion(artist_info["name"])
        choice = match_cb("artist", artist_info["name"], candidates)
        if choice == SKIP:
            return None, "skipped"
        if choice:
            try:
                _backfill_artist_spotify_id(choice, artist_info)
            except Exception:
                log.warning("Could not backfill artist %r:\n%s",
                            artist_info["name"], traceback.format_exc())
            notion_id = choice

    if notion_id:
        log.info("Matched Notion artist: %r", artist_info["name"])
        registry[spotify_id] = {
            "notion_page_id": notion_id,
            "name": artist_info["name"],
            "status": "pre_existing",
            "first_seen": now, "last_synced": now,
            "history": [{"action": "found_existing", "timestamp": now}],
        }
        return notion_id, "pre_existing"

    time.sleep(0.35)
    notion_id = _create_artist_in_notion(artist_info)
    registry[spotify_id] = {
        "notion_page_id": notion_id,
        "name": artist_info["name"],
        "status": "added",
        "first_seen": now, "last_synced": now,
        "history": [{"action": "added", "timestamp": now,
                     "fields": [k for k in artist_info if artist_info[k] is not None]}],
    }
    log.info("Created Notion artist: %r", artist_info["name"])
    return notion_id, "added"
