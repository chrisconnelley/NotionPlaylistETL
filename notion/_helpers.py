import re
import traceback

from logger import log
from notion._api import _notion_get

# Sentinel returned by match_cb when the user chooses to skip an item entirely.
SKIP = "__skip__"


def _apostrophe_variants(text: str) -> list:
    """Return the text plus versions with straight/curly apostrophes swapped."""
    straight = "\u0027"
    curly_r  = "\u2019"
    curly_l  = "\u2018"
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
    - The base title (before common subtitle separators like ' - ', ' (', ' [')
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


def _page_title(page: dict) -> str:
    parts = page.get("properties", {}).get("Name", {}).get("title", [])
    return "".join(t.get("plain_text", "") for t in parts).strip()


def _song_artist_names(page: dict) -> str:
    """Extract artist names from a Songs page's Song Artists relation property."""
    relation = page.get("properties", {}).get("Song Artists", {}).get("relation", [])
    if not relation:
        return ""
    names = []
    for rel in relation[:5]:
        try:
            artist_page = _notion_get(f"pages/{rel['id']}")
            artist_name = _page_title(artist_page)
            if artist_name:
                names.append(artist_name)
        except Exception:
            log.debug("Could not fetch artist page %s: %s",
                      rel.get("id"), traceback.format_exc().splitlines()[-1])
    return ", ".join(names)
