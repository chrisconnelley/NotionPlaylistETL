import time
import traceback
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

from config import NOTION_SONGS_DB_ID
from logger import log
from notion._api import _notion_post, _notion_request
from notion._helpers import SKIP, _page_title, _song_artist_names, _song_title_variants, _apostrophe_variants, _chunks
from notion._artists import _ensure_artist, _batch_lookup_artists

# Module-level cache: normalized_url -> {notion_page_id, title, spotify_url}
_NOTION_SONGS_CACHE = {}
_SONGS_CACHE_LOADED = False
_SONGS_CACHE_LOCK = threading.Lock()


def _normalize_spotify_url(url: str) -> str:
    """Normalize Spotify URL for comparison: strip query params, fragments, trailing slash."""
    if not url:
        return ""
    parsed = urlparse(url)
    # Reconstruct URL with just scheme, netloc, and path (no query or fragment)
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/").lower()
    return normalized


def _load_all_songs_cache(force: bool = False) -> None:
    """Load all songs from Notion into memory cache. Safe to call multiple times."""
    global _NOTION_SONGS_CACHE, _SONGS_CACHE_LOADED

    with _SONGS_CACHE_LOCK:
        if _SONGS_CACHE_LOADED and not force:
            return

        log.info("Loading all songs from Notion into cache...")
        try:
            all_pages = []
            result = _notion_post(f"databases/{NOTION_SONGS_DB_ID}/query", {
                "page_size": 100,
            })
            all_pages.extend(result.get("results", []))

            # Handle pagination
            while result.get("has_more"):
                time.sleep(0.35)
                result = _notion_post(f"databases/{NOTION_SONGS_DB_ID}/query", {
                    "page_size": 100,
                    "start_cursor": result.get("next_cursor"),
                })
                all_pages.extend(result.get("results", []))

            # Build cache
            _NOTION_SONGS_CACHE.clear()
            for page in all_pages:
                spotify_url = page.get("properties", {}).get("Spotify URL", {}).get("url")
                if spotify_url:
                    norm_url = _normalize_spotify_url(spotify_url)
                    title = _page_title(page)
                    _NOTION_SONGS_CACHE[norm_url] = {
                        "notion_page_id": page["id"],
                        "title": title,
                        "spotify_url": spotify_url,
                    }

            _SONGS_CACHE_LOADED = True
            log.info("Loaded %d songs into cache", len(_NOTION_SONGS_CACHE))
        except Exception:
            log.warning("Failed to load songs cache:\n%s", traceback.format_exc())
            _SONGS_CACHE_LOADED = False


def _update_songs_cache(page_id: str, title: str, spotify_url: str) -> None:
    """Add or update a song in the cache after creation."""
    if not spotify_url:
        return
    with _SONGS_CACHE_LOCK:
        norm_url = _normalize_spotify_url(spotify_url)
        _NOTION_SONGS_CACHE[norm_url] = {
            "notion_page_id": page_id,
            "title": title,
            "spotify_url": spotify_url,
        }
        log.debug("Updated songs cache with %r", title)


def _batch_lookup_songs(urls: list, registry: dict) -> None:
    """Batch-check cache for songs by Spotify URL. Updates registry in-place for found items."""
    if not urls:
        return

    log.info("Pre-flight: checking %d unregistered song URL(s) in Notion", len(urls))

    # Ensure cache is loaded
    _load_all_songs_cache()

    # Normalize all input URLs
    normalized_urls = {_normalize_spotify_url(url): url for url in urls}

    # Log first few URLs being searched for detailed debugging
    for url in urls[:5]:
        log.info("Pre-flight: searching for song URL: %s", url)

    try:
        found_count = 0
        found_urls = set()
        now = datetime.now(timezone.utc).isoformat()

        # Look up URLs in cached data
        with _SONGS_CACHE_LOCK:
            for norm_url, original_url in normalized_urls.items():
                if norm_url in _NOTION_SONGS_CACHE:
                    cached_entry = _NOTION_SONGS_CACHE[norm_url]
                    registry[original_url] = {
                        "notion_page_id": cached_entry["notion_page_id"],
                        "name": cached_entry["title"],
                        "status": "pre_existing",
                        "first_seen": now,
                        "last_synced": now,
                        "history": [{"action": "found_existing", "timestamp": now}],
                    }
                    found_count += 1
                    found_urls.add(original_url)
                    log.info("Pre-flight: matched song %r by Spotify URL", cached_entry["title"])

        # Log which songs were NOT found
        not_found = [url for url in urls if url not in found_urls]
        if not_found:
            log.info("Pre-flight: %d song(s) NOT found in Notion (will use name-based matching):", len(not_found))
            for url in not_found:  # Log all missing URLs for debugging
                log.info("  - NOT found: %s", url)

        log.info("Pre-flight: batch song lookup complete — found %d/%d", found_count, len(urls))
    except Exception:
        log.warning("Pre-flight song batch lookup failed:\n%s", traceback.format_exc())


