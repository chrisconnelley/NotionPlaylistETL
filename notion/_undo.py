import time

from logger import log
from notion._api import _notion_request


def undo_export(manifest: dict, progress_cb=None, stop_event=None) -> dict:
    """
    Archive all Notion pages listed in the undo manifest.
    Order: playlist_songs → songs → artists → playlist (reverse of creation).
    Returns {"archived": int, "failed": int, "errors": list[str]}.
    """
    ordered_keys = ["playlist_songs", "songs", "artists", "playlist"]
    all_ids = []
    for key in ordered_keys:
        all_ids.extend((key, pid) for pid in manifest.get(key, []))

    total = len(all_ids)
    result = {"archived": 0, "failed": 0, "errors": []}

    for i, (category, page_id) in enumerate(all_ids):
        if stop_event and stop_event.is_set():
            break
        if progress_cb:
            progress_cb(i, total, category)
        try:
            time.sleep(0.35)
            _notion_request("PATCH", f"pages/{page_id}",
                            json={"archived": True})
            result["archived"] += 1
            log.info("Archived %s page %s", category, page_id[:12])
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append(f"{category} {page_id[:12]}: {exc}")
            log.error("Failed to archive %s page %s: %s",
                      category, page_id[:12], exc)

    if progress_cb:
        progress_cb(total, total, "done")
    return result
