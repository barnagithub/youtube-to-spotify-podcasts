"""Step 4: save accepted matches to Spotify "Your Episodes". Run before 5_remove_from_youtube.py.

Accepted = items you confirmed on the review page, plus "auto" items you did not override.
Dry run unless --go. Saved items are recorded in apply_progress.json and skipped next time.
Max ~70 lines.
"""
import argparse

from common import PROGRESS_FILE, load_plan, load_settings, save_json, spotify_client

SPOTIFY_URIS_PER_REQUEST = 40  # documented maximum for PUT /me/library


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--go", action="store_true", help="actually save the episodes")
    args = parser.parse_args()

    matches, decisions, plan, progress = load_plan()
    pending = [(m, uri) for m, uri in plan if m["playlist_item_id"] not in progress["saved"]]
    undecided = sum(1 for m in matches if m["status"] == "review" and m["playlist_item_id"] not in decisions)
    print(f"{len(plan)} items are matched to Spotify; {len(plan) - len(pending)} already saved, "
          f"{len(pending)} to save.")
    if undecided:
        print(f"{undecided} 'review' items have no decision yet and will stay on YouTube.")
    not_matched = sum(1 for m in matches if m["status"] == "pending")
    if not_matched:
        print(f"{not_matched} items aren't matched yet (2_match.py stopped early); they stay on "
              "YouTube until a later 2_match.py run matches them.")
    if not args.go:
        for m, uri in pending[:15]:
            print(f"  {m['title'][:70]:70}  ->  {uri}")
        print("\nDry run. Add --go to save these on Spotify.")
        return
    if not pending:
        return

    sp = spotify_client(load_settings())
    for i in range(0, len(pending), SPOTIFY_URIS_PER_REQUEST):
        chunk = pending[i:i + SPOTIFY_URIS_PER_REQUEST]
        sp.current_user_saved_episodes_add([uri for _, uri in chunk])
        progress["saved"] += [m["playlist_item_id"] for m, _ in chunk]
        save_json(PROGRESS_FILE, progress)
        print(f"Saved {i + len(chunk)}/{len(pending)} episodes on Spotify")
    print("\nDone. Next: 5_remove_from_youtube.py")


if __name__ == "__main__":
    main()
