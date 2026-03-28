# NotionPlaylistETL

Fetches your Spotify playlists and exports tracks to CSV for Notion import. Also shows lyrics in-app.

## Prerequisites (Mac + Homebrew)

Homebrew Python doesn't include tkinter. Install it first:

```bash
brew install python-tk@3.13
```

## First-time setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then open `.env` and fill in your Spotify credentials:

```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

The redirect URI must also be registered in your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).

## Run

```bash
source .venv/bin/activate
python main.py
```

Or without activating the venv:

```bash
.venv/bin/python main.py
```

## After cloning / returning to this repo

1. If `.venv/` is missing, run the **First-time setup** steps above.
2. If `.env` is missing, re-add your Spotify credentials (never committed).
3. `playlist_cache.json` will be regenerated on first run.
