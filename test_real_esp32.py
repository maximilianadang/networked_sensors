"""Contract tests for the laptop-side ESP32 HTTP/SSE adapter."""

from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from networked_sensors.dashboard import (
    DashboardRuntime,
    DashboardServer,
    INDEX_HTML,
    build_handler,
    load_dashboard_asset,
    parse_args,
)
from networked_sensors.recorder import FlowRunRecorder
from networked_sensors.supervisor_core import (
    RealEsp32Source,
    SimulatedDxmr90Source,
    SimulatedEsp32Source,
    SourceMerger,
)


DASHBOARD_CSS = load_dashboard_asset("dashboard.css")
APP_JS = load_dashboard_asset("app.js")
API_JS = load_dashboard_asset("api.js")
CHARTS_JS = load_dashboard_asset("charts.js")
CONFIG_JS = load_dashboard_asset("config.js")
DOM_JS = load_dashboard_asset("dom.js")
METRICS_JS = load_dashboard_asset("components/metrics.js")
METADATA_JS = load_dashboard_asset("components/metadata.js")
SOURCES_JS = load_dashboard_asset("components/sources.js")
TOOLBAR_JS = load_dashboard_asset("components/toolbar.js")


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
        self.assertIn("{5, 6, 9, 10}", (root / "wiring_esp32.h").read_text())
        self.assertIn("solenoidOn[3]", primary)
        self.assertIn("const char HTML[]", legacy)
        self.assertIn('"text/html"', legacy)
        self.assertIn('id="sol3"', INDEX_HTML)
        self.assertIn('id="espPressureAdc"', INDEX_HTML)
        self.assertIn('id="espFlowAdc"', INDEX_HTML)
        self.assertIn('mode === "off"', SOURCES_JS)
        self.assertIn("solenoid${index + 1}_connected", TOOLBAR_JS)
        self.assertIn("const pendingSolenoids = new Set()", TOOLBAR_JS)
        self.assertIn("function stopPollingFallback()", APP_JS)
        self.assertIn("pollingIntervalMs: 100", CONFIG_JS)

    def test_dashboard_solenoid_keyboard_shortcuts_are_guarded(self) -> None:
        for key in range(1, 5):
            self.assertIn(f'aria-keyshortcuts="{key}"', INDEX_HTML)
        self.assertIn('document.addEventListener("keydown", event => {', APP_JS)
        self.assertIn('async function toggleSolenoid(index)', TOOLBAR_JS)
        self.assertIn('void toolbar.toggleSolenoid(index)', APP_JS)
        self.assertIn('target.isContentEditable', DOM_JS)
        self.assertIn('["INPUT", "TEXTAREA", "SELECT"]', DOM_JS)
        self.assertIn('event.defaultPrevented || event.repeat', DOM_JS)
        self.assertIn('event.ctrlKey || event.altKey || event.metaKey', DOM_JS)
        self.assertIn('if (!button || button.disabled', TOOLBAR_JS)

    def test_dashboard_export_download_does_not_navigate_live_page(self) -> None:
        self.assertNotIn('window.location.href = "/api/export/latest"', API_JS)
        self.assertIn('const link = document.createElement("a")', API_JS)
        self.assertIn('exportLatest: "/api/export/latest"', API_JS)
        self.assertIn('link.download = "export.csv"', API_JS)
        self.assertIn('document.body.appendChild(link)', API_JS)
        self.assertIn('link.click()', API_JS)
        self.assertIn('link.remove()', API_JS)

    def test_dashboard_light_theme_and_operator_panel_order(self) -> None:
        self.assertIn("color-scheme: light", DASHBOARD_CSS)
        self.assertIn("--control: #ffffff", DASHBOARD_CSS)
        self.assertIn("--chart-bg: #ffffff", DASHBOARD_CSS)
        self.assertIn('ctx.fillStyle = themeColor("--chart-bg")', CHARTS_JS)
        self.assertIn('ctx.strokeStyle = themeColor("--chart-grid")', CHARTS_JS)
        self.assertIn('ctx.fillStyle = themeColor("--chart-label")', CHARTS_JS)
        self.assertIn('themeColor(item.color)', CHARTS_JS)
        self.assertNotIn('ctx.fillStyle = "#12161b"', CHARTS_JS)
        self.assertNotIn("background: #242a32", DASHBOARD_CSS)
        stepper_panel = INDEX_HTML.index("<h2>Motor Control</h2>")
        metadata_panel = INDEX_HTML.index("<h2>Test Metadata</h2>")
        sources_panel = INDEX_HTML.index("<span>Source details</span>")
        self.assertLess(stepper_panel, metadata_panel)
        self.assertLess(stepper_panel, sources_panel)
        self.assertLess(metadata_panel, sources_panel)
        self.assertLess(
            INDEX_HTML.index("</section>", metadata_panel),
            sources_panel,
        )
        self.assertIn('<article class="control-panel stepper-panel">', INDEX_HTML)
        self.assertIn('<article class="metadata-panel">', INDEX_HTML)
        self.assertIn('<details class="source-panel source-drawer">', INDEX_HTML)
        source_drawer_rule = DASHBOARD_CSS.split(".source-drawer {", 1)[1].split(
            "}",
            1,
        )[0]
        source_body_rule = DASHBOARD_CSS.split(
            ".source-drawer .source-body {",
            1,
        )[1].split("}", 1)[0]
        self.assertNotIn("position: fixed", source_drawer_rule)
        self.assertIn("width: 100%", source_drawer_rule)
        self.assertIn("position: static", source_body_rule)
        self.assertNotIn("padding-right: 265px", DASHBOARD_CSS)
        self.assertIn(
            '<label>Powder flow rate (g/s)<input name="powder_flow_rate_g_per_s"',
            INDEX_HTML,
        )
        self.assertIn(
            '<label>Desired test duration (s)<input name="test_duration_s"',
            INDEX_HTML,
        )
        self.assertLess(
            INDEX_HTML.index('name="powder_flow_rate_g_per_s"'),
            INDEX_HTML.index('name="test_duration_s"'),
        )
        self.assertIn(
            '<label class="wide">Description<input name="description"',
            INDEX_HTML,
        )
        self.assertIn('class="wide metadata-notes"', INDEX_HTML)
        self.assertIn(
            ".lower-grid {\n  display: grid;\n  grid-template-columns: repeat(2, minmax(0, 1fr));",
            DASHBOARD_CSS,
        )

    def test_dashboard_uses_field_readable_type_and_compact_top_controls(self) -> None:
        self.assertNotIn("<h1>Flow Management Supervisor</h1>", INDEX_HTML)
        self.assertNotIn('id="configuredMode"', INDEX_HTML)
        self.assertNotIn('id="sampleText"', INDEX_HTML)
        self.assertNotIn('id="recordingText"', INDEX_HTML)
        self.assertNotIn('class="readout"', INDEX_HTML)
        self.assertIn('id="clockText" class="clock-text"', INDEX_HTML)
        self.assertIn("Dashboard connecting", INDEX_HTML)
        self.assertIn("Motion controller", INDEX_HTML)
        self.assertIn("Not recording", INDEX_HTML)
        self.assertIn(
            "`Dashboard ${label.toLowerCase()}`",
            APP_JS,
        )
        self.assertIn(
            'stepperMode === "controllino" ? "Controllino MAXI" : "Arduino Yun"',
            SOURCES_JS,
        )
        self.assertIn(
            'recording ? "Recording" : "Not recording"',
            TOOLBAR_JS,
        )
        self.assertIn("date.toISOString()", TOOLBAR_JS)
        self.assertIn('} UTC`', TOOLBAR_JS)
        self.assertNotIn("date.toLocaleString", TOOLBAR_JS)
        self.assertIn("font-size: 1.25rem", DASHBOARD_CSS)
        self.assertIn(">E-STOP</button>", INDEX_HTML)
        self.assertNotIn("SOFTWARE E-STOP", INDEX_HTML)
        self.assertIn(
            "grid-template-columns: repeat(6, minmax(0, 1fr))",
            DASHBOARD_CSS,
        )
        self.assertIn(
            "grid-template-columns: minmax(360px, 1fr) minmax(240px, 300px) minmax(480px, 1fr)",
            DASHBOARD_CSS,
        )
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr))", DASHBOARD_CSS)
        self.assertIn("min-height: 100dvh", DASHBOARD_CSS)
        self.assertIn(
            "clamp(56px, 8dvh, 72px)",
            DASHBOARD_CSS,
        )
        self.assertIn("clamp(60px, 8dvh, 82px)", DASHBOARD_CSS)
        self.assertIn("minmax(105px, 2fr)", DASHBOARD_CSS)
        self.assertIn("minmax(375px, 3fr)", DASHBOARD_CSS)
        self.assertEqual(DASHBOARD_CSS.count("overflow: hidden"), 3)
        self.assertIn("overflow: hidden !important", DASHBOARD_CSS)
        self.assertIn(
            "never conceal a layout failure with overflow clipping",
            DASHBOARD_CSS,
        )
        self.assertIn("max-width: 300px", DASHBOARD_CSS)
        self.assertIn("min-height: 76px", DASHBOARD_CSS)
        self.assertIn("font-size: 1.25rem", DASHBOARD_CSS)
        self.assertNotRegex(DASHBOARD_CSS, r"font-size:\s*0\.")
        self.assertIn("`${16 * ratio}px system-ui, sans-serif`", CHARTS_JS)

    def test_dashboard_chart_layout_and_open_flow_sums(self) -> None:
        pressure_panel = INDEX_HTML.index("<h2>Pressure (bar)</h2>")
        esp_flow_panel = INDEX_HTML.index("<h2>ESP32 Mass Flow (g/min)</h2>")
        sick_flow_panel = INDEX_HTML.index("<h2>SICK Mass Flow (g/min)</h2>")
        self.assertLess(pressure_panel, esp_flow_panel)
        self.assertLess(esp_flow_panel, sick_flow_panel)
        self.assertIn("main {\n  width: 100%;\n  margin: 0;", DASHBOARD_CSS)
        self.assertNotIn("width: min(1480px, 100%)", DASHBOARD_CSS)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", DASHBOARD_CSS)
        self.assertIn("height: 320px", DASHBOARD_CSS)

        self.assertIn('id="pressureChart"', INDEX_HTML)
        self.assertIn('id="espFlowChart"', INDEX_HTML)
        self.assertIn('id="sickFlowChart"', INDEX_HTML)
        self.assertNotIn('id="sickPressureChart"', INDEX_HTML)
        self.assertNotIn('id="flowChart"', INDEX_HTML)

        for field in (
            "esp32_p1_bar",
            "esp32_p2_bar",
            "esp32_p3_bar",
            "dxmr90_port1_pressure_bar",
            "dxmr90_port2_pressure_bar",
            "esp32_f1_gmin",
            "esp32_f2_gmin",
            "esp32_f3_gmin",
            "dxmr90_port1_mass_flow_g_min",
            "dxmr90_port2_mass_flow_g_min",
        ):
            self.assertIn(f'key: "{field}"', CONFIG_JS)

        self.assertNotIn("espFlowSumToggle", INDEX_HTML)
        self.assertNotIn("sickFlowSumToggle", INDEX_HTML)
        self.assertNotIn("chart-toggle", INDEX_HTML)
        self.assertEqual(INDEX_HTML.count(">Open SUM</span>"), 2)
        self.assertIn('key: "esp32_open_flow_gmin"', CONFIG_JS)
        self.assertIn('key: "dxmr90_open_total_mass_flow_g_min"', CONFIG_JS)

    def test_dashboard_metric_grid_uses_channel_and_open_line_summaries(self) -> None:
        self.assertIn(
            "grid-template-columns: repeat(6, minmax(140px, 1fr))",
            DASHBOARD_CSS,
        )
        self.assertIn("<label>ESP32 Pressure</label>", INDEX_HTML)
        for channel in range(1, 4):
            self.assertIn(f'id="mEspP{channel}"', INDEX_HTML)
            self.assertIn(
                f'numberValue(sample, "esp32_p{channel}_bar", digits.esp32Pressure)',
                METRICS_JS,
            )
        self.assertIn("<label>SICK Pressure (max)</label>", INDEX_HTML)
        self.assertIn('setText(els.mSickPressure, maxNumberValue(sample, [', METRICS_JS)
        self.assertIn('"dxmr90_port1_pressure_bar"', METRICS_JS)
        self.assertIn('"dxmr90_port2_pressure_bar"', METRICS_JS)

        self.assertIn("<label>Open-Line Air Flow</label>", INDEX_HTML)
        self.assertIn(
            'numberValue(sample, "esp32_open_flow_gmin", digits.massFlow)',
            METRICS_JS,
        )
        self.assertIn("<label>Air:Powder Ratio</label>", INDEX_HTML)
        self.assertIn('id="mAirPowderRatio"', INDEX_HTML)
        self.assertIn(
            "airFlowGPerMin / (powderFlowGPerS * 60)",
            METRICS_JS,
        )
        self.assertLess(
            INDEX_HTML.index("<label>Open-Line Air Flow</label>"),
            INDEX_HTML.index("<label>Air:Powder Ratio</label>"),
        )

        self.assertIn("<label>SICK Flow · Solenoid 4</label>", INDEX_HTML)
        self.assertIn(
            'numberValue(sample, "dxmr90_open_total_mass_flow_g_min", digits.massFlow)',
            METRICS_JS,
        )
        self.assertIn("<label>Heartbeat</label>", INDEX_HTML)
        self.assertNotIn("<label>P combined</label>", INDEX_HTML)

    def test_powder_metadata_derives_web_position_setpoints(self) -> None:
        self.assertIn(
            "geometry.powder_mass_per_stepper_travel_g_per_mm",
            METADATA_JS,
        )
        self.assertIn(
            "const speedMmS = powderFlowGPerS / powderMassPerTravel;",
            METADATA_JS,
        )
        self.assertIn("speedMmS * durationS", METADATA_JS)
        self.assertIn("applyMotionPlan({speedMmS, distanceMm});", METADATA_JS)
        self.assertIn("onPowderFlowChange(powderFlowGPerS);", METADATA_JS)


