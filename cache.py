import json
import os
import traceback

from config import CACHE_PATH, TRACKS_CACHE_DIR
from logger import log


def load_playlist_cache() -> list[dict] | None:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        log.info("Loaded %d playlists from cache (%s)", len(data), CACHE_PATH)
        return data
    except FileNotFoundError:
        log.debug("No playlist cache found at %s", CACHE_PATH)
    except Exception:
        log.warning("Could not read playlist cache:\n%s", traceback.format_exc())
    return None


def save_playlist_cache(playlists: list[dict]) -> None:
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(playlists, f, ensure_ascii=False, indent=2)
        log.debug("Saved %d playlists to cache", len(playlists))
    except Exception:
        log.warning("Could not save playlist cache:\n%s", traceback.format_exc())


def load_tracks_cache(playlist_id: str) -> list[dict] | None:
    path = os.path.join(TRACKS_CACHE_DIR, f"{playlist_id}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        log.debug("Loaded %d tracks from cache for playlist %s", len(data), playlist_id)
        return data
    except FileNotFoundError:
        return None
    except Exception:
        log.warning("Could not read tracks cache for %s:\n%s",
                    playlist_id, traceback.format_exc())
        return None


def save_tracks_cache(playlist_id: str, tracks: list[dict]) -> None:
    path = os.path.join(TRACKS_CACHE_DIR, f"{playlist_id}.json")
    try:
        data = [{k: v for k, v in t.items() if k != "Lyrics"} for t in tracks]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.debug("Saved %d tracks to cache for playlist %s", len(tracks), playlist_id)
    except Exception:
        log.warning("Could not save tracks cache:\n%s", traceback.format_exc())
