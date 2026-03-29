import json
import os
import re
import traceback

import requests as http
from notion_client import Client

from config import (NOTION_API_KEY, BASE_DIR, NOTION_SYNC_DIR,
                    NOTION_SONGS_DB_ID, NOTION_ARTISTS_DB_ID,
                    NOTION_JESSIE_PLAYLISTS_DB_ID, NOTION_TERI_PLAYLISTS_DB_ID,
                    NOTION_JESSIE_PL_SONGS_DB_ID, NOTION_TERI_PL_SONGS_DB_ID)
from logger import log

SCHEMA_DIR = os.path.join(BASE_DIR, "notion_schema")
_NOTION_VERSION = "2022-06-28"

# Sentinel returned by match_cb when the user chooses to skip an item entirely.
SKIP = "__skip__"


def _apostrophe_variants(text: str) -> list:
    """Return the text plus a version with straight/curly apostrophes swapped."""
    straight  = "\u0027"   # '
    curly_r   = "\u2019"   # '  (right single quotation mark)
    curly_l   = "\u2018"   # '  (left single quotation mark)
    seen = [text]
    for old, new in [(straight, curly_r), (curly_r, straight), (curly_l, straight)]:
        variant = text.replace(old, new)
        if variant not in seen:
            seen.append(variant)
    return seen


def _song_title_variants(name: str) -> list:
    """
    Return search-term variants for a song title:
    - The full name
    - The base title (everything before common subtitle separators like ' - ', ' (', ' [')
    Each variant is also expanded with apostrophe swaps.
    """
    base = re.split(r"\s[-\u2013\u2014(]\s*|\s[\(\[]", name)[0].strip()
    terms = [name]
    if base and base != name:
        terms.append(base)
    result = []
    for t in terms:
        for v in _apostrophe_variants(t):
            if v not in result:
                result.append(v)
    return result


def get_notion_client() -> Client:
    return Client(auth=NOTION_API_KEY)


def fetch_databases() -> list[dict]:
    """Return all Notion databases the integration has access to."""
    if not NOTION_API_KEY:
        raise ValueError("NOTION_API_KEY is not set in .env")

    client = get_notion_client()
    databases = []
    cursor = None

    while True:
        kwargs = {}
        if cursor:
            kwargs["start_cursor"] = cursor

        response = client.search(**kwargs)

        for item in response.get("results", []):
            # The Notion API now returns databases as "data_source"
            if item.get("object") not in ("database", "data_source"):
                continue
            title_parts = item.get("title", [])
            name = "".join(t.get("plain_text", "") for t in title_parts).strip()
            databases.append({
                "id": item["id"],
                "name": name or "<Untitled>",
            })

        if response.get("has_more"):
            cursor = response.get("next_cursor")
        else:
            break

    log.info("Found %d Notion database(s)", len(databases))
    return databases


def _notion_request(method: str, path: str, **kwargs) -> dict:
    """
    Raw HTTP request against the Notion API with rate-limit retry and error logging.
    Retries once after 60 s on HTTP 429, then raises.
    """
    import time as _t
    url = f"https://api.notion.com/v1/{path}"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": _NOTION_VERSION,
    }
    if method == "POST" or method == "PATCH":
        headers["Content-Type"] = "application/json"

    for attempt in (1, 2):
        resp = getattr(http, method.lower())(url, headers=headers, timeout=15, **kwargs)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            log.warning("Notion rate limit hit on %s %s — waiting %ds (attempt %d/2)",
                        method, path, retry_after, attempt)
            _t.sleep(retry_after)
            continue
        if not resp.ok:
            try:
                body = resp.json()
                log.error("Notion API error %d on %s %s: %s — %s",
                          resp.status_code, method, path,
                          body.get("code", ""), body.get("message", resp.text[:200]))
            except Exception:
                log.error("Notion API error %d on %s %s: %s",
                          resp.status_code, method, path, resp.text[:200])
            resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}  # unreachable


def _notion_post(path: str, body: dict) -> dict:
    return _notion_request("POST", path, json=body)


