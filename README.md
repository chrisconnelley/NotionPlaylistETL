# NotionPlaylistETL

Python 3.12 / tkinter desktop app that fetches your Spotify playlists, displays lyrics, and exports tracks to a set of Notion databases (Artists, Songs, Playlists, Playlist Songs) or CSV.

## Features

- Browse and search your Spotify playlists
- View lyrics inline
- Export to four linked Notion databases with full relation wiring
- Auto-create mode: skip match dialogs when Spotify URLs are the definitive identifier
- Undo/rollback: revert an export by archiving created records
- Export to CSV as an alternative
- Persistent settings: auto-start export, close-on-complete, console log level

## Prerequisites (Mac + Homebrew)

Homebrew Python doesn't include tkinter. Install it first:

```bash
brew install python-tk@3.12
```

## First-time setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then open `.env` and fill in your credentials:

```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
NOTION_API_KEY=...
```

- The Spotify redirect URI must be registered in your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
- The Notion API key comes from a [Notion integration](https://www.notion.so/my-integrations). The integration must be connected to the workspace page where databases will be created.

## Run

```bash
source .venv/bin/activate
python main.py
```

Or without activating the venv:

```bash
.venv/bin/python main.py
```

## Notion setup

On first launch (or after a reset), the app detects missing databases and walks you through setup:

1. Prompts for a parent page in your Notion workspace
2. Creates a "MusicTunnel" page with four databases (Artists, Songs, Playlists, Playlist Songs)
3. Wires up relations between databases
4. Runs an integration test to verify everything works

You can reset and re-create databases from the Settings tab.

## After cloning / returning to this repo

1. If `.venv/` is missing, run the **First-time setup** steps above.
2. If `.env` is missing, re-add your credentials (never committed).
3. `playlist_cache.json` and `app_settings.json` are regenerated on first run.
