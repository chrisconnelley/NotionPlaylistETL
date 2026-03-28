import csv
import re
from datetime import datetime

from logger import log


def export_to_csv(tracks: list[dict], output_path: str) -> None:
    fieldnames = ["Track Name", "Artist(s)", "Album", "Release Date",
                  "Duration", "Spotify URL", "Added At", "Added By"]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in tracks:
            writer.writerow({k: t.get(k, "") for k in fieldnames})
    log.info("Exported %d tracks to %s", len(tracks), output_path)


def default_filename(playlist_name: str) -> str:
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", playlist_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe_name}_{timestamp}.csv"
