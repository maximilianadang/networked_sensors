"""Contract tests for the laptop-side ESP32 HTTP/SSE adapter."""

from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from networked_sensors.dashboard import DashboardRuntime, INDEX_HTML, parse_args
from networked_sensors.supervisor_core import RealEsp32Source, SourceMerger


class Esp32FirmwareLayoutTests(unittest.TestCase):
    def test_primary_firmware_is_headless_and_legacy_page_is_archived(self) -> None:
        root = Path(__file__).resolve().parent
        primary = (root / "Flow_management_unit_sch1.ino").read_text()
        legacy = (
            root
            / "legacy"
            / "Flow_management_unit_sch1"
            / "Flow_management_unit_sch1.ino"
        ).read_text()

        self.assertNotIn("const char HTML[]", primary)
        self.assertNotIn('"text/html"', primary)
        self.assertNotIn('server.on("/test/', primary)
        self.assertIn('\\"api_version\\":3', primary)
        self.assertIn('\\"p_adc_ok\\":%s', primary)
        self.assertIn('\\"f_adc_ok\\":%s', primary)
        self.assertNotIn("FATAL: sensor hardware unavailable", primary)
        self.assertIn('\\"p_v\\":[', primary)
        self.assertIn('\\"f_v\\":[', primary)
        self.assertIn("{5, 6, 9, 10}", primary)
        self.assertIn("solenoidOn[3]", primary)
        self.assertIn("const char HTML[]", legacy)
        self.assertIn('"text/html"', legacy)
        self.assertIn('id="sol3"', INDEX_HTML)
        self.assertIn("const solenoidCount = 4", INDEX_HTML)
        self.assertIn('id="espPressureAdc"', INDEX_HTML)
        self.assertIn('id="espFlowAdc"', INDEX_HTML)
        self.assertIn('mode === "off"', INDEX_HTML)
        self.assertIn("realAndLive", INDEX_HTML)
        self.assertIn("const pendingSolenoids = new Set()", INDEX_HTML)
        self.assertIn("function stopPollingFallback()", INDEX_HTML)
        self.assertIn("}, 100);", INDEX_HTML)

    def test_dashboard_solenoid_keyboard_shortcuts_are_guarded(self) -> None:
        for key in range(1, 5):
            self.assertIn(f'aria-keyshortcuts="{key}"', INDEX_HTML)
        self.assertIn('document.addEventListener("keydown", event => {', INDEX_HTML)
        self.assertIn('async function toggleSolenoid(index)', INDEX_HTML)
        self.assertIn('void toggleSolenoid(index)', INDEX_HTML)
        self.assertIn('target.isContentEditable', INDEX_HTML)
        self.assertIn('["INPUT", "TEXTAREA", "SELECT"]', INDEX_HTML)
        self.assertIn('event.defaultPrevented || event.repeat', INDEX_HTML)
        self.assertIn('event.ctrlKey || event.altKey || event.metaKey', INDEX_HTML)
        self.assertIn('if (!button || button.disabled) return;', INDEX_HTML)

    def test_dashboard_export_download_does_not_navigate_live_page(self) -> None:
        self.assertNotIn('window.location.href = "/api/export/latest"', INDEX_HTML)
        self.assertIn('const link = document.createElement("a")', INDEX_HTML)
        self.assertIn('link.href = "/api/export/latest"', INDEX_HTML)
        self.assertIn('link.download = "export.csv"', INDEX_HTML)
        self.assertIn('document.body.appendChild(link)', INDEX_HTML)
        self.assertIn('link.click()', INDEX_HTML)
        self.assertIn('link.remove()', INDEX_HTML)


class _Esp32ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Esp32ContractHandler)
        self.solenoids = [False, True, False, True]
        self.toggle_requests: list[int] = []
        self.toggle_started = threading.Event()
        self.release_toggle = threading.Event()
        self.release_toggle.set()
        self.release_stream = threading.Event()
        self.reading_payload = (
            b'{"v":3,"sample_ms":100,"p_adc_ok":true,"f_adc_ok":true,'
            b'"p":[1.25,2.5,3.75],"f":[10.0,20.5,30.25],'
            b'"p_v":[1.0,1.5,2.0],"f_v":[1.1,1.2,1.3],'
            b'"sol":[false,true,false,true]}'
        )


