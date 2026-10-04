"""Unit tests for the offline logic: parsing, scoring, classification, the apply plan and the
playlist comparison.

Run with: .venv\\Scripts\\python -m unittest -v
"""
import importlib
import unittest
from datetime import date

from common import resolve

# The step scripts start with their run-order number, which a plain import statement can't name.
parse_iso_duration = importlib.import_module("1_export_youtube").parse_iso_duration
_match = importlib.import_module("2_match")
classify, day_gap, duration_closeness = _match.classify, _match.day_gap, _match.duration_closeness
episode_numbers, parse_day, rank = _match.episode_numbers, _match.parse_day, _match.rank
title_similarity = _match.title_similarity

URI_A = "spotify:episode:" + "a" * 22
URI_B = "spotify:episode:" + "b" * 22


def episode(name, minutes, released, uri=URI_A):
    return {"id": uri[-22:], "uri": uri, "name": name, "show": "Show", "duration_s": minutes * 60,
            "release_date": released, "release_date_precision": "day"}


def video(title, minutes, published="2025-03-10T15:00:00Z"):
    return {"playlist_item_id": "PI_" + title[:8], "video_id": "v", "title": title, "channel": "C",
            "duration_s": minutes * 60, "published": published}


class Parsing(unittest.TestCase):
    def test_iso_duration(self):
        self.assertEqual(parse_iso_duration("PT1H02M03S"), 3723)
        self.assertEqual(parse_iso_duration("PT45M"), 2700)
        self.assertEqual(parse_iso_duration("P1DT1S"), 86401)
        self.assertEqual(parse_iso_duration("P0D"), 0)  # what YouTube reports for live streams
        self.assertIsNone(parse_iso_duration(None))

    def test_parse_day_handles_spotify_precisions(self):
        self.assertEqual(parse_day("2024"), date(2024, 1, 1))
        self.assertEqual(parse_day("2024-05"), date(2024, 5, 1))
        self.assertEqual(parse_day("2024-05-07"), date(2024, 5, 7))
        self.assertEqual(parse_day("2024-05-07T23:00:00Z"), date(2024, 5, 7))

    def test_day_gap_ignores_imprecise_dates(self):
        self.assertEqual(day_gap("2024-05-07T10:00:00Z", "2024-05-05"), 2)
        self.assertIsNone(day_gap("2024-05-07T10:00:00Z", "2024", "year"))

    def test_episode_numbers(self):
        self.assertEqual(episode_numbers("Lex Fridman Podcast #412 – Guest"), {412})
        self.assertEqual(episode_numbers("Ep. 87: Something"), {87})
        self.assertEqual(episode_numbers("Episode 5 | Title"), {5})
        self.assertEqual(episode_numbers("No numbers here"), set())


class Scoring(unittest.TestCase):
    def test_title_with_show_name_and_guest_still_matches(self):
        yt = "The Diary Of A CEO: Why Sleep Matters More Than You Think | Dr Matthew Walker"
        self.assertGreaterEqual(title_similarity(yt, "Why Sleep Matters More Than You Think"), 0.8)

    def test_noise_words_do_not_count(self):
        self.assertGreaterEqual(title_similarity("Building Habits That Last [FULL EPISODE] 4K",
                                                 "Building Habits That Last"), 0.9)

    def test_unrelated_titles_score_low(self):
        self.assertLess(title_similarity("The history of Rome in ten minutes",
                                         "Cooking pasta with Italian grandmothers"), 0.45)

    def test_short_titles_are_not_counted_as_contained(self):
        self.assertLess(title_similarity("Q&A", "Q&A with listeners about money and careers"), 0.45)

    def test_duration_bands(self):
        self.assertEqual(duration_closeness(3600, 3690), "strong")
        self.assertEqual(duration_closeness(3600, 3900), "weak")
        self.assertEqual(duration_closeness(3600, 5400), "far")
        self.assertIsNone(duration_closeness(None, 3600))


