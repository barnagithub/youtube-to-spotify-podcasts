"""Step 5: remove items from the YouTube playlist whose episode 4_save_to_spotify.py already saved.

Only items in the current plan AND recorded as saved in apply_progress.json are removed, so
nothing leaves YouTube before it is on Spotify. Dry run unless --go. Each delete costs 50 units
of the Google project's daily quota; when it runs out the run stops, and the next run resumes.
Max ~80 lines.
"""
import argparse

from common import PROGRESS_FILE, delete_playlist_item, load_plan, save_json, youtube_client


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--go", action="store_true", help="actually remove the videos")
    args = parser.parse_args()

    _, _, plan, progress = load_plan()
    saved, removed = set(progress["saved"]), set(progress["removed"])
    pending = [m for m, _ in plan if m["playlist_item_id"] in saved and m["playlist_item_id"] not in removed]
    not_saved = sum(1 for m, _ in plan if m["playlist_item_id"] not in saved)
    print(f"{len(pending)} saved items to remove from YouTube; {len(removed)} already removed.")
    if not_saved:
        print(f"{not_saved} matched items aren't saved on Spotify yet and stay on YouTube. "
              "Run 4_save_to_spotify.py --go first.")
    if not args.go:
        for m in pending[:15]:
            print(f"  {m['title'][:90]}")
        print("\nDry run. Add --go to remove these from the YouTube playlist.")
        return

    yt = youtube_client()
    for n, m in enumerate(pending, 1):
        if delete_playlist_item(yt, m["playlist_item_id"]) == "quota":
            print(f"\nYouTube daily quota used up after {n - 1} removals. "
                  "Run this again tomorrow (quota resets at midnight Pacific time).")
            return
        progress["removed"].append(m["playlist_item_id"])
        save_json(PROGRESS_FILE, progress)
        print(f"Removed {n}/{len(pending)} from YouTube: {m['title']}")
    print("\nDone. What's left in the YouTube playlist is everything not moved to Spotify.")


if __name__ == "__main__":
    main()
