"""Step 2: match exported YouTube items to Spotify episodes. Changes nothing on either service.

For each channel, find its Spotify show (kept in shows.json, which you may edit), fetch that
show's episodes, and score every video against them locally. Videos that still have no good
candidate fall back to a plain Spotify episode search. Writes matches.json. Max ~350 lines.

Every Spotify answer is cached (shows.json, show_episodes.json, episode_search.json), so a run
stopped by Spotify's rate limit or by --max-requests picks up where it stopped; only new
requests are sent. A stopped run still writes matches.json, with unreached items "pending".
"""
import argparse
import re
import unicodedata
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from common import (EPISODE_CACHE_FILE, ITEMS_FILE, MATCHES_FILE, SEARCH_CACHE_FILE, SHOWS_FILE,
                    SpotifyStop, load_json, load_settings, require, save_json, spotify_client)

# --- Thresholds. Raising them sends more items to manual review; lowering them risks
# --- removing a YouTube video whose Spotify "match" is a different episode.
SHOW_NAME_AUTO = 0.85      # channel-name vs show-name similarity to adopt a show without asking
AUTO_TITLE = 0.80          # title similarity that, with a close duration, is accepted automatically
AUTO_TITLE_WITH_DATE = 0.60  # lower bar when the release dates are also within AUTO_MAX_DAYS
AUTO_MAX_DAYS = 3
REVIEW_TITLE = 0.45        # below this (and no other evidence) the item is "none"
REVIEW_MAX_DAYS = 7
# Duration is "strong" within max(seconds, fraction of length); YouTube and Spotify cuts of the
# same episode differ by intros and ad reads, so exact equality is rare.
STRONG_DURATION = (120, 0.03)
WEAK_DURATION = (300, 0.10)
# Paging a show's episodes (newest first) stops this far before the oldest video of that channel.
EPISODE_LOOKBACK = timedelta(days=60)
SEARCH_QUERY_WORDS = 10
KEEP_CANDIDATES = 3
# The search cache is also written on exit (including a rate-limit stop); this only bounds what
# a crash or a closed window could lose.
SEARCH_SAVE_EVERY = 25
# Spotify requests per run before stopping cleanly. The last Spotify penalty came after roughly
# 900 requests in one run; stopping below that and resuming hours later costs less than a penalty.
DEFAULT_MAX_REQUESTS = 600

STOPWORDS = {"the", "a", "an", "and", "of", "with", "to", "in", "on", "for", "is", "at", "by",
             "ft", "feat", "full", "episode", "ep", "podcast", "official", "video", "audio", "4k", "hd"}