class Classification(unittest.TestCase):
    def best(self, v, *eps):
        ranked = rank(v, list(eps))
        return ranked[0] if ranked else None

    def test_same_title_and_length_is_auto(self):
        v = video("Why Sleep Matters | Dr Walker", 92)
        self.assertEqual(classify(self.best(v, episode("Why Sleep Matters", 93, "2025-03-10"))), "auto")

    def test_same_title_wrong_length_is_not_auto(self):
        v = video("Why Sleep Matters | Dr Walker", 92)
        self.assertEqual(classify(self.best(v, episode("Why Sleep Matters", 20, "2025-03-10"))), "none")

    def test_matching_episode_number_and_length_is_auto(self):
        v = video("#412 – A completely different marketing title", 180)
        self.assertEqual(classify(self.best(v, episode("#412 – Guest Name: Topic", 182, "2025-03-09"))), "auto")

    def test_adjacent_episode_number_is_never_auto(self):
        v = video("Money Talk #299 – Common money mistakes", 60)
        best = self.best(v, episode("Money Talk #300 – Common money mistakes", 60, "2025-03-10"))
        self.assertNotEqual(classify(best), "auto")

    def test_marketing_number_conflict_goes_to_review(self):
        v = video("The #1 mistake people make with money", 60)
        best = self.best(v, episode("Ep 300: The #1 mistake people make with money", 60, "2025-03-10"))
        self.assertEqual(best["numbers"], "match")  # both mention #1, so this one is fine
        best = self.best(v, episode("Ep 300: The mistake people make with money", 60, "2025-03-10"))
        self.assertEqual(classify(best), "review")

    def test_weak_title_with_close_length_and_date_is_review(self):
        v = video("You won't believe what happened next", 75, "2025-03-10T00:00:00Z")
        self.assertEqual(classify(self.best(v, episode("Mailbag 12", 76, "2025-03-11"))), "review")

    def test_no_candidates_is_none(self):
        self.assertEqual(classify(None), "none")

    def test_best_candidate_wins_ranking(self):
        v = video("Why Sleep Matters | Dr Walker", 92)
        right = episode("Why Sleep Matters", 93, "2025-03-10", URI_B)
        wrong = episode("Why Diet Matters", 40, "2024-01-01", URI_A)
        self.assertEqual(rank(v, [wrong, right])[0]["uri"], URI_B)


class ApplyPlan(unittest.TestCase):
    def matches(self):
        auto = {"playlist_item_id": "1", "title": "a", "status": "auto", "candidates": [{"uri": URI_A}]}
        review = {"playlist_item_id": "2", "title": "b", "status": "review", "candidates": [{"uri": URI_B}]}
        none = {"playlist_item_id": "3", "title": "c", "status": "none", "candidates": []}
        return [auto, review, none]

    def test_auto_moves_review_waits_for_decision(self):
        plan = resolve(self.matches(), {})
        self.assertEqual([(m["playlist_item_id"], uri) for m, uri in plan], [("1", URI_A)])

    def test_decisions_override_both_ways(self):
        decisions = {"1": {"episode_uri": None}, "2": {"episode_uri": URI_B}, "3": {"episode_uri": URI_A}}
        plan = resolve(self.matches(), decisions)
        self.assertEqual([(m["playlist_item_id"], uri) for m, uri in plan], [("2", URI_B), ("3", URI_A)])

    def test_bad_uri_stops_the_run(self):
        with self.assertRaises(SystemExit):
            resolve(self.matches(), {"2": {"episode_uri": "https://example.com"}})


class ComparePlaylists(unittest.TestCase):
    def test_playlist_id_from_url_or_bare_id(self):
        from common import parse_playlist_id
        pid = "PLaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.assertEqual(parse_playlist_id(pid), pid)
        self.assertEqual(parse_playlist_id(f"https://www.youtube.com/playlist?list={pid}"), pid)
        self.assertEqual(parse_playlist_id(f"https://www.youtube.com/watch?v=abc&list={pid}&index=3"), pid)
        with self.assertRaises(SystemExit):
            parse_playlist_id("https://www.youtube.com/watch?v=abc")

    def test_share_link_extras_are_dropped(self):
        from common import parse_playlist_id
        self.assertEqual(parse_playlist_id("PLbbbbbbbbbbb&si=xxxxxxxxxxxxxxxx"), "PLbbbbbbbbbbb")
        self.assertEqual(parse_playlist_id("https://youtube.com/playlist?list=PLbbbbbbbbbbb&si=xxxx"),
                         "PLbbbbbbbbbbb")

    def test_overlap_keeps_every_source_copy(self):
        from compare_playlists import overlap
        source = [{"video_id": "a", "playlist_item_id": "s1"}, {"video_id": "b", "playlist_item_id": "s2"},
                  {"video_id": "a", "playlist_item_id": "s3"}]
        target = [{"video_id": "a", "playlist_item_id": "t1"}, {"video_id": "c", "playlist_item_id": "t2"}]
        self.assertEqual([it["playlist_item_id"] for it in overlap(source, target)], ["s1", "s3"])


