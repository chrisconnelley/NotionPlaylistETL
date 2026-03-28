import os
import re
import traceback

import requests

from config import LYRICS_CACHE_DIR
from logger import log


def _cache_path(artist: str, title: str) -> str:
    safe = re.sub(r'[\\/*?:"<>|]', "_", f"{artist}__{title}")
    return os.path.join(LYRICS_CACHE_DIR, safe[:180] + ".txt")


def fetch_lyrics(artist: str, title: str) -> str:
    clean_title = re.sub(r"\s*[\(\[].*?[\)\]]", "", title).strip()
    first_artist = artist.split(",")[0].strip()
    cache_file = _cache_path(first_artist, clean_title)

    if os.path.exists(cache_file):
        with open(cache_file, encoding="utf-8") as f:
            log.debug("Lyrics cache hit: %r by %r", clean_title, first_artist)
            return f.read()

    url = (
        f"https://api.lyrics.ovh/v1/"
        f"{requests.utils.quote(first_artist)}/"
        f"{requests.utils.quote(clean_title)}"
    )
    lyrics = ""
    try:
        resp = requests.get(url, timeout=6)
        if resp.status_code == 200:
            lyrics = resp.json().get("lyrics", "")
            if lyrics:
                log.debug("Lyrics found: %r by %r", clean_title, first_artist)
            else:
                log.debug("No lyrics in response: %r by %r", clean_title, first_artist)
        else:
            log.debug("Lyrics HTTP %d: %r by %r",
                      resp.status_code, clean_title, first_artist)
    except Exception:
        log.debug("Lyrics fetch error for %r by %r:\n%s",
                  clean_title, first_artist, traceback.format_exc())
        return ""

    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(lyrics)
    return lyrics
