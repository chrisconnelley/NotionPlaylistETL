"""
Notion database setup — detect new Teamspace and create databases from schemas.

When NOTION_API_KEY is updated to a new Teamspace, this detects that the
configured database IDs are invalid and offers to create the databases.
"""

import json
import os
import re
from typing import Optional

from notion_client import Client

from config import BASE_DIR, NOTION_API_KEY
from logger import log


SCHEMA_DIR = os.path.join(BASE_DIR, "notion_schema")


def get_notion_client() -> Client:
    """Get Notion client for the configured API key."""
    if not NOTION_API_KEY:
        raise ValueError("NOTION_API_KEY not set in .env")
    return Client(auth=NOTION_API_KEY)


def check_database_exists(db_id: str) -> bool:
    """Check if a Notion database exists and is accessible."""
    try:
        client = get_notion_client()
        client.databases.retrieve(db_id)
        return True
    except Exception:
        return False


def check_databases_exist(db_ids: dict[str, str]) -> dict[str, bool]:
    """
    Check which configured databases exist in Notion.
    Returns {db_name: exists} mapping.
    """
    results = {}
    for name, db_id in db_ids.items():
        results[name] = check_database_exists(db_id)
    return results


def load_schema(name: str) -> dict:
    """Load a database schema from notion_schema/."""
    from notion_config import DB_NAMES

    if name not in DB_NAMES:
        raise ValueError(f"Unknown database: {name}")

    path = os.path.join(SCHEMA_DIR, DB_NAMES[name])
    if not os.path.exists(path):
        raise FileNotFoundError(f"Schema not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_page(title: str, parent_page_id: Optional[str] = None) -> str:
    """
    Create a page in Notion.

    Args:
        title: Page title
        parent_page_id: Parent page ID (None for workspace root)

    Returns:
        page_id of the created page
    """
    client = get_notion_client()

    if parent_page_id:
        parent = {"type": "page_id", "page_id": parent_page_id}
    else:
        parent = {"type": "workspace", "workspace": True}

    log.info(f"Creating page '{title}'…")
    response = client.pages.create(
        parent=parent,
        properties={"title": [{"type": "text", "text": {"content": title}}]},
    )

    page_id = response["id"]
    log.info(f"✓ Created page '{title}' (ID: {page_id})")
    return page_id


def create_database(name: str, parent_page_id: str) -> str:
    """
    Create a Notion database from schema inside a page.

    Returns the database_id of the created database.

    Args:
        name: Database name (from DB_NAMES)
        parent_page_id: Page ID where the database will be created (required)

    Note: Creates database with all properties via direct API call.
    """
    from notion._api import _notion_post

    schema = load_schema(name)

    log.info(f"Creating database '{name}'…")

    # Build all properties (excluding relations, formulas, rollups which need other DBs to exist)
    skip_types = {"relation", "formula", "rollup"}
    properties = {}

    for prop_name, prop_def in schema["properties"].items():
        prop_type = prop_def.get("type")

        # Skip special types
        if prop_type in skip_types:
            log.debug(f"  Skipping {prop_name} ({prop_type}) — will add after relations set up")
            continue

        # Build property config
        prop_config = {"type": prop_type}

        # Add type-specific config from schema
        if prop_type in prop_def:
            type_config = prop_def[prop_type]

            # For multi_select, remove old option IDs (Notion will generate new ones)
            if prop_type == "multi_select" and isinstance(type_config, dict) and "options" in type_config:
                type_config = dict(type_config)
                type_config["options"] = [
                    {"name": opt.get("name"), "color": opt.get("color")}
                    for opt in type_config.get("options", [])
                    if opt.get("name")  # Only include options with names
                ]

            prop_config[prop_type] = type_config

        properties[prop_name] = prop_config
        log.debug(f"  Property '{prop_name}': {json.dumps(prop_config)}")

    # Create database via direct API call
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": name}}],
        "properties": properties,
        "is_inline": False,
    }

    try:
        log.debug(f"Sending database creation payload with {len(properties)} properties")
        response = _notion_post("databases", payload)
        new_db_id = response["id"]
        log.info(f"✓ Created database '{name}' with {len(properties)} properties (ID: {new_db_id})")
        return new_db_id

    except Exception as e:
        log.error(f"Failed to create database '{name}':")
        log.error(f"  Error: {e}")
        log.error(f"  Attempted properties: {list(properties.keys())}")

        # Log sample properties for debugging
        for i, (pname, pconfig) in enumerate(list(properties.items())[:3]):
            log.debug(f"    Sample property {i+1}: {pname} = {json.dumps(pconfig)}")

        raise