def load_registry(name: str) -> dict:
    """Load a sync registry JSON file. Returns {} if missing."""
    path = os.path.join(NOTION_SYNC_DIR, f"{name}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        log.warning("Could not load registry %r:\n%s", name, traceback.format_exc())
        return {}


def save_registry(name: str, data: dict) -> None:
    """Save a sync registry to JSON."""
    os.makedirs(NOTION_SYNC_DIR, exist_ok=True)
    path = os.path.join(NOTION_SYNC_DIR, f"{name}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        log.warning("Could not save registry %r:\n%s", name, traceback.format_exc())


def _page_title(page: dict) -> str:
    parts = page.get("properties", {}).get("Name", {}).get("title", [])
    return "".join(t.get("plain_text", "") for t in parts).strip()


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


def _search_similar_artists_in_notion(name: str) -> list:
    """Return up to 10 Notion artist records whose name contains any word from `name`."""
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


def _find_artist_by_name_in_notion(name: str) -> "tuple[str, str] | None":
    result = _notion_post(f"databases/{NOTION_ARTISTS_DB_ID}/query", {
        "filter": {
            "property": "Name",
            "title": {"equals": name},
        },
        "page_size": 1,
    })
    pages = result.get("results", [])
    if not pages:
        return None
    return pages[0]["id"], _page_title(pages[0])


def _backfill_artist_spotify_id(notion_page_id: str, artist_info: dict) -> None:
    """Patch an existing Notion artist record with Spotify metadata it's missing."""
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
    Return (notion_page_id, status) where status is 'pre_existing' or 'added'.
    match_cb(kind, item_name, candidates) -> notion_page_id | None
        Called for every match (auto or interactive) so the user can confirm.
        Returns the chosen page ID, or None to create a new record.
    """
    import time
    from datetime import datetime, timezone
    spotify_id = artist_info["id"]
    now = datetime.now(timezone.utc).isoformat()

    if spotify_id in registry:
        return registry[spotify_id]["notion_page_id"], "pre_existing"

    notion_id = None

    # Step 1: match by Spotify Artist ID — present as single candidate for confirmation
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
            # Backfill only if this specific match was confirmed
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
            "first_seen": now,
            "last_synced": now,
            "history": [{"action": "found_existing", "timestamp": now}],
        }
        return notion_id, "pre_existing"

    time.sleep(0.35)
    notion_id = _create_artist_in_notion(artist_info)
    registry[spotify_id] = {
        "notion_page_id": notion_id,
        "name": artist_info["name"],
        "status": "added",
        "first_seen": now,
        "last_synced": now,
        "history": [{"action": "added", "timestamp": now,
                     "fields": [k for k in artist_info if artist_info[k] is not None]}],
    }
    log.info("Created Notion artist: %r", artist_info["name"])
    return notion_id, "added"


def _find_song_in_notion(spotify_url: str) -> "tuple[str, str] | None":
    result = _notion_post(f"databases/{NOTION_SONGS_DB_ID}/query", {
        "filter": {
            "property": "Spotify URL",
            "url": {"equals": spotify_url},
        },
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
    for variant in _song_title_variants(name):
        result = _notion_post(f"databases/{NOTION_SONGS_DB_ID}/query", {
            "filter": {
                "property": "Name",
                "title": {"equals": variant},
            },
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


def _song_artist_names(page: dict) -> str:
    """Extract artist names from a Songs page's Song Artists relation property."""
    relation = page.get("properties", {}).get("Song Artists", {}).get("relation", [])
    if not relation:
        return ""
    names = []
    for rel in relation[:5]:  # cap at 5 to avoid too many fetches
        try:
            artist_page = _notion_get(f"pages/{rel['id']}")
            artist_name = _page_title(artist_page)
            if artist_name:
                names.append(artist_name)
        except Exception:
            log.debug("Could not fetch artist page %s for song artist names: %s",
                      rel.get("id"), traceback.format_exc().splitlines()[-1])
    return ", ".join(names)


def _search_similar_songs_in_notion(name: str) -> list:
    """Return up to 10 Notion song records whose name contains words from `name`."""
    seen_ids: set = set()
    candidates: list = []
    # Full name + base title (before separators), each with apostrophe variants,
    # then fall back to just the first word
    search_terms = _song_title_variants(name)
    first_word = name.split()[0]
    for v in _apostrophe_variants(first_word):
        if v not in search_terms:
            search_terms.append(v)

    for search_term in search_terms:
        result = _notion_post(f"databases/{NOTION_SONGS_DB_ID}/query", {
            "filter": {"property": "Name", "title": {"contains": search_term}},
            "page_size": 10,
        })
        for page in result.get("results", []):
            if page["id"] in seen_ids:
                continue
            page_name = _page_title(page)
            if page_name:
                artist_names = _song_artist_names(page)
                display = f"{page_name}  —  {artist_names}" if artist_names else page_name
                candidates.append({"id": page["id"], "name": display})
                seen_ids.add(page["id"])
        if candidates:
            return candidates
    return []


def _backfill_song_spotify_url(notion_page_id: str, track: dict) -> None:
    """Patch an existing Notion song record with Spotify metadata it's missing."""
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
        properties["Song Artists"] = {
            "relation": [{"id": pid} for pid in artist_page_ids]
        }
    result = _notion_post("pages", {
        "parent": {"database_id": NOTION_SONGS_DB_ID},
        "properties": properties,
    })
    return result["id"]


def _ensure_song(track: dict, artist_page_ids: list, registry: dict,
                 match_cb=None) -> str:
    """
    Return status: 'pre_existing' or 'added'.
    match_cb(kind, name, candidates) -> notion_page_id | None
        Called for every match so the user can confirm.
    """
    import time
    from datetime import datetime, timezone
    spotify_url = track.get("Spotify URL", "")
    if not spotify_url:
        raise ValueError(f"No Spotify URL for track: {track.get('Track Name')}")
    now = datetime.now(timezone.utc).isoformat()

    # Display label shown in the match dialog: "Title — Artist 1, Artist 2"
    artist_str = ", ".join(a["name"] for a in track.get("Artists", []))
    _display = (f"{track['Track Name']}  —  {artist_str}"
                if artist_str else track["Track Name"])

    if spotify_url in registry:
        return "pre_existing"

    notion_id = None

    # Step 1: match by Spotify URL — present as single candidate for confirmation
    time.sleep(0.35)
    result = _find_song_in_notion(spotify_url)
    if result:
        match_id, match_name = result
        if match_cb:
            choice = match_cb("song", _display,
                              [{"id": match_id, "name": match_name}])
            if choice == SKIP:
                return "skipped"
            notion_id = choice
        else:
            notion_id = match_id

    # Step 2: exact name match
    if not notion_id:
        time.sleep(0.35)
        result = _find_song_by_name_in_notion(track["Track Name"])
        if result:
            match_id, match_name = result
            if match_cb:
                choice = match_cb("song", _display,
                                  [{"id": match_id, "name": match_name}])
                if choice == SKIP:
                    return "skipped"
                notion_id = choice
            else:
                notion_id = match_id
            # Backfill only if this specific match was confirmed
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
            "first_seen": now,
            "last_synced": now,
            "history": [{"action": "found_existing", "timestamp": now}],
        }
        return "pre_existing"

    time.sleep(0.35)
    notion_id = _create_song_in_notion(track, artist_page_ids)
    registry[spotify_url] = {
        "notion_page_id": notion_id,
        "name": track["Track Name"],
        "status": "added",
        "first_seen": now,
        "last_synced": now,
        "history": [{"action": "added", "timestamp": now}],
    }
    log.info("Created Notion song: %r", track["Track Name"])
    return "added"


def export_tracks(tracks: list, sp, progress_cb=None, status_cb=None,
                  artist_status_cb=None, stop_event=None, match_cb=None,
                  verify_batch=False) -> dict:
    """
    Export tracks to Notion Songs and Song Artists databases.
    - progress_cb(current, total, track_name): called per track
    - status_cb(spotify_url, display_status): song Notion status for UI column
    - artist_status_cb(spotify_url, display_status): artist Notion status for UI column
    - match_cb(kind, name, candidates) -> page_id | None: confirm/interactive match
      kind is 'artist' or 'song'
    - stop_event: threading.Event checked between tracks for cancellation
    Returns summary dict with counts and name lists.
    """
    missing = [t for t in tracks if not t.get("Artists")]
    if missing:
        raise ValueError(
            f"{len(missing)} track(s) are missing artist ID data. "
            "Please click Refresh in the playlist tab, then try again."
        )

    # Batch-fetch full artist details from Spotify
    all_artist_ids = list({
        a["id"] for t in tracks for a in t.get("Artists", [])
    })
    artist_details = {}
    for i in range(0, len(all_artist_ids), 50):
        batch = all_artist_ids[i:i + 50]
        try:
            results = sp.artists(batch)
            for a in (results.get("artists") or []):
                if a:
                    artist_details[a["id"]] = {
                        "name": a["name"],
                        "id": a["id"],
                        "spotify_url": a.get("external_urls", {}).get("spotify"),
                        "genres": a.get("genres", []),
                        "popularity": a.get("popularity"),
                        "followers": (a.get("followers") or {}).get("total"),
                        "image_url": a["images"][0]["url"] if a.get("images") else None,
                    }
        except Exception:
            log.warning("Could not fetch artist details batch:\n%s", traceback.format_exc())

    artists_reg = load_registry("artists")
    songs_reg   = load_registry("songs")

    if verify_batch:
        import time as _time
        batch_urls = {t.get("Spotify URL") for t in tracks if t.get("Spotify URL")}
        batch_artist_ids = {a["id"] for t in tracks for a in t.get("Artists", [])}
        for url in batch_urls:
            if url not in songs_reg:
                continue
            _time.sleep(0.35)
            try:
                r = _notion_get(f"pages/{songs_reg[url]['notion_page_id']}")
                if r.get("archived") or r.get("in_trash"):
                    raise ValueError("archived")
            except Exception:
                log.info("Stale song cache removed: %r", songs_reg[url].get("name"))
                del songs_reg[url]
        for aid in batch_artist_ids:
            if aid not in artists_reg:
                continue
            _time.sleep(0.35)
            try:
                r = _notion_get(f"pages/{artists_reg[aid]['notion_page_id']}")
                if r.get("archived") or r.get("in_trash"):
                    raise ValueError("archived")
            except Exception:
                log.info("Stale artist cache removed: %r", artists_reg[aid].get("name"))
                del artists_reg[aid]

    summary = {
        "added_songs": 0, "existing_songs": 0, "skipped_songs": 0,
        "added_artists": 0, "pre_existing_artists": 0, "skipped_artists": 0,
        "errors": [],
        "added_song_names": [],
        "existing_song_names": [],
        "added_artist_names": [],
        "existing_artist_names": [],
    }

    _STATUS_DISPLAY = {
        "pre_existing": "In Notion",
        "added": "Added",
        "skipped": "Skipped",
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

            song_status = _ensure_song(track, artist_page_ids, songs_reg,
                                       match_cb=match_cb)
            if song_status == "added":
                summary["added_songs"] += 1
                summary["added_song_names"].append(track["Track Name"])
            elif song_status == "skipped":
                summary["skipped_songs"] += 1
            else:
                summary["existing_songs"] += 1
                summary["existing_song_names"].append(track["Track Name"])

            if status_cb:
                status_cb(track.get("Spotify URL", ""),
                          _STATUS_DISPLAY.get(song_status, "—"))

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
                "error": str(traceback.format_exc().splitlines()[-1]),
            })

    save_registry("artists", artists_reg)
    save_registry("songs", songs_reg)

    if progress_cb:
        progress_cb(len(tracks), len(tracks), "")

    log.info("Notion export complete: %s",
             {k: v for k, v in summary.items() if not isinstance(v, list)})
    return summary


