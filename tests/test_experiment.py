"""Worksheet validation and event-driven health checks without device I/O."""

import unittest
from pathlib import Path
from unittest.mock import Mock

from dashboard_app.experiment import ExperimentRunner, parse_program


EXAMPLE = (Path(__file__).resolve().parents[1] / "examples" / "esp32_health.csv").read_text()
SOURCE_EXAMPLE = (Path(__file__).resolve().parents[1] / "examples" / "source_health.csv").read_text()
RECORDING_EXAMPLE = (Path(__file__).resolve().parents[1] / "examples" / "start_recording.csv").read_text()
RECORDING_AFTER_ESP32 = "\n".join(RECORDING_EXAMPLE.splitlines()[index] for index in (0, 1, 4)) + "\n"
MOVE_EXAMPLE = (Path(__file__).resolve().parents[1] / "examples" / "move_stepper.csv").read_text()
MOVE_AFTER_ESP32 = "\n".join((MOVE_EXAMPLE.splitlines()[0], MOVE_EXAMPLE.splitlines()[1], MOVE_EXAMPLE.splitlines()[-1])) + "\n"


def healthy_sample(source="esp32", **changes):
    source = "stepper" if source == "controllino" else source
    sample = {
        f"{source}_age_ms": 100,
        f"{source}_connected": True,
        f"{source}_mode": "sim",
        f"{source}_transport_error": None,
        "esp32_pressure_adc_ready": True,
        "esp32_flow_adc_ready": True,
    }
    changes = {key.replace("controllino_", "stepper_", 1) if key.startswith("controllino_") else key: value
               for key, value in changes.items()}
    sample.update(changes)
    return sample