def add_relations_to_database(
    db_id: str,
    db_name: str,
    id_mapping: dict[str, str],
    created_dual_pairs: set[tuple[str, str]] | None = None,
) -> None:
    """
    Add relation properties to a database after all databases are created.

    Uses raw PATCH API instead of SDK client for reliability.

    Args:
        db_id: The database to update
        db_name: Name of the database (to load schema)
        id_mapping: {old_db_id: new_db_id} mapping for all databases
        created_dual_pairs: Set of (db_id_a, db_id_b) tuples for dual relations
            already created. Updated in-place to track new ones. Pass the same set
            across all calls to avoid creating duplicate bidirectional relations.
    """
    from notion._api import _notion_request, _notion_get
    import time

    if created_dual_pairs is None:
        created_dual_pairs = set()

    schema = load_schema(db_name)
    old_db_id = schema["database_id"]
    new_db_id = id_mapping[old_db_id]

    relation_count = 0
    for prop_name, prop_def in schema["properties"].items():
        if prop_def.get("type") != "relation":
            continue

        # Map old relation reference to new ID
        old_relation_id = prop_def["relation"].get("database_id")
        if old_relation_id not in id_mapping:
            log.warning(f"Skipping relation '{prop_name}': target DB not in mapping")
            continue

        new_relation_id = id_mapping[old_relation_id]
        relation_type = prop_def["relation"].get("type", "dual_property")

        # For dual_property relations, skip if the reverse was already created.
        # Notion auto-creates the synced property on the target DB, so adding
        # the relation from both sides would create duplicates.
        if relation_type == "dual_property":
            pair = tuple(sorted((new_db_id, new_relation_id)))
            if pair in created_dual_pairs:
                log.debug(f"  Skipping dual relation '{prop_name}' — reverse already created")
                continue
            created_dual_pairs.add(pair)

        # Notion API requires the type key to also appear as a nested object
        relation_config = {
            "database_id": new_relation_id,
            "type": relation_type,
            relation_type: {},
        }
        payload = {
            "properties": {
                prop_name: {
                    "relation": relation_config,
                }
            }
        }

        try:
            log.debug(f"  PATCH databases/{db_id} — adding '{prop_name}' → {new_relation_id[:8]}… ({relation_type})")
            _notion_request("PATCH", f"databases/{db_id}", json=payload)
            time.sleep(0.5)  # Give Notion time to propagate dual relations

            # Verify the property was actually added
            db_check = _notion_get(f"databases/{db_id}")
            actual_props = db_check.get("properties", {})
            if prop_name in actual_props and actual_props[prop_name].get("type") == "relation":
                relation_count += 1
                log.info(f"  ✓ Added and verified relation '{prop_name}' ({relation_type})")
            else:
                # Check if it was created under a different name (auto-generated)
                found = False
                for actual_name, actual_def in actual_props.items():
                    if actual_def.get("type") == "relation":
                        target = actual_def.get("relation", {}).get("database_id")
                        if target == new_relation_id:
                            relation_count += 1
                            log.info(f"  ✓ Relation created as '{actual_name}' (expected '{prop_name}')")
                            found = True
                            break
                if not found:
                    log.error(f"  ✗ Relation '{prop_name}' NOT found after PATCH!")
                    log.error(f"    Actual properties: {list(actual_props.keys())}")
        except Exception as e:
            log.error(f"  ✗ Failed to add relation '{prop_name}': {e}")
            import traceback
            log.debug(f"    {traceback.format_exc().splitlines()[-1]}")

    log.info(f"Added {relation_count} relation(s) to '{db_name}'")


