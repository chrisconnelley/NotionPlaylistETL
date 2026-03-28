import spotipy
from spotipy.oauth2 import SpotifyOAuth

from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, REDIRECT_URI, SCOPE
from logger import log


def get_spotify_client() -> spotipy.Spotify:
    log.debug("Creating SpotifyOAuth client (redirect=%s)", REDIRECT_URI)
    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        open_browser=True,
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def fetch_user_playlists(sp: spotipy.Spotify) -> list[dict]:
    playlists = []
    results = sp.current_user_playlists(limit=50)
    while results:
        for item in results["items"]:
            if item is None:
                log.warning("Skipping None item in playlists response")
                continue
            owner = item.get("owner") or {}
            playlists.append({
                "id": item["id"],
                "name": item.get("name", "<unnamed>"),
                "total": None,
                "owner": owner.get("display_name") or owner.get("id", "unknown"),
            })
        results = sp.next(results) if results.get("next") else None
    log.info("Fetched %d playlists", len(playlists))
    return playlists


def fetch_all_tracks(sp: spotipy.Spotify, playlist_id: str,
                     progress_cb=None) -> list[dict]:
    tracks = []
    log.debug("Fetching tracks for playlist %s", playlist_id)
    results = sp.playlist_items(
        playlist_id,
        fields="items(added_at,added_by.id,"
               "track(name,id,duration_ms,external_urls,artists(name),album(name,release_date)),"
               "item(name,id,duration_ms,external_urls,artists(name),album(name,release_date)))"
               ",next",
        limit=100,
    )
    while results:
        for item in results["items"]:
            track = item.get("track") or item.get("item")
            if not track:
                log.debug("Skipping item — no 'track'/'item' key. Present keys: %s",
                          list(item.keys()) if item else "None")
                continue
            release_date = track["album"].get("release_date", "")
            year = release_date[:4] if release_date else ""
            duration_ms = track.get("duration_ms") or 0
            duration_sec = duration_ms // 1000
            duration_fmt = f"{duration_sec // 60}:{duration_sec % 60:02d}"
            tracks.append({
                "Track Name": track["name"],
                "Artist(s)": ", ".join(a["name"] for a in track["artists"]),
                "Album": track["album"]["name"],
                "Release Date": release_date,
                "Year": year,
                "Duration": duration_fmt,
                "Spotify URL": track["external_urls"].get("spotify", ""),
                "Added At": item.get("added_at", ""),
                "Added By": item.get("added_by", {}).get("id", ""),
                "Lyrics": None,
            })
        if progress_cb:
            progress_cb(len(tracks))
        results = sp.next(results) if results.get("next") else None
    log.info("Fetched %d tracks for playlist %s", len(tracks), playlist_id)
    return tracks
