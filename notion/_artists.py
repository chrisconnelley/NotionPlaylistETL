import time
import traceback
from datetime import datetime, timezone
from urllib.parse import urlparse

from config import NOTION_ARTISTS_DB_ID
from logger import log
from notion._api import _notion_post, _notion_request
from notion._helpers import SKIP, _apostrophe_variants, _page_title, _chunks


def _normalize_spotify_url(url: str) -> str:
    """Normalize Spotify URL for comparison: strip query params, fragments, trailing slash."""
    if not url:
        return ""
    parsed = urlparse(url)
    # Reconstruct URL with just scheme, netloc, and path (no query or fragment)
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/").lower()
    return normalized


def _batch_lookup_artists(artist_ids: list, registry: dict) -> None:
    """Batch-check Notion for artists by Spotify Artist ID. Updates registry in-place for found items."""
    if not artist_ids:
        return

    log.info("Pre-flight: checking %d unregistered artist ID(s) in Notion", len(artist_ids))
    try:
        found_count = 0
        for chunk in _chunks(artist_ids, 50):
            time.sleep(0.35)
            result = _notion_post(f"databases/{NOTION_ARTISTS_DB_ID}/query", {
                "filter": {"or": [{"property": "Spotify Artist ID", "rich_text": {"equals": aid}} for aid in chunk]},
                "page_size": len(chunk),
            })
            now = datetime.now(timezone.utc).isoformat()
            for page in result.get("results", []):
                artist_id_prop = page.get("properties", {}).get("Spotify Artist ID", {})
                aid = "".join(t.get("plain_text", "") for t in artist_id_prop.get("rich_text", []))
                if aid and aid not in registry:
                    name = _page_title(page)
                    registry[aid] = {
                        "notion_page_id": page["id"],
                        "name": name,
                        "status": "pre_existing",
                        "first_seen": now,
                        "last_synced": now,
                        "history": [{"action": "found_existing", "timestamp": now}],
                    }
                    found_count += 1
                    log.info("Pre-flight: matched artist %r by Spotify Artist ID", name)
        log.info("Pre-flight: batch artist lookup complete — found %d/%d", found_count, len(artist_ids))
    except Exception:
        log.warning("Pre-flight artist batch lookup failed:\n%s", traceback.format_exc())


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


def _find_artist_by_name_in_notion(name: str) -> list:
    """Find artists by exact name match. Returns list of candidate dicts with id, name, spotify_url."""
    result = _notion_post(f"databases/{NOTION_ARTISTS_DB_ID}/query", {
        "filter": {"property": "Name", "title": {"equals": name}},
        "page_size": 10,
    })
    candidates = []
    seen_ids = set()
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
    return candidates


def _search_similar_artists_in_notion(name: str) -> list:
    """Search for similar artists using a single OR query across all search terms."""
    seen_ids: set = set()
    candidates: list = []
    base_terms = [name, name.split()[0]]
    search_terms = []
    for t in base_terms:
        for v in _apostrophe_variants(t):
            if v not in search_terms:
                search_terms.append(v)

    # Single OR query combining all contains conditions
    result = _notion_post(f"databases/{NOTION_ARTISTS_DB_ID}/query", {
        "filter": {"or": [{"property": "Name", "title": {"contains": term}} for term in search_terms]},
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
    return candidates


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
    Always checks Notion first. Registry tracks pre-flight ID matches (auto-accepted).
    Spotify Artist ID matches don't require user confirmation (100% definitive).
    """
    spotify_id = artist_info["id"]
    now = datetime.now(timezone.utc).isoformat()

    # Fast path: if pre-flight batch found this artist by Spotify ID, auto-accept (no dialog)
    if spotify_id in registry:
        log.info("Artist %r auto-matched by Spotify Artist ID (definitive)", artist_info["name"])
        return registry[spotify_id]["notion_page_id"], "pre_existing"

    notion_id = None

    # Combined search: exact + similar matches (all at once)
    time.sleep(0.35)
    exact = _find_artist_by_name_in_notion(artist_info["name"])
    time.sleep(0.35)
    similar = _search_similar_artists_in_notion(artist_info["name"])

    # Merge: exact matches first, deduplicated
    # IMPORTANT: Filter out candidates with a different Spotify URL (impossible matches)
    seen_ids = set()
    candidates = []
    for c in exact + similar:
        if c["id"] in seen_ids:
            continue
        # Reject if candidate has a Spotify URL that differs from the artist's URL
        if c.get("spotify_url") and artist_info.get("spotify_url"):
            if c["spotify_url"] != artist_info["spotify_url"]:
                norm_candidate = _normalize_spotify_url(c["spotify_url"])
                norm_input = _normalize_spotify_url(artist_info["spotify_url"])
                if norm_candidate != norm_input:
                    log.debug("Rejecting candidate %r: different Spotify URL (%s vs %s)",
                             c["name"], c["spotify_url"][-20:], artist_info["spotify_url"][-20:])
                    continue
        candidates.append(c)
        seen_ids.add(c["id"])

    if candidates:
        log.info("Found %d candidate match(es) for artist %r (exact: %d, similar: %d)",
                 len(candidates), artist_info["name"], len(exact), len(similar))
    else:
        log.debug("No exact or similar matches found for artist %r", artist_info["name"])

    # Show all candidates in a single dialog (if match_cb available)
    if match_cb:
        # Append last-4 of Spotify URL to display for disambiguation
        spotify_suffix = f"  […{artist_info['spotify_url'][-4:]}]" if artist_info.get("spotify_url") else ""
        display_with_url = artist_info["name"] + spotify_suffix
        choice = match_cb("artist", display_with_url, candidates)
        if choice == SKIP:
            return None, "skipped"
        if choice is not None:
            # User selected a match
            notion_id = choice
    elif candidates:
        # Programmatic mode: auto-accept first match
        notion_id = candidates[0]["id"]

    # Backfill if a match was selected
    if notion_id:
        try:
            _backfill_artist_spotify_id(notion_id, artist_info)
        except Exception:
            log.warning("Could not backfill artist %r:\n%s",
                        artist_info["name"], traceback.format_exc())

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

    # No match found or user clicked "Create New" — always create for artists
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
