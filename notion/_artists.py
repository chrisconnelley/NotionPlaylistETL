import threading
import traceback

from logger import log
from notion._api import _notion_post, _notion_request
from notion._helpers import (
    SKIP, _apostrophe_variants, _page_title,
    _normalize_spotify_url, _make_registry_entry, _merge_candidates,
    _artist_spotify_url,
)

# Module-level cache: normalized_url -> {notion_page_id, name, spotify_url, spotify_artist_id}
_NOTION_ARTISTS_CACHE = {}
# Secondary index: spotify_artist_id -> normalized_url (for legacy rows without URL)
_ARTISTS_ID_INDEX = {}
_ARTISTS_CACHE_LOADED = False
_ARTISTS_CACHE_LOCK = threading.Lock()


def _get_artists_db_id():
    """Get Artists database ID dynamically (always gets current value from config)."""
    from config import NOTION_ARTISTS_DB_ID
    return NOTION_ARTISTS_DB_ID


def _load_all_artists_cache(force: bool = False) -> None:
    """Load all artists from Notion into memory cache. Safe to call multiple times."""
    global _NOTION_ARTISTS_CACHE, _ARTISTS_ID_INDEX, _ARTISTS_CACHE_LOADED

    artists_db_id = _get_artists_db_id()
    if artists_db_id == "missing":
        log.debug("Skipping artists cache load — databases not configured")
        return

    with _ARTISTS_CACHE_LOCK:
        if _ARTISTS_CACHE_LOADED and not force:
            return

        log.info("Loading all artists from Notion into cache...")
        try:
            all_pages = []
            result = _notion_post(f"databases/{artists_db_id}/query", {
                "page_size": 100,
            })
            all_pages.extend(result.get("results", []))

            while result.get("has_more"):
                result = _notion_post(f"databases/{artists_db_id}/query", {
                    "page_size": 100,
                    "start_cursor": result.get("next_cursor"),
                })
                all_pages.extend(result.get("results", []))

            _NOTION_ARTISTS_CACHE.clear()
            _ARTISTS_ID_INDEX.clear()
            for page in all_pages:
                spotify_url = page.get("properties", {}).get("Spotify URL", {}).get("url")
                artist_id_prop = page.get("properties", {}).get("Spotify Artist ID", {})
                artist_id = "".join(t.get("plain_text", "") for t in artist_id_prop.get("rich_text", []))
                name = _page_title(page)
                entry = {
                    "notion_page_id": page["id"],
                    "name": name,
                    "spotify_url": spotify_url,
                    "spotify_artist_id": artist_id,
                }
                if spotify_url:
                    norm_url = _normalize_spotify_url(spotify_url)
                    _NOTION_ARTISTS_CACHE[norm_url] = entry
                    if artist_id:
                        _ARTISTS_ID_INDEX[artist_id] = norm_url
                elif artist_id:
                    # Legacy row: no URL but has ID — index by ID only
                    _ARTISTS_ID_INDEX[artist_id] = None  # no URL key in primary cache
                    # Store in primary cache keyed by constructed URL
                    constructed_url = _normalize_spotify_url(_artist_spotify_url(artist_id))
                    _NOTION_ARTISTS_CACHE[constructed_url] = entry
                    _ARTISTS_ID_INDEX[artist_id] = constructed_url

            _ARTISTS_CACHE_LOADED = True
            log.info("Loaded %d artists into cache", len(_NOTION_ARTISTS_CACHE))
        except Exception:
            log.warning("Failed to load artists cache:\n%s", traceback.format_exc())
            _ARTISTS_CACHE_LOADED = False


def _update_artists_cache(page_id: str, name: str, spotify_url: str, artist_id: str = "") -> None:
    """Add or update an artist in the cache after creation or backfill."""
    with _ARTISTS_CACHE_LOCK:
        entry = {
            "notion_page_id": page_id,
            "name": name,
            "spotify_url": spotify_url,
            "spotify_artist_id": artist_id,
        }
        if spotify_url:
            norm_url = _normalize_spotify_url(spotify_url)
            _NOTION_ARTISTS_CACHE[norm_url] = entry
            if artist_id:
                _ARTISTS_ID_INDEX[artist_id] = norm_url
        log.debug("Updated artists cache with %r", name)


