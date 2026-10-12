"""New IO through the production decoder, controls, sampling and recording."""
import csv
import json
import unittest
from datetime import datetime, timezone

import supervisor_core as core
from dashboard_app.experiment import parse_program
from dashboard_app.runtime import SourceMerger
import test_backend_boundary as boundary
from test_optional_reading_health import worksheet


class ControllinoChannelTests(unittest.TestCase):
    def setUp(self):
        self.fixture = boundary.BackendTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.controller = self.fixture.stepper
        self.controller.payload.update(
            fw="1.5.0", dkind="linace", dc=1, df=1, dr=2000, dd=0, da=0, dq=1, dx=0,
            adc=[250, 300, 350, 400, 450, 500, 550], adcage=60, adcseq=1, sol5=0, sol6=0)
        self.controller.refresh()

    def runtime(self):
        runtime = self.fixture.runtime()
        with runtime._condition:
            runtime._poll_locked(.1)
        return runtime

    def test_optional_wire_fields_keep_legacy_firmware_usable(self):
        legacy = boundary.Controller()
        self.assertFalse(legacy.status()["stepper_pressure_capable"])
        self.assertEqual(legacy.status()["stepper_dro_kind"], "absolute_dro")
        self.assertFalse(legacy.status()["stepper_solenoid1_capable"])
        self.assertFalse(legacy.status()["stepper_solenoid2_capable"])
        self.assertTrue(legacy.status()["stepper_solenoid3_capable"])
        for index in (0, 1):
            with self.assertRaises(RuntimeError):
                legacy.set_solenoid(index, True)
        self.assertEqual(legacy.commands, [])
        legacy.set_solenoid(2, True)
        self.assertEqual(legacy.commands, [b"V1 L4,1\n"])
        payload = self.controller.payload
        for change in ({"adc": [0] * 6}, {"adc": [True] + [0] * 6},
                       {"adc": [1024] + [0] * 6}, {"adcage": -1},
                       {"adcseq": True}, {"sol5": 2}, {"dkind": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.controller.decode_status_line(json.dumps({**payload, **change}))
        partial = dict(payload)
        del partial["adcseq"]
        with self.assertRaises(ValueError):
            self.controller.decode_status_line(json.dumps(partial))
        startup = self.controller.decode_status_line(json.dumps({**payload, "adcage": -1, "adcseq": 0}))
        self.assertIsNone(startup["stepper_pressure1_adc"])

    def test_calibration_history_and_recording_use_the_same_values(self):
        self.fixture.config_path.write_text(json.dumps({
            "version": 1, "stepper": {"dro_home": 135.46},
            "pressure_inputs": {"1": {"volts_per_count": .01, "calibration": {
                "voltage_min": .5, "voltage_max": 4.5,
                "pressure_min": 0, "pressure_max": 10, "unit": "psi", "reference": "gauge"}}}}))
        runtime = self.runtime()
        sample = runtime.latest
        self.assertEqual(sample["stepper_pressure1_value"], 5)
        self.assertEqual(sample["stepper_pressure3_adc"], 350)
        self.assertIsNone(sample["stepper_pressure3_voltage_v"])
        self.assertIsNone(sample["stepper_pressure5_value"])
        descriptors = runtime.dashboard_config()["controllino_pressures"]
        self.assertEqual(descriptors[0]["unit"], "psi")
        self.assertEqual(descriptors[2]["data_key"], "stepper_pressure3_value")
        runtime.set_recording(True)
        with runtime._condition:
            runtime._poll_locked(.2)
        self.assertEqual(runtime.history[-1]["stepper_pressure1_value"], 5)
        path = runtime.recorder.paths.merged_csv
        runtime.set_recording(False)
        with path.open() as stream:
            row = list(csv.DictReader(stream))[-1]
        self.assertEqual(float(row["stepper_pressure1_value"]), 5)
        self.assertEqual(row["stepper_pressure5_value"], "")
        self.assertEqual(runtime.run_config_locked()["system_config"]["pressure_inputs"]["1"]["calibration"]["unit"], "psi")
        self.controller.payload["adcage"] = 600
        self.controller.refresh()
        with runtime._condition:
            runtime._poll_locked(.3)
        self.assertFalse(runtime.latest["stepper_pressure1_valid"])
        self.assertIsNone(runtime.latest["stepper_pressure1_value"])

    def test_new_encoder_requires_its_own_zero(self):
        self.fixture.config_path.write_text(json.dumps({"version": 1, "stepper": {"dro_home": 135.46}}))
        runtime = self.runtime()
        self.assertFalse(runtime.latest["stepper_dro_zero_set"])
        self.assertIsNone(runtime.latest["stepper_dro_zeroed_position_mm"])
        runtime.set_stepper_dro_zero()
        self.assertEqual(runtime.system_config.stepper_dro_kind, "linace")
        self.assertEqual(runtime.latest["stepper_dro_zeroed_position_mm"], 0)
        self.assertEqual(self.controller.commands, [])

    def test_all_three_relay_paths_confirm_and_stop(self):
        runtime = self.runtime()
        for index, wire_id in ((0, 5), (1, 6), (2, 4)):
            result = runtime.toggle_solenoid(index)
            self.assertEqual(self.controller.commands[-1], f"V1 L{wire_id},1\n".encode())
            self.assertTrue(result["state"])
            self.assertTrue(result["solenoids"][index])
            self.assertEqual(len(result["solenoids"]), 3)
        self.assertEqual(self.fixture.esp32.solenoid_states(), (False,) * 4)
        receipt = runtime.devices.emergency_stop(runtime.devices.status())
        state = self.controller.status()
        self.assertTrue(receipt.matches(state))
        state["stepper_solenoid2_on"] = True
        self.assertFalse(receipt.matches(state))
        before = len(self.controller.commands)
        for index in (0, 1, 2):
            with self.assertRaises(RuntimeError):
                self.controller.set_solenoid(index, True)
        self.assertEqual(len(self.controller.commands), before)
        self.assertEqual([d["label"] for d in runtime.dashboard_config()["solenoids"]],
                         ["R3 solenoid", "R4 solenoid", "R5 solenoid"])
        self.assertEqual(runtime.dashboard_config()["solenoid_count"], 3)
        for index in (3, 4, 5):
            with self.assertRaises(ValueError):
                runtime.toggle_solenoid(index)

    def test_experiment_relay_targets_keep_their_individual_evidence(self):
        program = worksheet(dict(action="requested_state", source="controllino",
                                 device="solenoid1;solenoid2;solenoid3", set_point="open",
                                 max_age_ms=1000, health="ok", timeout_s=10))
        action = parse_program(program)[0]
        runtime = self.runtime()
        runtime._request_experiment_state(action, runtime.latest)
        self.assertEqual(self.controller.commands, [b"V1 L5,1\n", b"V1 L6,1\n", b"V1 L4,1\n"])
        checks = action.evaluate({**runtime.latest, **self.controller.status()})
        self.assertTrue(all(c["ok"] for c in checks.values()))
        wrong_owner = parse_program(program.replace("controllino", "esp32"))[0]
        before = list(self.controller.commands)
        with self.assertRaisesRegex(RuntimeError, "controlled by controllino"):
            runtime._request_experiment_state(wrong_owner, runtime.latest)
        self.assertEqual(self.controller.commands, before)

    def test_flow_gates_follow_installed_relays_instead_of_old_esp32_outputs(self):
        esp32 = self.fixture.esp32
        for index in range(4):
            esp32.set_solenoid(index, True)
        merger = SourceMerger(self.fixture.sources, stale_after_s=.5)
        sample = merger.poll(1, datetime.now(timezone.utc))
        self.assertGreater(sample["esp32_f1_gmin"], 0)
        self.assertEqual(sample["esp32_open_flow_gmin"], 0)
        self.assertEqual(sample["dxmr90_open_total_mass_flow_g_min"], 0)
        for index in range(4):
            esp32.set_solenoid(index, False)
        for index in range(3):
            self.controller.set_solenoid(index, True)
            sample = merger.poll(1.1 + index * .1, datetime.now(timezone.utc))
            expected = sum(sample[f"esp32_f{number}_gmin"] for number in range(1, index + 2))
            self.assertAlmostEqual(sample["esp32_open_flow_gmin"], expected)
            self.assertEqual(sample["dxmr90_open_total_mass_flow_g_min"],
                             sample["dxmr90_total_mass_flow_g_min"] if index == 2 else 0)
        self.controller.emit = False
        sample = merger.poll(2, datetime.now(timezone.utc))
        self.assertIsNone(sample["esp32_open_flow_gmin"])
        self.assertIsNone(sample["dxmr90_open_total_mass_flow_g_min"])

    def test_recording_ignores_old_esp32_valves_and_snapshots_logical_mapping(self):
        runtime = self.runtime()
        self.fixture.esp32.set_solenoid(0, True)
        with runtime._condition:
            runtime._poll_locked(.2)
        self.assertFalse(runtime.recording)
        runtime.toggle_solenoid(0)
        self.assertTrue(runtime.recording)
        mapping = runtime.recorder.run_config["solenoids"]
        self.assertEqual([(r["index"], r["relay"], r["wire_id"]) for r in mapping],
                         [(0, "R3", 5), (1, "R4", 6), (2, "R5", 4)])

    def test_legacy_esp32_mode_keeps_four_outputs_recording_and_csv(self):
        sources = [self.fixture.esp32, self.fixture.sources[1], core.SimulatedStepperSource()]
        runtime = self.fixture.runtime(sources)
        with runtime._condition:
            runtime._poll_locked(.1)
        self.assertEqual(runtime.dashboard_config()["solenoid_count"], 4)
        action = parse_program(worksheet(dict(action="requested_state", source="esp32",
            device="solenoid4", set_point="open", max_age_ms=1000, health="ok", timeout_s=10)))[0]
        runtime._request_experiment_state(action, runtime.latest)
        with runtime._condition:
            runtime._poll_locked(.2)
        self.assertTrue(runtime.recording)
        self.assertTrue(runtime.latest["solenoid4_on"])
        self.assertEqual(runtime.latest["dxmr90_open_total_mass_flow_g_min"],
                         runtime.latest["dxmr90_total_mass_flow_g_min"])
        self.assertEqual(self.controller.commands, [])


if __name__ == "__main__":
    unittest.main()
