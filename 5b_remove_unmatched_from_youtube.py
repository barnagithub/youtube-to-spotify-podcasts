"""Step 5b (alternative to 5): remove items from the YouTube playlist that are NOT on Spotify.

For a copy of a playlist (e.g. Watch Later copied into a new one): after matching and review,
this leaves only the videos that have a Spotify episode, which you then handle by hand.
Removed: items you marked "Not on Spotify", and "none" items you didn't decide on.
Kept: anything moving to Spotify, "review" items you haven't decided yet, "pending" items
2_match.py hasn't reached, and deleted/private videos. Dry run unless --go. Each delete costs
50 units of the daily quota; when it runs out the run stops, and the next run resumes.
Max ~80 lines.
"""
import argparse

from common import PROGRESS_FILE, delete_playlist_item, load_plan, save_json, youtube_client


def unmatched_items(matches, decisions):
    """Items with no Spotify episode: your "Not on Spotify" decision, or no decision and "none"."""
    out = []
    for m in matches:
        decision = decisions.get(m["playlist_item_id"])
        if decision is not None:
            if decision.get("episode_uri") is None:
                out.append(m)
        elif m["status"] == "none":
            out.append(m)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--go", action="store_true", help="actually remove the videos")
    args = parser.parse_args()

    matches, decisions, _, progress = load_plan()
    removed = set(progress.setdefault("removed_unmatched", []))
    pending = [m for m in unmatched_items(matches, decisions) if m["playlist_item_id"] not in removed]
    undecided = sum(1 for m in matches if m["playlist_item_id"] not in decisions
                    and m["status"] in ("review", "pending"))
    print(f"{len(pending)} items without a Spotify match to remove from YouTube; "
          f"{len(removed)} already removed.")
    if undecided:
        print(f"{undecided} items are still 'review' (undecided) or 'pending' (not matched yet) and "
              "stay on YouTube. Finish 2_match.py and the review page, then run this again.")
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
        progress["removed_unmatched"].append(m["playlist_item_id"])
        save_json(PROGRESS_FILE, progress)
        print(f"Removed {n}/{len(pending)} from YouTube: {m['title']}")
    print("\nDone. What's left in the YouTube playlist is what's on Spotify (plus anything undecided).")


if __name__ == "__main__":
    main()
