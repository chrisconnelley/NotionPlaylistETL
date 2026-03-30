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
| `_registry.py` | 85 | `load_registry`, `save_registry`, `validate_registry` (now optional; registries are ephemeral) |
| `_helpers.py` | 70 | `SKIP`, `_page_title`, `_apostrophe_variants`, `_song_title_variants`, `_song_artist_names`, `_chunks` |
| `_artists.py` | 213 | Artist find/create/backfill/ensure + pre-flight `_batch_lookup_artists` |
| `_songs.py` | 370 | Song find/create/backfill/ensure + pre-flight `_batch_lookup_songs` + `export_tracks` |
| `_playlists.py` | 241 | Playlist find/create/backfill/ensure + pre-flight `_batch_lookup_playlists` + `export_playlist` |
| `_playlist_songs.py` | 355 | Lyrics blocks, playlist song ensure/repair + pre-flight batches + `export_playlist_songs` |
| `_schema.py` | 118 | `fetch_databases`, `snapshot_schema`, `get_notion_client` |

### UI classes
| Class | File | Purpose |
|---|---|---|
| `App` | `ui/app.py` | Root `tk.Tk`; owns `ttk.Notebook`, manages tab lifecycle, Spotify connection |
| `PlaylistBrowser` | `ui/browser.py` | Listbox of all playlists; double-click opens a `PlaylistTab` |
| `PlaylistTab` | `ui/playlist_tab.py` | Treeview of tracks + lyrics panel; Export/Refresh buttons |
| `ExportDialog` | `ui/export_dialog.py` | Unified export in three phases: Playlist record (Phase 1) → Songs & Artists (Phase 2) → Playlist Songs (Phase 3) |
| `NotionMatchDialog` | `ui/match_dialog.py` | Interactive dialog when a name match candidate is found |
| `ConsoleTab` | `ui/console.py` | Settings with schema verification; live log viewer below |

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
NOTION_PLAYLISTS_DB_ID          = "5b0f6317-..."
NOTION_PLAYLIST_SONGS_DB_ID     = "93b98cda-..."
```
Ownership is tracked via "Created By" and "Created For" properties on playlist records; no separate per-owner databases.

## Registries (ephemeral, per-export)
Registries are temporary dicts created at the start of each export, not persisted to disk. They track what was created/matched during the export for deduplication and to avoid querying Notion multiple times for the same item.

| Registry | Key | Purpose |
|---|---|---|
| `songs_reg` | Spotify track URL | Maps URLs to created/matched song page IDs |
| `artists_reg` | Spotify artist ID | Maps artist IDs to created/matched artist page IDs |
| `playlists_reg` | Spotify playlist ID | Maps playlist IDs to created/matched playlist page IDs |
| `pl_songs_reg` | `"{playlist_id}:{track_url}"` | Maps playlist song entries to created/matched page IDs |

**Pre-flight batch lookups**: Before the main item loop, batch queries check Notion for all unregistered items and populate the registry. Items found by Spotify URL/ID are auto-matched (no user dialog). Items not found proceed through name-based matching (exact name, then similarity search).

## Notion export flow (notion/ package)

### Export Phases (ui/export_dialog.py)
1. **Phase 1 — Playlist Record**: Create/verify the playlist in Notion (prerequisite for Phases 2 & 3)
2. **Phase 2 — Songs & Artists**: Pre-flight batch lookup, then ensure each track + artists
3. **Phase 3 — Playlist Songs**: Create/repair playlist song records with lyrics

### Songs + Artists (`export_tracks`)
**Pre-flight batch lookup** (new optimization):
- Collect all unregistered song URLs and artist IDs from tracks
- Query Notion in bulk (50-item chunks) using compound OR filters
- Found items are auto-added to registry (no user dialog needed for URL/ID matches)
- Items not found in pre-flight proceed through name-based matching

**Per-item match chain** (after pre-flight):
1. Registry fast-path: if URL/ID found in pre-flight, return "pre_existing" (no dialog)
2. Exact name search in Notion → candidate shown in `NotionMatchDialog` → backfill only if confirmed
3. Similarity search → `NotionMatchDialog`
4. Create new if no match confirmed

**IMPORTANT**: `_backfill_song_spotify_url` / `_backfill_artist_spotify_id` only run AFTER the user confirms the match (`notion_id == match_id`). Never backfill before confirmation.

### Playlists (`export_playlist`)
**Pre-flight batch lookup**:
- Collect all unregistered playlist IDs from input
- Query Notion for matching Spotify URLs (construct `https://open.spotify.com/playlist/{id}`)
- Found items are auto-matched to registry

**Per-item match chain** (same as songs/artists):
1. Registry fast-path: if Spotify URL found in pre-flight, return "pre_existing"
2. Exact name match
3. Similarity search
4. Create new if no match

Also fetches and sets `Playlist Cover` (external URL) from Spotify.

### Playlist Songs (`export_playlist_songs`)
- **Pre-flight batch lookups** for all songs and artists (same as export_tracks)
- **Direct Notion lookup** for playlist: `_find_playlist_by_spotify_url()` (not registry-based)
- Exports missing songs/artists on the fly (no need to run songs export separately)
- Creates a Playlist Song record linking playlist, song, and artists
- Adds lyrics as a two-column Notion block (splits at 1900 chars, Notion rich_text limit)
- **Idempotent**: re-running repairs missing Song/Playlist/Artist relations on existing records
- `_find_playlist_song` falls back to name+playlist search when song relation is empty
- `_repair_playlist_song` PATCHes any missing relations without touching lyrics/notes; skips archived pages gracefully