def _batch_lookup_artists(artist_ids: list, registry: dict, artist_details: dict = None) -> None:
    """Batch-check Notion cache for artists by Spotify URL (then ID fallback). Updates registry in-place."""
    if not artist_ids:
        return
    if artist_details is None:
        artist_details = {}

    log.info("Pre-flight: checking %d unregistered artist ID(s) in Notion", len(artist_ids))

    _load_all_artists_cache()

    try:
        found_count = 0
        for aid in artist_ids:
            # Derive Spotify URL for this artist
            url = (artist_details.get(aid, {}).get("spotify_url")
                   or _artist_spotify_url(aid))
            norm_url = _normalize_spotify_url(url)

            cached = None
            matched_by = None

            # Primary: lookup by URL
            if norm_url in _NOTION_ARTISTS_CACHE:
                cached = _NOTION_ARTISTS_CACHE[norm_url]
                matched_by = "Spotify URL"

            # Fallback: lookup by artist ID
            if not cached and aid in _ARTISTS_ID_INDEX:
                idx_url = _ARTISTS_ID_INDEX[aid]
                if idx_url and idx_url in _NOTION_ARTISTS_CACHE:
                    cached = _NOTION_ARTISTS_CACHE[idx_url]
                    matched_by = "Spotify Artist ID"

            if cached and url not in registry:
                needs_backfill = not cached.get("spotify_url")
                registry[url] = _make_registry_entry(
                    cached["notion_page_id"], cached["name"], "found_existing",
                    _needs_url_backfill=needs_backfill)
                found_count += 1
                log.info("Pre-flight: matched artist %r by %s", cached["name"], matched_by)

        log.info("Pre-flight: batch artist lookup complete — found %d/%d", found_count, len(artist_ids))
    except Exception:
        log.warning("Pre-flight artist batch lookup failed:\n%s", traceback.format_exc())


def _find_artist_in_notion(spotify_url: str, spotify_artist_id: str = "") -> "tuple[str, str] | None":
    """Find artist by Spotify URL first, then fall back to Spotify Artist ID."""
    if spotify_url:
        result = _notion_post(f"databases/{_get_artists_db_id()}/query", {
            "filter": {"property": "Spotify URL", "url": {"equals": spotify_url}},
            "page_size": 1,
        })
        pages = result.get("results", [])
        if pages:
            return pages[0]["id"], _page_title(pages[0])

    if spotify_artist_id:
        result = _notion_post(f"databases/{_get_artists_db_id()}/query", {
            "filter": {
                "property": "Spotify Artist ID",
                "rich_text": {"equals": spotify_artist_id},
            },
            "page_size": 1,
        })
        pages = result.get("results", [])
        if pages:
            return pages[0]["id"], _page_title(pages[0])

    return None


def _search_artists_in_notion(name: str) -> list:
    """Search for artists by exact + similar name in a single OR query.
    Returns list of candidate dicts with id, name, spotify_url."""
    base_terms = [name, name.split()[0]]
    contains_terms = []
    for t in base_terms:
        for v in _apostrophe_variants(t):
            if v not in contains_terms:
                contains_terms.append(v)

    # Combine exact (equals) and fuzzy (contains) in one query
    exact_variants = _apostrophe_variants(name)
    filters = [{"property": "Name", "title": {"equals": v}} for v in exact_variants]
    filters += [{"property": "Name", "title": {"contains": term}} for term in contains_terms]

    result = _notion_post(f"databases/{_get_artists_db_id()}/query", {
        "filter": {"or": filters},
        "page_size": 20,
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

    log.info("Artist search candidates for %r: %d result(s)", name, len(candidates))
    return candidates


def _fetch_artist_details(sp, artist_ids: list) -> dict:
    """Fetch full artist details from Spotify in batches of 50. Returns {artist_id: info_dict}."""
    details = {}
    for i in range(0, len(artist_ids), 50):
        batch = artist_ids[i:i + 50]
        try:
            results = sp.artists(batch)
            for a in (results.get("artists") or []):
                if a:
                    details[a["id"]] = {
                        "name": a["name"], "id": a["id"],
                        "spotify_url": a.get("external_urls", {}).get("spotify"),
                        "genres": a.get("genres", []),
                        "popularity": a.get("popularity"),
                        "followers": (a.get("followers") or {}).get("total"),
                        "image_url": a["images"][0]["url"] if a.get("images") else None,
                    }
        except Exception:
            log.warning("Could not fetch artist details batch:\n%s", traceback.format_exc())
    return details


def _backfill_artist_metadata(notion_page_id: str, artist_info: dict) -> None:
    properties: dict = {}
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
        "parent": {"database_id": _get_artists_db_id()},
        "properties": properties,
    })
    return result["id"]


