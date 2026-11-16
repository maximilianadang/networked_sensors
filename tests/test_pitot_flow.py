"""Nitrogen Pitot derivation, missing inputs and saved derived measurements."""
import csv
import math
import tempfile
import unittest
from pathlib import Path

from controllino_io import PITOT_FIELDS, _pitot_mach, transform_pitot_sample
from recorder import FlowRunRecorder


def measurement(**changes):
    sample = {"ed593_connected": True, "ed593_transport_error": None,
              "ed593_tc0_ready": True, "ed593_tc0_temperature_c": 20.0,
              "timestamp_iso": "2026-11-12T12:00:00+00:00", "elapsed_s": 0.0}
    for channel, value in ((1, 4.0), (8, 1.0), (3, 2.0), (4, .5)):
        sample[f"stepper_pressure{channel}_value"] = value
        sample[f"stepper_pressure{channel}_valid"] = True
    sample.update(changes)
    return sample


class PitotTests(unittest.TestCase):
    def test_signed_total_pressure_static_density_and_nitrogen_velocity(self):
        sample = measurement()
        transform_pitot_sample(sample)
        self.assertEqual(sample["pitot_total_pressure_pa"], 1100)
        self.assertEqual(sample["pitot_static_pressure_pa"], 1050)
        mach = math.sqrt(5 * ((1100 / 1050) ** (2 / 7) - 1))
        rt = 296.8 * 293.15 / (1 + 0.2 * mach ** 2)
        density = 1050 / rt
        self.assertAlmostEqual(sample["pitot_density_kg_m3"], density)
        self.assertAlmostEqual(sample["pitot_velocity_m_s"], mach * math.sqrt(1.4 * rt))
        zero = measurement(stepper_pressure4_value=0)
        transform_pitot_sample(zero)
        self.assertEqual(zero["pitot_velocity_m_s"], 0)

    def test_sonic_boundary_and_supersonic_velocity(self):
        self.assertEqual(_pitot_mach(1.893), 1)
        self.assertGreater(_pitot_mach(1.893001), 1)
        for mach in (1.5, 3.0):
            ratio = (1.2 * mach**2)**3.5 * (6 / (7 * mach**2 - 1))**2.5
            sample = measurement(stepper_pressure4_value=11 * (1 - 1 / ratio))
            transform_pitot_sample(sample)
            speed = mach * math.sqrt(1.4 * 296.8 * 293.15 / (1 + 0.2 * mach**2))
            self.assertAlmostEqual(sample["pitot_velocity_m_s"], speed)

    def test_unavailable_invalid_and_nonphysical_inputs_clear_cached_results(self):
        changes = [{f"stepper_pressure{i}_valid": False} for i in (1, 8, 3, 4)]
        changes += [{f"stepper_pressure{i}_value": v} for i in (1, 8, 3, 4)
                    for v in (None, True, math.nan, math.inf)]
        changes += [{"ed593_connected": False}, {"ed593_transport_error": "timeout"},
                    {"ed593_tc0_ready": False}, {"ed593_tc0_temperature_c": None},
                    {"ed593_tc0_temperature_c": -273.15}, {"stepper_pressure4_value": -1},
                    {"stepper_pressure8_value": 20}, {"stepper_pressure4_value": 11}]
        for change in changes:
            with self.subTest(change=change):
                sample = measurement(**change)
                sample.update(dict.fromkeys(PITOT_FIELDS, 99))
                transform_pitot_sample(sample)
                self.assertTrue(all(sample[field] is None for field in PITOT_FIELDS))

    def test_recording_declares_derived_fields_before_inputs_become_available(self):
        first = measurement(stepper_pressure8_value=None)
        transform_pitot_sample(first)
        with tempfile.TemporaryDirectory() as directory:
            recorder = FlowRunRecorder(record_dir=Path(directory), metadata={}, run_config={},
                                       source_fields={}, first_sample=first)
            current = measurement(elapsed_s=1.0)
            transform_pitot_sample(current)
            recorder.record_sample(current, [])
            recorder.finish()
            with recorder.paths.merged_csv.open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["pitot_velocity_m_s"], "")
            self.assertAlmostEqual(float(rows[1]["pitot_velocity_m_s"]), current["pitot_velocity_m_s"])
            self.assertIn("pitot_density_kg_m3", recorder.paths.export_csv.read_text())