### Validate Registry (`validate_registry`)
- Manual validation tool: checks cached page IDs (from `notion_sync/`) still exist in Notion; removes stale entries
- Accepts optional `keys` set to scope validation to the current playlist's tracks only
- Note: since registries are now ephemeral per-export, this is primarily useful for cleaning old cached data

## _PLAYLIST_SONGS_CONFIG (notion/_playlist_songs.py)
Single consolidated configuration for playlist songs database:
```python
_PLAYLIST_SONGS_CONFIG = {
    "db_id":             NOTION_PLAYLIST_SONGS_DB_ID,
    "song_relation":     "Song",
    "playlist_relation": "Playlist",
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

## Schema Verification
The Console tab includes a **Verify Schema** button that:
- Fetches the current schema for all accessible Notion databases
- Saves snapshots to `notion_schema/*.json` (auto-sanitized filenames)
- Useful for validating database structure after changes or troubleshooting property issues

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

## Completed Optimizations

### ✅ Batch Notion Queries (DONE)
- **Performance impact**: ~50-60s → ~10-15s per 50-track playlist export (7-8x faster)
- **Implementation**:
  - Pre-flight batch lookups for all songs, artists, and playlists (lines 12-73 in `_songs.py`, `_artists.py`, `_playlists.py`)
  - Compound OR filters to query 50 items per request instead of 1-3 calls per item
  - Auto-accept definitive matches (Spotify URL/ID) without user dialog
  - Variant queries rewritten to use single OR filter instead of looping (lines 78-93, 95-129 in `_songs.py`)
  - Registry is ephemeral per-export (no disk I/O overhead)
- **Result**: From 60+ sequential Notion API calls per playlist down to ~5-10 calls

### ✅ Unified Match Dialog with Spotify URL Hints (DONE)
- **User benefit**: Single consolidated dialog showing all candidates (exact matches first, then similar), with Spotify URL disambiguation (last 4 chars)
- **Implementation**:
  - Combined exact name search + similarity search into single pass (no sequential dialogs)
  - All candidates shown together, sorted (exact first, deduplicated)
  - Spotify URL last-4 shown in dialog header and each listbox row for disambiguation
  - URL normalization handles format variations (query params, fragments, trailing slash, case-insensitive)
  - Dialog appears even with zero candidates, allowing user to choose "Create New" or "Skip"
  - URL filtering: rejects candidates with different Spotify URLs (impossible matches)
  - Applied to `_songs.py`, `_artists.py`, `_playlists.py`, `ui/match_dialog.py`
- **Fixed bugs**:
  - "Create New" button now properly creates records when clicked
  - Match dialog was skipped for items with no candidates (now shows dialog in all interactive cases)
  - False match candidates with different Spotify URLs now filtered out

### ✅ Ephemeral Registries & Background Song Cache (DONE)
- **Removed**: Persistent registry storage (`notion/_registry.py` deleted, registry persistence in config.py/ui modules removed)
- **Implementation**:
  - Registries created fresh per export, not stored to disk (ephemeral per-export lifecycle)
  - Background song cache loaded at app startup (`_load_all_songs_cache()` in background thread)
  - Pre-flight batch lookups use cached data (O(1) lookups) instead of API queries
  - Cache automatically updated when new songs created during export
  - Thread-safe with locking to prevent race conditions
- **Impact**: Faster pre-flight lookups, cleaner registry lifecycle, no stale disk data

## Planned Improvements (Future)

### Remaining Bottlenecks & Issues

**Match Accuracy**: False positives/negatives due to:
- No string similarity scoring (exact match only; Levenshtein distance could rank candidates)
- Apostrophe variants don't handle diacritics (é, ñ, ü)
- No artist disambiguation (100+ artists named "The Weeknd")
- Search limited to first 10 results; real match might be #11

**User Experience**:
- Export dialog is modal (grab_set) — prevents viewing Console during export
- Match dialog shows only page name; no preview/details
- No undo/rollback mechanism
- Error messages show only last line (context lost)

**Data Integrity**:
- Known issue: Duplicate song records without Spotify URLs can be created in edge cases
- Duplicate Playlist Song records if track appears twice (rare but possible)
- Match dialog blocks indefinitely (no timeout)

### Recommended Improvements (Priority Order)

#### 🟡 Medium Priority (Nice UX, Low-Medium Effort)

1. **Similarity Scoring for Match Candidates**
   - Use Levenshtein distance to rank candidates
   - Pre-select highest-confidence match
   - Show confidence % in dialog
   - Files: `notion/_songs.py`, `notion/_artists.py`, `ui/match_dialog.py`

2. **Match Dialog Preview**
   - Show Spotify data vs Notion data side-by-side
   - Add "View in Notion" button
   - Show last-edited timestamp
   - Files: `ui/match_dialog.py`

3. **Auto-Timeout on Match Dialogs**
   - Auto-proceed to "Create New" after 30 seconds
   - Prevent UI freeze if user forgets about dialog
   - Files: `ui/export_dialog.py`

#### 🟢 Nice-to-Have (Polish, Low Effort)

4. **Better Error Messages**
   - Include operation context + full error
   - Files: `notion/_songs.py`, `notion/_artists.py`

5. **Undo/Rollback Export**
   - Track created pages, allow "Revert Export"
   - Delete created pages, restore registry
   - Files: `notion/__init__.py`, `ui/export_dialog.py`