def validate_registry(name: str, progress_cb=None,
                      keys: "set | None" = None) -> "tuple[int, int]":
    """
    Check page IDs stored in a registry against Notion.
    Removes entries whose pages have been deleted or archived.
    progress_cb(current, total, entry_name): called before each Notion request.
    keys: if provided, only validate entries whose registry key is in this set.
    Returns (removed_count, remaining_count).
    """
    import time
    registry = load_registry(name)
    if not registry:
        log.info("validate_registry(%r): empty, nothing to check", name)
        return 0, 0

    entries = [(k, v) for k, v in registry.items() if keys is None or k in keys]
    total = len(entries)
    log.info("validate_registry(%r): checking %d/%d entries",
             name, total, len(registry))

    to_remove = []
    for i, (key, entry) in enumerate(entries):
        entry_name = entry.get("name", key)
        if progress_cb:
            progress_cb(i, total, entry_name)
        log.debug("Checking %s entry %d/%d: %r", name, i + 1, total, entry_name)

        page_id = entry.get("notion_page_id")
        if not page_id:
            log.info("  %r has no page ID — removing", entry_name)
            to_remove.append(key)
            continue

        time.sleep(0.35)
        try:
            result = _notion_get(f"pages/{page_id}")
            if result.get("archived") or result.get("in_trash"):
                log.info("  %r is archived/deleted in Notion — removing", entry_name)
                to_remove.append(key)
            else:
                log.debug("  %r OK", entry_name)
        except Exception:
            log.info("  %r not found in Notion (404) — removing", entry_name)
            to_remove.append(key)

    for key in to_remove:
        del registry[key]

    if to_remove:
        save_registry(name, registry)

    log.info("validate_registry(%r): removed %d, kept %d", name, len(to_remove), len(registry))
    return len(to_remove), len(registry)


