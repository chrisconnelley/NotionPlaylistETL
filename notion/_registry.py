import json
import os
import time
import traceback

from config import NOTION_SYNC_DIR
from logger import log
from notion._api import _notion_get


def load_registry(name: str) -> dict:
    """Load a sync registry JSON file. Returns {} if missing."""
    path = os.path.join(NOTION_SYNC_DIR, f"{name}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        log.warning("Could not load registry %r:\n%s", name, traceback.format_exc())
        return {}


def save_registry(name: str, data: dict) -> None:
    """Save a sync registry to JSON."""
    os.makedirs(NOTION_SYNC_DIR, exist_ok=True)
    path = os.path.join(NOTION_SYNC_DIR, f"{name}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        log.warning("Could not save registry %r:\n%s", name, traceback.format_exc())


def validate_registry(name: str, progress_cb=None,
                      keys: "set | None" = None) -> "tuple[int, int]":
    """
    Check page IDs stored in a registry against Notion.
    Removes entries whose pages have been deleted or archived.
    progress_cb(current, total, entry_name): called before each Notion request.
    keys: if provided, only validate entries whose registry key is in this set.
    Returns (removed_count, remaining_count).
    """
    registry = load_registry(name)
    if not registry:
        log.info("validate_registry(%r): empty, nothing to check", name)
        return 0, 0

    entries = [(k, v) for k, v in registry.items() if keys is None or k in keys]
    total = len(entries)
    log.info("validate_registry(%r): checking %d/%d entries", name, total, len(registry))

    to_remove = []
    for i, (key, entry) in enumerate(entries):
        entry_name = entry.get("name", key)
        if progress_cb:
            progress_cb(i, total, entry_name)
        log.debug("Checking %s entry %d/%d: %r", name, i + 1, total, entry_name)

        page_id = entry.get("notion_page_id")
        if not page_id:
            log.info("  %r has no page ID — removing", entry_name)
            to_remove.append(key)
            continue

        time.sleep(0.35)
        try:
            result = _notion_get(f"pages/{page_id}")
            if result.get("archived") or result.get("in_trash"):
                log.info("  %r is archived/deleted in Notion — removing", entry_name)
                to_remove.append(key)
            else:
                log.debug("  %r OK", entry_name)
        except Exception:
            log.info("  %r not found in Notion (404) — removing", entry_name)
            to_remove.append(key)

    for key in to_remove:
        del registry[key]

    if to_remove:
        save_registry(name, registry)

    log.info("validate_registry(%r): removed %d, kept %d", name, len(to_remove), len(registry))
    return len(to_remove), len(registry)