def _find_song_in_notion(spotify_url: str) -> "tuple[str, str] | None":
    result = _notion_post(f"databases/{NOTION_SONGS_DB_ID}/query", {
        "filter": {"property": "Spotify URL", "url": {"equals": spotify_url}},
        "page_size": 1,
    })
    pages = result.get("results", [])
    if not pages:
        return None
    page = pages[0]
    title = _page_title(page)
    artists = _song_artist_names(page)
    display = f"{title}  —  {artists}" if artists else title
    return page["id"], display


def _find_song_by_name_in_notion(name: str) -> list:
    """Find songs by exact name match. Returns list of candidate dicts with id, name, spotify_url."""
    variants = _song_title_variants(name)
    result = _notion_post(f"databases/{NOTION_SONGS_DB_ID}/query", {
        "filter": {"or": [{"property": "Name", "title": {"equals": v}} for v in variants]},
        "page_size": 10,
    })
    candidates = []
    seen_ids = set()
    for page in result.get("results", []):
        if page["id"] in seen_ids:
            continue
        title = _page_title(page)
        artists = _song_artist_names(page)
        display = f"{title}  —  {artists}" if artists else title
        spotify_url = page.get("properties", {}).get("Spotify URL", {}).get("url")
        candidates.append({
            "id": page["id"],
            "name": display,
            "spotify_url": spotify_url,
        })
        seen_ids.add(page["id"])
    return candidates


def _search_similar_songs_in_notion(name: str) -> list:
    """Search for similar songs using a single OR query across all search terms."""
    seen_ids: set = set()
    candidates: list = []
    search_terms = _song_title_variants(name)
    first_word = name.split()[0]
    for v in _apostrophe_variants(first_word):
        if v not in search_terms:
            search_terms.append(v)

    log.debug("Searching for similar songs to %r with terms: %s", name, search_terms[:3])

    # Single OR query combining all contains conditions
    result = _notion_post(f"databases/{NOTION_SONGS_DB_ID}/query", {
        "filter": {"or": [{"property": "Name", "title": {"contains": term}} for term in search_terms]},
        "page_size": 10,
    })

    all_results = result.get("results", [])
    log.debug("Similarity search found %d total result(s) for %r", len(all_results), name)

    for page in all_results:
        if page["id"] in seen_ids:
            continue
        page_name = _page_title(page)
        if page_name:
            artist_names = _song_artist_names(page)
            display = f"{page_name}  —  {artist_names}" if artist_names else page_name
            spotify_url = page.get("properties", {}).get("Spotify URL", {}).get("url")
            candidates.append({
                "id": page["id"],
                "name": display,
                "spotify_url": spotify_url,
            })
            seen_ids.add(page["id"])
            log.debug("  Candidate: %s", display)

    log.info("Similarity search candidates for %r: %d result(s)", name, len(candidates))
    return candidates


def _backfill_song_spotify_url(notion_page_id: str, track: dict) -> None:
    properties: dict = {}
    if track.get("Spotify URL"):
        properties["Spotify URL"] = {"url": track["Spotify URL"]}
    if track.get("Album"):
        properties["Album"] = {"rich_text": [{"text": {"content": track["Album"]}}]}
    year = track.get("Year", "")
    if year:
        try:
            properties["Release Year"] = {"number": int(year)}
        except (ValueError, TypeError):
            pass
    if not properties:
        return
    _notion_request("PATCH", f"pages/{notion_page_id}", json={"properties": properties})
    log.info("Backfilled Spotify metadata on existing song: %r", track.get("Track Name"))


def _create_song_in_notion(track: dict, artist_page_ids: list) -> str:
    properties = {
        "Name": {"title": [{"text": {"content": track["Track Name"]}}]},
        "Spotify URL": {"url": track["Spotify URL"]},
        "Album": {"rich_text": [{"text": {"content": track.get("Album", "")}}]},
    }
    year = track.get("Year", "")
    if year:
        try:
            properties["Release Year"] = {"number": int(year)}
        except (ValueError, TypeError):
            pass
    if artist_page_ids:
        properties["Song Artists"] = {"relation": [{"id": pid} for pid in artist_page_ids]}
    result = _notion_post("pages", {
        "parent": {"database_id": NOTION_SONGS_DB_ID},
        "properties": properties,
    })
    page_id = result["id"]

    # Update cache with newly created song
    _update_songs_cache(page_id, track["Track Name"], track["Spotify URL"])

    return page_id


