import json
import os

from config import BASE_DIR

_SETTINGS_PATH = os.path.join(BASE_DIR, "app_settings.json")

_DEFAULTS = {
    "auto_start": False,
    "close_on_complete": False,
    "console_log_level": "DEBUG",
}


def load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            stored = json.load(f)
        return {**_DEFAULTS, **stored}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULTS)


def save_settings(settings: dict) -> None:
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
