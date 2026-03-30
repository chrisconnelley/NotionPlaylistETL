import os

from dotenv import load_dotenv

load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
SCOPE = "playlist-read-private playlist-read-collaborative"

NOTION_API_KEY = os.getenv("NOTION_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, "playlist_cache.json")
LYRICS_CACHE_DIR = os.path.join(BASE_DIR, "lyrics")
TRACKS_CACHE_DIR = os.path.join(BASE_DIR, "tracks")

os.makedirs(LYRICS_CACHE_DIR, exist_ok=True)
os.makedirs(TRACKS_CACHE_DIR, exist_ok=True)

NOTION_SONGS_DB_ID              = "8ccd2adb-8912-493c-afe1-1b06c3433828"
NOTION_ARTISTS_DB_ID            = "d5367ed0-480a-4ccc-9ad4-bda5e56fbf91"
NOTION_PLAYLISTS_DB_ID          = "5b0f6317-af53-401f-9741-3f862542f6cf"
NOTION_PLAYLIST_SONGS_DB_ID     = "93b98cda-f7c4-4699-9eb2-a518feb9b7ac"