class SpotifyRateLimits(unittest.TestCase):
    """PacedSpotify with spotipy's real request method replaced, so nothing reaches Spotify."""

    def client(self, answers):
        from unittest import mock

        import spotipy
        from spotipy.exceptions import SpotifyException

        import common
        sp = common.spotify_client({"spotify_client_id": "x" * 32})
        replies = iter(answers)

        def fake_call(self, method, url, payload, params):
            reply = next(replies)
            if isinstance(reply, int):  # a 429 asking to wait this many seconds
                raise SpotifyException(429, -1, "rate limited", headers={"Retry-After": str(reply)})
            return reply

        self.addCleanup(mock.patch.stopall)
        mock.patch.object(spotipy.Spotify, "_internal_call", fake_call).start()
        self.sleeps = []
        mock.patch("time.sleep", self.sleeps.append).start()
        return sp

    def test_short_wait_is_waited_out_then_retried(self):
        sp = self.client([5, {"ok": 1}])
        self.assertEqual(sp.search(q="x", type="episode"), {"ok": 1})
        self.assertIn(6, self.sleeps)  # Retry-After + 1

    def test_long_wait_stops_with_a_time_instead_of_hanging(self):
        sp = self.client([37398])
        with self.assertRaises(SystemExit) as stop:
            sp.search(q="x", type="episode")
        self.assertIn("10h 23m", str(stop.exception))
        self.assertNotIn(37399, self.sleeps)

    def test_request_limit_stops_before_sending_more(self):
        from common import SpotifyStop
        sp = self.client([{}, {}, {}])
        sp.max_requests = 2
        sp.search(q="a", type="episode")
        sp.search(q="b", type="episode")
        with self.assertRaises(SpotifyStop) as stop:
            sp.search(q="c", type="episode")
        self.assertIn("after 2 Spotify requests", str(stop.exception))
        self.assertEqual(sp.requests_sent, 2)

    def test_zero_limit_sends_nothing(self):
        from common import SpotifyStop
        sp = self.client([{}])
        sp.max_requests = 0
        with self.assertRaises(SpotifyStop):
            sp.search(q="a", type="episode")
        self.assertEqual(sp.requests_sent, 0)

    def test_calls_are_spaced_out(self):
        from common import SPOTIFY_MIN_INTERVAL_S
        sp = self.client([{}, {}])
        sp.search(q="a", type="episode")
        sp.search(q="b", type="episode")
        self.assertTrue(self.sleeps and 0 < self.sleeps[-1] <= SPOTIFY_MIN_INTERVAL_S)


class SpotifyRateLimitsOverHttp(unittest.TestCase):
    """The same 429 handling through spotipy's real HTTP stack, against a local fake server."""

    def serve(self, replies):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        replies = iter(replies)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status, retry_after = next(replies)
                body = b'{"error": {"status": 429, "message": "rate limited"}}' if status == 429 else b"{}"
                self.send_response(status)
                if retry_after is not None:
                    self.send_header("Retry-After", str(retry_after))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        from common import _paced_spotify_class
        sp = _paced_spotify_class()(auth="test-token", retries=5, status_retries=5, backoff_factor=0,
                                    status_forcelist=(500, 502, 503, 504))
        sp.prefix = f"http://127.0.0.1:{server.server_port}/v1/"
        return sp

    def test_long_retry_after_stops_quickly(self):
        import time
        sp = self.serve([(429, 37398)])
        started = time.monotonic()
        with self.assertRaises(SystemExit) as stop:
            sp.search(q="x", type="episode")
        self.assertLess(time.monotonic() - started, 5)  # spotipy alone would sleep ~10 hours here
        self.assertIn("10h 23m", str(stop.exception))

    def test_short_retry_after_is_retried(self):
        sp = self.serve([(429, 0), (200, None)])
        self.assertEqual(sp.search(q="x", type="episode"), {})


class PartialMatches(unittest.TestCase):
    def test_unreached_items_are_pending_and_stay_on_youtube(self):
        items = [{"playlist_item_id": "1", "title": "a", "unavailable": False},
                 {"playlist_item_id": "2", "title": "b", "unavailable": False},
                 {"playlist_item_id": "3", "title": "Deleted video", "unavailable": True}]
        done = {"1": {**items[0], "status": "auto", "source": "show", "candidates": [{"uri": URI_A}]}}
        rows = _match.build_matches(items, done)
        self.assertEqual([r["status"] for r in rows], ["auto", "pending", "unavailable"])
        self.assertEqual([(m["playlist_item_id"], uri) for m, uri in resolve(rows, {})], [("1", URI_A)])


class SearchCache(unittest.TestCase):
    def test_repeated_query_is_answered_from_cache(self):
        from unittest import mock
        sp = mock.Mock()
        sp.search.return_value = {"episodes": {"items": [None]}}
        cache = {}
        _match.search_episodes(sp, "Some Title #12", cache)
        _match.search_episodes(sp, "some title #12", cache)
        self.assertEqual(sp.search.call_count, 1)
        self.assertEqual(cache, {"some title 12": []})


if __name__ == "__main__":
    unittest.main()
