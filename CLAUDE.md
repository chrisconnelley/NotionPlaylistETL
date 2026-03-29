# NotionPlaylistETL — Claude Context

## What this project does
Python/tkinter desktop app that fetches Spotify playlists via Spotipy, displays lyrics, and exports tracks to Notion (Songs DB, Song Artists DB, Playlists DB, Playlist Songs DB) or CSV.

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
NOTION_API_KEY=...
```
Redirect URI must also be registered in the Spotify Developer Dashboard.

## Architecture

### Module layout
| Module | Purpose |
|---|---|
| `main.py` | Entry point |
| `config.py` | All env vars and Notion DB IDs |
| `notion/` | Notion API package (see below) |
| `spotify.py` | `fetch_all_tracks`, `fetch_user_playlists` |
| `lyrics.py` | `fetch_lyrics` (lyrics.ovh) with local file cache |
| `export.py` | `export_to_csv` |
| `cache.py` | Per-playlist track cache (`tracks/`) |
| `logger.py` | Logging setup + in-app queue for ConsoleTab |
| `theme.py` | Dark-mode color constants |

### notion/ package
| File | Lines | Purpose |
|---|---|---|
| `__init__.py` | 20 | Re-exports public API — external imports unchanged |
| `_api.py` | 50 | `_notion_request`, `_notion_post`, `_notion_get` |
| `_registry.py` | 85 | `load_registry`, `save_registry`, `validate_registry` |
| `_helpers.py` | 63 | `SKIP`, `_page_title`, `_apostrophe_variants`, `_song_title_variants`, `_song_artist_names` |
| `_artists.py` | 189 | Artist find/create/backfill/ensure functions |
| `_songs.py` | 337 | Song find/create/backfill/ensure + `export_tracks` |
| `_playlists.py` | 186 | Playlist find/create/backfill/ensure + `export_playlist` |
| `_playlist_songs.py` | 354 | Lyrics blocks, playlist song ensure/repair + `export_playlist_songs` |
| `_schema.py` | 118 | `fetch_databases`, `snapshot_schema`, `get_notion_client` |

### UI classes
| Class | File | Purpose |
|---|---|---|
| `App` | `ui/app.py` | Root `tk.Tk`; owns `ttk.Notebook`, manages tab lifecycle, Spotify connection |
| `PlaylistBrowser` | `ui/browser.py` | Listbox of all playlists; double-click opens a `PlaylistTab` |
| `PlaylistTab` | `ui/playlist_tab.py` | Treeview of tracks + lyrics panel; Export/Refresh buttons |
| `NotionExportDialog` | `ui/notion_export_dialog.py` | Export tracks to Songs + Song Artists DBs |
| `PlaylistExportDialog` | `ui/playlist_export_dialog.py` | Two-phase: create playlist record, then playlist song records |
| `NotionMatchDialog` | `ui/match_dialog.py` | Interactive dialog when a name match candidate is found |
| `ConsoleTab` | `ui/console_tab.py` | Live log viewer; Save Log, Copy All, Clear buttons |

### Startup flow
1. `App.__init__` → `_connect()`
2. Load `playlist_cache.json` immediately → `browser.load(cached, from_cache=True)`
3. Background thread: authenticate Spotify → `fetch_user_playlists` → update browser + save cache

### Opening a playlist tab
1. Double-click in `PlaylistBrowser` → `App._open_playlist()`
2. Guard: if `self._sp is None` (still connecting), show status message and return
3. `PlaylistTab.__init__` → background thread → `_load_tracks()`
4. `_load_tracks`: check tracks cache → else fetch from Spotify
5. Populate treeview, then start second background thread for lyrics

## Notion DB IDs (config.py)
```python
NOTION_SONGS_DB_ID              = "8ccd2adb-..."
NOTION_ARTISTS_DB_ID            = "d5367ed0-..."
NOTION_JESSIE_PLAYLISTS_DB_ID   = "5b0f6317-..."
NOTION_TERI_PLAYLISTS_DB_ID     = "91b88eb5-..."
NOTION_JESSIE_PL_SONGS_DB_ID    = "93b98cda-..."
NOTION_TERI_PL_SONGS_DB_ID      = "1c741983-..."
```

## Local registries (notion_sync/)
JSON files keyed by Spotify identifier → Notion page ID + status.
| File | Key |
|---|---|
| `songs.json` | Spotify track URL |
| `artists.json` | Spotify artist ID |
| `playlists.json` | Spotify playlist ID |
| `playlist_songs.json` | `"{playlist_spotify_id}:{track_spotify_url}"` |

## Notion export flow (notion.py)

### Songs + Artists (`export_tracks`)
Three-step match chain per track:
1. Registry lookup by Spotify URL/ID
2. Exact name search in Notion → candidate shown in `NotionMatchDialog` → backfill only if confirmed
3. Similarity search → `NotionMatchDialog`
4. Create new if no match confirmed

**IMPORTANT**: `_backfill_song_spotify_url` / `_backfill_artist_spotify_id` only run AFTER the user confirms the match (`notion_id == match_id`). Never backfill before confirmation.

### Playlists (`export_playlist`)
Same three-step chain. Also fetches and sets `Playlist Cover` (external URL) from Spotify.

### Playlist Songs (`export_playlist_songs`)
- Exports missing songs/artists on the fly (no need to run songs export separately)
- Creates a Playlist Song record linking playlist, song, and artists
- Adds lyrics as a two-column Notion block (splits at 1900 chars, Notion rich_text limit)
- **Idempotent**: re-running repairs missing Song/Playlist/Artist relations on existing records
- `_find_playlist_song` falls back to name+playlist search when song relation is empty
- `_repair_playlist_song` PATCHes any missing relations without touching lyrics/notes

### Validate Registry (`validate_registry`)
- Checks cached page IDs still exist in Notion; removes stale entries
- Accepts optional `keys` set to scope validation to the current playlist's tracks only

## _PLAYLIST_SONGS_DB config (notion.py)
Maps playlist DB ID → songs DB ID + relation property names (differ per Jessie/Teri):
```python
_PLAYLIST_SONGS_DB = {
    NOTION_JESSIE_PLAYLISTS_DB_ID: {
        "db_id":             NOTION_JESSIE_PL_SONGS_DB_ID,
        "song_relation":     "Song",
        "playlist_relation": "Jessie Playlist",
    },
    NOTION_TERI_PLAYLISTS_DB_ID: {
        "db_id":             NOTION_TERI_PL_SONGS_DB_ID,
        "song_relation":     "Songs",
        "playlist_relation": "▶️ Teri & Chris\u2019s Playlists",  # U+2019 curly apostrophe
    },
}
```

## Known quirks / gotchas
- Spotify API: `current_user_playlists()` returns `items` (not `tracks`) for track-count — skip count during list load, fetch per-playlist when tab opens
- Notion property names use curly apostrophe U+2019 (`'`), not straight U+0027 (`'`) — `_apostrophe_variants()` handles search; hardcode U+2019 in property name strings
- Notion data_source relations cannot be used as rollup sources via API — must be added manually in Notion UI
- Notion rich_text limit: 2000 chars per element; lyrics split at 1900 chars
- Notion `files` property for cover images uses `{"type": "external", "external": {"url": ...}}`
- 403 on playlist_items: playlist is private/owned by another user — handled gracefully

## Threading pattern for match dialogs
Export runs on a background thread. When a match candidate is found:
1. Background thread calls `match_cb(kind, name, candidates)`
2. `match_cb` schedules `_show_match_dialog` on the main thread via `self.after(0, ...)`
3. Background thread blocks on `done_event.wait()`
4. Main thread shows dialog, sets `result_holder["choice"]`, calls `done_event.set()`
5. Background thread resumes with the user's choice

`SKIP` sentinel (`"__skip__"`): returned by `match_cb` when user skips a track entirely.

## Logging
- Uses Python `logging` module; writes to stderr and an in-app queue
- `ConsoleTab` polls the queue every 150ms and renders color-coded log lines
- On app close (`WM_DELETE_WINDOW`), log is auto-saved to `etl_log_<timestamp>.txt`

## CSV export columns
`Track Name`, `Artist(s)`, `Album`, `Release Date`, `Duration`, `Spotify URL`, `Added At`, `Added By`

## Files not in git
| File | Reason |
|---|---|
| `.env` | Contains Spotify/Notion credentials |
| `.cache` | Spotipy OAuth token |
| `playlist_cache.json` | Personal playlist data |
| `tracks/` | Per-playlist track caches |
| `lyrics/` | Per-song lyrics caches |
| `notion_sync/` | Local registry JSON files |
| `etl_log_*.txt` | Runtime logs |
| `.venv/` | Virtualenv |
