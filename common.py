"""Shared paths, settings, API clients and the move-to-Spotify plan for every step.

Every file the tool reads or writes lives in this folder. Max ~270 lines.
"""
import json
import os
import re
import sys
from pathlib import Path

# Windows consoles and pipes may use a code page (e.g. cp1250) that can't show every character in
# video titles and channel names; print a replacement character instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / "settings.json"
GOOGLE_CLIENT_SECRET_FILE = ROOT / "client_secret.json"
GOOGLE_TOKEN_FILE = ROOT / "youtube_token.json"
SPOTIFY_TOKEN_FILE = ROOT / "spotify_token.json"

ITEMS_FILE = ROOT / "youtube_items.json"
SHOWS_FILE = ROOT / "shows.json"
EPISODE_CACHE_FILE = ROOT / "show_episodes.json"
SEARCH_CACHE_FILE = ROOT / "episode_search.json"
MATCHES_FILE = ROOT / "matches.json"
DECISIONS_FILE = ROOT / "decisions.json"
PROGRESS_FILE = ROOT / "apply_progress.json"  # {"saved": [...], "removed": [...]} playlist item IDs

# Spotify no longer accepts "localhost" redirect URIs; it must be an explicit loopback address.
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPES = "user-library-read user-library-modify"
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube"]
EPISODE_URI = re.compile(r"^spotify:episode:[A-Za-z0-9]{22}$")

# Spotify doesn't publish its rate limit for development-mode apps, and going over it earns a
# penalty of hours that grows when repeated. Calls are spaced at least this far apart. A 1 s gap
# still drew a penalty after roughly 900 requests in one run, so this is a guess, not a known-safe value.
SPOTIFY_MIN_INTERVAL_S = 3.0
# A 429 asking to wait up to this long is waited out; a longer one stops the run instead of
# hanging (spotipy on its own would sleep for the whole penalty).
SPOTIFY_MAX_WAIT_S = 120


