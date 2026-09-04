"""Lean browser assets over the shared, unchanged dashboard API."""

from urllib.parse import urlparse

from dashboard_app.config import DASHBOARD_ASSET_DIR
from dashboard_app.http import build_handler as shared_handler


# Explicit allowlist: never turn a request path into an arbitrary filesystem read.
ASSETS = {
    f"/assets/{name}": "text/css" if name.endswith(".css") else "text/javascript"
    for name in (
        "dashboard-lean.css", "app-lean.js", "api-lean.js",
        "components/stepper-lean.js", "components/toolbar-lean.js",
        "components/metadata-lean.js",
    )
}


def build_handler(runtime, quiet=True):
    """Override presentation only; recording, SSE and device commands stay shared."""
    class Handler(shared_handler(runtime, quiet)):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self._send_html((DASHBOARD_ASSET_DIR / "index-lean.html").read_text())
            if path not in ASSETS:
                return super().do_GET()
            payload = (DASHBOARD_ASSET_DIR / path.removeprefix("/assets/")).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ASSETS[path] + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

    return Handler