class ExperimentTests(unittest.TestCase):
    def runner(self, csv_text=EXAMPLE, request_state=None, abort_state=None):
        runner = ExperimentRunner(request_state=request_state, abort_state=abort_state)
        runner.load(csv_text, "example.csv", 0)
        return runner

    def test_controllino_names_communicator_and_stepper_remains_device(self):
        action = parse_program(SOURCE_EXAMPLE)[-1]
        self.assertEqual(action.source, "controllino")
        self.assertIn("stepper", action.devices)
        for path in (Path(__file__).resolve().parents[1] / "examples").glob("*.csv"):
            with self.subTest(path=path.name):
                actions = parse_program(path.read_text())
                self.assertNotIn("stepper", {item.source for item in actions})
                if any(item.source == "controllino" for item in actions):
                    with self.assertRaises(ValueError):
                        parse_program(path.read_text().replace(",controllino,", ",stepper,"))

    def test_csv_accepts_spreadsheet_bom_newlines_and_reordered_columns(self):
        program = parse_program("\ufefftimeout_s,source,device,health,max_age_ms,action\r\n"
                                "10,esp32,pressure_adc;flow_adc,ok,1000,telemetry_health\r\n\r\n")
        self.assertEqual(program[0].as_dict(), parse_program(EXAMPLE)[0].as_dict())

    def test_invalid_worksheets_are_rejected_before_loading(self):
        invalid = (
            "", EXAMPLE.splitlines()[0],
            EXAMPLE.replace("1000", ""), EXAMPLE.replace("1000", "NaN"),
            EXAMPLE.replace("1000", "inf"), EXAMPLE.replace("1000", "-1"),
            EXAMPLE.replace("1000", "true"), EXAMPLE.replace(",10", ",0"),
            EXAMPLE.replace(",10", ",-1"), EXAMPLE.replace(",10", ",inf"),
            EXAMPLE.replace("telemetry_health", "start"),
            EXAMPLE.replace("esp32", "unknown"), EXAMPLE.replace(",ok,", ",, "),
            EXAMPLE.replace("pressure_adc;flow_adc", "pressure_adc;typo"),
            EXAMPLE.replace("pressure_adc;flow_adc", "pressure_adc;pressure_adc"),
            EXAMPLE.replace("device", "health"),
            EXAMPLE.replace("timeout_s", "timeout_s,extra"),
            EXAMPLE.replace(",10", ",10,extra"),
            EXAMPLE.replace("pressure_adc;flow_adc", '"unclosed'),
        )
        runner = self.runner()
        before = runner.snapshot()
        for csv_text in invalid:
            with self.subTest(csv=csv_text), self.assertRaises(ValueError):
                runner.load(csv_text, "invalid.csv", 1)
            self.assertEqual(runner.snapshot(), before)
        for csv_text in (None, 42, "x" * (256 * 1024 + 1)):
            with self.assertRaises(ValueError):
                parse_program(csv_text)

    def test_start_is_external_and_success_requires_a_telemetry_event(self):
        runner = self.runner()
        runner.on_sample(healthy_sample(), 0.1)
        self.assertEqual(runner.state, "ready")
        runner.start(1)
        self.assertEqual(runner.state, "running")
        self.assertEqual(runner.snapshot()["results"][0]["status"], "waiting")
        runner.on_sample(healthy_sample(), 0.9)
        self.assertEqual(runner.state, "running")
        runner.on_sample(healthy_sample(), 1.1)
        state = runner.snapshot()
        self.assertEqual(state["state"], "completed")
        self.assertEqual(state["results"][0]["status"], "passed")
        self.assertEqual([event["event"] for event in state["events"]],
                         ["start", "row_started", "row_passed", "completed"])

    def test_every_required_check_must_pass_in_the_same_observation(self):
        missing_error = healthy_sample()
        del missing_error["esp32_transport_error"]
        missing_readiness = healthy_sample()
        del missing_readiness["esp32_flow_adc_ready"]
        samples = [missing_error, missing_readiness] + [healthy_sample(**change) for change in (
            {"esp32_age_ms": None}, {"esp32_age_ms": True}, {"esp32_age_ms": -1},
            {"esp32_age_ms": float("nan")}, {"esp32_age_ms": 1001},
            {"esp32_transport_error": "connection reset"},
            {"esp32_pressure_adc_ready": False}, {"esp32_flow_adc_ready": False},
            {"esp32_pressure_adc_ready": 1},
        )]
        for sample in samples:
            with self.subTest(sample=sample):
                runner = self.runner()
                runner.start(0)
                runner.on_sample(sample, 0.1)
                self.assertEqual(runner.state, "running")
                self.assertFalse(all(check["ok"] for check in runner.snapshot()["results"][0]["checks"].values()))
        runner.on_sample(healthy_sample(esp32_age_ms=1000), 0.2)
        self.assertEqual(runner.state, "completed")

    def test_worksheet_freshness_replaces_dashboard_connected_threshold(self):
        runner = self.runner(EXAMPLE.replace("1000", "10000"))
        runner.start(0)
        runner.on_sample(healthy_sample(esp32_age_ms=6000, esp32_connected=False), 0.1)
        self.assertEqual(runner.state, "completed")
        checks = runner.snapshot()["results"][0]["checks"]
        self.assertTrue(checks["freshness"]["ok"])
        self.assertTrue(checks["health"]["ok"])
        self.assertFalse(checks["health"]["connected"])

    def test_connected_and_mode_are_optional_diagnostics_only(self):
        for connected in (True, False, None, 1):
            with self.subTest(connected=connected):
                runner = self.runner()
                runner.start(0)
                sample = healthy_sample(esp32_connected=connected)
                del sample["esp32_mode"]
                runner.on_sample(sample, 0.1)
                self.assertEqual(runner.state, "completed")
        runner = self.runner()
        runner.start(0)
        sample = healthy_sample()
        del sample["esp32_connected"]
        del sample["esp32_age_ms"]
        runner.on_sample(sample, 0.1)
        self.assertEqual(runner.state, "running")
        checks = runner.snapshot()["results"][0]["checks"]
        self.assertTrue(checks["health"]["ok"])
        self.assertFalse(checks["freshness"]["ok"])

    def test_timeout_without_data_and_late_success_do_not_advance(self):
        for late_sample in (False, True):
            with self.subTest(late_sample=late_sample):
                runner = self.runner()
                runner.start(0)
                runner.tick(9.9)
                self.assertEqual(runner.state, "running")
                if late_sample:
                    runner.on_sample(healthy_sample(), 10)
                else:
                    runner.tick(10)
                self.assertEqual(runner.state, "failed")
                self.assertEqual(runner.snapshot()["results"][0]["reason"], "timeout")
                runner.on_sample(healthy_sample(), 11)
                self.assertEqual(runner.state, "failed")
                runner.start(12)
                runner.on_sample(healthy_sample(), 12.1)
                self.assertEqual(runner.state, "completed")

    def test_rows_run_in_order_with_a_separate_deadline_per_row(self):
        runner = self.runner(EXAMPLE + "telemetry_health,dxmr90,250,ok,,2\n")
        runner.start(0)
        sample = healthy_sample()
        sample.update(healthy_sample("dxmr90"))
        runner.on_sample(sample, 9)
        state = runner.snapshot()
        self.assertEqual(state["current_row"], 2)
        self.assertEqual(state["deadline_s"], 11)
        self.assertEqual([result["status"] for result in state["results"]], ["passed", "waiting"])
        runner.on_sample(sample, 9.1)
        self.assertEqual(runner.state, "completed")
        runner.start(20)
        runner.on_sample(healthy_sample(), 20.1)
        runner.on_sample(healthy_sample(), 20.2)
        runner.tick(22.1)
        self.assertEqual(runner.state, "failed")
        self.assertEqual(runner.snapshot()["results"][1]["status"], "failed")

    def test_dro_requires_its_own_freshness_and_capability(self):
        runner = self.runner(EXAMPLE + "telemetry_health,controllino,250,ok,dro,2\n")
        runner.start(0)
        runner.on_sample(healthy_sample(), 0.1)
        sample = healthy_sample("controllino", stepper_dro_capable=True, stepper_dro_fresh=True,
                                stepper_dro_sample_age_ms=300)
        runner.on_sample(sample, 0.2)
        self.assertEqual(runner.state, "running")
        sample["stepper_dro_sample_age_ms"] = 150
        sample["stepper_dro_capable"] = False
        runner.on_sample(sample, 0.3)
        self.assertEqual(runner.state, "running")
        sample["stepper_dro_capable"] = True
        sample["stepper_dro_fresh"] = False
        runner.on_sample(sample, 0.4)
        self.assertEqual(runner.state, "completed")

    def test_dxmr90_errors_missing_and_stale_samples_block_its_row(self):
        for change in ({"dxmr90_transport_error": "Modbus timeout"},
                       {"dxmr90_age_ms": None}, {"dxmr90_age_ms": 1001}):
            with self.subTest(change=change):
                runner = self.runner(SOURCE_EXAMPLE)
                runner.start(0)
                runner.on_sample(healthy_sample(), 0.1)
                runner.on_sample(healthy_sample("dxmr90", **change), 0.2)
                self.assertEqual(runner.snapshot()["current_row"], 2)
                runner.tick(10.1)
                self.assertEqual(runner.state, "failed")
                self.assertEqual(len(runner.snapshot()["results"]), 2)

    def test_all_sources_share_the_same_schema_and_results(self):
        runner = self.runner(SOURCE_EXAMPLE)
        runner.start(0)
        runner.on_sample(healthy_sample(), 0.1)
        runner.on_sample(healthy_sample("dxmr90"), 0.2)
        self.assertEqual(runner.snapshot()["current_row"], 3)
        sample = healthy_sample("controllino", stepper_command_capable=True,
                                stepper_dro_capable=True, stepper_dro_sample_age_ms=100,
                                stepper_servo_capable=True, stepper_solenoid4_capable=True)
        for field, value in (("stepper_transport_error", "controller disconnected"),
                             ("stepper_age_ms", 1001), ("stepper_command_capable", False),
                             ("stepper_dro_capable", False), ("stepper_servo_capable", None),
                             ("stepper_solenoid4_capable", False),
                             ("stepper_dro_sample_age_ms", 1001)):
            with self.subTest(field=field):
                runner.on_sample(dict(sample, **{field: value}), 0.3)
                self.assertEqual(runner.state, "running")
                self.assertEqual(runner.snapshot()["current_row"], 3)
        runner.on_sample(sample, 0.4)
        state = runner.snapshot()
        self.assertEqual(state["state"], "completed")
        self.assertEqual([result["status"] for result in state["results"]], ["passed"] * 3)
        self.assertEqual(set(state["results"][2]["checks"]),
                         {"freshness", "health", "stepper", "dro", "servo", "solenoid4"})

    def test_active_program_cannot_be_replaced_or_started_twice(self):
        runner = self.runner()
        program_id = runner.program_id
        runner.load(EXAMPLE, "replacement.csv", 1)
        with self.assertRaises(RuntimeError):
            runner.start(2, program_id)
        runner.start(2, runner.program_id)
        before = runner.snapshot()
        with self.assertRaises(RuntimeError):
            runner.start(3)
        with self.assertRaises(RuntimeError):
            runner.load(EXAMPLE, "replacement.csv", 3)
        self.assertEqual(runner.snapshot(), before)
        before["program"][0]["max_age_ms"] = 999999
        self.assertEqual(runner.snapshot()["program"][0]["max_age_ms"], 1000)

    def test_recording_schema_requires_explicit_set_point_and_no_ignored_conditions(self):
        action = parse_program(RECORDING_EXAMPLE)[-1]
        self.assertEqual(action.as_dict()["set_point"], "on")
        self.assertEqual(action.as_dict()["source"], "recording")
        for invalid in (
            RECORDING_AFTER_ESP32.replace(",10,on", ",10,off"),
            RECORDING_AFTER_ESP32.replace(",10,on", ",10,"),
            RECORDING_AFTER_ESP32.replace("requested_state,recording", "requested_state,servo"),
            RECORDING_AFTER_ESP32.replace("recording,,,,", "recording,1000,,,"),
            RECORDING_AFTER_ESP32.replace("recording,,,,", "recording,,ok,,"),
            RECORDING_AFTER_ESP32.replace("recording,,,,", "recording,,,dro,"),
            RECORDING_AFTER_ESP32.replace(",10,on", ",0,on"),
            RECORDING_AFTER_ESP32.replace("pressure_adc;flow_adc,10,", "pressure_adc;flow_adc,10,on"),
        ):
            with self.subTest(csv=invalid), self.assertRaises(ValueError):
                parse_program(invalid)

    def test_recording_request_is_sent_once_and_confirmed_by_a_later_event(self):
        request_state = Mock()
        runner = self.runner(RECORDING_AFTER_ESP32, request_state)
        runner.start(0)
        runner.on_sample(healthy_sample(), 0.1)
        request_state.assert_not_called()
        runner.on_sample({}, 0.2, {"recording": False})
        request_state.assert_called_once()
        self.assertEqual(runner.state, "running")
        self.assertTrue(runner.snapshot()["results"][-1]["requested"])
        for observed in (False, None, 1, "on"):
            runner.on_sample({}, 0.3, {"recording": observed})
            self.assertEqual(runner.state, "running")
        request_state.assert_called_once()
        runner.on_sample({}, 0.4, {"recording": True})
        self.assertEqual(runner.state, "completed")
        check = runner.snapshot()["results"][-1]["checks"]["requested_state"]
        self.assertEqual(check, {"ok": True, "requested": "on", "observed": True})
        request_state.assert_called_once()

    def test_recording_failure_and_unconfirmed_timeout_do_not_advance(self):
        for request_state, reason in ((None, "requested_state_unavailable"),
                                      (Mock(side_effect=OSError("disk full")), "request_failed")):
            with self.subTest(reason=reason):
                runner = self.runner(RECORDING_AFTER_ESP32, request_state)
                runner.start(0)
                runner.on_sample(healthy_sample(), 0.1)
                runner.on_sample({}, 0.2)
                self.assertEqual(runner.state, "failed")
                self.assertEqual(runner.snapshot()["results"][-1]["reason"], reason)
                runner.on_sample({}, 0.3, {"recording": True})
                self.assertEqual(runner.state, "failed")
        request_state = Mock()
        runner = self.runner(RECORDING_AFTER_ESP32, request_state)
        runner.start(0)
        runner.on_sample(healthy_sample(), 0.1)
        runner.on_sample({}, 0.2)
        runner.on_sample({}, 0.3, {"recording": False})
        runner.tick(10.2)
        self.assertEqual(runner.state, "failed")
        self.assertEqual(runner.snapshot()["results"][-1]["reason"], "timeout")
        request_state.assert_called_once()

    def test_move_schema_exposes_speed_duration_direction_and_derived_distance(self):
        action = parse_program(MOVE_EXAMPLE)[-1]
        self.assertEqual(action.distance_mm, 10)
        self.assertEqual(action.direction, "forward")
        for values in ("0,10,forward", "1,0,forward", "NaN,10,forward",
                       "1,inf,forward", "1,10,sideways", "1,,forward", "1e300,1e300,forward"):
            with self.subTest(values=values), self.assertRaises(ValueError):
                parse_program(MOVE_AFTER_ESP32.replace("1,10,forward", values))
        for values in ("controllino,,ok,stepper,", "controllino,1000,,stepper,", "controllino,1000,ok,dro,"):
            with self.subTest(values=values), self.assertRaises(ValueError):
                parse_program(MOVE_AFTER_ESP32.replace("controllino,1000,ok,stepper,", values))

    def actuator_program(self, source, device, target, pulse=""):
        return ("action,source,max_age_ms,health,timeout_s,set_point,device,pulse_us\n"
                "telemetry_health,esp32,1000,ok,10,,pressure_adc;flow_adc,\n"
                f"requested_state,{source},1000,ok,10,{target},{device},{pulse}\n")

    def test_solenoid_group_parser_and_all_target_confirmation(self):
        program = self.actuator_program("esp32", "solenoid3; solenoid1;solenoid2", "open")
        action = parse_program(program)[1]
        self.assertEqual(action.device, "solenoid3;solenoid1;solenoid2")
        self.assertEqual(action.devices, ("solenoid3", "solenoid1", "solenoid2"))
        request = Mock()
        runner = self.runner(program, request)
        runner.start(0)
        runner.on_sample(healthy_sample(), 0.1)
        runner.on_sample(healthy_sample(esp32_sol1=False, esp32_sol2=False, esp32_sol3=False), 0.2)
        request.assert_called_once_with(action)
        runner.on_sample(healthy_sample(esp32_sol1=True, esp32_sol2=True, esp32_sol3=False), 0.3)
        self.assertEqual(runner.state, "running")
        checks = runner.results[-1]["checks"]
        self.assertTrue(checks["solenoid1"]["ok"])
        self.assertTrue(checks["solenoid2"]["ok"])
        self.assertFalse(checks["solenoid3"]["ok"])
        runner.on_sample(healthy_sample(esp32_sol1=True, esp32_sol2=True, esp32_sol3=True), 0.4)
        self.assertEqual(runner.state, "completed")
        request.assert_called_once()

    def test_solenoid_groups_reject_empty_duplicate_incompatible_and_unknown_targets(self):
        for device, source in (("solenoid1;solenoid1", "esp32"),
                               ("solenoid1;", "esp32"), (";solenoid1", "esp32"),
                               ("solenoid1;;solenoid2", "esp32"),
                               ("solenoid1;solenoid5", "esp32"),
                               ("solenoid1;brushless_motor", "esp32"),
                               ("solenoid4;brushless_motor", "controllino"),
                               ("solenoid1;solenoid4", "controllino")):
            with self.subTest(device=device), self.assertRaises(ValueError):
                parse_program(self.actuator_program(source, device, "open"))

    def test_solenoid_group_records_partial_dispatch_failures_without_retry(self):
        program = self.actuator_program("esp32", "solenoid1;solenoid2;solenoid3", "closed")
        dispatched = {"targets": {"solenoid1": {"status": "requested", "set_point": "closed"},
                                   "solenoid2": {"status": "failed", "error": "connection reset"},
                                   "solenoid3": {"status": "requested", "set_point": "closed"}}}
        request = Mock(return_value=dispatched)
        runner = self.runner(program, request)
        runner.start(0)
        runner.on_sample(healthy_sample(), 0.1)
        runner.on_sample(healthy_sample(), 0.2)
        self.assertEqual(runner.state, "failed")
        self.assertEqual(runner.results[-1]["request"], dispatched)
        self.assertIn("solenoid2: connection reset", runner.results[-1]["error"])
        runner.on_sample(healthy_sample(esp32_sol1=False, esp32_sol2=False, esp32_sol3=False), 0.3)
        request.assert_called_once()

    def test_solenoid_group_missing_confirmation_times_out(self):
        request = Mock()
        runner = self.runner(self.actuator_program("esp32", "solenoid1;solenoid2;solenoid3", "closed"), request)
        runner.start(0)
        runner.on_sample(healthy_sample(), 0.1)
        runner.on_sample(healthy_sample(), 0.2)
        runner.on_sample(healthy_sample(esp32_sol1=False, esp32_sol2=False), 0.3)
        self.assertEqual(runner.state, "running")
        self.assertFalse(runner.results[-1]["checks"]["solenoid3"]["ok"])
        runner.tick(10.2)
        self.assertEqual(runner.state, "failed")
        request.assert_called_once()

    def test_actuators_dispatch_once_then_verify_reported_output(self):
        for source, device, target, pulse, field, observed in (
                ("esp32", "solenoid1", "open", "", "esp32_sol1", True),
                ("esp32", "solenoid2", "closed", "", "esp32_sol2", False),
                ("esp32", "solenoid3", "open", "", "esp32_sol3", True),
                ("esp32", "solenoid4", "closed", "", "esp32_sol4", False),
                ("controllino", "solenoid4", "open", "", "stepper_solenoid4_on", True),
                ("controllino", "brushless_motor", "on", "", "stepper_brushless_motor_on", True),
                ("controllino", "brushless_motor", "off", "", "stepper_brushless_motor_on", False),
                ("controllino", "brushless_motor", "pulse", "1400", "stepper_brushless_motor_setpoint_us", 1400)):
            with self.subTest(device=device, target=target, source=source):
                request = Mock()
                runner = self.runner(self.actuator_program(source, device, target, pulse), request)
                runner.start(0)
                runner.on_sample(healthy_sample(), 0.1)
                runner.on_sample(healthy_sample(source, **{f"{source}_age_ms": 1001}), 0.2)
                request.assert_not_called()
                runner.on_sample(healthy_sample(source), 0.3)
                self.assertEqual(runner.state, "running")
                runner.on_sample(healthy_sample(source), 0.4)
                self.assertEqual(runner.state, "running")
                runner.on_sample(healthy_sample(source, **{field: observed}), 0.5)
                self.assertEqual(runner.state, "completed")
                request.assert_called_once()
                check = runner.snapshot()["results"][-1]["checks"]["requested_state"]
                self.assertTrue(check["ok"])
                self.assertEqual(check["field"], field)
                if device.startswith("solenoid"):
                    self.assertEqual(check["requested"], target)
                    self.assertEqual(check["observed"], target)
                    self.assertIs(check["output_on"], observed)
                    self.assertEqual(check["valve_type"], "normally_closed")
                    self.assertEqual(check["evidence"], "electrical_output")

    def test_actuator_parser_rejects_ignored_or_invalid_operands(self):
        program = self.actuator_program("controllino", "brushless_motor", "pulse", "1400")
        for bad in (program.replace("brushless_motor", "unknown"),
                    program.replace("controllino", "dxmr90"),
                    program.replace("pulse,", "toggle,"),
                    program.replace("1400", ""), program.replace("1400", "1400.5"),
                    program.replace("1400", "nan"), program.replace("pulse,", "on,"),
                    program.replace("controllino,1000,ok,10", "controllino,1000,ok,0"),
                    self.actuator_program("controllino", "solenoid1", "open"),
                    self.actuator_program("esp32", "solenoid1", "on"),
                    self.actuator_program("esp32", "solenoid1", "off"),
                    self.actuator_program("esp32", "brushless_motor", "on")):
            with self.subTest(program=bad), self.assertRaises(ValueError):
                parse_program(bad)

    def test_actuator_failures_do_not_repeat_commands(self):
        program = self.actuator_program("controllino", "brushless_motor", "on")
        for case in ("timeout", "stale", "transport", "rejected", "dispatch"):
            with self.subTest(case=case):
                request = Mock(side_effect=OSError("unavailable") if case == "dispatch" else None)
                runner = self.runner(program, request)
                runner.start(0)
                runner.on_sample(healthy_sample(), 0.1)
                runner.on_sample(healthy_sample("controllino"), 0.2)
                if case == "timeout":
                    runner.tick(10.2)
                elif case != "dispatch":
                    runner.on_sample(healthy_sample("controllino", stepper_brushless_motor_on=True,
                                                   stepper_age_ms=1001 if case == "stale" else 100,
                                                   stepper_transport_error="lost" if case == "transport" else None),
                                     0.3, {"stepper_command_error": "rejected" if case == "rejected" else None})
                self.assertEqual(runner.state, "failed")
                runner.on_sample(healthy_sample("controllino", stepper_brushless_motor_on=True), 0.4)
                request.assert_called_once()

    def test_mode_dispatch_once_and_wait_for_fresh_reported_mode(self):
        for mode, firmware_mode in (("positional", "web_position"), ("directional", "local_velocity")):
            csv_text = MOVE_AFTER_ESP32.replace("15,move,1,10,forward", f"10,{mode},,,")
            request = Mock()
            runner = self.runner(csv_text, request)
            runner.start(0)
            runner.on_sample(healthy_sample(), 0.1)
            runner.on_sample(healthy_sample("controllino", stepper_age_ms=1001), 0.2)
            request.assert_not_called()
            runner.on_sample(healthy_sample("controllino", stepper_control_mode=firmware_mode), 0.3)
            request.assert_called_once()
            self.assertEqual(runner.state, "running")
            runner.on_sample(healthy_sample("controllino", stepper_control_mode="other"), 0.4)
            self.assertEqual(runner.state, "running")
            runner.on_sample(healthy_sample("controllino", stepper_control_mode=firmware_mode), 0.5)
            self.assertEqual(runner.state, "completed")
            self.assertEqual(runner.snapshot()["results"][-1]["checks"]["requested_state"],
                             {"ok": True, "requested": mode, "observed": mode})
            request.assert_called_once()

    def test_mode_invalid_rows_and_failure_evidence(self):
        program = MOVE_AFTER_ESP32.replace("15,move,1,10,forward", "10,positional,,,")
        for invalid in (program.replace("positional", "position"),
                        program.replace("positional", "web_position"),
                        program.replace("positional", "local_velocity"),
                        program.replace("positional,,,", "positional,1,,"),
                        program.replace("controllino,1000,ok,stepper,", "controllino,1000,ok,dro,"),
                        program.replace("controllino,1000,ok,stepper,", "controllino,,ok,stepper,"),
                        program.replace("controllino,1000,ok,stepper,", "controllino,1000,,stepper,")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_program(invalid)
        for case in ("timeout", "stale", "transport", "rejected", "dispatch"):
            with self.subTest(case=case):
                request = Mock(side_effect=RuntimeError("busy") if case == "dispatch" else None)
                abort = Mock()
                runner = self.runner(program, request, abort)
                runner.start(0)
                runner.on_sample(healthy_sample(), 0.1)
                runner.on_sample(healthy_sample("controllino"), 0.2)
                if case == "timeout":
                    runner.tick(10.2)
                elif case != "dispatch":
                    runner.on_sample(healthy_sample("controllino", stepper_control_mode="web_position",
                                                   stepper_age_ms=1001 if case == "stale" else 100,
                                                   stepper_transport_error="lost" if case == "transport" else None),
                                     0.3, {"stepper_command_error": "busy" if case == "rejected" else None})
                self.assertEqual(runner.state, "failed")
                request.assert_called_once()
                abort.assert_not_called()

    def test_move_dispatch_waits_for_health_then_matching_firmware_completion(self):
        request = Mock(return_value={"command_id": "move-1", "distance_mm": 10})
        runner = self.runner(MOVE_AFTER_ESP32, request)
        runner.start(0)
        runner.on_sample(healthy_sample(), 0.1)
        runner.on_sample(healthy_sample("controllino", stepper_age_ms=1001), 0.2)
        request.assert_not_called()
        runner.on_sample(healthy_sample("controllino"), 0.3)
        request.assert_called_once()
        for identity, state, moving in (("old", "completed", False),
                                        ("move-1", "moving", True),
                                        ("move-1", "completed", True)):
            runner.on_sample(healthy_sample("controllino", stepper_command_id=identity,
                                           stepper_state=state, stepper_moving=moving), 0.4)
            self.assertEqual(runner.state, "running")
        runner.on_sample(healthy_sample("controllino", stepper_command_id="move-1",
                                       stepper_state="completed", stepper_moving=False), 0.5)
        self.assertEqual(runner.state, "completed")
        request.assert_called_once()

    def test_move_fault_timeout_and_lost_telemetry_request_stop_once(self):
        for fault in ("timeout", "aborted", "stale", "transport", "rejected"):
            with self.subTest(fault=fault):
                request = Mock(return_value={"command_id": "move-1"})
                abort = Mock()
                runner = self.runner(MOVE_AFTER_ESP32, request, abort)
                runner.start(0)
                runner.on_sample(healthy_sample(), 0.1)
                runner.on_sample(healthy_sample("controllino"), 0.2)
                sample = healthy_sample("controllino", stepper_command_id="move-1",
                                        stepper_state="moving", stepper_moving=True)
                if fault == "timeout":
                    runner.tick(15.2)
                else:
                    changes = {"aborted": {"stepper_state": "aborted"},
                               "stale": {"stepper_age_ms": 1001},
                               "transport": {"stepper_transport_error": "connection lost"},
                               "rejected": {}}
                    sample.update(changes[fault])
                    runner.on_sample(sample, 0.3, {"stepper_command_error": "busy" if fault == "rejected" else None})
                    if fault in ("stale", "transport"):
                        self.assertEqual(runner.state, "running")
                        runner.tick(15.2)  # Start was not confirmed before its deadline.
                self.assertEqual(runner.state, "failed")
                self.assertTrue(runner.snapshot()["results"][-1]["stop_requested"])
                runner.on_sample(sample, 0.4)
                abort.assert_called_once()
                request.assert_called_once()

    def test_started_move_outlives_handshake_timeout_and_telemetry_lag(self):
        request = Mock(return_value={"command_id": "move-1"})
        abort = Mock()
        text = MOVE_AFTER_ESP32.replace("15,move,1,10,forward", "1,move,1,60,forward")
        runner = self.runner(text, request, abort)
        runner.start(0)
        runner.on_sample(healthy_sample(), .1)
        runner.on_sample(healthy_sample("controllino"), .2)
        moving = healthy_sample("controllino", stepper_command_id="move-1",
                                stepper_state="moving", stepper_moving=True)
        runner.on_sample(moving, .3)
        self.assertTrue(runner.results[-1]["motion_started"])
        self.assertIsNone(runner.snapshot()["deadline_s"])
        runner.tick(120)
        lagged = dict(moving, stepper_age_ms=999999, stepper_transport_error="lag spike")
        runner.on_sample(lagged, 121)
        self.assertEqual(runner.state, "running")
        self.assertEqual(set(runner.results[-1]["checks"]), {"move"})
        # Neither another command's completion nor a still-moving status finishes it.
        runner.on_sample(dict(lagged, stepper_command_id="other", stepper_state="completed", stepper_moving=False), 122)
        runner.on_sample(dict(lagged, stepper_state="completed", stepper_moving=True), 123)
        self.assertEqual(runner.state, "running")
        runner.on_sample(dict(lagged, stepper_state="completed", stepper_moving=False), 124)
        self.assertEqual(runner.state, "completed")
        abort.assert_not_called()
        request.assert_called_once()

    def test_move_waits_for_start_telemetry_recovery_within_handshake_timeout(self):
        runner = self.runner(MOVE_AFTER_ESP32, Mock(return_value={"command_id": "move-1"}))
        runner.start(0)
        runner.on_sample(healthy_sample(), .1)
        runner.on_sample(healthy_sample("controllino"), .2)
        moving = healthy_sample("controllino", stepper_command_id="move-1",
                                stepper_state="moving", stepper_moving=True)
        runner.on_sample(dict(moving, stepper_age_ms=1001), .3)
        self.assertFalse(runner.results[-1].get("motion_started", False))
        self.assertEqual(runner.state, "running")
        runner.on_sample(moving, .4)
        self.assertTrue(runner.results[-1]["motion_started"])
        runner.tick(100)
        self.assertEqual(runner.state, "running")

    def test_started_move_still_halts_for_matching_controller_fault(self):
        for state in ("aborted", "emergency_stop", "limit_blocked", "fault"):
            with self.subTest(state=state):
                stop = Mock()
                runner = self.runner(MOVE_AFTER_ESP32, Mock(return_value={"command_id": "move-1"}), stop)
                runner.start(0)
                runner.on_sample(healthy_sample(), .1)
                runner.on_sample(healthy_sample("controllino"), .2)
                moving = healthy_sample("controllino", stepper_command_id="move-1",
                                        stepper_state="moving", stepper_moving=True)
                runner.on_sample(moving, .3)
                runner.on_sample(dict(moving, stepper_state=state, stepper_moving=False), 100)
                self.assertEqual(runner.state, "failed")
                self.assertEqual(runner.results[-1]["reason"], "move_aborted")
                stop.assert_called_once()

    def test_move_stop_failure_is_reported_without_claiming_stop_confirmation(self):
        runner = self.runner(MOVE_AFTER_ESP32, Mock(return_value={"command_id": "move-1"}),
                             Mock(side_effect=OSError("unreachable")))
        runner.start(0)
        runner.on_sample(healthy_sample(), 0.1)
        runner.on_sample(healthy_sample("controllino"), 0.2)
        runner.tick(15.2)
        result = runner.snapshot()["results"][-1]
        self.assertEqual(result["stop_error"], "unreachable")
        self.assertNotIn("stop_requested", result)


if __name__ == "__main__":
    unittest.main()