def _notion_get(path: str) -> dict:
    return _notion_request("GET", path)


# ──────────────────────────────────────────────────────────────────────
# Playlist export
# ──────────────────────────────────────────────────────────────────────

def _find_playlist_by_spotify_url(spotify_url: str, db_id: str) -> "tuple[str, str] | None":
    result = _notion_post(f"databases/{db_id}/query", {
        "filter": {
            "property": "Spotify URL",
            "url": {"equals": spotify_url},
        },
        "page_size": 1,
    })
    pages = result.get("results", [])
    if not pages:
        return None
    return pages[0]["id"], _page_title(pages[0])


def _find_playlist_by_name(name: str, db_id: str) -> "tuple[str, str] | None":
    for variant in _apostrophe_variants(name):
        result = _notion_post(f"databases/{db_id}/query", {
            "filter": {
                "property": "Name",
                "title": {"equals": variant},
            },
            "page_size": 1,
        })
        pages = result.get("results", [])
        if pages:
            return pages[0]["id"], _page_title(pages[0])
    return None


def _search_similar_playlists(name: str, db_id: str) -> list:
    """Return up to 10 Notion playlist records whose name contains words from `name`."""
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
    """
    Return (notion_page_id, status) where status is 'pre_existing', 'added', or 'skipped'.
    match_cb(kind, item_name, candidates) -> page_id | SKIP | None
    """
    import time
    from datetime import datetime, timezone
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
            "first_seen": now,
            "last_synced": now,
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
        "first_seen": now,
        "last_synced": now,
        "history": [{"action": "added", "timestamp": now}],
    }
    log.info("Created Notion playlist: %r", playlist["name"])
    return notion_id, "added"


