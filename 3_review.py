"""Step 3: serve review.html on 127.0.0.1 and record your decisions in decisions.json.

Each click on the page is saved immediately, so you can stop (Ctrl+C) and continue later.
A decision is {"episode_uri": "spotify:episode:..."} (move to Spotify) or {"episode_uri": null}
(keep on YouTube); removing a decision falls back to 2_match.py's verdict. Max ~100 lines.
"""
import argparse
import json
import sys
import webbrowser
# Single-threaded on purpose: requests then update decisions.json one at a time.
from http.server import BaseHTTPRequestHandler, HTTPServer

from common import DECISIONS_FILE, EPISODE_URI, MATCHES_FILE, ROOT, load_json, require, save_json

PORT = 8765
PAGE = ROOT / "review.html"


class Handler(BaseHTTPRequestHandler):
    matches = []
    decisions = {}

    def send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/data":
            self.send(200, {"matches": self.matches, "decisions": self.decisions})
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/decision":
            return self.send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            item_id, decision = body["playlist_item_id"], body["decision"]
        except (ValueError, KeyError, TypeError):
            return self.send(400, {"error": "bad request"})
        if item_id not in {m["playlist_item_id"] for m in self.matches}:
            return self.send(400, {"error": "unknown item"})
        if decision is None:
            self.decisions.pop(item_id, None)
        else:
            uri = decision.get("episode_uri") if isinstance(decision, dict) else "invalid"
            if uri is not None and not EPISODE_URI.match(uri):
                return self.send(400, {"error": "not a Spotify episode link"})
            self.decisions[item_id] = {"episode_uri": uri}
        save_json(DECISIONS_FILE, self.decisions)
        self.send(200, {"ok": True})

    def log_message(self, *args):
        pass  # keep the console quiet; every click would otherwise print a line


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-browser", action="store_true", help="don't open the page automatically")
    args = parser.parse_args()
    Handler.matches = require(MATCHES_FILE, "2_match.py")
    Handler.decisions = load_json(DECISIONS_FILE, {})
    try:
        server = HTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        sys.exit(f"Port {PORT} is busy. Is the review page already running?")
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Review page: {url}  (Ctrl+C to stop; decisions are saved as you click)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
