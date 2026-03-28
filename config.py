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
