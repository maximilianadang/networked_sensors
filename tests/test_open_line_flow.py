"""Open-line measurement selection and recording use one merged, healthy value."""
import csv
import math
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import supervisor_core as core
from dashboard_app.runtime import SourceMerger
from recorder import FlowRunRecorder


class Feed:
    """In-memory source; suppress emission to exercise real merger freshness."""
    period_s = .1

    def __init__(self, name, mode, values):
        self.name, self.mode, self.values = name, mode, dict(values)
        self.expected_fields = (*values, f"{name}_transport_error")
        self.emit, self.last_error = True, None

    def poll(self, elapsed_s):
        return core.SourceReading(self.name, self.mode, elapsed_s, dict(self.values)) if self.emit else None


class OpenLineFlowTests(unittest.TestCase):
    def setUp(self):
        self.stepper = Feed("stepper", "controllino", {})
        self.stepper.emit = False  # SICK still measures while the controller is absent.
        self.dxmr90 = Feed("dxmr90", "real", {
            "dxmr90_total_mass_flow_g_min": 30,
            "dxmr90_port3_mass_flow_g_min": 12,
            "dxmr90_port3_flow_valid": True, "dxmr90_port3_error": None,
        })
        self.esp32 = Feed("esp32", "real", {
            "esp32_sol1": True, "esp32_sol2": False, "esp32_sol3": False, "esp32_sol4": False,
            "esp32_f1_gmin": 42, "esp32_f2_gmin": 100, "esp32_f3_gmin": 100,
            "esp32_f_combined_gmin": 242,
        })
        self.sources = [self.stepper, self.dxmr90, self.esp32]
        self.merger = SourceMerger(self.sources, stale_after_s=.5)
        self.now = datetime.now(timezone.utc)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def sample(self, elapsed=0):
        return self.merger.poll(elapsed, self.now)

    def recorder(self, first):
        recorder = FlowRunRecorder(record_dir=Path(self.directory.name), metadata={},
            run_config={"stepper_source": self.stepper.mode},
            source_fields={source.name: source.expected_fields for source in self.sources},
            first_sample=first)
        self.addCleanup(recorder._flush_and_close)
        return recorder

    def test_controllino_uses_third_sensor_without_controller_or_valve_gating(self):
        for state in (False, True):
            self.esp32.values.update({f"esp32_sol{i}": state for i in range(1, 5)})
            row = self.sample()
            self.assertFalse(row["stepper_connected"])
            self.assertEqual(row["open_line_air_flow_g_min"], 12)
            self.assertEqual(row["open_line_air_flow_source"], "dxmr90_port3")
            self.assertEqual(row["dxmr90_total_mass_flow_g_min"], 30)
        self.dxmr90.values["dxmr90_port3_mass_flow_g_min"] = 0
        self.assertEqual(self.sample()["open_line_air_flow_g_min"], 0)

    def test_missing_invalid_and_failed_third_never_fall_back_to_other_flow(self):
        original = dict(self.dxmr90.values)
        for change in ({"dxmr90_port3_flow_valid": False}, {"dxmr90_port3_flow_valid": None},
                       {"dxmr90_port3_error": "Port 3 unavailable"},
                       *({"dxmr90_port3_mass_flow_g_min": value}
                         for value in (None, True, "12", math.nan, math.inf))):
            with self.subTest(change=change):
                self.dxmr90.values = dict(original, **change)
                row = self.sample()
                self.assertIsNone(row["open_line_air_flow_g_min"])
                self.assertEqual(row["open_line_air_flow_source"], "dxmr90_port3")
                self.assertEqual(row["dxmr90_total_mass_flow_g_min"], 30)

    def test_cached_third_is_blank_when_master_is_stale_or_has_transport_error(self):
        self.assertEqual(self.sample()["open_line_air_flow_g_min"], 12)
        self.dxmr90.emit = False
        stale = self.sample(1)
        self.assertFalse(stale["dxmr90_connected"])
        self.assertEqual(stale["dxmr90_port3_mass_flow_g_min"], 12)  # raw evidence retained
        self.assertIsNone(stale["open_line_air_flow_g_min"])
        self.dxmr90.emit = True
        self.dxmr90.last_error = "Connection lost"
        failed = self.sample(1.1)
        self.assertTrue(failed["dxmr90_connected"])
        self.assertIsNone(failed["open_line_air_flow_g_min"])
        self.dxmr90.last_error = None
        self.assertEqual(self.sample(1.2)["open_line_air_flow_g_min"], 12)

    def test_legacy_open_channels_keep_valve_selection_but_require_healthy_esp32(self):
        self.stepper.mode = "usb"
        row = self.sample()
        self.assertEqual(row["open_line_air_flow_g_min"], 42)
        self.assertEqual(row["open_line_air_flow_source"], "esp32_open_channels")
        self.esp32.values["esp32_sol1"] = False
        self.assertEqual(self.sample()["open_line_air_flow_g_min"], 0)
        self.esp32.last_error = "SSE disconnected"
        failed = self.sample()
        self.assertEqual(failed["esp32_open_flow_gmin"], 0)
        self.assertIsNone(failed["open_line_air_flow_g_min"])
        self.esp32.last_error = None
        self.esp32.emit = False
        self.assertIsNone(self.sample(1)["open_line_air_flow_g_min"])

    def test_recording_uses_canonical_third_mean_and_suppresses_old_esp32_statistics(self):
        self.dxmr90.values["dxmr90_port3_mass_flow_g_min"] = None
        self.dxmr90.values["dxmr90_port3_flow_valid"] = False
        recorder = self.recorder(self.sample())
        for elapsed, value, valid in ((.1, 20, True), (.2, 900, False),
                                       (.3, 40, True), (.4, 0, True), (.5, 100, True)):
            self.dxmr90.values.update(dxmr90_port3_mass_flow_g_min=value,
                                     dxmr90_port3_flow_valid=valid)
            recorder.record_sample(self.sample(elapsed), self.merger.fresh_readings())
        metrics = recorder.finish()["metrics"]
        self.assertEqual(metrics["open_line_air_flow_g_per_min_active"], 30)
        self.assertEqual(metrics["open_line_air_flow_source"], "dxmr90_port3")
        self.assertEqual(metrics["air_flow_g_per_min_active"], [None, None, None])
        self.assertIsNone(metrics["air_flow_combined_g_per_min_active"])
        with recorder.paths.merged_csv.open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([row["open_line_air_flow_g_min"] for row in rows], ["", "20", "", "40", "0", "100"])
        self.assertEqual(rows[2]["dxmr90_port3_mass_flow_g_min"], "900")
        self.assertEqual(rows[0]["esp32_f_combined_gmin"], "242")
        exported = recorder.paths.export_csv.read_text()
        self.assertIn("# open_line_air_flow_source:,dxmr90_port3", exported)
        self.assertIn("# open_line_air_flow_g_per_min_active:,30.00", exported)
        self.assertIn("# air_flow_combined_g_per_min:,\n", exported)

    def test_missing_or_zero_third_never_acquires_an_esp32_summary(self):
        for value in (None, 0):
            self.dxmr90.values["dxmr90_port3_mass_flow_g_min"] = value
            metrics = self.recorder(self.sample()).summary()["metrics"]
            self.assertIsNone(metrics["open_line_air_flow_g_per_min_active"])
            self.assertEqual(metrics["air_flow_g_per_min_active"], [None, None, None])
            self.assertIsNone(metrics["air_flow_combined_g_per_min_active"])

    def test_legacy_recording_preserves_old_flow_statistics_and_adds_open_line_mean(self):
        self.stepper.mode = "usb"
        metrics = self.recorder(self.sample()).summary()["metrics"]
        self.assertEqual(metrics["air_flow_g_per_min_active"], [42, 100, 100])
        self.assertEqual(metrics["air_flow_combined_g_per_min_active"], 242)
        self.assertEqual(metrics["open_line_air_flow_g_per_min_active"], 42)
        self.assertEqual(metrics["open_line_air_flow_source"], "esp32_open_channels")


if __name__ == "__main__":
    unittest.main()
