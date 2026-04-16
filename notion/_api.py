import traceback
import time as _t

import requests as http

from config import NOTION_API_KEY
from logger import log

_NOTION_VERSION = "2022-06-28"

MAX_RETRIES = 3


def _notion_request(method: str, path: str, **kwargs) -> dict:
    """Raw HTTP request against the Notion API with rate-limit and timeout retry."""
    url = f"https://api.notion.com/v1/{path}"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": _NOTION_VERSION,
    }
    if method in ("POST", "PATCH"):
        headers["Content-Type"] = "application/json"

    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = getattr(http, method.lower())(url, headers=headers, timeout=15, **kwargs)
        except (http.exceptions.Timeout, http.exceptions.ConnectionError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = 2 ** (attempt - 1)  # 1s, 2s, 4s, ...
                log.warning("Notion %s %s timed out — retrying in %ds (attempt %d/%d)",
                            method, path, delay, attempt, MAX_RETRIES)
                _t.sleep(delay)
                continue
            log.error("Notion %s %s failed after %d attempts: %s",
                      method, path, MAX_RETRIES, exc)
            raise

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            log.warning("Notion rate limit hit on %s %s — waiting %ds (attempt %d/%d)",
                        method, path, retry_after, attempt, MAX_RETRIES)
            _t.sleep(retry_after)
            continue
        if not resp.ok:
            try:
                body = resp.json()
                log.error("Notion API error %d on %s %s: %s — %s",
                          resp.status_code, method, path,
                          body.get("code", ""), body.get("message", resp.text[:200]))
            except Exception:
                log.error("Notion API error %d on %s %s: %s",
                          resp.status_code, method, path, resp.text[:200])
            resp.raise_for_status()
        return resp.json()

    # Exhausted retries on rate-limit
    if last_exc:
        raise last_exc
    resp.raise_for_status()
    return {}


def _notion_post(path: str, body: dict) -> dict:
    return _notion_request("POST", path, json=body)


def _notion_get(path: str) -> dict:
    return _notion_request("GET", path)