def _integration_test_with_test_data(name_to_id: dict[str, str]) -> bool:
    """
    Create test data to verify all database relations work.

    Args:
        name_to_id: {db_name: new_db_id} mapping (e.g. {"Songs": "abc-123", ...})

    Returns True if successful, False otherwise. Cleans up test data after verification.
    """
    from notion._api import _notion_post, _notion_request, _notion_get
    import time

    try:
        songs_db_id = name_to_id.get("Songs")
        artists_db_id = name_to_id.get("Song Artists")
        playlists_db_id = name_to_id.get("Playlists")
        playlist_songs_db_id = name_to_id.get("Playlist Songs")

        if not all([songs_db_id, artists_db_id, playlists_db_id, playlist_songs_db_id]):
            log.warning("Could not find all database IDs for integration test")
            log.warning(f"  Available keys: {list(name_to_id.keys())}")
            return False

        # First, discover actual relation property names on the Playlist Songs DB
        # (dual_property synced relations may have auto-generated names)
        log.debug("  Discovering relation property names on Playlist Songs DB…")
        ps_db = _notion_get(f"databases/{playlist_songs_db_id}")
        ps_props = ps_db.get("properties", {})

        # Find relation properties by target database ID
        song_rel_name = None
        artist_rel_name = None
        playlist_rel_name = None
        for prop_name, prop_def in ps_props.items():
            if prop_def.get("type") != "relation":
                continue
            target_db = prop_def.get("relation", {}).get("database_id")
            if target_db == songs_db_id:
                song_rel_name = prop_name
            elif target_db == artists_db_id:
                artist_rel_name = prop_name
            elif target_db == playlists_db_id:
                playlist_rel_name = prop_name

        log.debug(f"  Relation properties found: Song={song_rel_name!r}, "
                   f"Artist={artist_rel_name!r}, Playlist={playlist_rel_name!r}")

        if not all([song_rel_name, artist_rel_name, playlist_rel_name]):
            log.warning("  ✗ Not all relation properties found on Playlist Songs DB")
            log.warning(f"    All properties: {list(ps_props.keys())}")
            return False

        # Also discover the Song→Artists relation name on Songs DB
        songs_db = _notion_get(f"databases/{songs_db_id}")
        songs_props = songs_db.get("properties", {})
        song_artist_rel_name = None
        for prop_name, prop_def in songs_props.items():
            if prop_def.get("type") == "relation":
                target_db = prop_def.get("relation", {}).get("database_id")
                if target_db == artists_db_id:
                    song_artist_rel_name = prop_name
                    break

        log.debug(f"  Songs DB artist relation: {song_artist_rel_name!r}")

        # Create test data
        log.debug("  Creating test artist…")
        artist_response = _notion_post("pages", {
            "parent": {"database_id": artists_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": "TEST ARTIST"}}]},
                "Spotify Artist ID": {"rich_text": [{"text": {"content": "test-artist-id"}}]},
            }
        })
        artist_id = artist_response["id"]
        time.sleep(0.35)

        log.debug("  Creating test song…")
        song_props = {
            "Name": {"title": [{"text": {"content": "TEST SONG"}}]},
            "Spotify URL": {"url": "https://open.spotify.com/track/test-song-id"},
        }
        if song_artist_rel_name:
            song_props[song_artist_rel_name] = {"relation": [{"id": artist_id}]}
        song_response = _notion_post("pages", {
            "parent": {"database_id": songs_db_id},
            "properties": song_props,
        })
        song_id = song_response["id"]
        time.sleep(0.35)

        log.debug("  Creating test playlist…")
        playlist_response = _notion_post("pages", {
            "parent": {"database_id": playlists_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": "TEST PLAYLIST"}}]},
                "Spotify URL": {"url": "https://open.spotify.com/playlist/test-playlist-id"},
            }
        })
        playlist_id = playlist_response["id"]
        time.sleep(0.35)

        log.debug("  Creating test playlist song (linking all relations)…")
        ps_response = _notion_post("pages", {
            "parent": {"database_id": playlist_songs_db_id},
            "properties": {
                "Name": {"title": [{"text": {"content": "TEST SONG IN PLAYLIST"}}]},
                song_rel_name: {"relation": [{"id": song_id}]},
                artist_rel_name: {"relation": [{"id": artist_id}]},
                playlist_rel_name: {"relation": [{"id": playlist_id}]},
            }
        })
        ps_id = ps_response["id"]
        time.sleep(0.35)

        # Verify relations
        log.debug("  Verifying relations…")
        ps_verify = _notion_get(f"pages/{ps_id}")
        props = ps_verify.get("properties", {})

        song_relation = props.get(song_rel_name, {}).get("relation", [])
        artist_relation = props.get(artist_rel_name, {}).get("relation", [])
        playlist_relation = props.get(playlist_rel_name, {}).get("relation", [])

        if song_relation and artist_relation and playlist_relation:
            log.info("  ✓ All relations verified!")
        else:
            log.warning("  ✗ Relations not properly created:")
            log.warning(f"    {song_rel_name}: {len(song_relation)} items")
            log.warning(f"    {artist_rel_name}: {len(artist_relation)} items")
            log.warning(f"    {playlist_rel_name}: {len(playlist_relation)} items")
            return False

        # Cleanup
        log.debug("  Cleaning up test data…")
        try:
            _notion_request("PATCH", f"pages/{ps_id}", json={"archived": True})
            _notion_request("PATCH", f"pages/{song_id}", json={"archived": True})
            _notion_request("PATCH", f"pages/{artist_id}", json={"archived": True})
            _notion_request("PATCH", f"pages/{playlist_id}", json={"archived": True})
            log.debug("  ✓ Test data cleaned up")
        except Exception as e:
            log.warning(f"  Could not clean up test data: {e}")

        return True

    except Exception as e:
        log.error(f"Integration test failed: {e}")
        import traceback
        for line in traceback.format_exc().splitlines():
            log.debug(f"  {line}")
        return False