class _Esp32ContractHandler(BaseHTTPRequestHandler):
    server: _Esp32ContractServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        if urlparse(self.path).path != "/events":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        solenoids = str(self.server.solenoids).lower().encode("ascii")
        self.wfile.write(b"event: sol\n")
        self.wfile.write(b"data: " + solenoids + b"\n\n")
        self.wfile.write(b"id: 100\n")
        self.wfile.write(b"event: reading\n")
        self.wfile.write(b"data: " + self.server.reading_payload + b"\n\n")
        self.wfile.flush()
        self.server.release_stream.wait(2.0)

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        parsed = urlparse(self.path)
        if parsed.path != "/solenoid/toggle":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        index = int(parse_qs(parsed.query)["n"][0])
        self.server.toggle_requests.append(index)
        self.server.solenoids[index] = not self.server.solenoids[index]
        body = b"ON" if self.server.solenoids[index] else b"OFF"
        self.server.toggle_started.set()
        self.server.release_toggle.wait(2.0)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RealEsp32SourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Esp32ContractServer()
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.server_thread.start()
        host, port = self.server.server_address
        self.source = RealEsp32Source(
            f"http://{host}:{port}",
            timeout=0.5,
            reconnect_s=0.05,
        )

    def tearDown(self) -> None:
        self.server.release_toggle.set()
        self.server.release_stream.set()
        self.source.close()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=1.0)

    def _wait_for_reading(self) -> object:
        deadline = time.monotonic() + 2.0
        elapsed_s = 0.0
        while time.monotonic() < deadline:
            reading = self.source.poll(elapsed_s)
            if reading is not None:
                return reading
            elapsed_s += 0.01
            time.sleep(0.01)
        self.fail(f"timed out waiting for ESP32 reading: {self.source.last_error}")

    def test_requires_and_decodes_versioned_headless_firmware_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "required payload version 2 or 3"):
            RealEsp32Source.decode_reading_data(
                '{"p":[1.25,2.5,3.75],"f":[10,20.5,30.25]}'
            )
        with self.assertRaisesRegex(ValueError, "JSON object"):
            RealEsp32Source.decode_reading_data("[]")

        values = RealEsp32Source.decode_reading_data(
            '{"v":3,"sample_ms":42,"p_adc_ok":true,"f_adc_ok":true,'
            '"p":[1,2,3],"f":[4,5,6],'
            '"p_v":[0.9,1.3,1.7],"f_v":[1.1,1.2,1.3],'
            '"sol":[true,false,true,false]}'
        )
        self.assertEqual(values["esp32_payload_version"], 3)
        self.assertEqual(values["esp32_sample_ms"], 42)
        self.assertTrue(values["esp32_pressure_adc_ready"])
        self.assertTrue(values["esp32_flow_adc_ready"])
        self.assertEqual(values["esp32_p_combined_bar"], 1.0)
        self.assertEqual(values["esp32_f_combined_gmin"], 15.0)
        self.assertEqual(values["esp32_p2_volt"], 1.3)
        self.assertEqual(values["esp32_f3_volt"], 1.3)
        self.assertEqual(values["esp32_sol3"], True)
        self.assertEqual(values["esp32_sol4"], False)

        legacy_values = RealEsp32Source.decode_reading_data(
            '{"v":2,"sample_ms":42,"p":[1,2,3],"f":[4,5,6],'
            '"p_v":[0.9,1.3,1.7],"f_v":[1.1,1.2,1.3],'
            '"sol":[true,false,true,false]}'
        )
        self.assertTrue(legacy_values["esp32_pressure_adc_ready"])
        self.assertTrue(legacy_values["esp32_flow_adc_ready"])

        missing_pressure = RealEsp32Source.decode_reading_data(
            '{"v":3,"sample_ms":43,"p_adc_ok":false,"f_adc_ok":true,'
            '"p":[null,null,null],"f":[4,5,6],'
            '"p_v":[null,null,null],"f_v":[1.1,1.2,1.3],'
            '"sol":[false,false,false,false]}'
        )
        self.assertFalse(missing_pressure["esp32_pressure_adc_ready"])
        self.assertTrue(missing_pressure["esp32_flow_adc_ready"])
        self.assertIsNone(missing_pressure["esp32_p1_bar"])
        self.assertIsNone(missing_pressure["esp32_p_combined_bar"])
        self.assertEqual(missing_pressure["esp32_f_combined_gmin"], 15.0)

        with self.assertRaisesRegex(ValueError, "3 null values"):
            RealEsp32Source.decode_reading_data(
                '{"v":3,"sample_ms":43,"p_adc_ok":false,"f_adc_ok":true,'
                '"p":[0,0,0],"f":[4,5,6],'
                '"p_v":[null,null,null],"f_v":[1.1,1.2,1.3],'
                '"sol":[false,false,false,false]}'
            )

        with self.assertRaisesRegex(ValueError, "exactly 4 booleans"):
            RealEsp32Source.decode_reading_data(
                '{"v":3,"sample_ms":42,"p_adc_ok":true,"f_adc_ok":true,'
                '"p":[1,2,3],"f":[4,5,6],'
                '"p_v":[0.9,1.3,1.7],"f_v":[1.1,1.2,1.3],'
                '"sol":[true,false,true]}'
            )
        with self.assertRaisesRegex(ValueError, "exactly 3"):
            RealEsp32Source.decode_reading_data(
                '{"v":3,"p_adc_ok":true,"f_adc_ok":true,'
                '"p":[1,2],"f":[1,2,3]}'
            )
        with self.assertRaisesRegex(ValueError, "finite"):
            RealEsp32Source.decode_reading_data(
                '{"v":3,"p_adc_ok":true,"f_adc_ok":true,'
                '"p":[1,2,NaN],"f":[1,2,3]}'
            )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            RealEsp32Source.decode_reading_data(
                '{"v":1,"p":[1,2,3],"f":[1,2,3]}'
            )
        with self.assertRaisesRegex(ValueError, "sample_ms"):
            RealEsp32Source.decode_reading_data(
                '{"v":3,"p_adc_ok":true,"f_adc_ok":true,'
                '"p":[1,2,3],"f":[1,2,3]}'
            )

    def test_sse_stream_projects_readings_and_solenoids_into_schema(self) -> None:
        reading = self._wait_for_reading()
        self.assertEqual(reading.mode, "real")
        self.assertEqual(reading.values["esp32_p2_bar"], 2.5)
        self.assertEqual(reading.values["esp32_f3_gmin"], 30.25)
        self.assertEqual(reading.values["esp32_sol1"], False)
        self.assertEqual(reading.values["esp32_sol2"], True)
        self.assertEqual(reading.values["esp32_sol4"], True)
        self.assertEqual(reading.values["esp32_payload_version"], 3)
        self.assertEqual(reading.values["esp32_sample_ms"], 100)
        self.assertTrue(reading.values["esp32_pressure_adc_ready"])
        self.assertTrue(reading.values["esp32_flow_adc_ready"])
        self.assertEqual(reading.values["esp32_p1_volt"], 1.0)
        self.assertEqual(reading.values["esp32_f3_volt"], 1.3)

        merger = SourceMerger([self.source], stale_after_s=0.5)
        deadline = time.monotonic() + 2.0
        sample = None
        elapsed_s = 0.0
        while time.monotonic() < deadline:
            candidate = merger.poll(elapsed_s, datetime.now(timezone.utc))
            if candidate["esp32_connected"]:
                sample = candidate
                break
            elapsed_s += 0.01
            time.sleep(0.01)
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(sample["esp32_p1_volt"], 1.0)
        self.assertIsNone(sample["esp32_transport_error"])

    def test_missing_adcs_keep_transport_and_solenoid_state_live(self) -> None:
        self.server.reading_payload = (
            b'{"v":3,"sample_ms":101,"p_adc_ok":false,"f_adc_ok":false,'
            b'"p":[null,null,null],"f":[null,null,null],'
            b'"p_v":[null,null,null],"f_v":[null,null,null],'
            b'"sol":[false,true,false,true]}'
        )
        merger = SourceMerger([self.source], stale_after_s=0.5)
        deadline = time.monotonic() + 2.0
        sample = None
        elapsed_s = 0.0
        while time.monotonic() < deadline:
            candidate = merger.poll(elapsed_s, datetime.now(timezone.utc))
            if candidate["esp32_connected"]:
                sample = candidate
                break
            elapsed_s += 0.01
            time.sleep(0.01)
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertTrue(sample["esp32_connected"])
        self.assertFalse(sample["esp32_pressure_adc_ready"])
        self.assertFalse(sample["esp32_flow_adc_ready"])
        self.assertIsNone(sample["esp32_p_combined_bar"])
        self.assertIsNone(sample["esp32_f_combined_gmin"])
        self.assertEqual(sample["esp32_sol2"], True)

    def test_solenoid_toggle_uses_existing_post_endpoint(self) -> None:
        self._wait_for_reading()
        state = self.source.toggle_solenoid(3)
        self.assertFalse(state)
        self.assertEqual(self.server.toggle_requests, [3])
        self.assertEqual(self.source.solenoid_states(), (False, True, False, False))

    def test_mdns_address_is_cached_for_control_requests(self) -> None:
        source = RealEsp32Source("http://testbench.local")
        address = (2, 1, 6, "", ("192.168.8.42", 80))
        try:
            with patch(
                "networked_sensors.supervisor_core.socket.getaddrinfo",
                return_value=[address],
            ) as resolver:
                self.assertEqual(
                    source._resolve_transport_base_url(),
                    "http://192.168.8.42",
                )
                self.assertEqual(
                    source._resolve_transport_base_url(),
                    "http://192.168.8.42",
                )
                resolver.assert_called_once()
        finally:
            source.close()

    def test_dashboard_runtime_accepts_real_esp32_source(self) -> None:
        host, port = self.server.server_address
        runtime = DashboardRuntime(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=0.5,
            history_limit=20,
            record_dir=Path("/tmp/real-esp32-dashboard-test-recordings"),
            esp32_source="real",
            esp32_base_url=f"http://{host}:{port}",
            esp32_timeout=0.5,
            dxmr90_source="off",
            stepper_source="off",
            stepper_port="/dev/null",
            stepper_baud=9600,
            dxmr90_host="127.0.0.1",
            dxmr90_port=502,
            dxmr90_unit_id=1,
            dxmr90_timeout=0.1,
            dxmr90_addressing="one-based",
            dxmr90_word_order="high-low",
            dxmr90_data_path="direct",
            dxmr90_rate_hz=10.0,
        )
        try:
            runtime.start()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                state = runtime.state()
                if state["sample"]["esp32_connected"]:
                    break
                time.sleep(0.02)
            else:
                self.fail("dashboard did not receive the fake ESP32 SSE stream")

            state = runtime.state()
            self.assertEqual(state["run"]["esp32_source"], "real")
            self.assertEqual(state["sample"]["esp32_p3_bar"], 3.75)
            self.server.release_toggle.clear()
            result: dict[str, object] = {}
            errors: list[BaseException] = []

            def issue_toggle() -> None:
                try:
                    result["payload"] = runtime.toggle_solenoid(3)
                except BaseException as exc:  # pragma: no cover - surfaced below
                    errors.append(exc)

            before_sequence = runtime.sequence
            command_thread = threading.Thread(target=issue_toggle)
            command_thread.start()
            self.assertTrue(self.server.toggle_started.wait(1.0))
            time.sleep(0.25)
            self.assertTrue(command_thread.is_alive())
            self.assertGreaterEqual(runtime.sequence, before_sequence + 2)
            self.server.release_toggle.set()
            command_thread.join(timeout=1.0)
            self.assertFalse(command_thread.is_alive())
            if errors:
                raise errors[0]
            toggled = result["payload"]
            assert isinstance(toggled, dict)
            self.assertFalse(toggled["state"])
            self.assertEqual(self.server.toggle_requests, [3])
        finally:
            runtime.stop()

    def test_dashboard_cli_accepts_real_esp32_configuration(self) -> None:
        args = parse_args(
            [
                "--esp32-source",
                "real",
                "--esp32-url",
                "http://192.168.8.42",
                "--esp32-timeout",
                "1.25",
            ]
        )
        self.assertEqual(args.esp32_source, "real")
        self.assertEqual(args.esp32_url, "http://192.168.8.42")
        self.assertEqual(args.esp32_timeout, 1.25)


if __name__ == "__main__":
    unittest.main()