def _ensure_artist(artist_info: dict, registry: dict,
                   match_cb=None, auto_create: bool = False) -> "tuple[str, str]":
    """
    Return (notion_page_id, status) where status is 'pre_existing', 'added', or 'skipped'.
    match_cb(kind, item_name, candidates) -> notion_page_id | None
    Always checks Notion first. Registry keyed by Spotify URL.
    Spotify URL/ID matches don't require user confirmation (100% definitive).
    If auto_create=True, skip name-based matching and create directly when not URL/ID-matched.
    """
    spotify_url = artist_info.get("spotify_url") or _artist_spotify_url(artist_info["id"])
    # Ensure artist_info always has spotify_url for downstream use
    if not artist_info.get("spotify_url"):
        artist_info["spotify_url"] = spotify_url

    # Fast path: if pre-flight batch found this artist by URL, auto-accept (no dialog)
    if spotify_url in registry:
        entry = registry[spotify_url]
        log.info("Artist %r auto-matched by Spotify URL (definitive)", artist_info["name"])
        # Backfill metadata if matched by ID but URL was missing in Notion
        if entry.get("_needs_url_backfill"):
            try:
                _backfill_artist_metadata(entry["notion_page_id"], artist_info)
                _update_artists_cache(entry["notion_page_id"], artist_info["name"],
                                      spotify_url, artist_info["id"])
                entry.pop("_needs_url_backfill", None)
            except Exception:
                log.warning("Could not backfill artist %r:\n%s",
                            artist_info["name"], traceback.format_exc())
        return entry["notion_page_id"], "pre_existing"

    # Auto-create: not found in Notion, skip name matching and create directly
    if auto_create:
        log.info("Artist %r not in Notion by URL — auto-creating", artist_info["name"])
        notion_id = _create_artist_in_notion(artist_info)
        registry[spotify_url] = _make_registry_entry(
            notion_id, artist_info["name"], "added")
        _update_artists_cache(notion_id, artist_info["name"],
                              spotify_url, artist_info["id"])
        return notion_id, "added"

    notion_id = None

    # Combined search: exact + similar matches in one query
    all_matches = _search_artists_in_notion(artist_info["name"])
    candidates = _merge_candidates(all_matches, [], spotify_url)

    if candidates:
        log.info("Found %d candidate(s) for artist %r", len(candidates), artist_info["name"])

    # Show all candidates in a single dialog (if match_cb available)
    if match_cb:
        # Append last-4 of Spotify URL to display for disambiguation
        spotify_suffix = f"  […{spotify_url[-4:]}]" if spotify_url else ""
        display_with_url = artist_info["name"] + spotify_suffix
        choice = match_cb("artist", display_with_url, candidates)
        if choice == SKIP:
            return None, "skipped"
        if choice is not None:
            notion_id = choice
    elif candidates:
        # Programmatic mode: auto-accept first match
        notion_id = candidates[0]["id"]

    # Backfill if a match was selected
    if notion_id:
        try:
            _backfill_artist_metadata(notion_id, artist_info)
            _update_artists_cache(notion_id, artist_info["name"],
                                  spotify_url, artist_info["id"])
        except Exception:
            log.warning("Could not backfill artist %r:\n%s",
                        artist_info["name"], traceback.format_exc())

    if notion_id:
        log.info("Matched Notion artist: %r", artist_info["name"])
        registry[spotify_url] = _make_registry_entry(
            notion_id, artist_info["name"], "found_existing")
        return notion_id, "pre_existing"

    # No match found or user clicked "Create New" — always create for artists
    notion_id = _create_artist_in_notion(artist_info)
    registry[spotify_url] = _make_registry_entry(
        notion_id, artist_info["name"], "added")
    _update_artists_cache(notion_id, artist_info["name"],
                          spotify_url, artist_info["id"])
    log.info("Created Notion artist: %r", artist_info["name"])
    return notion_id, "added"