def _ensure_song(track: dict, artist_page_ids: list, registry: dict,
                 match_cb=None) -> str:
    """Return status: 'pre_existing', 'added', or 'skipped'.
    Always checks Notion first. Registry tracks pre-flight URL matches (auto-accepted).
    Spotify URL matches don't require user confirmation (100% definitive)."""
    spotify_url = track.get("Spotify URL", "")
    if not spotify_url:
        raise ValueError(f"No Spotify URL for track: {track.get('Track Name')}")
    now = datetime.now(timezone.utc).isoformat()

    artist_str = ", ".join(a["name"] for a in track.get("Artists", []))
    _display = (f"{track['Track Name']}  —  {artist_str}"
                if artist_str else track["Track Name"])

    # Fast path: if pre-flight batch found this song by Spotify URL, auto-accept (no dialog)
    if spotify_url in registry:
        log.info("Song %r auto-matched by Spotify URL (definitive)", track["Track Name"])
        return "pre_existing"

    # Also check with normalized URL (in case of format variations)
    normalized_url = _normalize_spotify_url(spotify_url)
    for reg_url, reg_data in registry.items():
        if _normalize_spotify_url(reg_url) == normalized_url:
            log.info("Song %r auto-matched by normalized Spotify URL (definitive)", track["Track Name"])
            return "pre_existing"

    log.warning("Song %r NOT found in pre-flight batch. Spotify URL: %s", track["Track Name"], spotify_url)
    log.debug("  Registry keys: %s", list(registry.keys())[:3])

    notion_id = None

    # Combined search: exact + similar matches (all at once)
    time.sleep(0.35)
    exact = _find_song_by_name_in_notion(track["Track Name"])
    time.sleep(0.35)
    similar = _search_similar_songs_in_notion(track["Track Name"])

    # Merge: exact matches first, deduplicated
    # IMPORTANT: Filter out candidates with a different Spotify URL (impossible matches)
    seen_ids = set()
    candidates = []
    for c in exact + similar:
        if c["id"] in seen_ids:
            continue
        # Reject if candidate has a Spotify URL that differs from the track's URL
        if c.get("spotify_url") and c["spotify_url"] != spotify_url:
            norm_candidate = _normalize_spotify_url(c["spotify_url"])
            norm_input = _normalize_spotify_url(spotify_url)
            if norm_candidate != norm_input:
                log.debug("Rejecting candidate %r: different Spotify URL (%s vs %s)",
                         c["name"], c["spotify_url"][-20:], spotify_url[-20:])
                continue
        candidates.append(c)
        seen_ids.add(c["id"])

    if candidates:
        log.info("Found %d candidate match(es) for %r (exact: %d, similar: %d)",
                 len(candidates), track["Track Name"], len(exact), len(similar))
    else:
        log.debug("No exact or similar matches found for %r", track["Track Name"])

    # Show all candidates in a single dialog (if match_cb available)
    user_clicked_create_new = False
    if candidates and match_cb:
        # Append last-4 of Spotify URL to display for disambiguation
        spotify_suffix = f"  […{spotify_url[-4:]}]" if spotify_url else ""
        display_with_url = _display + spotify_suffix
        choice = match_cb("song", display_with_url, candidates)
        if choice == SKIP:
            return "skipped"
        if choice is None:
            # User clicked "Create New"
            user_clicked_create_new = True
        else:
            notion_id = choice
    elif candidates and not match_cb:
        # Programmatic mode: auto-accept first match
        notion_id = candidates[0]["id"]

    # Backfill if a match was selected
    if notion_id:
        try:
            _backfill_song_spotify_url(notion_id, track)
        except Exception:
            log.warning("Could not backfill song %r:\n%s",
                        track["Track Name"], traceback.format_exc())

    if notion_id:
        log.info("Matched Notion song: %r", track["Track Name"])
        registry[spotify_url] = {
            "notion_page_id": notion_id,
            "name": track["Track Name"],
            "status": "pre_existing",
            "first_seen": now, "last_synced": now,
            "history": [{"action": "found_existing", "timestamp": now}],
        }
        return "pre_existing"

    # No match found, or user clicked "Create New" — create the record
    if not match_cb or user_clicked_create_new:
        time.sleep(0.35)
        notion_id = _create_song_in_notion(track, artist_page_ids)
        registry[spotify_url] = {
            "notion_page_id": notion_id,
            "name": track["Track Name"],
            "status": "added",
            "first_seen": now, "last_synced": now,
            "history": [{"action": "added", "timestamp": now}],
        }
        log.info("Created Notion song: %r", track["Track Name"])
        return "added"

    # match_cb is present but user skipped all matches and didn't choose to create
    log.warning("Song %r: No matches found and user did not confirm creation", track["Track Name"])
    return "skipped"