def normalize(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9#]+", " ", text).split())


def tokens(text):
    return [t for t in normalize(text).replace("#", " ").split() if t not in STOPWORDS]


def episode_numbers(text):
    """Episode numbers written as '#123', 'Ep. 123', 'Episode 123' or 'E123'."""
    found = re.findall(r"(?:#|\bep(?:isode)?\.?\s*|\be)(\d{1,5})\b", (text or "").lower())
    return {int(n) for n in found}


def title_similarity(yt_title, ep_name):
    """0..1. Whole-string similarity, or how much of the shorter title appears in the longer one
    (YouTube titles often add the show name or guest list around the episode name)."""
    a, b = tokens(yt_title), tokens(ep_name)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    contained = 0.0
    if len(set(shorter)) >= 3:  # a two-word title is "contained" in too many things to count
        contained = len(set(shorter) & set(longer)) / len(set(shorter))
    return round(max(ratio, contained), 3)


def duration_closeness(yt_seconds, ep_seconds):
    if not yt_seconds or not ep_seconds:
        return None
    diff = abs(yt_seconds - ep_seconds)
    length = max(yt_seconds, ep_seconds)
    if diff <= max(STRONG_DURATION[0], STRONG_DURATION[1] * length):
        return "strong"
    if diff <= max(WEAK_DURATION[0], WEAK_DURATION[1] * length):
        return "weak"
    return "far"


def parse_day(value):
    """YouTube timestamps and Spotify release dates ('2024', '2024-05', '2024-05-01') -> date."""
    if not value:
        return None
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    parts = [int(p) for p in value.split("-")]
    return date(parts[0], *(parts[1:] + [1, 1])[:2]) if len(parts) < 3 else date(*parts)


def day_gap(yt_published, release_date, precision="day"):
    a, b = parse_day(yt_published), parse_day(release_date)
    if not a or not b or precision != "day":
        return None
    return abs((a - b).days)


def score_candidate(item, ep):
    title = title_similarity(item["title"], ep["name"])
    duration = duration_closeness(item.get("duration_s"), ep.get("duration_s"))
    days = day_gap(item.get("published"), ep.get("release_date"), ep.get("release_date_precision", "day"))
    yt_nums, ep_nums = episode_numbers(item["title"]), episode_numbers(ep["name"])
    numbers = None
    if yt_nums and ep_nums:
        numbers = "match" if yt_nums & ep_nums else "conflict"
    score = title
    score += {"strong": 0.3, "weak": 0.1, "far": -0.5, None: 0}[duration]
    score += {"match": 0.3, "conflict": -0.3, None: 0}[numbers]
    score += 0.1 if days is not None and days <= REVIEW_MAX_DAYS else 0
    return {**ep, "title_sim": title, "duration": duration, "days": days,
            "numbers": numbers, "score": round(score, 3)}


def classify(best):
    if best is None:
        return "none"
    if best["numbers"] == "conflict":
        # Usually a different episode, but titles like "The #1 mistake" also trip this.
        return "review" if best["title_sim"] >= AUTO_TITLE else "none"
    close_date = best["days"] is not None and best["days"] <= AUTO_MAX_DAYS
    if best["duration"] == "strong" and (
            best["title_sim"] >= AUTO_TITLE or best["numbers"] == "match"
            or (best["title_sim"] >= AUTO_TITLE_WITH_DATE and close_date)):
        return "auto"
    if best["duration"] == "far" and best["numbers"] != "match":
        return "none"
    if (best["title_sim"] >= REVIEW_TITLE or best["numbers"] == "match"
            or (best["duration"] == "strong" and best["days"] is not None and best["days"] <= REVIEW_MAX_DAYS)):
        return "review"
    return "none"


def rank(item, episodes):
    scored = sorted((score_candidate(item, ep) for ep in episodes), key=lambda c: c["score"], reverse=True)
    return scored[:KEEP_CANDIDATES]


# --- Spotify calls ---------------------------------------------------------------------------

def simplify_episode(ep, show_name=None):
    return {"id": ep["id"], "uri": ep["uri"], "name": ep["name"], "show": show_name,
            "duration_s": round(ep["duration_ms"] / 1000) if ep.get("duration_ms") else None,
            "release_date": ep.get("release_date"),
            "release_date_precision": ep.get("release_date_precision", "day")}


def find_show(sp, channel):
    """Search shows named like the channel. Adopted only when the name is a near match."""
    results = sp.search(q=channel, type="show", limit=10)["shows"]["items"]
    candidates = []
    for s in results:
        if not s:
            continue
        sim = SequenceMatcher(None, normalize(channel), normalize(s["name"])).ratio()
        candidates.append({"id": s["id"], "name": s["name"], "publisher": s.get("publisher"),
                           "similarity": round(sim, 3)})
    candidates.sort(key=lambda c: c["similarity"], reverse=True)
    best = candidates[0] if candidates and candidates[0]["similarity"] >= SHOW_NAME_AUTO else None
    return {"show_id": best["id"] if best else None, "show_name": best["name"] if best else None,
            "candidates": candidates[:5]}


def fetch_show_episodes(sp, show_id, show_name, oldest_needed):
    episodes, offset = [], 0
    while True:
        page = sp.show_episodes(show_id, limit=50, offset=offset)
        batch = [ep for ep in page["items"] if ep]
        episodes += [simplify_episode(ep, show_name) for ep in batch]
        offset += 50
        oldest = parse_day(batch[-1].get("release_date")) if batch else None
        if not page.get("next") or (oldest and oldest_needed and oldest < oldest_needed):
            return {"episodes": episodes, "complete": not page.get("next"),
                    "oldest": oldest.isoformat() if oldest else None}


def episodes_for_show(sp, cache, show_id, show_name, oldest_needed, refresh):
    cached = cache.get(show_id)
    covers = cached and (cached["complete"] or (
        cached["oldest"] and oldest_needed and parse_day(cached["oldest"]) <= oldest_needed))
    if refresh or not covers:
        cache[show_id] = fetch_show_episodes(sp, show_id, show_name, oldest_needed)
        save_json(EPISODE_CACHE_FILE, cache)
    return cache[show_id]["episodes"]


def search_episodes(sp, title, search_cache):
    """Spotify episode search, answered from episode_search.json when this query ran before."""
    query = " ".join(normalize(title).replace("#", " ").split()[:SEARCH_QUERY_WORDS])
    if not query:
        return []
    if query not in search_cache:
        results = sp.search(q=query, type="episode", limit=10)["episodes"]["items"]
        search_cache[query] = [simplify_episode(ep) for ep in results if ep]
        if len(search_cache) % SEARCH_SAVE_EVERY == 0:
            save_json(SEARCH_CACHE_FILE, search_cache)
    return search_cache[query]


# --- Orchestration ---------------------------------------------------------------------------

def build_matches(items, results):
    """matches.json rows in playlist order. Items the run didn't reach are "pending": they stay on
    YouTube for now, and the next run matches them."""
    rows = []
    for it in items:
        if it["playlist_item_id"] in results:
            rows.append(results[it["playlist_item_id"]])
        else:
            status = "unavailable" if it["unavailable"] else "pending"
            rows.append({**it, "status": status, "source": None, "candidates": []})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="refetch show episodes instead of using the cache")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                        help=f"stop after this many Spotify requests (default {DEFAULT_MAX_REQUESTS}); "
                             "0 uses only cached answers, e.g. to see progress while Spotify blocks you")
    args = parser.parse_args()

    sp = spotify_client(load_settings(), max_requests=max(args.max_requests, 0))
    items = require(ITEMS_FILE, "1_export_youtube.py")
    shows = load_json(SHOWS_FILE, {})
    cache = load_json(EPISODE_CACHE_FILE, {})
    search_cache = load_json(SEARCH_CACHE_FILE, {})

    by_channel = {}
    for it in items:
        if not it["unavailable"]:
            by_channel.setdefault(it["channel"], []).append(it)

    results, stop = {}, None
    try:
        match_all(sp, by_channel, shows, cache, search_cache, args.refresh, results)
    except SpotifyStop as e:
        stop = e
    finally:  # keep finished searches even when a stop or Ctrl+C ends the run
        save_json(SEARCH_CACHE_FILE, search_cache)

    matches = build_matches(items, results)
    save_json(MATCHES_FILE, matches)
    counts = {s: sum(m["status"] == s for m in matches)
              for s in ("auto", "review", "none", "pending", "unavailable")}
    print(f"\nWrote {MATCHES_FILE.name}: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n))
    if stop:
        print("Pending items aren't matched yet. You can review and move the others now.")
        raise stop
    print(f"Channels with no show adopted are in {SHOWS_FILE.name}; set show_id there and rerun to use one.")


def match_all(sp, by_channel, shows, cache, search_cache, refresh, results):
    """Fills results (playlist_item_id -> match row) as it goes, so a stop keeps what's done."""
    for channel, videos in sorted(by_channel.items(), key=lambda kv: -len(kv[1])):
        if channel not in shows:  # keep entries you edited by hand
            shows[channel] = find_show(sp, channel)
            save_json(SHOWS_FILE, shows)
        show = shows[channel]
        episodes = []
        if show.get("show_id"):
            dates = [parse_day(v["published"]) for v in videos if v.get("published")]
            oldest_needed = min(dates) - EPISODE_LOOKBACK if dates else None
            episodes = episodes_for_show(sp, cache, show["show_id"], show.get("show_name"),
                                         oldest_needed, refresh)
        print(f"{channel}: {len(videos)} videos, show: {show.get('show_name') or '-'} "
              f"({len(episodes)} episodes)")
        for v in videos:
            candidates, source = rank(v, episodes), "show"
            if classify(candidates[0] if candidates else None) != "auto":
                searched = rank(v, search_episodes(sp, v["title"], search_cache))
                if searched and (not candidates or searched[0]["score"] > candidates[0]["score"]):
                    candidates, source = searched, "search"
            results[v["playlist_item_id"]] = {
                **v, "status": classify(candidates[0] if candidates else None),
                "source": source if candidates else None, "candidates": candidates}


if __name__ == "__main__":
    main()
