"""Remove videos from a SOURCE playlist that are already in a TARGET playlist. Separate from steps 1-5.

Compares by video ID. Lists the overlap (title + link) on screen and in playlist_overlap.csv;
with --go removes those items from SOURCE. TARGET is only read. Both playlists are read live
each run, so a run stopped by the YouTube quota simply resumes next time. Max ~90 lines.
"""
import argparse
import csv
import sys

from common import ROOT, delete_playlist_item, fetch_playlist_items, parse_playlist_id, youtube_client

OVERLAP_FILE = ROOT / "playlist_overlap.csv"


def overlap(source_items, target_items):
    """Source items whose video is also in the target (every copy, if the source has duplicates)."""
    target_ids = {it["video_id"] for it in target_items}
    return [it for it in source_items if it["video_id"] in target_ids]


def video_url(item):
    return f"https://www.youtube.com/watch?v={item['video_id']}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="playlist to remove from (ID or URL)")
    parser.add_argument("target", help="playlist to compare against, read only (ID or URL)")
    parser.add_argument("--go", action="store_true", help="actually remove the overlap from SOURCE")
    args = parser.parse_args()
    source, target = parse_playlist_id(args.source), parse_playlist_id(args.target)
    if source == target:
        sys.exit("Source and target are the same playlist.")

    yt = youtube_client()
    source_items = fetch_playlist_items(yt, source)
    target_items = fetch_playlist_items(yt, target)
    found = overlap(source_items, target_items)

    with open(OVERLAP_FILE, "w", newline="", encoding="utf-8-sig") as f:  # -sig so Excel reads accents
        writer = csv.writer(f)
        writer.writerow(["title", "channel", "url", "playlist_item_id"])
        writer.writerows([it["title"], it["channel"] or "", video_url(it), it["playlist_item_id"]] for it in found)

    print(f"Source: {len(source_items)} items. Target: {len(target_items)} items.")
    print(f"{len(found)} source items are also in the target (list saved to {OVERLAP_FILE.name}):\n")
    for it in found:
        print(f"  {it['title'][:80]:80}  {video_url(it)}")
    if not args.go:
        print("\nDry run. Add --go to remove these from the source playlist.")
        return

    for n, it in enumerate(found, 1):
        if delete_playlist_item(yt, it["playlist_item_id"]) == "quota":
            print(f"\nYouTube daily quota used up after {n - 1} removals. "
                  "Run this again tomorrow (quota resets at midnight Pacific time).")
            return
        print(f"Removed {n}/{len(found)}: {it['title']}")
    print("\nDone.")


if __name__ == "__main__":
    main()
