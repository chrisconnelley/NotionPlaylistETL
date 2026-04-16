# NotionPlaylistETL

Python 3.12 / tkinter desktop app. Fetches Spotify playlists via Spotipy, displays lyrics, exports to Notion (4 databases) or CSV.

## Run & Setup
```bash
source .venv/bin/activate && python main.py
```
Credentials in `.env`: `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, `NOTION_API_KEY`.

## Module Map

| Module | Purpose |
|---|---|
| `config.py` | Env vars; re-exports DB IDs from `notion_config.py` |
| `notion_config.py` | DB IDs, parent page ID, `DB_NAMES` schema mapping — regenerated per Teamspace by `notion/setup.py` |
| `settings.py` | `load_settings()` / `save_settings()` — persists UI prefs to `app_settings.json` |
| `spotify.py` | `fetch_all_tracks`, `fetch_user_playlists` |
| `lyrics.py` | `fetch_lyrics` with file cache |
| `export.py` | `export_to_csv` |
| `cache.py` | Per-playlist track cache (`tracks/`) |
| `logger.py` | Logging + in-app queue |
| `theme.py` | Dark-mode constants |

### notion/ package
| File | Purpose |
|---|---|
| `__init__.py` | Re-exports: `SKIP`, `export_tracks`, `export_playlist`, `export_playlist_songs`, `undo_export`, `fetch_databases`, `snapshot_schema` |
| `_api.py` | `_notion_request`, `_notion_post`, `_notion_get` — raw HTTP with rate-limit retry |
| `_helpers.py` | Shared utilities: `SKIP`, `_page_title`, `_song_artist_names` (cache-first), `_normalize_spotify_url`, `_artist_spotify_url`, `_make_registry_entry`, `_merge_candidates`, `_apostrophe_variants`, `_song_title_variants`, `_chunks` |
| `_artists.py` | Artist CRUD + cache (`_load_all_artists_cache`) + `_batch_lookup_artists` + `_search_artists_in_notion` + `_fetch_artist_details` + `_ensure_artist` (supports `auto_create`) |
| `_songs.py` | Song CRUD + `_batch_lookup_songs` + `_load_all_songs_cache` + `_search_songs_in_notion` + `export_tracks` + `_ensure_song` (supports `auto_create`) |
| `_playlists.py` | Playlist CRUD + `_batch_lookup_playlists` + `_all_playlists_have_urls` + `export_playlist` (supports `auto_create`) |
| `_playlist_songs.py` | Playlist song CRUD + lyrics blocks + `_get_playlist_songs_config()` (discovers relation names) + `export_playlist_songs` |
| `_undo.py` | `undo_export` — archives pages created during an export (rollback) |
| `_schema.py` | `fetch_databases`, `snapshot_schema` |
| `setup.py` | DB creation from `notion_schema/*.json`, relation wiring, integration test, `update_config_file` |
| `check_setup.py` | `check_notion_setup`, `_reload_config`, `get_db_ids` |

### UI
| Class | File | Purpose |
|---|---|---|
| `App` | `ui/app.py` | Root window; Notebook tabs, Spotify connection, Notion setup orchestration |
| `PlaylistBrowser` | `ui/browser.py` | Playlist listbox; double-click opens `PlaylistTab` |
| `PlaylistTab` | `ui/playlist_tab.py` | Track treeview + lyrics; Export/Refresh buttons |
| `ExportDialog` | `ui/export_dialog.py` | 3-phase export: Playlist → Songs & Artists → Playlist Songs; auto-create prompt; undo/rollback; auto-start countdown; close-on-complete |
| `NotionMatchDialog` | `ui/match_dialog.py` | Candidate picker for ambiguous Notion matches |
| `SettingsTab` | `ui/console.py` | Schema verify, Reset Notion Databases button, console log level filter, live log viewer |

## Config & DB ID Loading

DB IDs live in `notion_config.py` → re-exported by `config.py` → imported by `notion/_*.py` modules.

**Stale import problem**: Top-level `from config import NOTION_*_DB_ID` caches the value at import time. After `notion_config.py` is rewritten (setup/reset), `importlib.reload()` updates the module objects but code that already imported values still holds stale references.

**Current state of the fix**:
- `check_setup.py:_reload_config()` reloads `notion_config` → `config` → all `notion._*` modules
- `ui/export_dialog.py` imports `NOTION_PLAYLISTS_DB_ID` inside `_run()` (dynamic)
- All `notion/_*.py` modules use dynamic getter functions (`_get_playlists_db_id()`, `_get_artists_db_id()`, `_get_songs_db_id()`, `_get_playlist_songs_db_id()`) that import inside the function body — no stale top-level imports remain

## Notion Database Setup (`notion/setup.py`)

Triggered on startup via `App._check_notion_setup()` if `check_notion_setup()` finds missing DBs, or via Settings tab "Reset Notion Databases" button.

**Reset flow** (`SettingsTab._reset_notion_databases`):
1. Confirmation dialog
2. Delete all 4 databases + MusicTunnel page via Notion API
3. Reset `notion_config.py` to `"missing"` values (preserves parent page ID)
4. Triggers `App._on_notion_reset_complete()` → `_reload_config()` → `_check_notion_setup()` to offer re-setup

**Setup flow** (`setup_databases_in_page`):
1. Create "MusicTunnel" page under stored parent page
2. Create 4 databases from `notion_schema/*.json` (skips relation/formula/rollup properties)
3. Add relations via raw PATCH API (tracks `dual_property` pairs to avoid duplicates)
4. Verify all properties exist
5. Integration test: create test artist→song→playlist→playlist_song, verify relations, archive test data
6. Write new IDs to `notion_config.py`

**Dual relation handling**: `dual_property` relations auto-create the reverse property on the target DB. The `created_dual_pairs` set (sorted tuple of both DB IDs) prevents adding the same relation from both sides.

**Relation PATCH format**: The Notion API requires the relation type key to appear both as `"type": "dual_property"` AND as a nested object `"dual_property": {}` in the payload. Without the nested object key, the API returns a 400 validation error.

**Integration test**: Discovers actual relation property names by querying the Playlist Songs DB and matching `relation.database_id` to known DB IDs — avoids hardcoding names that Notion may auto-generate differently.

**Dynamic relation discovery** (`_playlist_songs.py`): `_get_playlist_songs_config()` queries the Playlist Songs database schema to find the actual property names for Song and Playlist relations by matching `relation.database_id`. Cached after first call; reset on module reload.

## Export Flow

### Registries (ephemeral per-export)
Temporary dicts tracking created/matched items during a single export. Built via `_make_registry_entry()`. Not persisted.

| Registry key | Format |
|---|---|
| `songs_reg` | Spotify track URL |
| `artists_reg` | Spotify artist URL (e.g. `https://open.spotify.com/artist/{id}`) |
| `playlists_reg` | Spotify playlist ID |
| `pl_songs_reg` | `"{playlist_id}:{track_url}"` |

### Match chain (songs, artists, playlists)
All three `_ensure_*` functions follow the same pattern using shared helpers:
1. **Pre-flight batch lookup** → auto-accept Spotify URL matches (no dialog)
2. **Registry fast-path** → return `"pre_existing"`, backfill missing metadata if needed
3. **Combined name search** (single OR query with exact `equals` + fuzzy `contains` filters) → `_merge_candidates()` dedup/filter → `NotionMatchDialog`
4. **Backfill** after match (user-confirmed or pre-flight), **create new** if no match

**Artist matching**: Uses a session-level cache (`_load_all_artists_cache`) like songs. Matches by Spotify URL first, falls back to Spotify Artist ID for legacy rows. On any match, `_backfill_artist_metadata` writes the URL (and genres, popularity, etc.) if missing. `_artist_spotify_url(id)` constructs canonical URLs from artist IDs. The Spotify Artist ID property is legacy and will be removed once all rows have URLs.

### Auto-create mode
When all tracks in a playlist have Spotify URLs, the export dialog offers to skip name-based matching. All pre-flight checks run in Phase 1 (before the playlist record is exported). Flow:
1. In Phase 1, `ExportDialog` runs pre-flight URL checks for songs, artists, **and the playlist**
2. Checks `_all_playlists_have_urls(db_id)` — only offers auto-create if every existing Notion playlist has a Spotify URL
3. If unmatched items exist (songs, artists, or playlist), prompts: "Create new records for all unmatched?" (Yes/No)
4. If confirmed, `auto_create=True` is passed to `export_playlist`, `export_tracks`, and `export_playlist_songs`
5. `_ensure_playlist` / `_ensure_song` / `_ensure_artist` with `auto_create=True` skip name search + match dialog and create directly when not found by URL

This eliminates per-item match dialogs when Spotify URLs are the definitive identifier.

### Playlist Songs (`export_playlist_songs`)
- Exports missing songs/artists on the fly
- Links playlist, song, and artists via relations
- Lyrics added as two-column Notion block (split at 1900 chars; Notion limit is 2000)
- Idempotent: re-run repairs missing relations on existing records

## Threading

Export runs on a background thread. Match dialogs use this pattern:
1. Background thread calls `match_cb(kind, name, candidates)`
2. `match_cb` schedules dialog on main thread via `self.after(0, ...)`
3. Background thread blocks on `threading.Event.wait()`
4. Main thread shows dialog → sets result → `event.set()`

`SKIP` sentinel = `"__skip__"`.

### Undo / Rollback
Each export phase returns the Notion page IDs of records it created (`created_page_ids`, `created_song_page_ids`, `created_artist_page_ids`). `ExportDialog._run()` collects these into an `_undo_manifest` dict with keys: `playlist_songs`, `songs`, `artists`, `playlist`. After a successful export, an "Undo Export (N)" button appears. Clicking it runs `undo_export()` in a background thread, which archives pages in reverse order (playlist songs → songs → artists → playlist) via `PATCH pages/{id} {"archived": true}`. Only records with status `"added"` are included — pre-existing and repaired records are never touched.

## Network Query Optimizations

**Combined name searches**: `_search_songs_in_notion` and `_search_artists_in_notion` each issue a single OR query combining both exact (`equals`) and fuzzy (`contains`) filters. Previously these were two separate functions with two API calls and a sleep between each. Playlist name search (`_find_playlist_by_name`) and similar playlist search (`_search_similar_playlists`) also use single OR queries across apostrophe variants.

**`_all_playlists_have_urls`**: Queries for playlists missing a URL (`is_empty: true`, `page_size: 1`) instead of paginating through the entire database. Single API call regardless of DB size.

**`_song_artist_names` cache-first resolution**: Resolves artist names from the in-memory `_NOTION_ARTISTS_CACHE` (keyed by `notion_page_id`) before falling back to individual `_notion_get` API calls. Eliminates up to 5 API calls per song candidate during match dialogs.

**No pre-emptive sleeps**: The `_ensure_*` functions, cache-loading pagination, and batch lookups no longer include `time.sleep(0.35)` before API calls. Rate limiting is handled entirely by `_notion_request`'s retry logic (respects `Retry-After` header on 429). Sleeps remain only in `setup.py` and `_undo.py` (infrequent one-time flows).

## Gotchas
- Notion property names use curly apostrophe U+2019 (`'`), not U+0027 — `_apostrophe_variants()` handles search
- Notion rich_text limit: 2000 chars per element
- Notion `files` property: `{"type": "external", "external": {"url": ...}}`
- `_notion_post` requires a body arg; use `_notion_request("GET", ...)` or `_notion_get()` for reads
- 403 on `playlist_items`: private/other-user playlist — handled gracefully
- Notion `dual_property` relations auto-create the reverse; creating from both sides produces duplicates

## App Settings (`settings.py`)

Persistent UI preferences stored in `app_settings.json` (git-ignored). Managed via `load_settings()` / `save_settings()`.

| Key | Default | Where set |
|---|---|---|
| `auto_start` | `false` | ExportDialog checkbox — starts 5-second countdown when dialog opens or when toggled on |
| `close_on_complete` | `false` | ExportDialog checkbox — auto-closes dialog and playlist tab 1.5s after completion (only if no undo-able items) |
| `console_log_level` | `"DEBUG"` | SettingsTab combobox — filters console display (all levels still go to terminal + log file) |

### Export dialog auto-start
When "Auto-start" is checked (either on dialog open or by clicking the checkbox), `_begin_countdown(5)` ticks down on the Start button text ("Starting in 5…", "Starting in 4…", …). User can abort by unchecking the checkbox or clicking Cancel. On reaching 0, `_start()` is called. The countdown is cancelled via `self.after_cancel()`.

### Close on complete
When checked and the export finishes with no undo-able items (all pre-existing), `_close_with_tab()` closes both the export dialog and the parent playlist tab after 1.5s.

### Export summary logging
After export completes, `_show_summary` logs the full summary text at INFO level via `log.info()`.

### Console log level filtering
`SettingsTab._append()` compares each message's level against the selected threshold using `_LEVEL_ORDER`. Messages below the threshold are silently dropped from the console display. The underlying logger remains at DEBUG — terminal output and saved log files are unaffected.

## Files not in git
`.env`, `.cache`, `playlist_cache.json`, `app_settings.json`, `tracks/`, `lyrics/`, `notion_sync/`, `etl_log_*.txt`, `.venv/`

## Known Issues
- Duplicate song records possible for items without Spotify URLs
- Match dialog blocks indefinitely (no timeout)

## Future Improvements
1. Similarity scoring for match candidates (Levenshtein)
2. Match dialog preview (Spotify vs Notion side-by-side)
3. Auto-timeout on match dialogs
4. Package as macOS executable via PyInstaller (`--onefile --windowed`). Requires: resolving cache/lyrics paths relative to executable (`sys._MEIPASS`), bundling or externalizing `.env`, handling Spotipy OAuth token cache path, and adding any hidden imports PyInstaller misses. Consider a `.spec` file for icon/name customization.
