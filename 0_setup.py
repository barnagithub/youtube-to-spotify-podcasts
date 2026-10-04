"""Step 0: create or update settings.json and check the rest of the local setup. Max ~100 lines.

Asks for the YouTube playlist and the Spotify app's client ID (Enter keeps the current value),
or takes them as --playlist / --spotify-client-id. settings.json and the Google client file stay
on this machine: .gitignore keeps them out of the repository.
"""
import argparse
import importlib.util
import re

from common import (GOOGLE_CLIENT_SECRET_FILE, SETTINGS_FILE, SPOTIFY_REDIRECT_URI, load_json,
                    parse_playlist_id, save_json)

REQUIRED_MODULES = ("spotipy", "googleapiclient", "google_auth_oauthlib")


def build_settings(current, playlist=None, client_id=None):
    """New settings from the current ones plus any new values; blank input keeps the old value."""
    settings = {k: v for k, v in current.items() if not str(v).startswith("PASTE_")}
    if playlist and playlist.strip():
        settings["youtube_playlist_id"] = parse_playlist_id(playlist)
    if client_id and client_id.strip():
        settings["spotify_client_id"] = client_id.strip()
    return settings


def ask(label, current):
    shown = f" [{current}]" if current else ""
    return input(f"{label}{shown}: ").strip()


def check_environment():
    """Warnings for anything later steps will trip over; empty when all is in place."""
    problems = []
    missing = [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    if missing:
        problems.append(f"Python packages missing ({', '.join(missing)}). Run this with the project's "
                        "Python (.venv\\Scripts\\python 0_setup.py) after installing requirements.txt.")
    if not GOOGLE_CLIENT_SECRET_FILE.exists():
        problems.append(f"{GOOGLE_CLIENT_SECRET_FILE.name} not found. Create a Desktop app OAuth client in "
                        "Google Cloud Console and save its JSON here (README, setup step 3).")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--playlist", help="YouTube playlist URL or ID")
    parser.add_argument("--spotify-client-id", help="Client ID from the Spotify Developer Dashboard")
    args = parser.parse_args()

    current = load_json(SETTINGS_FILE, {})
    interactive = args.playlist is None and args.spotify_client_id is None
    if interactive:
        print("Press Enter to keep a current value.\n")
        playlist = ask("YouTube playlist (URL or ID)", current.get("youtube_playlist_id"))
        client_id = ask(f"Spotify client ID (app redirect URI: {SPOTIFY_REDIRECT_URI})",
                        current.get("spotify_client_id"))
    else:
        playlist, client_id = args.playlist, args.spotify_client_id

    settings = build_settings(current, playlist, client_id)
    missing = [k for k in ("youtube_playlist_id", "spotify_client_id") if not settings.get(k)]
    if missing:
        raise SystemExit(f"Nothing saved: {', '.join(missing)} still needed.")
    if not re.fullmatch(r"[0-9a-f]{32}", settings["spotify_client_id"]):
        print("Note: that client ID doesn't look like the usual 32 letters and digits; check it was "
              "copied in full.")
    save_json(SETTINGS_FILE, settings)
    print(f"\nSaved {SETTINGS_FILE.name}: playlist {settings['youtube_playlist_id']}.")

    problems = check_environment()
    for p in problems:
        print(f"- {p}")
    print("\nNext: 1_export_youtube.py" if not problems else "\nFix the above, then run 1_export_youtube.py.")


if __name__ == "__main__":
    main()