def export_playlist(playlist: dict, db_id: str, match_cb=None) -> dict:
    """
    Ensure the playlist exists in the given Notion DB.
    Returns {"status": str, "page_id": str|None, "name": str}
    status is 'added', 'pre_existing', or 'skipped'.
    """
    registry = load_registry("playlists")
    page_id, status = _ensure_playlist(playlist, registry, match_cb=match_cb, db_id=db_id)
    save_registry("playlists", registry)
    log.info("Playlist export: %r → %s", playlist["name"], status)
    return {"status": status, "page_id": page_id, "name": playlist["name"]}


# ──────────────────────────────────────────────────────────────────────
# Playlist song export
# ──────────────────────────────────────────────────────────────────────

# Maps the playlist DB (destination chosen by user) to the corresponding
# playlist songs DB and the relation property names used in that DB.
_PLAYLIST_SONGS_DB = {
    NOTION_JESSIE_PLAYLISTS_DB_ID: {
        "db_id":             NOTION_JESSIE_PL_SONGS_DB_ID,
        "song_relation":     "Song",
        "playlist_relation": "Jessie Playlist",
    },
    NOTION_TERI_PLAYLISTS_DB_ID: {
        "db_id":             NOTION_TERI_PL_SONGS_DB_ID,
        "song_relation":     "🎤 Teri Songs",
        "playlist_relation": "▶️ Teri & Chris\u2019s Playlists",
    },
}