def setup_databases_in_page(
    parent_page_id: str,
    db_names: Optional[list[str]] = None,
) -> dict[str, str]:
    """
    Create a MusicTunnel page and all databases inside it.

    Args:
        parent_page_id: Parent page ID where MusicTunnel will be created (required)
        db_names: List of database names to create (defaults to all)

    Returns:
        {db_name: new_db_id} mapping
    """
    from notion_config import DB_NAMES

    if not parent_page_id:
        raise ValueError("parent_page_id is required")

    if db_names is None:
        db_names = list(DB_NAMES.keys())

    log.info(f"Setting up {len(db_names)} database(s)…")

    # Step 0: Create MusicTunnel page inside the parent
    try:
        music_tunnel_page_id = create_page("MusicTunnel", parent_page_id)
    except Exception as e:
        log.error(f"Failed to create MusicTunnel page: {e}")
        raise

    # Step 1: Create all databases inside MusicTunnel page (without relations)
    id_mapping = {}  # Maps old DB IDs to new ones
    for db_name in db_names:
        try:
            new_db_id = create_database(db_name, music_tunnel_page_id)

            # Also store mapping from old ID to new (for relations)
            schema = load_schema(db_name)
            old_db_id = schema["database_id"]
            id_mapping[old_db_id] = new_db_id
        except Exception as e:
            log.error(f"Failed to create '{db_name}': {e}")
            raise

    # Step 2: Add relations (now that all databases exist)
    # Track dual_property pairs to avoid creating duplicate bidirectional relations
    log.info("Adding relations…")
    created_dual_pairs: set[tuple[str, str]] = set()
    for db_name in db_names:
        schema = load_schema(db_name)
        new_db_id = id_mapping[schema["database_id"]]
        try:
            add_relations_to_database(new_db_id, db_name, id_mapping, created_dual_pairs)
        except Exception as e:
            log.error(f"Failed to add relations to '{db_name}': {e}")
            raise

    # Step 3: Verify all properties were created
    log.info("Verifying properties in created databases…")
    verification_issues = []
    for db_name in db_names:
        schema = load_schema(db_name)
        new_db_id = id_mapping[schema["database_id"]]
        try:
            verify_database_properties(new_db_id, db_name, schema)
        except Exception as e:
            log.warning(f"Could not verify properties for '{db_name}': {e}")
            verification_issues.append((db_name, str(e)))

    if verification_issues:
        log.warning(f"Verification found {len(verification_issues)} issue(s):")
        for db_name, issue in verification_issues:
            log.warning(f"  {db_name}: {issue}")

    # Build name→new_id mapping for the integration test
    name_to_new_id = {}
    for db_name in db_names:
        schema = load_schema(db_name)
        old_db_id = schema["database_id"]
        name_to_new_id[db_name] = id_mapping[old_db_id]

    # Step 4: Integration test - verify relations work by creating test data
    log.info("Running integration test with test data…")
    try:
        test_result = _integration_test_with_test_data(name_to_new_id)
        if test_result:
            log.info("✓ Integration test passed - all relations working!")
        else:
            log.warning("⚠ Integration test failed - relations may not be working correctly")
    except Exception as e:
        log.warning(f"⚠ Integration test failed: {e}")
        log.warning("  Databases were created but relation test encountered issues")

    # Step 5: Return new IDs in the expected format
    results = {}
    for db_name in db_names:
        schema = load_schema(db_name)
        old_db_id = schema["database_id"]
        new_db_id = id_mapping[old_db_id]
        results[db_name] = new_db_id

    log.info(f"✓ Setup complete. Created {len(results)} database(s).")
    return results