def export_tracks(tracks: list, sp, progress_cb=None, stop_event=None,
                  match_cb=None) -> dict:
    """
    Export tracks to Notion Songs and Song Artists databases.
    Returns summary dict with counts and name lists.
    """
    from notion._api import _notion_get

    missing = [t for t in tracks if not t.get("Artists")]
    if missing:
        raise ValueError(
            f"{len(missing)} track(s) are missing artist ID data. "
            "Please click Refresh in the playlist tab, then try again."
        )

    all_artist_ids = list({a["id"] for t in tracks for a in t.get("Artists", [])})
    artist_details = {}
    for i in range(0, len(all_artist_ids), 50):
        batch = all_artist_ids[i:i + 50]
        try:
            results = sp.artists(batch)
            for a in (results.get("artists") or []):
                if a:
                    artist_details[a["id"]] = {
                        "name": a["name"], "id": a["id"],
                        "spotify_url": a.get("external_urls", {}).get("spotify"),
                        "genres": a.get("genres", []),
                        "popularity": a.get("popularity"),
                        "followers": (a.get("followers") or {}).get("total"),
                        "image_url": a["images"][0]["url"] if a.get("images") else None,
                    }
        except Exception:
            log.warning("Could not fetch artist details batch:\n%s", traceback.format_exc())

    # Registries are now write-only (for tracking what was created)
    artists_reg = {}
    songs_reg   = {}

    # Pre-flight batch lookup: check Notion for songs/artists by Spotify ID/URL
    # This batches the lookups to avoid 50+ individual API calls
    unregistered_urls = [t["Spotify URL"] for t in tracks if t.get("Spotify URL")]
    unregistered_artist_ids = list({a["id"] for t in tracks for a in t.get("Artists", [])})

    # Log track names with their Spotify URLs for debugging
    log.debug("Tracks with Spotify URLs:")
    for track in tracks:
        if track.get("Spotify URL"):
            log.debug("  %r → %s", track.get("Track Name"), track["Spotify URL"])

    if unregistered_urls:
        _batch_lookup_songs(unregistered_urls, songs_reg)
    if unregistered_artist_ids:
        _batch_lookup_artists(unregistered_artist_ids, artists_reg)

    summary = {
        "added_songs": 0, "existing_songs": 0, "skipped_songs": 0,
        "added_artists": 0, "pre_existing_artists": 0, "skipped_artists": 0,
        "errors": [],
        "added_song_names": [], "existing_song_names": [],
        "added_artist_names": [], "existing_artist_names": [],
    }

    for i, track in enumerate(tracks):
        if stop_event and stop_event.is_set():
            log.info("Notion export cancelled after %d tracks", i)
            break
        if progress_cb:
            progress_cb(i, len(tracks), track.get("Track Name", ""))

        try:
            artist_page_ids = []
            artist_statuses = []
            for artist_stub in track.get("Artists", []):
                info = artist_details.get(artist_stub["id"],
                                          {"name": artist_stub["name"], "id": artist_stub["id"]})
                page_id, status = _ensure_artist(info, artists_reg, match_cb=match_cb)
                if page_id:
                    artist_page_ids.append(page_id)
                artist_statuses.append(status)
                if status == "added":
                    summary["added_artists"] += 1
                    summary["added_artist_names"].append(info["name"])
                elif status == "pre_existing":
                    summary["pre_existing_artists"] += 1
                    summary["existing_artist_names"].append(info["name"])
                elif status == "skipped":
                    summary["skipped_artists"] += 1

            song_status = _ensure_song(track, artist_page_ids, songs_reg, match_cb=match_cb)
            if song_status == "added":
                summary["added_songs"] += 1
                summary["added_song_names"].append(track["Track Name"])
            elif song_status == "skipped":
                summary["skipped_songs"] += 1
            else:
                summary["existing_songs"] += 1
                summary["existing_song_names"].append(track["Track Name"])

        except Exception:
            err_msg = traceback.format_exc()
            log.error("Error exporting %r:\n%s", track.get("Track Name"), err_msg)
            summary["errors"].append({
                "track": track.get("Track Name"),
                "error": err_msg.splitlines()[-1],
            })

    if progress_cb:
        progress_cb(len(tracks), len(tracks), "")
    log.info("Notion export complete: %s",
             {k: v for k, v in summary.items() if not isinstance(v, list)})
    return summary