def _lyrics_blocks(lyrics: str) -> list:
    """
    Build the two-column Notion block structure used on playlist song pages:
      Left  — heading_1 "Lyrics"  + paragraph block(s) with lyrics text
      Right — heading_1 "Notes"   + one empty paragraph

    Splits long lyrics into ≤1900-char paragraph blocks (Notion enforces a
    2000-char limit per rich_text element), breaking on newlines where possible.
    """
    MAX_CHARS = 1900

    if not lyrics:
        lyric_paras = [{"type": "paragraph", "paragraph": {"rich_text": []}}]
    else:
        chunks: list[str] = []
        current = ""
        for line in lyrics.splitlines(keepends=True):
            if len(current) + len(line) > MAX_CHARS:
                if current:
                    chunks.append(current)
                # Hard-split any single line that exceeds the limit
                while len(line) > MAX_CHARS:
                    chunks.append(line[:MAX_CHARS])
                    line = line[MAX_CHARS:]
                current = line
            else:
                current += line
        if current:
            chunks.append(current)

        lyric_paras = [
            {"type": "paragraph", "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": c}}]
            }}
            for c in chunks
        ]

    return [
        {
            "type": "column_list",
            "column_list": {
                "children": [
                    {
                        "type": "column",
                        "column": {"children": [
                            {"type": "heading_1", "heading_1": {
                                "rich_text": [{"type": "text",
                                               "text": {"content": "Lyrics"}}],
                            }},
                            *lyric_paras,
                        ]},
                    },
                    {
                        "type": "column",
                        "column": {"children": [
                            {"type": "heading_1", "heading_1": {
                                "rich_text": [{"type": "text",
                                               "text": {"content": "Notes"}}],
                            }},
                            {"type": "paragraph",
                             "paragraph": {"rich_text": []}},
                        ]},
                    },
                ],
            },
        },
        {"type": "paragraph", "paragraph": {"rich_text": []}},
        {"type": "paragraph", "paragraph": {"rich_text": []}},
    ]


def _find_playlist_song(song_page_id: str, playlist_page_id: str,
                        pl_songs_db_id: str,
                        song_prop: str, playlist_prop: str) -> "str | None":
    """Return the Notion page ID of an existing playlist song record, or None."""
    result = _notion_post(f"databases/{pl_songs_db_id}/query", {
        "filter": {
            "and": [
                {"property": song_prop,
                 "relation": {"contains": song_page_id}},
                {"property": playlist_prop,
                 "relation": {"contains": playlist_page_id}},
            ]
        },
        "page_size": 1,
    })
    pages = result.get("results", [])
    return pages[0]["id"] if pages else None


