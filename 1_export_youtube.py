"""Step 1: export the YouTube playlist to youtube_items.json. Read-only. Max ~120 lines.

Quota: playlistItems.list and videos.list cost 1 unit per page of 50, so a full export is cheap.
"""
import re
import sys

from googleapiclient.errors import HttpError

from common import (ITEMS_FILE, QUOTA_MESSAGE, fetch_playlist_items, load_settings, save_json,
                    youtube_client)


def parse_iso_duration(value):
    """'PT1H02M03S' -> 3723 seconds. Returns None for missing or live/unknown durations."""
    if not value:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not m:
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def add_durations(yt, items):
    ids = [it["video_id"] for it in items if not it["unavailable"]]
    durations = {}
    for i in range(0, len(ids), 50):
        try:
            resp = yt.videos().list(part="contentDetails", id=",".join(ids[i:i + 50])).execute()
        except HttpError as e:
            if e.resp.status == 403 and "quota" in str(e).lower():
                sys.exit(QUOTA_MESSAGE)
            raise
        for v in resp["items"]:
            durations[v["id"]] = parse_iso_duration(v["contentDetails"].get("duration"))
    for it in items:
        it["duration_s"] = durations.get(it["video_id"])


def main():
    settings = load_settings()
    yt = youtube_client()
    items = fetch_playlist_items(yt, settings["youtube_playlist_id"])
    add_durations(yt, items)
    save_json(ITEMS_FILE, items)
    unavailable = sum(it["unavailable"] for it in items)
    channels = len({it["channel"] for it in items if not it["unavailable"]})
    print(f"Exported {len(items)} items from {channels} channels to {ITEMS_FILE.name} "
          f"({unavailable} deleted/private, skipped by matching).")


if __name__ == "__main__":
    main()