class DashboardAssetServingTests(unittest.TestCase):
    def test_serves_local_assets_and_read_only_configuration(self) -> None:
        runtime = DashboardRuntime(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=5.0,
            history_limit=20,
            record_dir=Path("/tmp/dashboard-asset-test-recordings"),
            esp32_source="sim",
            dxmr90_source="sim",
            stepper_source="sim",
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
        server = DashboardServer(("127.0.0.1", 0), build_handler(runtime, quiet=True))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"
        try:
            expected = {
                "/": "text/html",
                "/assets/dashboard.css": "text/css",
                "/assets/app.js": "text/javascript",
                "/assets/components/stepper.js": "text/javascript",
                "/api/config": "application/json",
            }
            for path, content_type in expected.items():
                with self.subTest(path=path), urlopen(base_url + path) as response:
                    self.assertEqual(response.status, HTTPStatus.OK)
                    self.assertEqual(response.headers.get_content_type(), content_type)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertTrue(response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)
            runtime.stop()


class OpenFlowSummaryTests(unittest.TestCase):
    def test_merger_sums_only_channels_with_open_solenoids(self) -> None:
        esp32 = SimulatedEsp32Source(auto_sequence=False)
        dxmr90 = SimulatedDxmr90Source()
        merger = SourceMerger([esp32, dxmr90])
        timestamp = datetime.now(timezone.utc)

        closed = merger.poll(1.0, timestamp)
        self.assertEqual(closed["esp32_open_flow_gmin"], 0.0)
        self.assertEqual(closed["dxmr90_open_total_mass_flow_g_min"], 0.0)

        esp32.set_solenoid(0, True)
        esp32.set_solenoid(3, True)
        opened = merger.poll(1.1, timestamp)
        self.assertEqual(
            opened["esp32_open_flow_gmin"],
            opened["esp32_f1_gmin"],
        )
        self.assertEqual(
            opened["dxmr90_open_total_mass_flow_g_min"],
            opened["dxmr90_total_mass_flow_g_min"],
        )

        esp32.set_solenoid(1, True)
        two_esp32_lines = merger.poll(1.2, timestamp)
        self.assertAlmostEqual(
            two_esp32_lines["esp32_open_flow_gmin"],
            two_esp32_lines["esp32_f1_gmin"] + two_esp32_lines["esp32_f2_gmin"],
            places=2,
        )


class PowderMetadataRecordingTests(unittest.TestCase):
    def test_export_preserves_g_per_s_and_converts_legacy_g_per_min(self) -> None:
        with TemporaryDirectory() as directory:
            recorder = FlowRunRecorder(
                record_dir=Path(directory),
                metadata={
                    "sample_number": "powder-plan",
                    "sub_number": "1",
                    "powder_flow_rate_g_per_s": "6.0",
                    "test_duration_s": "4.0",
                },
                run_config={},
                source_fields={},
            )
            summary = recorder.finish()
            export_path = Path(summary["paths"]["export_csv"])
            export_text = export_path.read_text(encoding="utf-8")
            self.assertIn("# powder_flow_rate_g_per_min:,360.000", export_text)
            self.assertIn("# powder_flow_rate_g_per_s:,6.0", export_text)
            self.assertIn("# test_duration_s:,4.0", export_text)


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
