# NotionPlaylistETL — Claude Context

## What this project does
Python/tkinter desktop app that fetches Spotify playlists via Spotipy and exports tracks to CSV for Notion import. Also displays lyrics fetched in the background from lyrics.ovh.

## Stack
- **Python 3.12**, tkinter (stdlib GUI), spotipy, python-dotenv, requests
- **Venv** at `.venv/`
- **Credentials** in `.env` (never committed)

## Running the app
```bash
.venv/Scripts/python main.py        # Windows
.venv/bin/python main.py            # Mac/Linux
```

## Setup from scratch
```bash
python -m venv .venv
source .venv/bin/activate           # Mac/Linux
pip install -r requirements.txt
cp .env.example .env                # then fill in credentials
```

## .env keys
```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```
Redirect URI must also be registered in the Spotify Developer Dashboard.

## Architecture — main.py

### Core functions
| Function | Purpose |
|---|---|
| `get_spotify_client()` | Creates SpotifyOAuth client using .env credentials |
| `fetch_user_playlists(sp)` | Fetches all playlists (name + id only — no track count, avoids API inconsistency) |
| `fetch_all_tracks(sp, playlist_id)` | Paginates through all tracks in a playlist (100 per page) |
| `fetch_lyrics(artist, title)` | Fetches lyrics from lyrics.ovh; strips parentheticals, uses first artist only |
| `export_to_csv(tracks, path)` | Writes tracks to CSV with Notion-friendly columns |
| `load_playlist_cache()` / `save_playlist_cache()` | JSON cache at `playlist_cache.json` for instant startup |

### UI classes
| Class | Tab label | Purpose |
|---|---|---|
| `App` | — | Root `tk.Tk`; owns `ttk.Notebook`, manages tab lifecycle, Spotify connection |
| `PlaylistBrowser` | Playlists | Listbox of all playlists; double-click opens a `PlaylistTab` |
| `PlaylistTab` | Playlist name | Treeview of tracks + lyrics panel; Export and Close Tab buttons |
| `ConsoleTab` | Console | Live log viewer; Save Log, Copy All, Clear buttons |

### Startup flow
1. `App.__init__` → `_connect()`
2. Load `playlist_cache.json` immediately → `browser.load(cached, from_cache=True)`
3. Background thread: authenticate Spotify → `fetch_user_playlists` → update browser + save cache

### Opening a playlist tab
1. Double-click in `PlaylistBrowser` → `App._open_playlist()`
2. Guard: if `self._sp is None` (still connecting), show status message and return
3. `PlaylistTab.__init__` → background thread → `_load_tracks()`
4. `_load_tracks`: fetch real track total via `sp.playlist()`, then `fetch_all_tracks()`
5. Populate treeview, then start second background thread for lyrics

## Known Spotify API quirks
- `current_user_playlists()` returns `items` (not `tracks`) for the track-count field on this account — we skip the count entirely during list load and fetch it per-playlist when a tab opens
- Same `items` vs `tracks` discrepancy appears in `sp.playlist()` response; code checks both with `.get("tracks") or .get("items")`
- 403 on `playlist_items`: playlist is private/owned by another user — handled gracefully with a clear error message in the tab

## Logging
- Uses Python `logging` module; writes to stderr and an in-app queue
- `ConsoleTab` polls the queue every 150ms and renders color-coded log lines
- On app close (`WM_DELETE_WINDOW`), log is auto-saved to `etl_log_<timestamp>.txt`

## CSV export columns
`Track Name`, `Artist(s)`, `Album`, `Release Date`, `Duration`, `Spotify URL`, `Added At`, `Added By`

## Files not in git
| File | Reason |
|---|---|
| `.env` | Contains Spotify credentials |
| `.cache` | Spotipy OAuth token |
| `playlist_cache.json` | Personal playlist data |
| `etl_log_*.txt` | Runtime logs |
| `.venv/` | Virtualenv |
