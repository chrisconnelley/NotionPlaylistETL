import time
import traceback
from datetime import datetime, timezone

from config import NOTION_PLAYLISTS_DB_ID, NOTION_PLAYLIST_SONGS_DB_ID
from logger import log
from notion._api import _notion_post, _notion_request, _notion_get
from notion._artists import _ensure_artist, _batch_lookup_artists
from notion._songs import _ensure_song, _batch_lookup_songs

# Playlist songs database configuration
_PLAYLIST_SONGS_CONFIG = {
    "db_id":             NOTION_PLAYLIST_SONGS_DB_ID,
    "song_relation":     "Song",
    "playlist_relation": "Playlist",
}


def _lyrics_blocks(lyrics: str) -> list:
    """
    Two-column Notion block: Left = Lyrics, Right = Notes.
    Splits long lyrics at 1900 chars (Notion 2000-char limit per rich_text element).
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
            "column_list": {"children": [
                {"type": "column", "column": {"children": [
                    {"type": "heading_1", "heading_1": {
                        "rich_text": [{"type": "text", "text": {"content": "Lyrics"}}],
                    }},
                    *lyric_paras,
                ]}},
                {"type": "column", "column": {"children": [
                    {"type": "heading_1", "heading_1": {
                        "rich_text": [{"type": "text", "text": {"content": "Notes"}}],
                    }},
                    {"type": "paragraph", "paragraph": {"rich_text": []}},
                ]}},
            ]},
        },
        {"type": "paragraph", "paragraph": {"rich_text": []}},
        {"type": "paragraph", "paragraph": {"rich_text": []}},
    ]


def _find_playlist_song(song_page_id: str, playlist_page_id: str,
                        pl_songs_db_id: str, song_prop: str, playlist_prop: str,
                        track_name: str = "") -> "str | None":
    """Return Notion page ID of an existing playlist song, or None.
    Falls back to name+playlist search when song relation is empty.
    """
    result = _notion_post(f"databases/{pl_songs_db_id}/query", {
        "filter": {"and": [
            {"property": song_prop, "relation": {"contains": song_page_id}},
            {"property": playlist_prop, "relation": {"contains": playlist_page_id}},
        ]},
        "page_size": 1,
    })
    pages = result.get("results", [])
    if pages:
        return pages[0]["id"]

    if track_name:
        result = _notion_post(f"databases/{pl_songs_db_id}/query", {
            "filter": {"and": [
                {"property": "Name", "title": {"equals": track_name}},
                {"property": playlist_prop, "relation": {"contains": playlist_page_id}},
            ]},
            "page_size": 1,
        })
        pages = result.get("results", [])
        if pages:
            return pages[0]["id"]

    return None


def _create_playlist_song(track: dict, song_page_id: str, playlist_page_id: str,
                          artist_page_ids: list, track_num: int, db_config: dict) -> str:
    properties: dict = {
        "Name": {"title": [{"text": {"content": track["Track Name"]}}]},
        db_config["song_relation"]:     {"relation": [{"id": song_page_id}]},
        db_config["playlist_relation"]: {"relation": [{"id": playlist_page_id}]},
        "Playlist Order": {"number": track_num},
    }
    if artist_page_ids:
        properties["👩🏼\u200d🎤 Song Artists"] = {
            "relation": [{"id": pid} for pid in artist_page_ids]
        }
    result = _notion_post("pages", {
        "parent": {"database_id": db_config["db_id"]},
        "properties": properties,
        "children": _lyrics_blocks(track.get("Lyrics") or ""),
    })
    return result["id"]


def _repair_playlist_song(page_id: str, song_page_id: str,
                          playlist_page_id: str, artist_page_ids: list,
                          db_config: dict, track_name: str = "") -> "bool | None":
    """PATCH missing relations on an existing playlist song page.
    Returns True=repaired, False=already correct, None=wrong DB or archived (skip).
    """
    page = _notion_get(f"pages/{page_id}")

    parent_db = page.get("parent", {}).get("database_id", "").replace("-", "")
    expected_db = db_config["db_id"].replace("-", "")
    if parent_db != expected_db:
        log.warning("Playlist song %s belongs to DB %s, expected %s — skipping repair",
                    page_id[:12], parent_db[:8], expected_db[:8])
        return None

    # Skip patching if the page is archived (can't edit archived pages)
    if page.get("archived"):
        log.debug("Playlist song %s is archived — skipping repair", page_id[:12])
        return None

    props = page.get("properties", {})
    patch: dict = {}

    song_rel = props.get(db_config["song_relation"], {}).get("relation", [])
    if not any(r["id"] == song_page_id for r in song_rel):
        patch[db_config["song_relation"]] = {"relation": [{"id": song_page_id}]}

    pl_rel = props.get(db_config["playlist_relation"], {}).get("relation", [])
    if not any(r["id"] == playlist_page_id for r in pl_rel):
        patch[db_config["playlist_relation"]] = {"relation": [{"id": playlist_page_id}]}

    artist_rel = props.get("👩🏼\u200d🎤 Song Artists", {}).get("relation", [])
    existing_artist_ids = {r["id"] for r in artist_rel}
    if artist_page_ids and not all(aid in existing_artist_ids for aid in artist_page_ids):
        patch["👩🏼\u200d🎤 Song Artists"] = {
            "relation": [{"id": pid} for pid in artist_page_ids]
        }

    if not patch:
        return False

    try:
        _notion_request("PATCH", f"pages/{page_id}", json={"properties": patch})
        # Build more informative log message
        repaired_fields = []
        if "Song" in patch or db_config["song_relation"] in patch:
            repaired_fields.append("Song link")
        if "Playlist" in patch or db_config["playlist_relation"] in patch:
            repaired_fields.append("Playlist link")
        if "👩🏼\u200d🎤 Song Artists" in patch:
            repaired_fields.append("Artist links")

        song_info = f" '{track_name}'" if track_name else ""
        log.info("Repaired playlist song%s: set %s", song_info, ", ".join(repaired_fields))
        return True
    except Exception as e:
        # If patching fails due to archived blocks, skip gracefully
        if "archived" in str(e).lower():
            log.debug("Playlist song %s has archived blocks — skipping repair", page_id[:12])
            return None
        # Re-raise other errors
        raise


def _ensure_playlist_song(track: dict, song_page_id: str, playlist_page_id: str,
                          artist_page_ids: list, track_num: int,
                          playlist_spotify_id: str, registry: dict,
                          db_config: dict) -> str:
    """Ensure a playlist song record exists with correct relations. Idempotent.
    Always checks Notion first. Registry is write-only (no caching).
    """
    spotify_url = track.get("Spotify URL", "")
    reg_key = f"{playlist_spotify_id}:{spotify_url}"
    now = datetime.now(timezone.utc).isoformat()

    existing_id = None

    # Step 1: search Notion (always check Notion first, don't use registry cache)
    time.sleep(0.35)
    existing_id = _find_playlist_song(
        song_page_id, playlist_page_id,
        db_config["db_id"], db_config["song_relation"],
        db_config["playlist_relation"], track_name=track["Track Name"],
    )

    if existing_id:
        time.sleep(0.35)
        repair_result = _repair_playlist_song(
            existing_id, song_page_id, playlist_page_id, artist_page_ids, db_config,
            track_name=track["Track Name"])
        registry[reg_key] = {
            "notion_page_id": existing_id,
            "name": track["Track Name"],
            "status": "pre_existing",
            "first_seen": now, "last_synced": now,
            "history": [{"action": "found_existing", "timestamp": now}],
        }
        return "repaired" if repair_result else "pre_existing"

    time.sleep(0.35)
    page_id = _create_playlist_song(
        track, song_page_id, playlist_page_id, artist_page_ids, track_num, db_config)
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
                          sp=None, match_cb=None,
                          progress_cb=None, stop_event=None) -> dict:
    """
    Create playlist song records. Exports missing songs/artists on the fly.
    Tracks are only skipped if they have no Spotify URL.
    Returns a summary dict.
    """
    from notion._playlists import _find_playlist_by_spotify_url

    db_config = _PLAYLIST_SONGS_CONFIG

    # Write-only registries for tracking created items
    songs_reg     = {}
    artists_reg   = {}
    pl_songs_reg  = {}

    # Pre-flight batch lookup: check Notion for songs/artists by Spotify ID/URL
    unregistered_urls = [t["Spotify URL"] for t in tracks if t.get("Spotify URL")]
    unregistered_artist_ids = list({a["id"] for t in tracks for a in t.get("Artists", [])})
    if unregistered_urls:
        _batch_lookup_songs(unregistered_urls, songs_reg)
    if unregistered_artist_ids:
        _batch_lookup_artists(unregistered_artist_ids, artists_reg)

    # Find the playlist in Notion by Spotify ID (instead of relying on registry)
    spotify_url = f"https://open.spotify.com/playlist/{playlist_spotify_id}"
    playlist_lookup = _find_playlist_by_spotify_url(spotify_url, playlists_db_id)
    if not playlist_lookup:
        log.error("Playlist with Spotify ID %r not found in Notion database %r",
                  playlist_spotify_id, playlists_db_id)
        raise ValueError(
            f"Playlist {playlist_spotify_id!r} not found in Notion. "
            "Export the playlist to Notion first (Phase 2)."
        )
    playlist_page_id, _ = playlist_lookup

    # Pre-fetch Spotify artist details for any not yet in registry
    missing_artist_ids = list({
        a["id"] for t in tracks for a in t.get("Artists", [])
        if a["id"] not in artists_reg
    })
    artist_details: dict = {}
    if missing_artist_ids and sp:
        for i in range(0, len(missing_artist_ids), 50):
            batch = missing_artist_ids[i:i + 50]
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

    summary = {
        "added": 0, "pre_existing": 0, "repaired": 0, "skipped": 0,
        "errors": [],
        "added_names": [], "repaired_names": [], "skipped_names": [],
    }

    for i, track in enumerate(tracks):
        if stop_event and stop_event.is_set():
            log.info("Playlist songs export cancelled after %d tracks", i)
            break
        if progress_cb:
            progress_cb(i, len(tracks), track.get("Track Name", ""))

        spotify_url = track.get("Spotify URL", "")
        if not spotify_url:
            log.warning("Skipping playlist song — no Spotify URL: %r", track.get("Track Name"))
            summary["skipped"] += 1
            summary["skipped_names"].append(track["Track Name"])
            continue

        try:
            artist_page_ids = []
            for artist_stub in track.get("Artists", []):
                if artist_stub["id"] in artists_reg:
                    artist_page_ids.append(artists_reg[artist_stub["id"]]["notion_page_id"])
                else:
                    info = artist_details.get(
                        artist_stub["id"],
                        {"name": artist_stub["name"], "id": artist_stub["id"]},
                    )
                    page_id, _ = _ensure_artist(info, artists_reg, match_cb=match_cb)
                    if page_id:
                        artist_page_ids.append(page_id)

            if spotify_url not in songs_reg:
                song_status = _ensure_song(track, artist_page_ids, songs_reg, match_cb=match_cb)
                if song_status == "skipped":
                    summary["skipped"] += 1
                    summary["skipped_names"].append(track["Track Name"])
                    continue

            song_page_id = songs_reg[spotify_url]["notion_page_id"]
            status = _ensure_playlist_song(
                track, song_page_id, playlist_page_id,
                artist_page_ids, i + 1,
                playlist_spotify_id, pl_songs_reg, db_config,
            )
            summary[status] += 1
            if status == "added":
                summary["added_names"].append(track["Track Name"])
            elif status == "repaired":
                summary["repaired_names"].append(track["Track Name"])
        except Exception:
            err = traceback.format_exc()
            log.error("Error creating playlist song %r:\n%s", track.get("Track Name"), err)
            summary["errors"].append({
                "track": track.get("Track Name"),
                "error": err.splitlines()[-1],
            })

    if progress_cb:
        progress_cb(len(tracks), len(tracks), "")
    log.info("Playlist songs export complete: added=%d repaired=%d pre_existing=%d skipped=%d errors=%d",
             summary["added"], summary["repaired"], summary["pre_existing"],
             summary["skipped"], len(summary["errors"]))
    return summary
