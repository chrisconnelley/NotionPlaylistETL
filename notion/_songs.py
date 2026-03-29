import time
import traceback
from datetime import datetime, timezone

from config import NOTION_SONGS_DB_ID
from logger import log
from notion._api import _notion_post, _notion_request
from notion._helpers import SKIP, _page_title, _song_artist_names, _song_title_variants, _apostrophe_variants, _chunks
from notion._artists import _ensure_artist, _batch_lookup_artists


def _batch_lookup_songs(urls: list, registry: dict) -> None:
    """Batch-check Notion for songs by Spotify URL. Updates registry in-place for found items."""
    if not urls:
        return

    log.info("Pre-flight: checking %d unregistered song URL(s) in Notion", len(urls))

    # Log first few URLs being searched for detailed debugging
    for url in urls[:5]:
        log.info("Pre-flight: searching for song URL: %s", url)

    try:
        found_count = 0
        found_urls = set()
        for chunk in _chunks(urls, 50):
            time.sleep(0.35)
            log.debug("Pre-flight: querying Notion with %d song URLs", len(chunk))
            result = _notion_post(f"databases/{NOTION_SONGS_DB_ID}/query", {
                "filter": {"or": [{"property": "Spotify URL", "url": {"equals": url}} for url in chunk]},
                "page_size": len(chunk),
            })
            log.debug("Pre-flight: Notion returned %d song result(s)", len(result.get("results", [])))
            now = datetime.now(timezone.utc).isoformat()
            for page in result.get("results", []):
                url = page.get("properties", {}).get("Spotify URL", {}).get("url")
                if url and url not in registry:
                    title = _page_title(page)
                    registry[url] = {
                        "notion_page_id": page["id"],
                        "name": title,
                        "status": "pre_existing",
                        "first_seen": now,
                        "last_synced": now,
                        "history": [{"action": "found_existing", "timestamp": now}],
                    }
                    found_count += 1
                    found_urls.add(url)
                    log.info("Pre-flight: matched song %r by Spotify URL", title)

        # Log which songs were NOT found
        not_found = [url for url in urls if url not in found_urls]
        if not_found:
            log.info("Pre-flight: %d song(s) NOT found in Notion (will use name-based matching):", len(not_found))
            for url in not_found[:5]:  # Log first 5 missing
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


def _find_song_by_name_in_notion(name: str) -> "tuple[str, str] | None":
    """Find song by exact name match using a single OR query across all title variants."""
    variants = _song_title_variants(name)
    result = _notion_post(f"databases/{NOTION_SONGS_DB_ID}/query", {
        "filter": {"or": [{"property": "Name", "title": {"equals": v}} for v in variants]},
        "page_size": 1,
    })
    pages = result.get("results", [])
    if pages:
        page = pages[0]
        title = _page_title(page)
        artists = _song_artist_names(page)
        display = f"{title}  —  {artists}" if artists else title
        return page["id"], display
    return None


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
            candidates.append({"id": page["id"], "name": display})
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
    return result["id"]


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

    log.info("Song %r NOT found in pre-flight batch. Spotify URL: %s", track["Track Name"], spotify_url)

    notion_id = None

    # Step 2: exact name match (Step 1 — URL lookup — is handled by pre-flight batch)
    time.sleep(0.35)
    result = _find_song_by_name_in_notion(track["Track Name"])
    if result:
        match_id, match_name = result
        if match_cb:
            choice = match_cb("song", _display, [{"id": match_id, "name": match_name}])
            if choice == SKIP:
                return "skipped"
            notion_id = choice
        else:
            notion_id = match_id
        if notion_id == match_id:
            try:
                _backfill_song_spotify_url(match_id, track)
            except Exception:
                log.warning("Could not backfill song %r:\n%s",
                            track["Track Name"], traceback.format_exc())

    # Step 3: interactive similar-title search
    if not notion_id and match_cb:
        time.sleep(0.35)
        candidates = _search_similar_songs_in_notion(track["Track Name"])
        choice = match_cb("song", _display, candidates)
        if choice == SKIP:
            return "skipped"
        if choice:
            try:
                _backfill_song_spotify_url(choice, track)
            except Exception:
                log.warning("Could not backfill song %r:\n%s",
                            track["Track Name"], traceback.format_exc())
            notion_id = choice

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


def export_tracks(tracks: list, sp, progress_cb=None, status_cb=None,
                  artist_status_cb=None, stop_event=None, match_cb=None,
                  verify_batch=False) -> dict:
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
    _STATUS_DISPLAY = {"pre_existing": "In Notion", "added": "Added", "skipped": "Skipped"}

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

            if status_cb:
                status_cb(track.get("Spotify URL", ""), _STATUS_DISPLAY.get(song_status, "—"))
            if artist_status_cb:
                if "added" in artist_statuses:
                    artist_disp = "Added"
                elif "pre_existing" in artist_statuses:
                    artist_disp = "In Notion"
                elif all(s == "skipped" for s in artist_statuses):
                    artist_disp = "Skipped"
                else:
                    artist_disp = "—"
                artist_status_cb(track.get("Spotify URL", ""), artist_disp)

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
