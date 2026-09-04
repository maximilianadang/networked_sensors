#!/usr/bin/env python3
"""Simulation-only browser checks using an installed geckodriver (no pip packages).

Start geckodriver --port 4444, then run this file. Screenshots are optional.
No physical controller is contacted; the fixture intercepts all browser commands.
"""
import argparse
import base64
import json
import runpy
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from dashboard_app import DashboardRuntime, DashboardServer

ROOT = Path(__file__).resolve().parent
LEAN = runpy.run_path(str(ROOT / "dashboard-lean.py"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webdriver", default="http://127.0.0.1:4444")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    session = None

    def driver(path, data=None, method=None):
        url = args.webdriver + (f"/session/{session}" if session else "") + path
        request = urllib.request.Request(url, method=method,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=40) as response:
            payload = json.load(response)
        value = payload["value"]
        if isinstance(value, dict) and "error" in value:
            raise AssertionError(value)
        return value

    def execute(script, *values):
        return driver("/execute/sync", {"script": script, "args": list(values)})

    def wait_for(script):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if execute(script):
                return
            time.sleep(.05)
        raise AssertionError(f"Browser timeout: {script}")

    with tempfile.TemporaryDirectory(prefix="dashboard-lean-check-") as temp:
        settings = vars(LEAN["parse_args"]([]))
        for key in ("host", "port", "verbose_http"):
            settings.pop(key)
        settings.update(record_dir=Path(temp), system_config=Path(temp) / "system.json")
        runtime = DashboardRuntime(**{LEAN["RUNTIME_NAMES"].get(k, k): v for k, v in settings.items()})
        base = LEAN["build_handler"](runtime)

        class Handler(base):
            def do_GET(self):
                if self.path == "/fixture":
                    html = (ROOT / "dashboard_app/static/index-lean.html").read_text()
                    return self._send_html(html.replace(
                        '<script type="module" src="/assets/app-lean.js"></script>', ""))
                if self.path == "/checks.js":
                    body = (ROOT / "browser-checks-lean.js").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/javascript")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                super().do_GET()

        with DashboardServer(("127.0.0.1", 0), Handler) as server:
            runtime.start()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                session = driver("/session", {"capabilities": {"alwaysMatch": {
                    "browserName": "firefox", "moz:firefoxOptions": {"args": ["-headless"]}}}})["sessionId"]
                driver("/window/rect", {"width": 1440, "height": 986})
                driver("/url", {"url": url})
                wait_for("return document.getElementById('streamStatus')?.textContent === 'Dashboard live'")
                assert execute("return document.getElementById('mEspP1').textContent") != "--"
                print("PASS: full app initializes, SSE live, charts and measurements populated")
                if args.screenshots:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    (args.screenshots / "live-simulation.png").write_bytes(
                        base64.b64decode(driver("/screenshot")))
                driver("/url", {"url": url + "/fixture"})
                results = driver("/execute/async", {
                    "script": """const done = arguments[arguments.length - 1];
                    Promise.all([import('/checks.js'), fetch('/api/config').then(r=>r.json()),
                    fetch('/api/latest').then(r=>r.json()),
                    fetch('/api/history').then(r=>r.json())])
                    .then(([m,c,p,h])=>m.checkPanels(c,p.sample,h.history)).then(done,e=>done({error:e.message,stack:e.stack}));""",
                    "args": []})
                if isinstance(results, dict):
                    raise AssertionError(results)
                print("\n".join("PASS: " + result for result in results))
                for width, height in ((1920, 1080), (1440, 900), (1366, 768), (500, 844)):
                    for mode in ("local_velocity", "web_position"):
                        driver("/window/rect", {"width": width, "height": height + 86})
                        execute("""fixture.applySample({...fixture.state.latest,
                          stepper_control_mode: arguments[0], stepper_moving:false,
                          stepper_estop_latched:false, stepper_dro_capable:false,
                          stepper_local_enabled: arguments[0] === 'web_position'});
                          document.getElementById('stepperCommandFeedback').hidden=true;
                          fixture.paint();""", mode)
                        bounds = execute("""return {width:innerWidth,height:innerHeight,
                          scrollWidth:document.documentElement.scrollWidth,
                          scrollHeight:document.documentElement.scrollHeight,
                          overflow:[...document.querySelectorAll('.stepper-layout,.metadata-panel,.toolbar')]
                            .filter(e=>e.scrollHeight>e.clientHeight+2).map(e=>e.className),
                          chart:document.querySelector('canvas').clientHeight};""")
                        assert bounds["scrollWidth"] <= bounds["width"], bounds
                        assert not bounds["overflow"], bounds
                        if width > 1120:
                            assert bounds["scrollHeight"] <= bounds["height"] + 2, bounds
                            assert bounds["chart"] >= 100, bounds
                        print(f"PASS: {width}x{height} {mode} layout {bounds}")
                        if args.screenshots:
                            args.screenshots.mkdir(parents=True, exist_ok=True)
                            (args.screenshots / f"{width}-{mode}.png").write_bytes(
                                base64.b64decode(driver("/screenshot")))
            finally:
                if session:
                    driver("", method="DELETE")
                server.shutdown()
                runtime.stop()


if __name__ == "__main__":
    main()
