import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from logger import log

# Sentinel returned by match_cb when the user chooses to skip an item entirely.
SKIP = "__skip__"


def _artist_spotify_url(artist_id: str) -> str:
    """Construct canonical Spotify URL for an artist from their ID."""
    return f"https://open.spotify.com/artist/{artist_id}"


def _normalize_spotify_url(url: str) -> str:
    """Normalize Spotify URL: strip query params, fragments, trailing slash, lowercase."""
    if not url:
        return ""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/").lower()


def _make_registry_entry(notion_page_id: str, name: str, action: str,
                         **extra) -> dict:
    """Build a standard registry entry dict. action is 'found_existing' or 'added'."""
    now = datetime.now(timezone.utc).isoformat()
    status = "pre_existing" if action == "found_existing" else action
    entry = {
        "notion_page_id": notion_page_id,
        "name": name,
        "status": status,
        "first_seen": now,
        "last_synced": now,
        "history": [{"action": action, "timestamp": now}],
    }
    entry.update(extra)
    return entry


def _merge_candidates(exact: list, similar: list,
                      spotify_url: str = "") -> list:
    """Merge exact + similar candidate lists, dedup by ID, reject mismatched Spotify URLs."""
    norm_input = _normalize_spotify_url(spotify_url) if spotify_url else ""
    seen_ids = set()
    candidates = []
    for c in exact + similar:
        if c["id"] in seen_ids:
            continue
        if c.get("spotify_url") and norm_input:
            if _normalize_spotify_url(c["spotify_url"]) != norm_input:
                log.debug("Rejecting candidate %r: different Spotify URL", c["name"])
                continue
        candidates.append(c)
        seen_ids.add(c["id"])
    return candidates


def _chunks(lst: list, n: int):
    """Yield successive chunks of size n from a list."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


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
    """Extract artist names from a Songs page's Song Artists relation property.
    Resolves from the in-memory artist cache first, falls back to API only if needed."""
    relation = page.get("properties", {}).get("Song Artists", {}).get("relation", [])
    if not relation:
        return ""

    # Try to resolve from the artists cache (avoids N API calls)
    from notion._artists import _NOTION_ARTISTS_CACHE, _ARTISTS_CACHE_LOCK
    names = []
    unresolved = []
    with _ARTISTS_CACHE_LOCK:
        cache_by_page_id = {v["notion_page_id"]: v["name"]
                            for v in _NOTION_ARTISTS_CACHE.values()}
    for rel in relation[:5]:
        cached_name = cache_by_page_id.get(rel["id"])
        if cached_name:
            names.append(cached_name)
        else:
            unresolved.append(rel["id"])

    # Fallback: fetch unresolved artist pages individually via API
    if unresolved:
        from notion._api import _notion_get
        for page_id in unresolved:
            try:
                artist_page = _notion_get(f"pages/{page_id}")
                artist_name = _page_title(artist_page)
                if artist_name:
                    names.append(artist_name)
            except Exception:
                log.debug("Could not fetch artist page %s", page_id)

    return ", ".join(names)
