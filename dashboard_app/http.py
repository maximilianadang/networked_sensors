"""Stdlib HTTP routes for the local dashboard and supervisor API."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import DASHBOARD_ASSET_DIR, DASHBOARD_ASSET_TYPES, INDEX_HTML
from .runtime import DashboardRuntime


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True


def parse_body(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    content_type = handler.headers.get("Content-Type", "")
    if "application/json" in content_type:
        payload = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload
    decoded = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in decoded.items()}


def build_handler(runtime: DashboardRuntime, quiet: bool) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "FlowSupervisorHTTP/0.1"

        def log_message(self, fmt: str, *args: object) -> None:
            if not quiet:
                super().log_message(fmt, *args)

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if path == "/":
                    self._send_html(INDEX_HTML)
                elif path in DASHBOARD_ASSET_TYPES:
                    self._send_dashboard_asset(path)
                elif path == "/api/state":
                    self._send_json(runtime.state())
                elif path == "/api/config":
                    self._send_json(runtime.dashboard_config())
                elif path == "/api/latest":
                    self._send_json(runtime.latest_payload())
                elif path == "/api/history":
                    limit = int(query.get("limit", ["240"])[0])
                    self._send_json(runtime.history_payload(limit))
                elif path == "/api/stepper/status":
                    self._send_json(runtime.stepper_status())
                elif path == "/api/recordings":
                    self._send_json(runtime.recordings_payload())
                elif path == "/api/export/latest":
                    self._send_file(runtime.latest_export_path(), download_name="export.csv")
                elif path == "/api/export":
                    run_id = query.get("run_id", [""])[0]
                    filename = query.get("file", ["export.csv"])[0]
                    self._send_file(runtime.artifact_path(run_id, filename))
                elif path == "/api/events":
                    self._send_events()
                else:
                    self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except FileNotFoundError as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.NOT_FOUND,
                )

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            try:
                if path == "/api/run/start":
                    self._send_json({"run": runtime.set_recording(True)})
                elif path == "/api/run/stop":
                    self._send_json({"run": runtime.set_recording(False)})
                elif path == "/api/metadata":
                    metadata = runtime.update_metadata(parse_body(self))
                    self._send_json({"metadata": metadata})
                elif path == "/api/solenoid/toggle":
                    index = int(query.get("n", [""])[0])
                    self._send_json(runtime.toggle_solenoid(index))
                elif path == "/api/stepper/move":
                    self._send_json(runtime.move_stepper(parse_body(self)))
                elif path == "/api/stepper/stop":
                    self._send_json(runtime.stop_stepper())
                elif path == "/api/stepper/estop":
                    self._send_json(runtime.emergency_stop_stepper())
                elif path == "/api/stepper/estop/reset":
                    self._send_json(runtime.reset_stepper_emergency_stop())
                elif path == "/api/stepper/motor/toggle":
                    self._send_json(runtime.toggle_stepper_brushless_motor())
                elif path == "/api/stepper/motor/pulse":
                    self._send_json(
                        runtime.set_stepper_brushless_pulse(parse_body(self))
                    )
                elif path == "/api/stepper/home":
                    self._send_json(runtime.home_stepper())
                elif path == "/api/stepper/control-mode":
                    self._send_json(
                        runtime.set_stepper_control_mode(parse_body(self))
                    )
                elif path == "/api/stepper/local-run":
                    self._send_json(runtime.set_stepper_local_run(parse_body(self)))
                elif path == "/api/stepper/speed":
                    self._send_json(runtime.set_stepper_speed(parse_body(self)))
                elif path == "/api/stepper/dro-zero":
                    self._send_json(runtime.set_stepper_dro_zero())
                else:
                    self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except FileNotFoundError as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.NOT_FOUND,
                )
            except RuntimeError as exc:
                self._send_json(
                    {"error": str(exc)},
                    status=HTTPStatus.CONFLICT,
                )

        def _send_html(self, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _send_dashboard_asset(self, request_path: str) -> None:
            """Serve only the dashboard assets declared above."""

            content_type = DASHBOARD_ASSET_TYPES[request_path]
            relative_path = request_path.removeprefix("/assets/")
            payload = (DASHBOARD_ASSET_DIR / relative_path).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _send_json(
            self,
            payload: dict[str, object],
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, download_name: str | None = None) -> None:
            if not path.exists() or not path.is_file():
                self._send_json(
                    {"error": f"artifact not found: {path.name}"},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            suffix = path.suffix.lower()
            content_type = "application/json" if suffix == ".json" else "text/csv"
            payload = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            name = download_name or path.name
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            self.wfile.write(payload)

        def _send_sse_event(self, event: str, payload: dict[str, object]) -> None:
            body = (
                f"event: {event}\n"
                f"data: {json.dumps(payload, separators=(',', ':'), sort_keys=True)}\n\n"
            )
            self.wfile.write(body.encode("utf-8"))
            self.wfile.flush()

        def _send_events(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            sequence = 0
            try:
                state_payload = runtime.state()
                run_payload = state_payload.get("run", {})
                if isinstance(run_payload, dict):
                    self._send_sse_event("state", run_payload)
                while True:
                    sequence, sample = runtime.wait_for_sample(sequence)
                    if sample is None:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        continue
                    self._send_sse_event("sample", sample)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler
