"""Behavioral boundary tests; no physical devices or dashboard text assertions.

Run: python -m unittest discover -s tests -v
The controller double uses the production encoder/decoder but keeps all I/O in
memory. Tests cover both transport contracts and application coordination.
"""

import json
import threading
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import supervisor_core as core
from dashboard_app import runtime as app
from dashboard_app.http import build_handler
from dashboard_app.system_config import SystemConfig


class Controller(core.ControllinoStepperSource):
    """In-memory firmware peer, deliberately independent of DeviceControls."""

    def __init__(self):
        super().__init__("http://unused.invalid")
        self.payload = dict(v=1, t="s", d4=1, d5=1, d6=-1, d8=-1, lx=0, lp=0, ln=0,
                            b=0, r="none", sps=0, csps=1000, aps=0, ds=1, en=0, ut=1,
                            dc=0, df=0, dr=0, dd=0, da=-1, dq=0, dx=0, m=1, h=0,
                            a=1, e=0, mv=0, st=5, p=0, g=0, c=0, o=2, sol4=0,
                            fw="1.2.0", aux="servo", apin=4, sv=0, sp=2500,
                            smin=500, smax=2500, srel=1, sr=0)
        self.commands = []
        self.emit = self.apply = True
        self.refresh()

    def refresh(self):
        self._last_values = self.decode_status_line(json.dumps(self.payload))
        wire_id = self.payload["c"]
        if wire_id in self._command_names:
            self._last_values["stepper_command_id"] = self._command_names[wire_id]

    def _require_connected(self):
        return self._last_values

    def _write_command(self, command, description):
        self.commands.append(command)
        if not self.apply:
            return
        code, argument = command[3:4], command[4:].strip()
        p = self.payload
        if code == b"L":
            p["sol4"] = int(argument.split(b",")[1])
        elif code == b"A":
            args = [int(value) for value in argument.split(b",")]
            p.update(sv=int(args[0] != 0), sr=args[1] // 20 if len(args) == 2 else 0)
            if args[0]:
                p["sp"] = args[0]
        elif code == b"G":
            delta, speed, identity = map(int, argument.split(b","))
            p.update(c=identity, g=delta, csps=speed, mv=1, st=6, sps=speed if delta > 0 else -speed)
        elif code == b"X":
            p.update(mv=0, st=5, sps=0)
        elif code == b"M":
            p.update(m=int(argument), st=5 if int(argument) else 0)
        elif code == b"R":
            direction = int(argument)
            p.update(mv=int(direction != 0), sps=direction * p["csps"], st=1 if direction else 0)
        elif code == b"S":
            p["csps"] = int(argument)
        elif code == b"E":
            p.update(e=int(argument), mv=0, sol4=0, sv=0, sr=0)
        else:
            raise AssertionError(f"Unexpected firmware command: {command!r}")
        self.refresh()

    def poll(self, elapsed_s):
        if self.emit:
            if self._last_values.get("stepper_command_id") == self.pending_command_id:
                self.pending_command_id = None
            return core.SourceReading(self.name, self.mode, elapsed_s, dict(self._last_values))


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stepper = Controller()
        self.esp32 = core.SimulatedEsp32Source(auto_sequence=False)
        self.sources = [self.esp32, core.SimulatedDxmr90Source(), self.stepper]
        self.controls = core.DeviceControls(self.sources)
        self.config_path = Path(self.directory.name) / "system.json"

    def runtime(self, sources=None):
        with patch.object(core, "make_sources", return_value=sources or self.sources):
            runtime = app.DashboardRuntime(
                scenario="healthy", rate_hz=10, drop_after_s=2, stale_after_s=1,
                history_limit=10, record_dir=Path(self.directory.name),
                esp32_source="sim", dxmr90_source="sim", stepper_source="controllino",
                stepper_port="/dev/null", stepper_baud=9600,
                dxmr90_host="unused.invalid", dxmr90_port=502, dxmr90_unit_id=1,
                dxmr90_timeout=0.1, dxmr90_addressing="one-based", dxmr90_word_order="high-low",
                dxmr90_data_path="direct", dxmr90_rate_hz=10, system_config_path=self.config_path,
            )
        self.addCleanup(runtime.stop)
        return runtime

    def test_direction_mapping_and_move_identity_survive_poll(self):
        runtime = self.runtime()
        for direction, steps in (("forward", -2520), ("reverse", 2520)):
            result = runtime.move_stepper(dict(distance_mm=10, speed_mm_s=1, direction=direction, command_id=direction))
            self.assertTrue(self.stepper.commands[-1].startswith(f"V1 G{steps},252,".encode()))
            self.assertEqual(result["stepper"]["stepper_command_id"], direction)
            self.assertEqual(result["resolved_direction"], direction)
            self.assertEqual(result["signed_distance_mm"], -10 if direction == "forward" else 10)
            self.assertIsNone(self.stepper.pending_command_id)
            self.assertFalse(runtime.stop_stepper()["stepper"]["stepper_moving"])
        runtime.set_stepper_control_mode({"web_position": False})
        for requested, wire in ((1, -1), (-1, 1), (0, 0)):
            result = runtime.set_stepper_local_run({"direction": requested})
            self.assertEqual(self.stepper.commands[-1], f"V1 R{wire}\n".encode())
            self.assertEqual(result["direction"], requested)
            self.assertEqual(result["stepper"]["stepper_moving"], bool(requested))
        result = runtime.set_stepper_speed({"speed_mm_s": 1.5})
        self.assertAlmostEqual(result["requested_speed_mm_s"], 378 / core.DEFAULT_STEPPER_STEPS_PER_MM)

    def test_yun_uses_physical_direction_not_browser_direction(self):
        for mode in ("usb", "network", "sim"):
            self.stepper.mode = mode
            self.stepper.move = Mock()
            for physical, sign in (("forward", 1), ("reverse", -1), ("both", 1)):
                current = {"stepper_authorized_direction": physical}
                self.controls.move(current, dict(distance_mm=10, speed_mm_s=1, direction="ignored"))
                self.stepper.move.assert_called_with(sign * 10, 1, None)
            with self.assertRaisesRegex(RuntimeError, "direction is unavailable"):
                self.controls.move({}, dict(distance_mm=10, speed_mm_s=1))

    def test_capabilities_and_invalid_requests_never_send(self):
        runtime = self.runtime()
        for method, values in (
            (runtime.move_stepper, {}),
            (runtime.move_stepper, dict(distance_mm=999, speed_mm_s=1, direction="forward")),
            (runtime.move_stepper, dict(distance_mm=True, speed_mm_s=1)),
            (runtime.move_stepper, dict(distance_mm=float("nan"), speed_mm_s=1)),
            (runtime.set_stepper_control_mode, {"web_position": 1}),
            (runtime.set_stepper_local_run, {"direction": True}),
            (runtime.set_stepper_speed, {"speed_mm_s": True}),
            (runtime.set_stepper_brushless_pulse, {"pulse_us": 999}),
            (runtime.set_stepper_servo, {"action": "zero"}),
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                method(values)
        for index in (-1, 4, True, "0"):
            with self.assertRaises(ValueError):
                runtime.toggle_solenoid(index)
        with self.assertRaisesRegex(RuntimeError, "brushless motor control"):
            runtime.toggle_stepper_brushless_motor()
        self.assertEqual(self.stepper.commands, [])

    def test_servo_clockwise_hold_return_release_and_persistence(self):
        runtime = self.runtime()
        runtime.set_stepper_servo({"action": "settings", "displacement_deg": 135})
        self.assertEqual(self.stepper.commands, [])
        result = runtime.set_stepper_servo({"action": "on"})
        self.assertEqual(self.stepper.commands[-1], b"V1 A1500\n")
        self.assertTrue(result["stepper"]["stepper_servo_enabled"])
        with self.assertRaisesRegex(RuntimeError, "Turn the servo Off"):
            runtime.set_stepper_servo({"action": "endpoint"})
        runtime.set_stepper_servo({"action": "off", "displacement_deg": 999})
        self.assertEqual(self.stepper.commands[-1], b"V1 A2500,1500\n")
        with self.assertRaises(RuntimeError):
            runtime.set_stepper_servo({"action": "endpoint"})
        self.stepper.payload.update(sv=0, sr=0)
        self.stepper.refresh()
        runtime.set_stepper_servo({"action": "endpoint"})
        self.assertEqual(self.stepper.commands[-1], b"V1 A2500,1500\n")
        saved = SystemConfig(self.config_path).snapshot()["servo"]
        self.assertEqual(saved, {"off_pulse_us": 2500, "displacement_deg": 135})
        for angle in (-1, 270.1, True, float("nan")):
            with self.assertRaises(ValueError):
                runtime.set_stepper_servo({"action": "on", "displacement_deg": angle})
        runtime.set_stepper_servo({"pulse_us": 0})
        self.assertEqual(self.stepper.commands[-1], b"V1 A0\n")

    def test_confirmation_rejects_stale_wrong_and_rejected_status(self):
        before = self.controls.status()
        receipt = self.controls.stop(before)
        self.assertFalse(receipt.confirmed(before))
        fresh = self.controls.status()
        self.assertTrue(receipt.confirmed(fresh))
        self.assertFalse(receipt.confirmed(dict(fresh, stepper_moving=True)))
        self.stepper.pending_command_error = "wrong_mode"
        with self.assertRaisesRegex(RuntimeError, "rejected Stop: wrong_mode"):
            receipt.confirmed(fresh)

    def test_unconfirmed_servo_does_not_save_settings(self):
        runtime = self.runtime()
        self.stepper.apply = False
        before = runtime.system_config.snapshot()
        with patch.object(app.time, "monotonic", side_effect=[0, 0, 2]):
            with self.assertRaisesRegex(RuntimeError, "did not confirm servo position"):
                runtime.set_stepper_servo({"action": "on", "displacement_deg": 45})
        self.assertEqual(runtime.system_config.snapshot(), before)
        self.assertFalse(self.config_path.exists())

    def test_confirmation_wait_releases_lock_for_polling(self):
        runtime = self.runtime()
        current = self.controls.status()
        self.stepper.apply = False
        receipt = self.controls.stop(current)
        polled = threading.Event()
        def publish():
            with runtime._condition:
                self.stepper.refresh()
                runtime._poll_locked(0.1)
                polled.set()
        with runtime._condition:
            worker = threading.Thread(target=publish, daemon=True)
            worker.start()
            confirmed = runtime._confirm_command_locked(receipt)
        worker.join(1)
        self.assertTrue(polled.is_set())
        self.assertFalse(confirmed["stepper_moving"])

    def test_servo_write_does_not_hold_the_sampling_lock(self):
        runtime = self.runtime()
        sampled = threading.Event()
        original = self.stepper._write_command
        def write(*args):
            def poll():
                with runtime._condition:
                    runtime._poll_locked(0.1)
                sampled.set()
            worker = threading.Thread(target=poll, daemon=True)
            worker.start()
            self.assertTrue(sampled.wait(1))
            worker.join(1)
            original(*args)
        self.stepper._write_command = write
        runtime.set_stepper_servo({"action": "on"})

    def test_simulated_home_and_yun_brushless_behavior(self):
        sources = core.make_sources(esp32_source="sim", dxmr90_source="sim", stepper_source="sim",
                                    esp32_auto_sequence=False)
        runtime = self.runtime(sources)
        runtime.set_stepper_control_mode({"web_position": True})
        self.assertTrue(runtime.home_stepper()["stepper"]["stepper_homed"])
        result = runtime.set_stepper_brushless_pulse({"pulse_us": 1400})
        self.assertEqual(result["setpoint_us"], 1400)
        result = runtime.toggle_stepper_brushless_motor()
        self.assertTrue(result["on"])
        self.assertEqual(result["pulse_us"], 1400)
        result = runtime.emergency_stop_stepper()
        self.assertFalse(result["stepper"]["stepper_brushless_motor_on"])
        with self.assertRaisesRegex(RuntimeError, "E-STOP"):
            runtime.toggle_stepper_brushless_motor()

    def test_each_valve_starts_recording_and_manual_stop_stays_stopped(self):
        runtime = self.runtime()
        for index in range(4):
            with self.subTest(valve=index + 1):
                runtime.toggle_solenoid(index)
                self.assertTrue(runtime.recording)
                self.assertEqual(runtime.recorder._row_count, 2 if index == 3 else 1)
                runtime.set_recording(False)
                with runtime._condition:
                    runtime._poll_locked(0.2)
                self.assertFalse(runtime.recording)
                runtime.toggle_solenoid(index)
                self.assertFalse(runtime.recording)
        self.assertEqual(self.stepper.commands, [b"V1 L4,1\n", b"V1 L4,0\n"])
        self.assertEqual(self.esp32.solenoid_states(), (False,) * 4)
        self.assertTrue(runtime.latest_export_path().exists())

    def test_relay_remains_independent_of_esp32_and_disconnect_is_not_activation(self):
        self.sources[0] = core.DisabledSource("esp32", 0.1, self.esp32.expected_fields)
        runtime = self.runtime()
        runtime.toggle_solenoid(3)
        self.assertTrue(runtime.latest["solenoid4_on"])
        runtime.set_recording(False)
        with self.assertRaisesRegex(RuntimeError, "not live"):
            runtime.toggle_solenoid(0)
        self.stepper.emit = False
        with runtime._condition:
            runtime._poll_locked(2)
        self.assertIsNone(runtime.latest["solenoid4_on"])
        self.stepper.emit = True
        with runtime._condition:
            runtime._poll_locked(2.1)
        self.assertFalse(runtime.recording)

    def test_emergency_stop_dispatch_bypasses_runtime_lock_and_turns_everything_off(self):
        runtime = self.runtime()
        sent = threading.Event()
        original = self.stepper._write_command
        def write(*args):
            original(*args)
            sent.set()
        self.stepper._write_command = write
        result, errors = [], []
        def stop():
            try:
                result.append(runtime.emergency_stop_stepper())
            except Exception as exc:
                errors.append(exc)
        with runtime._condition:
            worker = threading.Thread(target=stop, daemon=True)
            worker.start()
            dispatched_without_lock = sent.wait(1)
        worker.join(2)
        self.assertTrue(dispatched_without_lock)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(result[0]["confirmed"])
        self.assertTrue(result[0]["stepper"]["stepper_estop_latched"])
        self.assertFalse(runtime.reset_stepper_emergency_stop()["stepper"]["stepper_estop_latched"])

    def test_dro_zero_survives_restart_without_touching_device_position(self):
        runtime = self.runtime()
        self.stepper._last_values.update(stepper_dro_capable=True, stepper_dro_fresh=True,
                                         stepper_dro_position_mm=126.12, stepper_dro_frame_count=1)
        with runtime._condition:
            runtime._poll_locked(0.1)
        runtime.set_stepper_dro_zero()
        restarted = self.runtime()
        self.assertEqual(restarted.latest["stepper_dro_zeroed_position_mm"], 0)
        self.assertEqual(restarted.latest["stepper_dro_position_mm"], 126.12)
        self.assertEqual(self.stepper.commands, [])
        self.stepper._last_values["stepper_dro_position_mm"] = 116.12
        with restarted._condition:
            restarted._poll_locked(0.2)
        self.assertEqual(restarted.latest["stepper_dro_zeroed_position_mm"], -10)

    def test_dro_velocity_uses_new_frames_and_resets_when_stale(self):
        runtime = self.runtime()
        self.stepper._last_values.update(stepper_dro_capable=True, stepper_dro_fresh=True,
                                         stepper_dro_sample_age_ms=0, stepper_dro_position_mm=10)
        for frame, elapsed in enumerate((0.1, 0.2, 0.3), start=1):
            self.stepper._last_values.update(stepper_dro_position_mm=10 - elapsed * 2,
                                             stepper_dro_valid_frame_count=frame)
            with runtime._condition:
                runtime._poll_locked(elapsed)
        self.assertAlmostEqual(runtime.latest["stepper_dro_velocity_mm_s"], -2)
        self.stepper._last_values["stepper_dro_fresh"] = False
        with runtime._condition:
            runtime._poll_locked(0.4)
        self.assertIsNone(runtime.latest["stepper_dro_velocity_mm_s"])

    def test_merge_flow_and_missing_stale_sources(self):
        samples = list(app.iter_merged_samples(40, scenario="all_stale", drop_after_s=0.2, stale_after_s=0.5))
        self.assertTrue(samples[0]["stepper_connected"])
        self.assertFalse(samples[-1]["stepper_connected"])
        self.assertFalse(samples[-1]["esp32_connected"])
        merger = app.SourceMerger(self.sources)
        sample = merger.poll(0, datetime.now(timezone.utc))
        self.assertEqual(sample["dxmr90_open_total_mass_flow_g_min"], 0)
        self.stepper.set_solenoid(3, True)
        sample = merger.poll(0.1, datetime.now(timezone.utc))
        self.assertEqual(sample["dxmr90_open_total_mass_flow_g_min"], sample["dxmr90_total_mass_flow_g_min"])

    def test_usb_and_network_keep_identical_controller_wire_commands(self):
        for cls in (core.ControllinoStepperSource, core.ControllinoUsbStepperSource):
            with self.subTest(transport=cls.__name__):
                source = cls("http://unused.invalid")
                source._last_values = source.decode_status_line(json.dumps(self.stepper.payload))
                source._require_connected = Mock(return_value=source._last_values)
                source._write_command = Mock()
                source.set_solenoid(3, True)
                self.assertEqual(source._write_command.call_args.args[0], b"V1 L4,1\n")
                source.set_servo_pulse(1500)
                self.assertEqual(source._write_command.call_args.args[0], b"V1 A1500\n")
                source.set_servo_pulse(2500, release_ms=1500)
                self.assertEqual(source._write_command.call_args.args[0], b"V1 A2500,1500\n")
                source.close()

    def test_http_routes_assets_commands_errors_metadata_and_recordings(self):
        runtime = self.runtime()
        handler = build_handler(runtime)
        def request(method, path, body=None):
            payload = json.dumps(body or {}).encode()
            class Connection:
                response = bytearray()
                def makefile(self, *args):
                    return BytesIO(f"{method} {path} HTTP/1.0\r\nContent-Type: application/json\r\n"
                                   f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
                def sendall(self, data):
                    self.response.extend(data)
            connection = Connection()
            handler(connection, ("127.0.0.1", 0), None)
            headers, data = bytes(connection.response).split(b"\r\n\r\n", 1)
            status = int(headers.split()[1])
            return status, json.loads(data) if b"application/json" in headers else data
        for path in ("/", "/assets/app-lean.js", "/assets/dashboard-lean.css", "/api/state",
                     "/api/config", "/api/latest", "/api/history", "/api/stepper/status"):
            with self.subTest(path=path):
                self.assertEqual(request("GET", path)[0], 200)
        self.assertEqual(request("POST", "/api/stepper/servo", {"action": "on"})[0], 200)
        self.assertEqual(request("POST", "/api/stepper/servo", {"action": "endpoint"})[0], 409)
        self.assertEqual(request("POST", "/api/stepper/move", {"distance_mm": True})[0], 400)
        self.assertEqual(request("POST", "/api/metadata", {"sample_number": "test"})[0], 200)
        self.assertEqual(request("POST", "/api/solenoid/toggle?n=0")[0], 200)
        self.assertTrue(runtime.recording)
        self.assertEqual(request("POST", "/api/run/stop")[0], 200)
        self.assertTrue(runtime.latest_export_path().exists())


if __name__ == "__main__":
    unittest.main()
