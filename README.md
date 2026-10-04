<!-- Setup and usage for people running the tool. Max ~150 lines. -->
# YouTube podcast playlist → Spotify

Finds which videos in a YouTube playlist are also available as Spotify podcast episodes, saves
those to Spotify "Your Episodes", and removes them from the YouTube playlist. What's left in the
playlist is everything that isn't on Spotify.

Nothing changes on YouTube or Spotify until you run steps 4 and 5 with `--go`.

## Setup (once)

### 1. Python environment
```bash
python -m venv .venv
```
```bash
.venv\Scripts\python -m pip install -r requirements.txt
```
In VS Code, open this folder (File → Open Folder) and `.vscode/settings.json` points the Python
extension at `.venv`, so the Run button and new terminals use it.

### 2. Spotify app
The account that owns the app needs Spotify Premium (Spotify's rule for personal developer apps).

1. In the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create an app.
2. Redirect URI: `http://127.0.0.1:8888/callback`. Spotify rejects `localhost` here.
3. Under "Which API/SDKs are you planning to use", tick **Web API**.
4. Copy the **Client ID** for `0_setup.py`. The Client Secret isn't used (the scripts log in with PKCE).

### 3. Google / YouTube app
1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable
   **YouTube Data API v3**.
2. **Google Auth Platform → Audience**: user type External, publishing status **Testing**.
   Under **Test users**, add the Google account that owns the playlist. Skipping this gives
   `Error 403: access_denied` at login.
3. **Google Auth Platform → Clients**: create a client of type **Desktop app**. A Web
   application client gives `Error 400: redirect_uri_mismatch`.
4. Download the client JSON and save it in this folder as `client_secret.json`.

### 4. Your settings
```bash
.venv\Scripts\python 0_setup.py
```
It asks for the YouTube playlist (URL or ID) and the Spotify client ID, writes `settings.json`,
and checks the packages and `client_secret.json`. Run it again to switch playlists. Your own
files (settings, credentials, tokens, everything the scripts write) are in `.gitignore`.

### First login
The first script that needs each service opens a browser to log in:
- **Google:** pick the account you added as a test user. If your channel is a Brand Account, pick
  the channel that owns the playlist. On "Google hasn't verified this app", click **Continue**.
- **Spotify:** approve the app.

Logins are saved as `youtube_token.json` and `spotify_token.json`. While the Google app is in
Testing, Google expires its login after about a week; the next run just asks you to log in again.

## Running it, in order

| Step | Command | What it does |
|---|---|---|
| 0 | `.venv\Scripts\python 0_setup.py` | Creates or updates `settings.json` (once, or to switch playlists). |
| 1 | `.venv\Scripts\python 1_export_youtube.py` | Reads the playlist into `youtube_items.json` (titles, channels, lengths, dates). |
| 2 | `.venv\Scripts\python 2_match.py` | Finds each channel's Spotify show and scores every video against its episodes. Writes `matches.json`. Changes nothing. |
| 3 | `.venv\Scripts\python 3_review.py` | Opens the review page in your browser. Each click is saved to `decisions.json`. Ctrl+C to stop. |
| 4 | `.venv\Scripts\python 4_save_to_spotify.py --go` | Saves accepted episodes to Spotify "Your Episodes". |
| 5 | `.venv\Scripts\python 5_remove_from_youtube.py --go` | Removes those videos from the YouTube playlist. |

Run steps 4 and 5 without `--go` first to see what they would do.

**What counts as accepted:** your decision on the review page wins. With no decision, an item
`2_match.py` rated "auto" moves; "review" and "none" items stay on YouTube.

**Order is enforced:** step 5 only removes a video once step 4 recorded its episode as saved
(`apply_progress.json`). Both steps skip work already done, so re-running them is safe.

## The review page
Tabs for items to review, auto-matched, no match, and ones you've decided. Each item shows up to
a few Spotify candidates with how well the title, length, release date and episode number agree.
Per item you can pick a candidate, paste a Spotify episode link you found yourself (the Search
Spotify / Search Google links help), mark it "Not on Spotify", or undo your decision.

## Fixing matches
- **Wrong or missing show for a channel:** edit `shows.json`. Set that channel's `show_id` (from
  the show's Spotify URL, or from its `candidates` list) and run `2_match.py` again. Entries you
  edit are kept on later runs.
- **Stale episode lists:** `2_match.py --refresh` refetches episodes instead of using
  `show_episodes.json`.
- **Too strict / too loose:** the thresholds are at the top of `2_match.py`, with what each one
  trades off.

## Removing what's already in another playlist
A separate tool, not part of steps 1–5. It removes from a **source** playlist every video that
is also in a **target** playlist, comparing by video ID. The target is only read.
```bash
.venv\Scripts\python compare_playlists.py SOURCE TARGET
```
```bash
.venv\Scripts\python compare_playlists.py SOURCE TARGET --go
```
SOURCE and TARGET can be playlist IDs or any YouTube link containing `list=`. The first command
lists the overlap (title and link) and writes it to `playlist_overlap.csv`; `--go` then removes
those items from the source. You must own the source playlist; the target can be any playlist
you can open. Watch Later can't be used as either: YouTube's API doesn't expose it.

## YouTube API quota
Every YouTube call these scripts make is charged to one daily quota, belonging to your Google
Cloud project. It's shared by all of them: steps 1 and 5 and `compare_playlists.py`, reads included.

| Call | Used by | Cost |
|---|---|---|
| Read a playlist page (up to 50 items) | step 1, `compare_playlists.py` | 1 unit |
| Look up video lengths (up to 50 videos) | step 1 | 1 unit |
| Remove one item from a playlist | step 5, `compare_playlists.py --go` | 50 units |

Removals are what use it up. Your quota and today's usage are in Cloud Console under **APIs &
Services → YouTube Data API v3 → Quotas**. It **resets at midnight Pacific time** (09:00 in
Central Europe for most of the year), so run reads before big removal batches.

**When it runs out**, the script says so and stops. Nothing is lost: step 5 continues from
`apply_progress.json`, `compare_playlists.py` re-reads both playlists and finds only what's left,
and step 1 just runs again.

**Don't create extra Cloud projects for more quota**: YouTube's API policies forbid working
around quota limits. The quota-extension form in Cloud Console is the allowed route.

## Spotify rate limit
Spotify doesn't publish its limits for personal apps; going over them earns a penalty of hours
that grows when repeated. Step 2 sends by far the most requests, so on a big playlist it works
in several sittings:
- **Calls are spaced out** by `SPOTIFY_MIN_INTERVAL_S` in `common.py`.
- **Each run stops itself** after `--max-requests` requests (default in `2_match.py`), before
  Spotify does. Run it again a few hours later to continue.
- **A short "wait N seconds" is waited out**; **a long penalty stops the run** with the time to
  try again, instead of hanging until then.
- **Every answer is cached** (`shows.json`, `show_episodes.json`, `episode_search.json`), so a
  rerun sends only the requests it hasn't made yet.
- **A stopped run still writes `matches.json`.** Items it didn't reach are "pending" (their own
  tab on the review page) and stay on YouTube; you can review and move the rest meanwhile.
  `2_match.py --max-requests 0` rebuilds `matches.json` from the cache without contacting Spotify.

## Limits
- **Watch Later:** YouTube's API can't add to Watch Later. Do that by hand on YouTube if you want
  the remaining videos there.
- Deleted and private videos in the playlist are skipped and left in place.

## Tests
```bash
.venv\Scripts\python -m unittest -v
```
Covers the offline logic: duration/date parsing, title scoring, auto/review/none rules, which
items move to Spotify, playlist links, and the playlist comparison. Nothing in the tests calls
YouTube or Spotify.
