"""Serve frontend/ locally and proxy /api/* and /images/* to the deployed
CloudFront site, so UI changes can be previewed without a deploy.

    python scripts/dev_server.py https://dxxxx.cloudfront.net   # then open http://localhost:8000

Uploads still go straight to S3, which only allows the CloudFront origin
(add http://localhost:8000 to extra_cors_origins to upload from here).
"""

import sys
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
UPSTREAM = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else sys.exit(__doc__)


class Handler(SimpleHTTPRequestHandler):
    def _proxy(self):
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(UPSTREAM + self.path, data=body, method=self.command)
        if self.headers.get("content-type"):
            req.add_header("content-type", self.headers["content-type"])
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, headers, data = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as err:
            status, headers, data = err.code, err.headers, err.read()
        self.send_response(status)
        self.send_header("content-type", headers.get("content-type", "application/octet-stream"))
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith(("/api/", "/images/")):
            return self._proxy()
        return super().do_GET()

    def do_POST(self):
        return self._proxy()


if __name__ == "__main__":
    print(f"http://localhost:8000  (proxying /api and /images to {UPSTREAM})")
    ThreadingHTTPServer(("127.0.0.1", 8000), partial(Handler, directory=str(FRONTEND))).serve_forever()