def _create_playlist_song(track: dict, song_page_id: str, playlist_page_id: str,
                          artist_page_ids: list, track_num: int,
                          db_config: dict) -> str:
    """Create a playlist song record with properties and lyrics block content."""
    properties: dict = {
        "Name": {"title": [{"text": {"content": track["Track Name"]}}]},
        db_config["song_relation"]:     {"relation": [{"id": song_page_id}]},
        db_config["playlist_relation"]: {"relation": [{"id": playlist_page_id}]},
        "Playlist Order": {"number": track_num},
    }
    if artist_page_ids:
        properties["👩🏼‍🎤 Song Artists"] = {
            "relation": [{"id": pid} for pid in artist_page_ids]
        }

    result = _notion_post("pages", {
        "parent": {"database_id": db_config["db_id"]},
        "properties": properties,
        "children": _lyrics_blocks(track.get("Lyrics") or ""),
    })
    return result["id"]


def _ensure_playlist_song(track: dict, song_page_id: str, playlist_page_id: str,
                          artist_page_ids: list, track_num: int,
                          playlist_spotify_id: str, registry: dict,
                          db_config: dict) -> str:
    """
    Ensure a playlist song record exists.  Returns 'pre_existing', 'added', or 'skipped'.
    Registry key: "{playlist_spotify_id}:{spotify_track_url}"

    Existing records are never modified — lyrics and notes written at creation
    time are left intact so any manual edits the user has made are preserved.
    """
    import time
    from datetime import datetime, timezone

    spotify_url = track.get("Spotify URL", "")
    reg_key = f"{playlist_spotify_id}:{spotify_url}"
    now = datetime.now(timezone.utc).isoformat()

    if reg_key in registry:
        return "pre_existing"

    # Check Notion in case the record already exists outside the registry
    time.sleep(0.35)
    existing_id = _find_playlist_song(
        song_page_id, playlist_page_id,
        db_config["db_id"], db_config["song_relation"], db_config["playlist_relation"],
    )
    if existing_id:
        log.info("Found existing playlist song: %r", track["Track Name"])
        registry[reg_key] = {
            "notion_page_id": existing_id,
            "name": track["Track Name"],
            "status": "pre_existing",
            "first_seen": now, "last_synced": now,
            "history": [{"action": "found_existing", "timestamp": now}],
        }
        return "pre_existing"

    time.sleep(0.35)
    page_id = _create_playlist_song(
        track, song_page_id, playlist_page_id, artist_page_ids, track_num, db_config,
    )
    registry[reg_key] = {
        "notion_page_id": page_id,
        "name": track["Track Name"],
        "status": "added",
        "first_seen": now, "last_synced": now,
        "history": [{"action": "added", "timestamp": now}],
    }
    log.info("Created playlist song: %r (track %d)", track["Track Name"], track_num)
    return "added"


