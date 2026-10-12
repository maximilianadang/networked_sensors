"""Recording summaries use configured Controllino pressure evidence, not ESP32."""
import csv
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from controllino_io import transform_pressure_sample, validate_pressure_config
from recorder import FlowRunRecorder


def calibration(unit="psi", reference="gauge", low=0, high=10):
    return {"voltage_min": .5, "voltage_max": 4.5,
            "pressure_min": low, "pressure_max": high,
            "unit": unit, "reference": reference}


class ControllinoRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = validate_pressure_config({
            "1": {"label": "Chamber pressure", "volts_per_count": .01,
                  "calibration": calibration()},
            "5": {"label": "Absolute inlet", "volts_per_count": .01,
                  "calibration": calibration("kPa", "absolute", 20, 120)},
        })
        self.recorders = []
        self.addCleanup(lambda: [r._flush_and_close() for r in self.recorders])

    def sample(self, elapsed=100, **changes):
        row = {
            "timestamp_iso": "2026-10-12T12:00:00Z", "elapsed_s": elapsed,
            "stepper_pressure_capable": True, "stepper_connected": True,
            "stepper_transport_error": None, "stepper_age_ms": 10,
            "stepper_pressure_sample_age_ms": 60,
            "esp32_p1_bar": 901, "esp32_p2_bar": 902, "esp32_p3_bar": 903,
            "esp32_p_combined_bar": 904,
            "esp32_f1_gmin": 20, "esp32_f2_gmin": 30, "esp32_f3_gmin": 40,
            "esp32_f_combined_gmin": 90,
        }
        row.update({f"stepper_pressure{i}_adc": 250 for i in range(1, 8)})
        row.update(changes)
        transform_pressure_sample(row, self.config)
        return row

    def recorder(self, first=None):
        recorder = FlowRunRecorder(
            record_dir=Path(self.temporary.name), metadata={"sample_number": "pressure"},
            run_config={"system_config": {"pressure_inputs": deepcopy(self.config)}},
            source_fields={}, first_sample=first)
        self.recorders.append(recorder)
        return recorder

    def test_configured_means_and_export_never_substitute_esp32_pressure(self):
        recorder = self.recorder(self.sample())
        recorder.record_sample(self.sample(100.5, stepper_pressure1_adc=350,
                                          stepper_pressure5_adc=150, esp32_f1_gmin=40), ())
        recorder.record_sample(self.sample(101.01, stepper_pressure1_adc=450,
                                          esp32_f1_gmin=0), ())
        summary = recorder.finish()
        metrics = summary["metrics"]
        self.assertEqual(metrics["pressure_bar_first1s"], [None, None, None])
        self.assertIsNone(metrics["pressure_combined_bar_first1s"])
        channels = metrics["controllino_pressure_first1s"]
        self.assertEqual([c["terminal"] for c in channels],
                         ["AI10", "AI11", "AI12", "AI13", "AI0", "AI1", "AI2"])
        self.assertEqual(channels[0], dict(id=1, label="Chamber pressure", terminal="AI10",
                                          group="low", group_index=1, group_label="Low pressure",
                                          unit="psi", reference="gauge", calibrated=True,
                                          mean=6.25, samples=2))
        self.assertEqual(channels[4], dict(id=5, label="Absolute inlet", terminal="AI0",
                                          group="high", group_index=1, group_label="High pressure",
                                          unit="psi", reference="absolute", calibrated=True,
                                          mean=round(57.5 / 6.894757293168361, 6), samples=2))
        self.assertIsNone(channels[1]["mean"])
        self.assertFalse(channels[1]["calibrated"])
        self.assertEqual(metrics["air_flow_g_per_min_active"][0], 30)
        exported = recorder.paths.export_csv.read_text()
        self.assertIn("# pressure_1_bar:,\n", exported)
        self.assertIn("# controllino_pressure_5_unit:,psi", exported)
        self.assertIn("# controllino_pressure_5_reference:,absolute", exported)
        self.assertIn("# controllino_pressure_1_group:,low", exported)
        self.assertIn("# controllino_pressure_5_group:,high", exported)
        self.assertIn("# controllino_pressure_5_group_index:,1", exported)
        self.assertIn("# controllino_pressure_5_group_label:,High pressure", exported)
        self.assertIn(f"# controllino_pressure_5_mean_first1s:,{57.5 / 6.894757293168361:.6f}", exported)
        with recorder.paths.merged_csv.open() as stream:
            raw = next(csv.DictReader(stream))
        self.assertEqual(raw["esp32_p1_bar"], "901")  # retained as raw source evidence
        self.assertEqual(raw["stepper_pressure1_value"], "5.0")
        self.assertAlmostEqual(float(raw["stepper_pressure5_value"]), 70 / 6.894757293168361)
        self.assertEqual(raw["stepper_pressure5_adc"], "250")
        self.assertEqual(raw["stepper_pressure5_voltage_v"], "2.5")
        saved = json.loads(recorder.paths.summary_json.read_text())
        self.assertEqual(saved["metrics"], metrics)
        self.assertEqual(saved["run_config"]["system_config"]["pressure_inputs"]["5"]["calibration"]["unit"], "kPa")

    def test_stale_invalid_and_nonpressure_values_do_not_contribute(self):
        recorder = self.recorder(self.sample())
        # Production freshness and calibration validation drive the valid flag.
        for elapsed, changes in [
            (100.1, {"stepper_pressure_sample_age_ms": 600}),
            (100.2, {"stepper_age_ms": 600}),
            (100.3, {"stepper_connected": False}),
            (100.4, {"stepper_pressure1_adc": 0}),
        ]:
            recorder.record_sample(self.sample(elapsed, **changes), ())
        for i, bad_value in enumerate((float("nan"), float("inf"), True, "42")):
            row = self.sample(100.5 + i * .1)
            row["stepper_pressure1_value"] = bad_value
            recorder.record_sample(row, ())
        metric = recorder.summary()["metrics"]["controllino_pressure_first1s"][0]
        self.assertEqual((metric["mean"], metric["samples"]), (5, 1))
        # Unknown calibration cannot acquire a pressure from raw counts/volts,
        # even if an inconsistent caller supplied an apparent value.
        row = self.sample(100.9)
        row["stepper_pressure2_value"] = 123
        recorder.record_sample(row, ())
        metric = recorder.summary()["metrics"]["controllino_pressure_first1s"][1]
        self.assertEqual((metric["mean"], metric["samples"]), (None, 0))

    def test_late_capability_and_disconnect_cannot_restore_legacy_summary(self):
        recorder = self.recorder(self.sample(stepper_pressure_capable=False))
        self.assertEqual(recorder.summary()["metrics"]["pressure_bar_first1s"], [901, 902, 903])
        recorder.record_sample(self.sample(100.2), ())
        recorder.record_sample(self.sample(100.3, stepper_pressure_capable=False,
                                          esp32_p1_bar=999), ())
        metrics = recorder.summary()["metrics"]
        self.assertEqual(metrics["pressure_bar_first1s"], [None, None, None])
        self.assertIsNone(metrics["pressure_combined_bar_first1s"])
        self.assertEqual(metrics["controllino_pressure_first1s"][0]["mean"], 5)
        self.assertEqual(metrics["controllino_pressure_first1s"][0]["samples"], 1)

    def test_existing_non_io_recordings_keep_legacy_pressure_and_flow(self):
        recorder = self.recorder(self.sample(stepper_pressure_capable=False))
        recorder.record_sample(self.sample(100.5, stepper_pressure_capable=False,
                                          esp32_p1_bar=903, esp32_p_combined_bar=906), ())
        metrics = recorder.finish()["metrics"]
        self.assertEqual(metrics["pressure_bar_first1s"], [902, 902, 903])
        self.assertEqual(metrics["pressure_combined_bar_first1s"], 905)
        self.assertEqual(metrics["air_flow_g_per_min_active"], [20, 30, 40])
        self.assertNotIn("controllino_pressure_first1s", metrics)
        self.assertIn("# pressure_1_bar:,902.000", recorder.paths.export_csv.read_text())

    def test_all_seven_calibrated_channels_record_psi_and_keep_their_references(self):
        self.config = validate_pressure_config({str(i): {
            "label": f"P{i}", "volts_per_count": .01,
            "calibration": calibration("psi", "absolute", i, i + 100),
        } for i in range(1, 8)})
        channels = self.recorder(self.sample()).summary()["metrics"]["controllino_pressure_first1s"]
        for c, source_psi in zip(channels, range(51, 58)):
            self.assertAlmostEqual(c["mean"], source_psi, places=6)
        self.assertEqual([(c["group"], c["group_index"]) for c in channels],
                         [("low", 1), ("low", 2), ("low", 3), ("low", 4),
                          ("high", 1), ("high", 2), ("high", 3)])
        self.assertTrue(all(c["unit"] == "psi" and c["reference"] == "absolute" for c in channels))


if __name__ == "__main__":
    unittest.main()