def verify_database_properties(db_id: str, db_name: str, schema: dict) -> None:
    """
    Verify that all expected properties exist in the created database.

    Args:
        db_id: The database ID to check
        db_name: Name of the database (for logging)
        schema: The schema dict containing expected properties
    """
    from notion._api import _notion_get

    log.info(f"Verifying properties in '{db_name}'…")

    # Fetch actual database properties from Notion
    try:
        db_response = _notion_get(f"databases/{db_id}")
        actual_props = db_response.get("properties", {})
    except Exception as e:
        log.error(f"Could not fetch database {db_id}: {e}")
        raise

    # Expected property types (from schema) — skip formula/rollup (not created via API)
    # but DO check relations (they should exist after step 2)
    skip_types = {"formula", "rollup"}
    expected_props = {
        name: prop_def.get("type")
        for name, prop_def in schema["properties"].items()
        if prop_def.get("type") not in skip_types
    }

    # Check what's actually there
    found_props = set(actual_props.keys())
    expected_prop_names = set(expected_props.keys())

    # Log results
    created = found_props & expected_prop_names
    missing = expected_prop_names - found_props

    log.info(f"  Expected {len(expected_prop_names)} properties, found {len(created)}")

    if created:
        log.debug(f"  ✓ Created properties ({len(created)}):")
        for prop_name in sorted(created):
            prop_type = actual_props[prop_name].get("type")
            log.debug(f"    • {prop_name}: {prop_type}")

    # Log any extra properties (e.g., auto-created dual relation synced properties)
    extra = found_props - expected_prop_names - {"title"}
    if extra:
        log.debug(f"  Extra properties ({len(extra)}) — likely auto-created synced relations:")
        for prop_name in sorted(extra):
            prop_type = actual_props[prop_name].get("type")
            log.debug(f"    • {prop_name}: {prop_type}")

    if missing:
        # For relation properties, check if Notion auto-created them under a different name
        # (dual_property synced relations may be named differently)
        relation_missing = {n for n in missing if expected_props[n] == "relation"}
        non_relation_missing = missing - relation_missing

        if relation_missing:
            log.warning(f"  ⚠ Expected relation properties not found by name ({len(relation_missing)}):")
            for prop_name in sorted(relation_missing):
                log.warning(f"    • {prop_name} — may exist under auto-generated name")

        if non_relation_missing:
            log.error(f"  ✗ Missing non-relation properties ({len(non_relation_missing)}):")
            for prop_name in sorted(non_relation_missing):
                expected_type = expected_props[prop_name]
                log.error(f"    • {prop_name} ({expected_type})")
            raise ValueError(f"Missing {len(non_relation_missing)} properties in '{db_name}'")


def update_config_file(id_mapping: dict[str, str], parent_page_id: Optional[str] = None) -> None:
    """
    Update notion_config.py with new database IDs and optionally parent page ID.

    Args:
        id_mapping: {db_name: new_db_id} mapping
        parent_page_id: Parent page ID to save for future setup
    """
    config_path = os.path.join(BASE_DIR, "notion_config.py")

    # Map database names to config variable names
    var_names = {
        "Songs": "NOTION_SONGS_DB_ID",
        "Song Artists": "NOTION_ARTISTS_DB_ID",
        "Playlists": "NOTION_PLAYLISTS_DB_ID",
        "Playlist Songs": "NOTION_PLAYLIST_SONGS_DB_ID",
    }

    # Read current file
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Update each ID using regex to replace the entire assignment
    for db_name, new_db_id in id_mapping.items():
        if db_name not in var_names:
            log.warning(f"Unknown database name: {db_name}")
            continue

        var_name = var_names[db_name]
        # Match the pattern: VARIABLE_NAME = "..."
        pattern = f'{var_name} = "[^"]*"'
        replacement = f'{var_name} = "{new_db_id}"'
        content = re.sub(pattern, replacement, content)

    # Update parent page ID if provided
    if parent_page_id:
        pattern = r'NOTION_PARENT_PAGE_ID = [^\n]*'
        replacement = f'NOTION_PARENT_PAGE_ID = "{parent_page_id}"'
        content = re.sub(pattern, replacement, content)
        log.info(f"✓ Saved parent page ID for future setups")

    # Write back
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)

    log.info(f"✓ Updated notion_config.py with new database IDs")