def export_playlist_songs(tracks: list, playlist_spotify_id: str,
                          playlists_db_id: str,
                          progress_cb=None, stop_event=None) -> dict:
    """
    Create playlist song records in the appropriate Playlist Songs DB.

    Requires the playlist and its songs/artists to already be in their
    respective registries.  Tracks whose songs are not in the songs registry
    are skipped with a warning.

    Returns a summary dict.
    """
    db_config = _PLAYLIST_SONGS_DB.get(playlists_db_id)
    if not db_config:
        raise ValueError(f"No playlist songs DB configured for playlist DB {playlists_db_id}")

    playlists_reg    = load_registry("playlists")
    songs_reg        = load_registry("songs")
    artists_reg      = load_registry("artists")
    pl_songs_reg     = load_registry("playlist_songs")

    playlist_entry = playlists_reg.get(playlist_spotify_id)
    if not playlist_entry:
        raise ValueError(
            f"Playlist {playlist_spotify_id!r} not found in playlists registry. "
            "Export the playlist to Notion first."
        )
    playlist_page_id = playlist_entry["notion_page_id"]

    summary = {
        "added": 0, "pre_existing": 0, "skipped": 0, "errors": [],
        "added_names": [], "skipped_names": [],
    }

    for i, track in enumerate(tracks):
        if stop_event and stop_event.is_set():
            log.info("Playlist songs export cancelled after %d tracks", i)
            break

        if progress_cb:
            progress_cb(i, len(tracks), track.get("Track Name", ""))

        spotify_url = track.get("Spotify URL", "")
        song_entry = songs_reg.get(spotify_url)
        if not song_entry:
            log.warning("Skipping playlist song — not in songs registry: %r",
                        track.get("Track Name"))
            summary["skipped"] += 1
            summary["skipped_names"].append(track["Track Name"])
            continue

        song_page_id = song_entry["notion_page_id"]
        artist_page_ids = [
            artists_reg[a["id"]]["notion_page_id"]
            for a in track.get("Artists", [])
            if a["id"] in artists_reg
        ]

        try:
            status = _ensure_playlist_song(
                track, song_page_id, playlist_page_id,
                artist_page_ids, i + 1,
                playlist_spotify_id, pl_songs_reg, db_config,
            )
            summary[status] += 1
            if status == "added":
                summary["added_names"].append(track["Track Name"])
        except Exception:
            err = traceback.format_exc()
            log.error("Error creating playlist song %r:\n%s", track.get("Track Name"), err)
            summary["errors"].append({
                "track": track.get("Track Name"),
                "error": err.splitlines()[-1],
            })

    save_registry("playlist_songs", pl_songs_reg)

    if progress_cb:
        progress_cb(len(tracks), len(tracks), "")

    log.info("Playlist songs export complete: added=%d pre_existing=%d skipped=%d errors=%d",
             summary["added"], summary["pre_existing"], summary["skipped"],
             len(summary["errors"]))
    return summary


def _resolve_database_ids() -> dict:
    """
    Return a mapping of data_source_id → database_id by scanning page parents
    in the full search results. The Notion API returns data_source_id values
    from search but the databases endpoint requires the real database_id.
    """
    if not NOTION_API_KEY:
        raise ValueError("NOTION_API_KEY is not set in .env")

    mapping = {}
    client = get_notion_client()
    cursor = None
    while True:
        kwargs = {}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.search(**kwargs)
        for item in response.get("results", []):
            parent = item.get("parent", {})
            if parent.get("type") == "data_source_id":
                ds_id = parent.get("data_source_id")
                db_id = parent.get("database_id")
                if ds_id and db_id:
                    mapping[ds_id] = db_id
        if response.get("has_more"):
            cursor = response.get("next_cursor")
        else:
            break
    return mapping


def snapshot_schema() -> list:
    """
    Fetch the full schema for every accessible Notion database and save each
    one as a JSON file under notion_schema/.

    Files are named by sanitised database title, e.g. song_artists.json.
    Existing files are overwritten so the snapshot is always current.
    """
    if not NOTION_API_KEY:
        raise ValueError("NOTION_API_KEY is not set in .env")

    os.makedirs(SCHEMA_DIR, exist_ok=True)
    databases = fetch_databases()
    id_map = _resolve_database_ids()

    saved = []
    for db in databases:
        database_id = id_map.get(db["id"], db["id"])
        try:
            detail = _notion_get(f"databases/{database_id}")
            title_parts = detail.get("title", [])
            name = "".join(t.get("plain_text", "") for t in title_parts).strip() or db["name"]

            schema = {
                "name": name,
                "data_source_id": db["id"],
                "database_id": database_id,
                "description": "".join(
                    t.get("plain_text", "")
                    for block in detail.get("description", [])
                    for t in ([block] if isinstance(block, dict) else [])
                ),
                "properties": detail.get("properties", {}),
            }

            safe_name = re.sub(r'[\\/*?:"<>| ]', "_", name).lower()
            path = os.path.join(SCHEMA_DIR, f"{safe_name}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(schema, f, ensure_ascii=False, indent=2)
            saved.append(name)
            log.debug("Saved schema: %s → %s", name, path)

        except Exception:
            log.warning("Could not snapshot schema for %r:\n%s",
                        db["name"], traceback.format_exc())

    log.info("Snapshotted %d database schema(s) to %s", len(saved), SCHEMA_DIR)
    return saved
