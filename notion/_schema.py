import json
import os
import re
import traceback

from notion_client import Client

from config import NOTION_API_KEY, BASE_DIR
from logger import log
from notion._api import _notion_get

SCHEMA_DIR = os.path.join(BASE_DIR, "notion_schema")


def get_notion_client() -> Client:
    return Client(auth=NOTION_API_KEY)


def fetch_databases() -> list[dict]:
    """Return all Notion databases the integration has access to."""
    if not NOTION_API_KEY:
        raise ValueError("NOTION_API_KEY is not set in .env")

    client = get_notion_client()
    databases = []
    cursor = None

    while True:
        kwargs = {}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.search(**kwargs)

        for item in response.get("results", []):
            if item.get("object") not in ("database", "data_source"):
                continue
            title_parts = item.get("title", [])
            name = "".join(t.get("plain_text", "") for t in title_parts).strip()
            databases.append({"id": item["id"], "name": name or "<Untitled>"})

        if response.get("has_more"):
            cursor = response.get("next_cursor")
        else:
            break

    log.info("Found %d Notion database(s)", len(databases))
    return databases


def _resolve_database_ids() -> dict:
    """Return a mapping of data_source_id → database_id."""
    if not NOTION_API_KEY:
        raise ValueError("NOTION_API_KEY is not set in .env")

    mapping = {}
    client = get_notion_client()
    cursor = None
    while True:
        kwargs = {}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.search(**kwargs)
        for item in response.get("results", []):
            parent = item.get("parent", {})
            if parent.get("type") == "data_source_id":
                ds_id = parent.get("data_source_id")
                db_id = parent.get("database_id")
                if ds_id and db_id:
                    mapping[ds_id] = db_id
        if response.get("has_more"):
            cursor = response.get("next_cursor")
        else:
            break
    return mapping


def snapshot_schema() -> list:
    """Fetch and save full schema for every accessible Notion database."""
    if not NOTION_API_KEY:
        raise ValueError("NOTION_API_KEY is not set in .env")

    os.makedirs(SCHEMA_DIR, exist_ok=True)
    databases = fetch_databases()
    id_map = _resolve_database_ids()

    saved = []
    for db in databases:
        database_id = id_map.get(db["id"], db["id"])
        try:
            detail = _notion_get(f"databases/{database_id}")
            title_parts = detail.get("title", [])
            name = "".join(t.get("plain_text", "") for t in title_parts).strip() or db["name"]

            schema = {
                "name": name,
                "data_source_id": db["id"],
                "database_id": database_id,
                "description": "".join(
                    t.get("plain_text", "")
                    for block in detail.get("description", [])
                    for t in ([block] if isinstance(block, dict) else [])
                ),
                "properties": detail.get("properties", {}),
            }

            safe_name = re.sub(r'[\\/*?:"<>| ]', "_", name).lower()
            path = os.path.join(SCHEMA_DIR, f"{safe_name}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(schema, f, ensure_ascii=False, indent=2)
            saved.append(name)
            log.debug("Saved schema: %s → %s", name, path)

        except Exception:
            log.warning("Could not snapshot schema for %r:\n%s",
                        db["name"], traceback.format_exc())

    log.info("Snapshotted %d database schema(s) to %s", len(saved), SCHEMA_DIR)
    return saved