def load_json(path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    # Write to a sibling temp file first so an interrupted run never leaves half a file behind.
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def parse_playlist_id(text):
    """Accept a bare playlist ID, an ID with share-link extras ("...&si=..."), or any YouTube URL
    containing list=<id>. Whether the ID exists is left to YouTube (see fetch_playlist_items)."""
    m = re.search(r"[?&]list=([A-Za-z0-9_-]+)", text)
    pid = m.group(1) if m else text.strip().split("&")[0].split("?")[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
        sys.exit(f"'{text}' isn't a playlist ID or playlist link.")
    return pid


def load_settings():
    settings = load_json(SETTINGS_FILE, {})
    missing = [k for k in ("youtube_playlist_id", "spotify_client_id")
               if not settings.get(k) or settings[k].startswith("PASTE_")]
    if missing:
        sys.exit(f"{SETTINGS_FILE.name} is missing {', '.join(missing)}. Run 0_setup.py first.")
    settings["youtube_playlist_id"] = parse_playlist_id(settings["youtube_playlist_id"])
    return settings


def require(path, made_by):
    if not path.exists():
        sys.exit(f"{path.name} not found. Run {made_by} first.")
    return load_json(path)


def resolve(matches, decisions):
    """[(match, episode_uri)] for every item that should move to Spotify: your decision wins,
    otherwise an "auto" match takes its top candidate. review.html shows the same rule."""
    plan = []
    for m in matches:
        decision = decisions.get(m["playlist_item_id"])
        if decision is not None:
            uri = decision.get("episode_uri")
        elif m["status"] == "auto":
            uri = m["candidates"][0]["uri"]
        else:
            uri = None
        if uri:
            if not EPISODE_URI.match(uri):
                sys.exit(f"Not a Spotify episode URI for '{m['title']}': {uri}")
            plan.append((m, uri))
    return plan


def load_plan():
    """The resolved plan plus progress shared by 4_save_to_spotify.py and 5_remove_from_youtube.py."""
    matches = require(MATCHES_FILE, "2_match.py")
    decisions = load_json(DECISIONS_FILE, {})
    progress = load_json(PROGRESS_FILE, {"saved": [], "removed": []})
    return matches, decisions, resolve(matches, decisions), progress


def youtube_client():
    """YouTube Data API client authorised as the user (needed to read unlisted items and delete)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if GOOGLE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_FILE), YOUTUBE_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not GOOGLE_CLIENT_SECRET_FILE.exists():
            sys.exit(f"{GOOGLE_CLIENT_SECRET_FILE.name} not found. Download the OAuth "
                     "desktop-app client from Google Cloud Console into this folder.")
        flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CLIENT_SECRET_FILE), YOUTUBE_SCOPES)
        creds = flow.run_local_server(port=0)
    GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


QUOTA_MESSAGE = ("YouTube's daily API quota for this Google project is used up. It resets at midnight "
                 "Pacific time; run this again after that.")

# YouTube shows these placeholder titles for videos that were deleted or made private.
UNAVAILABLE_TITLES = {"Deleted video", "Private video"}


def fetch_playlist_items(yt, playlist_id):
    """Every item of a playlist, in order. Costs 1 quota unit per page of 50."""
    from googleapiclient.errors import HttpError

    items, page_token = [], None
    while True:
        try:
            resp = yt.playlistItems().list(
                part="snippet,contentDetails,status", playlistId=playlist_id,
                maxResults=50, pageToken=page_token,
            ).execute()
        except HttpError as e:
            if e.resp.status == 404:
                sys.exit(f"YouTube can't find playlist '{playlist_id}'. Check the ID was copied in "
                         "full; a private playlist must belong to the account you logged in with.")
            if e.resp.status == 403 and "quota" in str(e).lower():
                sys.exit(QUOTA_MESSAGE)
            raise
        for it in resp["items"]:
            sn = it["snippet"]
            items.append({
                "playlist_item_id": it["id"],
                "video_id": it["contentDetails"]["videoId"],
                "position": sn.get("position"),
                "title": sn["title"],
                "channel": sn.get("videoOwnerChannelTitle"),
                "published": it["contentDetails"].get("videoPublishedAt"),
                "unavailable": sn["title"] in UNAVAILABLE_TITLES or not sn.get("videoOwnerChannelTitle"),
            })
        page_token = resp.get("nextPageToken")
        if not page_token:
            return items


def delete_playlist_item(yt, playlist_item_id):
    """Remove one item. Returns "removed", "gone" (already removed) or "quota" (daily quota used
    up; each delete costs 50 units, and the quota resets at midnight Pacific time)."""
    from googleapiclient.errors import HttpError

    try:
        yt.playlistItems().delete(id=playlist_item_id).execute()
        return "removed"
    except HttpError as e:
        if e.resp.status == 404:
            return "gone"
        if e.resp.status == 403 and "quota" in str(e).lower():
            return "quota"
        raise


class SpotifyStop(SystemExit):
    """The run must stop: Spotify imposed a long penalty, or the per-run request limit was reached.
    A SystemExit, so a script that doesn't catch it still exits with just the message."""


def spotify_client(settings, max_requests=None):
    """Spotify client using PKCE, so only the (non-secret) client ID is needed. Every call is
    paced and rate-limit answers are handled by PacedSpotify below; with max_requests, the
    request after that many raises SpotifyStop instead of being sent."""
    from spotipy.cache_handler import CacheFileHandler
    from spotipy.oauth2 import SpotifyPKCE

    auth = SpotifyPKCE(
        client_id=settings["spotify_client_id"],
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPES,
        cache_handler=CacheFileHandler(cache_path=str(SPOTIFY_TOKEN_FILE)),
    )
    # 429 is left out of the automatic retries so PacedSpotify sees it and can read Retry-After.
    sp = _paced_spotify_class()(auth_manager=auth, retries=5, status_retries=5, backoff_factor=1,
                                status_forcelist=(500, 502, 503, 504))
    sp.max_requests = max_requests
    return sp


def _paced_spotify_class():
    import time
    from datetime import datetime, timedelta

    import spotipy
    from spotipy.exceptions import SpotifyException

    class PacedSpotify(spotipy.Spotify):
        # Overrides two private spotipy methods (stable in the pinned 2.26.0); every public call
        # goes through _internal_call.
        _last_call = 0.0
        max_requests = None
        requests_sent = 0

        def _build_session(self):
            super()._build_session()
            # urllib3 retries any 429 that carries Retry-After, whatever status_forcelist says,
            # sleeping for the full value. Turn that off so the 429 reaches _internal_call.
            for adapter in self._session.adapters.values():
                adapter.max_retries.respect_retry_after_header = False

        def _internal_call(self, method, url, payload, params):
            while True:
                if self.max_requests is not None and self.requests_sent >= self.max_requests:
                    raise SpotifyStop(f"\nStopped after {self.requests_sent} Spotify requests (the per-run "
                                      "limit). Everything fetched so far is saved; run this again in a few "
                                      "hours to continue.")
                gap = self._last_call + SPOTIFY_MIN_INTERVAL_S - time.monotonic()
                if gap > 0:
                    time.sleep(gap)
                self.requests_sent += 1
                try:
                    return super()._internal_call(method, url, payload, params)
                except SpotifyException as e:
                    if e.http_status != 429:
                        raise
                    wait = int((e.headers or {}).get("Retry-After", SPOTIFY_MAX_WAIT_S))
                    if wait > SPOTIFY_MAX_WAIT_S:
                        until = datetime.now() + timedelta(seconds=wait)
                        raise SpotifyStop(f"\nSpotify rate limit: it asks to wait {wait // 3600}h "
                                          f"{wait % 3600 // 60}m, until about {until:%Y-%m-%d %H:%M}. "
                                          "Everything fetched so far is saved; run this again after that.")
                    print(f"  (Spotify rate limit, waiting {wait}s)")
                    time.sleep(wait + 1)
                finally:
                    self._last_call = time.monotonic()

    return PacedSpotify
