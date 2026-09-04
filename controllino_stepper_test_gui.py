#!/usr/bin/env python3
"""Script-free operator GUI for the Controllino SRX02-S bring-up firmware."""

from __future__ import annotations

import argparse
import html
import json
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def controller_request(base: str, path: str, method: str = "GET") -> dict:
    request = urllib.request.Request(base.rstrip("/") + path, method=method)
    with urllib.request.urlopen(request, timeout=2.0) as response:
        return json.loads(response.read())


def render_page(status: dict, acknowledgement: str) -> bytes:
    moving = status.get("state") == "moving"
    jogging = status.get("control_mode") == "hold_to_jog"
    code = status.get("direction", "-")
    direction = {"F": "forward", "R": "reverse"}.get(code, "none")
    if moving:
        pulse_output = f"Pulse output: {status.get('pulses_completed', '?')}"
        pulse_output += (
            " (continuous while held)\n"
            if jogging
            else f" / {status.get('pulse_target', '?')}\n"
        )
        output = (
            "State:        MOVING\n"
            f"Direction:    {direction.upper()}\n"
            f"{pulse_output}"
            f"Commanded:    {status.get('rate_pps', '?')} pulses/s\n"
            "Driver:       ENABLED"
        )
    else:
        output = (
            "State:        STOPPED\n"
            "Pulse output: INACTIVE (0 pulses/s now)\n"
            "Driver:       DISABLED\n\n"
            "RETAINED HISTORY\n"
            f"Last direction: {direction}\n"
            f"Last progress:  {status.get('pulses_completed', '?')} / {status.get('pulse_target', '?')}\n"
            f"Test setting:   {status.get('rate_pps', '?')} pulses/s when active"
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Controllino Stepper Test</title>
<style>
:root {{ color-scheme:dark; font-family:system-ui,sans-serif }}
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#15181c }}
main {{ width:min(680px,calc(100% - 32px)); padding:28px; background:#22272e; border:1px solid #46505c; border-radius:14px }}
h1 {{ margin:0 0 8px; font-size:1.5rem }}
.note {{ color:#b8c0ca; margin-bottom:22px }}
.status {{ padding:18px; margin-bottom:18px; background:#111418; border-radius:10px; font:1.05rem ui-monospace,monospace; line-height:1.7; white-space:pre-wrap }}
.ack {{ min-height:2.8em; padding:12px 14px; margin-bottom:18px; background:#18212b; border-left:4px solid #60a5fa; border-radius:6px }}
.controls {{ display:grid; grid-template-columns:1fr 1fr; gap:12px }}
form {{ margin:0 }} button {{ width:100%; min-height:64px; border:0; border-radius:10px; font-size:1.05rem; font-weight:700; cursor:pointer }}
.reverse {{ background:#8b5cf6; color:white }} .forward {{ background:#14b8a6; color:#071512 }}
.jog:active, .jog.held {{ transform:translateY(2px); box-shadow:inset 0 0 0 4px rgba(255,255,255,.55) }}
.wide {{ grid-column:1/-1 }} .stop {{ background:#ef4444; color:white; font-size:1.2rem }} .refresh {{ min-height:44px; background:#596575; color:white }}
</style>
</head>
<body><main>
<h1>Controllino SRX02-S Test</h1>
<div class="note">Press and hold a direction to jog at 1,000 pulses/s; releasing stops and disables the driver. A controller watchdog stops motion if browser heartbeats disappear. No limits or DRO are connected.</div>
<div class="status" id="status">{html.escape(output)}</div>
<div class="ack" id="ack">{html.escape(acknowledgement)}</div>
<div class="controls">
<button type="button" class="reverse jog" data-direction="reverse">Hold for Reverse</button>
<button type="button" class="forward jog" data-direction="forward">Hold for Forward</button>
<button type="button" class="wide stop" id="stop-button">STOP / DISABLE DRIVER</button>
<form class="wide" method="get" action="/"><button class="refresh">Refresh status</button></form>
</div>
<script>
(() => {{
  let timer = null;
  let held = null;
  const send = (path) => fetch(path, {{method: 'POST', cache: 'no-store', keepalive: true}});
  const statusBox = document.getElementById('status');
  const ackBox = document.getElementById('ack');
  function showStatus(s) {{
    const direction = s.direction === 'F' ? 'FORWARD' : s.direction === 'R' ? 'REVERSE' : 'NONE';
    if (s.state === 'moving') {{
      statusBox.textContent = `State:        MOVING\nDirection:    ${{direction}}\nPulse output: ${{s.pulses_completed}} (continuous while held)\nCommanded:    ${{s.rate_pps}} pulses/s\nDriver:       ENABLED`;
    }} else {{
      statusBox.textContent = `State:        STOPPED\nPulse output: INACTIVE (0 pulses/s now)\nDriver:       DISABLED\n\nRETAINED HISTORY\nLast direction: ${{direction.toLowerCase()}}\nLast progress:  ${{s.pulses_completed}}\nTest setting:   ${{s.rate_pps}} pulses/s when active`;
    }}
  }}
  function refreshStatus() {{
    return fetch('/api/status', {{cache: 'no-store'}}).then(r => r.json()).then(showStatus).catch(() => {{}});
  }}
  function stop() {{
    if (!held) return;
    held.classList.remove('held');
    held = null;
    clearInterval(timer);
    timer = null;
    send('/api/stop').then(r => r.json()).then(showStatus).catch(() => refreshStatus());
  }}
  function start(event) {{
    event.preventDefault();
    if (held) return;
    held = event.currentTarget;
    held.classList.add('held');
    held.setPointerCapture?.(event.pointerId);
    const direction = held.dataset.direction;
    send('/api/jog/start?direction=' + direction)
      .then(r => r.json()).then(showStatus)
      .catch(() => {{ ackBox.textContent = 'Failed to start jog'; stop(); }});
    timer = setInterval(() => send('/api/jog/keepalive?direction=' + direction).catch(stop), 150);
  }}
  document.querySelectorAll('.jog').forEach(button => {{
    button.addEventListener('pointerdown', start);
    button.addEventListener('pointerup', stop);
    button.addEventListener('pointercancel', stop);
    button.addEventListener('lostpointercapture', stop);
    button.addEventListener('contextmenu', event => event.preventDefault());
  }});
  document.getElementById('stop-button').addEventListener('click', () => {{
    clearInterval(timer);
    timer = null;
    if (held) held.classList.remove('held');
    held = null;
    send('/api/stop').then(r => r.json()).then(showStatus).catch(() => refreshStatus());
  }});
  window.addEventListener('blur', stop);
  document.addEventListener('visibilitychange', () => {{ if (document.hidden) stop(); }});
  setInterval(refreshStatus, 250);
}})();
</script>
</main></body></html>"""
    return document.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    controller = "http://10.77.0.10"
    last_acknowledgement = "No command sent from this page yet."
    command_sequence = 0

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def redirect_home(self) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            try:
                status = controller_request(self.controller, "/status")
                body = render_page(status, type(self).last_acknowledgement)
                self.send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", body)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                self.send_bytes(HTTPStatus.BAD_GATEWAY, "text/html; charset=utf-8", render_page({}, f"Communication error: {exc}"))
            return
        if path == "/api/status":
            self.proxy_json("/status", "GET")
            return
        self.send_bytes(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        commands = {
            "/command/reverse": ("Reverse", "/move?direction=reverse"),
            "/command/forward": ("Forward", "/move?direction=forward"),
            "/command/stop": ("Stop", "/stop"),
        }
        if parsed.path in commands:
            label, controller_path = commands[parsed.path]
            try:
                result = controller_request(self.controller, controller_path, "POST")
                type(self).command_sequence += 1
                type(self).last_acknowledgement = (
                    f"Command #{type(self).command_sequence} at {time.strftime('%H:%M:%S')} — "
                    f"{label}: {result.get('result', 'no result')}"
                )
                self.redirect_home()
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                type(self).last_acknowledgement = f"{label} failed: {exc}"
                self.send_bytes(
                    HTTPStatus.BAD_GATEWAY,
                    "text/html; charset=utf-8",
                    render_page({}, type(self).last_acknowledgement),
                )
            return
        if parsed.path == "/api/stop":
            self.proxy_json("/stop", "POST")
            return
        if parsed.path == "/api/move":
            direction = parse_qs(parsed.query).get("direction", [""])[0]
            if direction not in {"forward", "reverse"}:
                self.send_bytes(HTTPStatus.BAD_REQUEST, "text/plain", b"Invalid direction")
                return
            self.proxy_json(f"/move?direction={direction}", "POST")
            return
        if parsed.path in {"/api/jog/start", "/api/jog/keepalive"}:
            direction = parse_qs(parsed.query).get("direction", [""])[0]
            if direction not in {"forward", "reverse"}:
                self.send_bytes(HTTPStatus.BAD_REQUEST, "text/plain", b"Invalid direction")
                return
            controller_path = parsed.path.removeprefix("/api")
            self.proxy_json(f"{controller_path}?direction={direction}", "POST")
            return
        self.send_bytes(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

    def proxy_json(self, path: str, method: str) -> None:
        try:
            document = controller_request(self.controller, path, method)
            body = json.dumps(document, separators=(",", ":")).encode("utf-8")
            self.send_bytes(HTTPStatus.OK, "application/json", body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.send_bytes(HTTPStatus.BAD_GATEWAY, "application/json", json.dumps({"error": str(exc)}).encode())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--controller", default="http://10.77.0.10")
    args = parser.parse_args()
    Handler.controller = args.controller.rstrip("/")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Stepper test GUI: http://{args.host}:{args.port}/", flush=True)
    print(f"Controllino: {Handler.controller}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
