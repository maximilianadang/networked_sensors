"""Pressure conversions cannot turn unknown calibration or stale data into truth."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from controllino_io import (
    CONTROLLINO_RELAYS, PRESSURE_INPUTS, SOLENOID_COUNT, STANDARD_VOLTS_PER_COUNT,
    pressure_descriptors, pressure_fields, transform_pressure_sample, validate_pressure_config,
)
from dashboard_app.system_config import SystemConfig


MBAR_PER_PSI = 68.94757293168361


def sample(**changes):
    result = {"stepper_pressure_capable": True, "stepper_connected": True,
              "stepper_transport_error": None, "stepper_pressure_sample_age_ms": 20,
              "stepper_age_ms": 30, "stepper_pressure_sequence": 1}
    result.update({f"stepper_pressure{i}_adc": 200 for i in range(1, 8)})
    result.update(changes)
    return result


def calibration(**changes):
    result = dict(voltage_min=1, voltage_max=3, pressure_min=-1, pressure_max=9,
                  unit="psi", reference="gauge")
    result.update(changes)
    return result


class PressureTests(unittest.TestCase):
    def test_channel_identity_and_default_unknowns(self):
        self.assertEqual([(c["terminal"], c["arduino_pin"]) for c in PRESSURE_INPUTS],
                         [("AI10", "A10"), ("AI11", "A11"), ("AI12", "A14"),
                          ("AI13", "A15"), ("AI0", "A0"), ("AI1", "A1"), ("AI2", "A2")])
        self.assertEqual([c["id"] for c in PRESSURE_INPUTS if c["supply_known"]], [5, 6, 7])
        self.assertEqual([(c["group"], c["group_index"]) for c in PRESSURE_INPUTS],
                         [("low", 1), ("low", 2), ("low", 3), ("low", 4),
                          ("high", 1), ("high", 2), ("high", 3)])
        self.assertEqual(SOLENOID_COUNT, 3)
        self.assertEqual([(c["index"], c["relay"], c["pin"], c["wire_id"]) for c in CONTROLLINO_RELAYS],
                         [(0, "R3", 25, 5), (1, "R4", 26, 6), (2, "R5", 27, 4)])
        self.assertIsInstance(pressure_fields(), tuple)
        self.assertEqual(len(set(pressure_fields())), 28)

    def test_uncalibrated_fresh_raw_is_valid_but_never_a_pressure(self):
        config, values = validate_pressure_config(), sample()
        transform_pressure_sample(values, config)
        for index in range(1, 8):
            self.assertTrue(values[f"stepper_pressure{index}_valid"])
            self.assertIsNone(values[f"stepper_pressure{index}_value"])
        self.assertEqual(values["stepper_pressure1_voltage_v"], 200 * STANDARD_VOLTS_PER_COUNT)
        self.assertIsNone(values["stepper_pressure3_voltage_v"])
        self.assertIsNone(values["stepper_pressure4_voltage_v"])
        descriptors = pressure_descriptors(config)
        self.assertEqual([c["label"] for c in descriptors],
                         [f"Low pressure {i}" for i in range(1, 5)]
                         + [f"High pressure {i}" for i in range(1, 4)])
        self.assertEqual([c["group_label"] for c in descriptors],
                         ["Low pressure"] * 4 + ["High pressure"] * 3)
        self.assertEqual((descriptors[0]["unit"], descriptors[0]["data_key"]),
                         ("mbar", "stepper_pressure1_value"))
        self.assertEqual((descriptors[2]["unit"], descriptors[2]["data_key"]),
                         ("mbar", "stepper_pressure3_value"))
        self.assertEqual([c["unit"] for c in descriptors], ["mbar"] * 4 + ["psi"] * 3)
        self.assertTrue(all(c["data_key"] == c["value_key"] for c in descriptors))
        self.assertFalse(any(c["calibrated"] for c in descriptors))

    def test_linear_conversion_inclusive_endpoints_and_no_extrapolation(self):
        config = validate_pressure_config({"1": {"label": "Inlet", "volts_per_count": .01,
                                                  "calibration": calibration()}})
        for adc, expected in ((100, -1), (200, 4), (300, 9), (99, None), (301, None)):
            with self.subTest(adc=adc):
                values = sample(stepper_pressure1_adc=adc)
                transform_pressure_sample(values, config)
                if expected is None:
                    self.assertIsNone(values["stepper_pressure1_value"])
                else:
                    self.assertAlmostEqual(values["stepper_pressure1_value"], expected * MBAR_PER_PSI)
                self.assertEqual(values["stepper_pressure1_voltage_v"], adc * .01)
                self.assertEqual(values["stepper_pressure1_valid"], expected is not None)
        descriptor = pressure_descriptors(config)[0]
        self.assertEqual((descriptor["label"], descriptor["unit"], descriptor["reference"], descriptor["data_key"]),
                         ("Inlet", "mbar", "gauge", "stepper_pressure1_value"))

    def test_configured_calibration_still_requires_verified_voltage_scale(self):
        config = validate_pressure_config({"3": {"calibration": calibration()}})
        values = sample()
        transform_pressure_sample(values, config)
        self.assertFalse(values["stepper_pressure3_valid"])
        self.assertIsNone(values["stepper_pressure3_value"])
        config = validate_pressure_config({"3": {"volts_per_count": .01, "calibration": calibration()}})
        transform_pressure_sample(values, config)
        self.assertAlmostEqual(values["stepper_pressure3_value"], 4 * MBAR_PER_PSI)

    def test_equivalent_input_units_publish_group_units_without_rewriting_calibration(self):
        # Each source range describes the same -1..3 bar physical pressure.
        for channel_id in range(1, 8):
            output_unit, output_per_bar = ("mbar", 1000) if channel_id <= 4 else ("psi", 14.503773773020923)
            prefix = f"stepper_pressure{channel_id}_"
            for unit, units_per_bar in (("Pa", 100000), ("kPa", 100), ("MPa", .1),
                                        ("hPa", 1000), ("mbar", 1000), ("bar", 1),
                                        ("psi", 14.503773773020923)):
                with self.subTest(channel=channel_id, unit=unit):
                    original = {str(channel_id): {"volts_per_count": .01, "calibration": calibration(
                        pressure_min=-units_per_bar, pressure_max=3 * units_per_bar,
                        unit=unit, reference="absolute")}}
                    before = deepcopy(original)
                    config = validate_pressure_config(original)
                    for adc, expected in ((100, -1), (200, 1), (300, 3)):
                        values = sample(**{prefix + "adc": adc})
                        transform_pressure_sample(values, config)
                        self.assertAlmostEqual(values[prefix + "value"], expected * output_per_bar)
                        self.assertEqual(values[prefix + "voltage_v"], adc * .01)
                        self.assertEqual(values[prefix + "adc"], adc)
                    self.assertEqual(original, before)
                    self.assertEqual(config[str(channel_id)]["calibration"], before[str(channel_id)]["calibration"])
                    descriptor = pressure_descriptors(config)[channel_id - 1]
                    self.assertEqual((descriptor["unit"], descriptor["reference"]), (output_unit, "absolute"))

    def test_units_and_converted_ranges_fail_before_sampling(self):
        for values in (dict(unit="V"), dict(unit="counts"), dict(unit="PSI"),
                       dict(unit="mPa"), dict(unit="unknown"),
                       dict(unit="MPa", pressure_min=0, pressure_max=1e308),
                       dict(unit="bar", pressure_min=-1e307, pressure_max=1e307),
                       dict(unit="Pa", pressure_min=0, pressure_max=1e-323)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_pressure_config({"1": {"calibration": calibration(**values)}})

    def test_converted_ranges_are_validated_in_each_channels_published_unit(self):
        huge = calibration(pressure_min=0, pressure_max=1e307)
        for channel_id in range(1, 5):
            with self.subTest(channel=channel_id), self.assertRaisesRegex(ValueError, "span in mbar"):
                validate_pressure_config({str(channel_id): {"calibration": huge}})
        for channel_id in range(5, 8):
            normalized = validate_pressure_config({str(channel_id): {"calibration": huge}})
            self.assertEqual(normalized[str(channel_id)]["calibration"], huge)

    def test_combined_age_health_and_raw_validation_preserve_evidence(self):
        config = validate_pressure_config({"1": {"volts_per_count": .01, "calibration": calibration()}})
        fresh = sample(stepper_pressure_sample_age_ms=450, stepper_age_ms=50)
        transform_pressure_sample(fresh, config)
        self.assertTrue(fresh["stepper_pressure1_valid"])
        for change in ({"stepper_age_ms": 481}, {"stepper_pressure_sample_age_ms": -1},
                       {"stepper_pressure_sample_age_ms": True}, {"stepper_pressure_sample_age_ms": None},
                       {"stepper_pressure_capable": False}, {"stepper_connected": False},
                       {"stepper_transport_error": "timeout"}):
            with self.subTest(change=change):
                values = sample(**change)
                transform_pressure_sample(values, config)
                self.assertFalse(values["stepper_pressure1_valid"])
                self.assertIsNone(values["stepper_pressure1_value"])
                self.assertEqual(values["stepper_pressure1_adc"], 200)
                self.assertEqual(values["stepper_pressure1_voltage_v"], 2)
                self.assertFalse(values["stepper_pressure2_valid"])
        for adc in (True, -1, 1024, 10.5, None):
            values = sample(stepper_pressure1_adc=adc)
            transform_pressure_sample(values, config)
            self.assertFalse(values["stepper_pressure1_valid"])
            self.assertIsNone(values["stepper_pressure1_voltage_v"])
            self.assertTrue(values["stepper_pressure2_valid"])

    def test_configuration_rejects_nonfinite_ambiguous_or_reversed_values(self):
        invalid = [[], {"8": {}}, {1: {}}, {"1": {"volts_per_count": 0}},
                   {"1": {"volts_per_count": True}}, {"1": {"label": " "}},
                   {"1": {"unknown": 1}}, {"1": {"calibration": {}}}]
        for scale in (float("nan"), float("inf"), -1, 10**1000, 1e308):
            invalid.append({"1": {"volts_per_count": scale}})
        for change in (dict(voltage_min=3), dict(voltage_max=float("nan")), dict(pressure_min=10),
                       dict(pressure_min=True), dict(unit=""), dict(reference=""),
                       dict(pressure_min=-1e308, pressure_max=1e308)):
            invalid.append({"1": {"calibration": calibration(**change)}})
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_pressure_config(config)

    def test_normalization_does_not_modify_caller_or_global_defaults(self):
        original = {"1": {"calibration": calibration(), "label": " Inlet "}}
        before = deepcopy(original)
        normalized = validate_pressure_config(original)
        self.assertEqual(original, before)
        normalized["2"]["label"] = "Changed"
        self.assertEqual(validate_pressure_config()["2"]["label"], "Low pressure 2")


class SystemConfigMigrationTests(unittest.TestCase):
    def test_legacy_zero_is_bound_to_old_encoder_and_migration_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = json.dumps({"version": 1, "stepper": {"dro_zero_raw_mm": 135.46}})
            path.write_text(original)
            config = SystemConfig(path)
            self.assertEqual(config.stepper_dro_kind, "absolute_dro")
            self.assertEqual(config.stepper_dro_zero_raw_mm, 135.46)
            self.assertEqual(len(config.snapshot()["pressure_inputs"]), 7)
            self.assertEqual(path.read_text(), original)
            config.set_stepper_dro_zero_raw_mm(20.12, kind="linace")
            reloaded = SystemConfig(path)
            self.assertEqual(reloaded.stepper_dro_kind, "linace")
            self.assertEqual(reloaded.stepper_dro_zero_raw_mm, 20.12)
            self.assertNotIn("dro_zero_raw_mm", reloaded.snapshot()["stepper"])
            reloaded.set_stepper_dro_zero_raw_mm(21.23)
            self.assertEqual(SystemConfig(path).stepper_dro_kind, "linace")

    def test_new_empty_config_and_legacy_setter(self):
        config = SystemConfig(None)
        self.assertIsNone(config.stepper_dro_kind)
        self.assertIsNone(config.stepper_dro_zero_raw_mm)
        config.set_stepper_dro_zero_raw_mm(42)
        self.assertEqual(config.stepper_dro_kind, "absolute_dro")
        before = config.snapshot()
        with self.assertRaises(ValueError):
            config.set_stepper_dro_zero_raw_mm(43, kind=True)
        self.assertEqual(config.snapshot(), before)

    def test_pressure_configuration_loads_and_invalid_calibration_fails_early(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            payload = {"version": 1, "stepper": {"dro_home": None},
                       "pressure_inputs": {"1": {"calibration": calibration()}}}
            path.write_text(json.dumps(payload))
            config = SystemConfig(path)
            self.assertIsNone(config.stepper_dro_kind)
            self.assertEqual(config.snapshot()["pressure_inputs"]["1"]["calibration"]["unit"], "psi")
            detached = config.pressure_inputs
            detached["1"]["calibration"]["unit"] = "Changed"
            self.assertEqual(config.pressure_inputs["1"]["calibration"]["unit"], "psi")
            payload["pressure_inputs"]["1"]["calibration"]["voltage_max"] = 0
            path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                SystemConfig(path)


if __name__ == "__main__":
    unittest.main()
