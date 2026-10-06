"""Behavioral boundary tests; no physical devices or dashboard text assertions.

Run: python -m unittest discover -s tests -v
The controller double uses the production encoder/decoder but keeps all I/O in
memory. Tests cover both transport contracts and application coordination.
"""

import json
from concurrent.futures import Future
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


class ImmediateExecutor:
    """Deterministic transport outcomes for existing boundary tests.

    Dedicated concurrency tests use the real worker and blocked transports.
    """
    def submit(self, call, *args):
        future = Future()
        try:
            future.set_result(call(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, wait=True):
        pass


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
        elif code == b"B":
            p["bo"] = int(argument)
        elif code == b"P":
            p["bp"] = int(argument)
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
        runtime._experiment_executor.shutdown(wait=True)
        runtime._experiment_executor = ImmediateExecutor()
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

    def test_experiment_http_load_start_and_stream_remain_read_only(self):
        runtime = self.runtime()
        handler = build_handler(runtime)
        example = (Path(__file__).resolve().parents[1] / "examples" / "esp32_health.csv").read_text()

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
            return int(headers.split()[1]), data

        self.assertEqual(request("POST", "/api/experiment/start")[0], 409)
        self.assertEqual(request("POST", "/api/experiment/load", {"csv": "invalid"})[0], 400)
        self.assertEqual(request("GET", "/api/experiment/example.csv"), (200, example.encode()))
        all_source_example = (Path(__file__).resolve().parents[1] / "examples" / "source_health.csv").read_bytes()
        self.assertEqual(request("GET", "/api/experiment/source-health.csv"), (200, all_source_example))
        recording_example = (Path(__file__).resolve().parents[1] / "examples" / "start_recording.csv").read_bytes()
        self.assertEqual(request("GET", "/api/experiment/start-recording.csv"), (200, recording_example))
        move_example = (Path(__file__).resolve().parents[1] / "examples" / "move_stepper.csv").read_bytes()
        self.assertEqual(request("GET", "/api/experiment/move-stepper.csv"), (200, move_example))
        actuator_example = (Path(__file__).resolve().parents[1] / "examples" / "actuators.csv").read_bytes()
        self.assertEqual(request("GET", "/api/experiment/actuators.csv"), (200, actuator_example))
        temperature_example = (Path(__file__).resolve().parents[1] / "examples" / "temperatures.csv").read_bytes()
        self.assertEqual(request("GET", "/api/experiment/temperatures.csv"), (200, temperature_example))
        status, data = request("POST", "/api/experiment/load", {"csv": example, "name": "example.csv"})
        self.assertEqual(status, 200)
        program_id = json.loads(data)["experiment"]["program_id"]
        self.assertEqual(request("POST", "/api/experiment/start", {"program_id": program_id - 1})[0], 409)
        self.assertEqual(request("POST", "/api/experiment/start", {"program_id": program_id})[0], 200)
        self.assertEqual(request("POST", "/api/experiment/start")[0], 409)
        self.assertEqual(request("POST", "/api/experiment/load", {"csv": example})[0], 409)

        calls = 0
        def publish_sample(sequence):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise BrokenPipeError()
            with runtime._condition:
                runtime._poll_locked(0.1)
            return runtime.sequence, runtime.latest

        with patch.object(runtime, "wait_for_sample", side_effect=publish_sample):
            status, stream = request("GET", "/api/events")
        self.assertEqual(status, 200)
        experiment_events = [json.loads(block.split(b"data: ", 1)[1])
                             for block in stream.split(b"\n\n")
                             if block.startswith(b"event: experiment\n")]
        self.assertEqual([event["state"] for event in experiment_events], ["running", "completed"])
        for path in ("/api/experiment", "/api/state", "/api/latest"):
            status, data = request("GET", path)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)["experiment"]["state"], "completed")
        self.assertFalse(runtime.recording)
        self.assertEqual(self.stepper.commands, [])
        self.assertFalse(any(runtime.latest[f"solenoid{index}_on"] for index in range(1, 5)))

        lines = recording_example.decode().splitlines()
        csv_text = "\n".join((lines[0], lines[1], lines[-1])) + "\n"
        self.assertEqual(request("POST", "/api/experiment/load", {"csv": csv_text})[0], 200)
        self.assertEqual(request("POST", "/api/experiment/start")[0], 200)
        calls = 0
        def publish_recording(sequence):
            nonlocal calls
            calls += 1
            if calls > 3:
                raise BrokenPipeError()
            with runtime._condition:
                runtime._poll_locked((calls + 1) / 10)
            return runtime.sequence, runtime.latest
        with patch.object(runtime, "wait_for_sample", side_effect=publish_recording):
            status, stream = request("GET", "/api/events")
        self.assertEqual(status, 200)
        recording_states = [json.loads(block.split(b"data: ", 1)[1])["recording"]
                            for block in stream.split(b"\n\n")
                            if block.startswith(b"event: state\n")]
        self.assertEqual(recording_states, [False, True])
        self.assertEqual(runtime.experiment_state()["state"], "completed")
        self.assertTrue(runtime.recording)
        self.assertEqual(runtime.recorder.status_payload()["merged_rows"], 2)
        self.assertEqual(self.stepper.commands, [])

    def test_runtime_experiment_timeout_and_dxmr90_error_evidence(self):
        runtime = self.runtime()
        example = (Path(__file__).resolve().parents[1] / "examples" / "esp32_health.csv").read_text()
        runtime.load_experiment({"csv": example.replace("1000", "0")})
        runtime.start_experiment({})
        self.esp32.poll = Mock(return_value=None)
        self.sources[1].last_error = "Modbus connection reset"
        with runtime._condition:
            runtime._poll_locked(0.1)
        self.assertEqual(runtime.experiment_state()["state"], "running")
        self.assertTrue(runtime.latest["dxmr90_connected"])
        self.assertEqual(runtime.latest["dxmr90_transport_error"], "Modbus connection reset")
        with runtime._condition:
            runtime._poll_locked(10.1)
        self.assertEqual(runtime.experiment_state()["state"], "failed")
        self.assertEqual(runtime.experiment_state()["results"][0]["reason"], "timeout")
        self.assertEqual(self.stepper.commands, [])

    def test_experiment_uses_worksheet_age_when_dashboard_marks_source_stale(self):
        runtime = self.runtime()
        example = (Path(__file__).resolve().parents[1] / "examples" / "esp32_health.csv").read_text()
        runtime.load_experiment({"csv": example.replace("1000", "2000")})
        runtime.start_experiment({})
        self.esp32.poll = Mock(return_value=None)
        with runtime._condition:
            runtime._poll_locked(1.5)
        self.assertFalse(runtime.latest["esp32_connected"])
        self.assertEqual(runtime.latest["esp32_age_ms"], 1500)
        self.assertEqual(runtime.experiment_state()["state"], "completed")
        self.assertEqual(self.stepper.commands, [])

    def test_all_source_health_program_consumes_controller_and_modbus_telemetry(self):
        self.stepper.payload.update(dc=1, df=1, dr=13546, dd=0, da=0, dq=1, dx=0)
        self.stepper.refresh()
        runtime = self.runtime()
        example = (Path(__file__).resolve().parents[1] / "examples" / "source_health.csv").read_text()
        runtime.load_experiment({"csv": example})
        runtime.start_experiment({})
        for elapsed, row in ((0.1, 2), (0.2, 3), (0.3, None)):
            with runtime._condition:
                runtime._poll_locked(elapsed)
            self.assertEqual(runtime.experiment_state()["current_row"], row)
        state = runtime.experiment_state()
        self.assertEqual(state["state"], "completed")
        self.assertEqual([result["status"] for result in state["results"]], ["passed"] * 3)
        self.assertFalse(runtime.recording)
        self.assertEqual(self.stepper.commands, [])

    def test_spreadsheet_recording_starts_once_confirms_and_writes_each_sample_once(self):
        self.stepper.payload.update(dc=1, df=1, dr=13546, dd=0, da=0, dq=1, dx=0)
        self.stepper.refresh()
        runtime = self.runtime()
        example = (Path(__file__).resolve().parents[1] / "examples" / "start_recording.csv").read_text()
        runtime.load_experiment({"csv": example})
        runtime.start_experiment({})
        for elapsed in (0.1, 0.2, 0.3):
            with runtime._condition:
                runtime._poll_locked(elapsed)
            self.assertFalse(runtime.recording)
        with runtime._condition:
            runtime._poll_locked(0.4)
        self.assertTrue(runtime.recording)
        recorder = runtime.recorder
        self.assertEqual(recorder.status_payload()["merged_rows"], 1)
        self.assertEqual(runtime.experiment_state()["state"], "running")
        with runtime._condition:
            runtime._poll_locked(0.5)
        self.assertEqual(runtime.experiment_state()["state"], "completed")
        self.assertEqual(recorder.status_payload()["merged_rows"], 2)
        self.assertIs(runtime.recorder, recorder)
        runtime.start_experiment({})
        for elapsed in (0.6, 0.7, 0.8, 0.9, 1.0):
            with runtime._condition:
                runtime._poll_locked(elapsed)
        self.assertEqual(runtime.experiment_state()["state"], "completed")
        self.assertIs(runtime.recorder, recorder)
        self.assertEqual(recorder.status_payload()["merged_rows"], 7)
        runtime.set_recording(False)
        self.assertTrue(runtime.latest_export_path().exists())
        self.assertEqual(len(recorder.paths.merged_csv.read_text().splitlines()), 8)
        self.assertEqual(self.stepper.commands, [])

    def test_spreadsheet_disk_error_fails_the_row_without_claiming_recording(self):
        runtime = self.runtime()
        example = (Path(__file__).resolve().parents[1] / "examples" / "start_recording.csv").read_text()
        lines = example.splitlines()
        runtime.load_experiment({"csv": "\n".join((lines[0], lines[1], lines[-1])) + "\n"})
        runtime.start_experiment({})
        with runtime._condition:
            runtime._poll_locked(0.1)
        with patch.object(app, "FlowRunRecorder", side_effect=OSError("disk full")):
            with runtime._condition:
                runtime._poll_locked(0.2)
        self.assertFalse(runtime.recording)
        self.assertIsNone(runtime.recorder)
        result = runtime.experiment_state()["results"][-1]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "disk full")
        with runtime._condition:
            runtime._poll_locked(0.3)
        self.assertFalse(runtime.recording)
        self.assertEqual(self.stepper.commands, [])

    def test_temperature_example_runs_read_only_and_records_channel_evidence(self):
        from read_ed593_ascii import SimulatedEd593Source
        runtime = self.runtime([*self.sources, SimulatedEd593Source()])
        csv_text = (Path(__file__).resolve().parents[1] / "examples" / "temperatures.csv").read_text()
        runtime.load_experiment({"csv": csv_text})
        runtime.start_experiment({})
        for tick in range(1, 7):
            with runtime._condition:
                runtime._poll_locked(tick / 10)
        self.assertEqual(runtime.experiment_state()["state"], "completed")
        self.assertEqual(self.stepper.commands, [])
        self.assertFalse(runtime.recording)
        runtime.set_recording(True)
        with runtime._condition:
            runtime._poll_locked(1.1)
        paths = runtime.recorder.paths
        runtime.set_recording(False)
        merged = paths.merged_csv.read_text()
        raw = paths.source_csvs["ed593"].read_text()
        self.assertIn("ed593_tc0_temperature_c", merged)
        self.assertIn("ed593_tc0_fault", raw)
        self.assertIn("25.", raw)

    def actuator_program(self, source, device, target, pulse=""):
        return ("action,source,max_age_ms,health,timeout_s,set_point,device,pulse_us\n"
                "telemetry_health,esp32,1000,ok,10,,pressure_adc;flow_adc,\n"
                f"requested_state,{source},1000,ok,10,{target},{device},{pulse}\n")

    def test_spreadsheet_solenoid_group_dispatches_all_before_confirmation(self):
        runtime = self.runtime()
        program = self.actuator_program("esp32", "solenoid1;solenoid2;solenoid3", "open")
        # Exercise both open and closed with existing state-setting controls.
        for batch, target in enumerate(("open", "closed")):
            runtime.load_experiment({"csv": program.replace(",open,", f",{target},")})
            runtime.start_experiment({})
            base = batch * 0.5
            with patch.object(runtime.devices, "solenoid_state", wraps=runtime.devices.solenoid_state) as send:
                with runtime._condition:
                    runtime._poll_locked(base + 0.1)
                    runtime._poll_locked(base + 0.2)
                self.assertEqual(send.call_count, 3)
                self.assertEqual([call.args[0] for call in send.call_args_list], [0, 1, 2])
                self.assertEqual(self.esp32.solenoid_states()[:3], (target == "open",) * 3)
                self.assertEqual(runtime.experiment_state()["state"], "running")
                with runtime._condition:
                    runtime._poll_locked(base + 0.3)
                self.assertEqual(runtime.experiment_state()["state"], "completed")
                self.assertEqual(send.call_count, 3)
                checks = runtime.experiment_state()["results"][-1]["checks"]
                self.assertTrue(all(checks[name]["ok"] for name in ("solenoid1", "solenoid2", "solenoid3")))

    def test_solenoid_group_validates_all_owners_before_any_dispatch(self):
        runtime = self.runtime()
        runtime.load_experiment({"csv": self.actuator_program("esp32", "solenoid1;solenoid4", "open")})
        runtime.start_experiment({})
        with patch.object(runtime.devices, "solenoid_state") as send:
            with runtime._condition:
                runtime._poll_locked(0.1)
                runtime._poll_locked(0.2)
            send.assert_not_called()
        self.assertEqual(runtime.experiment_state()["state"], "failed")
        self.assertFalse(any(self.esp32.solenoid_states()))
        self.assertIn("solenoid4 is controlled by controllino", runtime.experiment_state()["results"][-1]["error"])

    def test_solenoid_group_reports_each_dispatch_and_does_not_retry_partial_failure(self):
        runtime = self.runtime()
        runtime.load_experiment({"csv": self.actuator_program("esp32", "solenoid1;solenoid2;solenoid3", "open")})
        runtime.start_experiment({})
        original = runtime.devices.solenoid_state
        def send(index, *args):
            if index == 1:
                raise OSError("connection reset")
            return original(index, *args)
        with patch.object(runtime.devices, "solenoid_state", side_effect=send) as commands:
            with runtime._condition:
                runtime._poll_locked(0.1)
                runtime._poll_locked(0.2)
                runtime._poll_locked(0.3)
            self.assertEqual(commands.call_count, 3)
        state = runtime.experiment_state()
        self.assertEqual(state["state"], "failed")
        self.assertEqual(self.esp32.solenoid_states()[:3], (True, False, True))
        targets = state["results"][-1]["request"]["targets"]
        self.assertEqual(targets["solenoid1"]["status"], "requested")
        self.assertEqual(targets["solenoid2"]["status"], "failed")
        self.assertEqual(targets["solenoid2"]["error"], "connection reset")
        self.assertEqual(targets["solenoid3"]["status"], "requested")

    def test_spreadsheet_solenoids_use_correct_source_and_explicit_state(self):
        runtime = self.runtime()
        for index in range(4):
            owner = "controllino" if index == 3 else "esp32"
            for on in (True, True, False):
                runtime.load_experiment({"csv": self.actuator_program(owner, f"solenoid{index + 1}", "open" if on else "closed")})
                runtime.start_experiment({})
                for elapsed in (0.1, 0.2, 0.3):
                    with runtime._condition:
                        runtime._poll_locked(elapsed)
                self.assertEqual(runtime.experiment_state()["state"], "completed")
                self.assertIs(runtime.latest[f"solenoid{index + 1}_on"], on)
        self.assertEqual(self.stepper.commands, [b"V1 L4,1\n", b"V1 L4,1\n", b"V1 L4,0\n"])

    def test_spreadsheet_brushless_configures_pulse_and_on_off_in_simulation(self):
        sources = core.make_sources(esp32_source="sim", dxmr90_source="sim", stepper_source="sim", esp32_auto_sequence=False)
        runtime = self.runtime(sources)
        program = (Path(__file__).resolve().parents[1] / "examples" / "actuators.csv").read_text()
        runtime.load_experiment({"csv": program})
        runtime.start_experiment({})
        observed_on = False
        for tick in range(1, 16):
            with runtime._condition:
                runtime._poll_locked(tick / 10)
            observed_on |= runtime.latest["stepper_brushless_motor_on"]
        self.assertEqual(runtime.experiment_state()["state"], "completed")
        self.assertTrue(observed_on)
        self.assertEqual(runtime.latest["stepper_brushless_motor_setpoint_us"], 1200)
        self.assertFalse(runtime.latest["stepper_brushless_motor_on"])
        self.assertFalse(runtime.latest["solenoid1_on"])

    def test_spreadsheet_brushless_uses_existing_wire_commands_when_capable(self):
        self.stepper.payload.update(aux="esc", bo=0, bp=1200)
        self.stepper.refresh()
        runtime = self.runtime()
        for index, (target, pulse) in enumerate((("pulse", "1400"), ("on", ""), ("off", ""))):
            runtime.load_experiment({"csv": self.actuator_program("controllino", "brushless_motor", target, pulse)})
            runtime.start_experiment({})
            for elapsed in (index + 0.1, index + 0.2, index + 0.3):
                with runtime._condition:
                    runtime._poll_locked(elapsed)
            self.assertEqual(runtime.experiment_state()["state"], "completed")
        self.assertEqual(self.stepper.commands, [b"V1 P1400\n", b"V1 B1\n", b"V1 B0\n"])

    def test_spreadsheet_solenoid_waits_for_observed_state_not_dispatch(self):
        self.stepper.apply = False
        runtime = self.runtime()
        runtime.load_experiment({"csv": self.actuator_program("controllino", "solenoid4", "open")})
        runtime.start_experiment({})
        for elapsed in (0.1, 0.2, 0.3):
            with runtime._condition:
                runtime._poll_locked(elapsed)
        self.assertEqual(runtime.experiment_state()["state"], "running")
        self.assertEqual(self.stepper.commands, [b"V1 L4,1\n"])
        with runtime._condition:
            runtime._poll_locked(10.2)
        self.assertEqual(runtime.experiment_state()["state"], "failed")
        self.assertFalse(runtime.experiment_state()["results"][-1]["checks"]["requested_state"]["ok"])
        self.assertEqual(self.stepper.commands, [b"V1 L4,1\n"])

    def test_spreadsheet_actuators_fail_on_wrong_owner_capability_bounds_and_estop(self):
        for index, (source, device, target, pulse) in enumerate((("esp32", "solenoid4", "open", ""),
                                              ("controllino", "brushless_motor", "on", ""),
                                              ("controllino", "brushless_motor", "pulse", "999"),
                                              ("controllino", "solenoid4", "open", ""))):
            with self.subTest(device=device, target=target, pulse=pulse):
                if device == "solenoid4" and source == "controllino":
                    self.stepper.payload.update(e=1)
                    self.stepper.refresh()
                runtime = self.runtime()
                if target == "pulse":
                    with self.assertRaisesRegex(ValueError, "Row 2:.*1000 through 2000"):
                        runtime.load_experiment({"csv": self.actuator_program(source, device, target, pulse)})
                    self.assertEqual(self.stepper.commands, [])
                    continue
                runtime.load_experiment({"csv": self.actuator_program(source, device, target, pulse)})
                runtime.start_experiment({})
                for elapsed in (index + 0.1, index + 0.2):
                    with runtime._condition:
                        runtime._poll_locked(elapsed)
                self.assertEqual(runtime.experiment_state()["state"], "failed")
                self.assertIn("error", runtime.experiment_state()["results"][-1])
                self.assertEqual(self.stepper.commands, [])

    def test_explicit_esp32_state_guards_existing_toggle_api(self):
        from unittest.mock import Mock
        source = Mock(spec=["toggle_solenoid"])
        self.controls.sources["esp32"] = source
        for observed, target in ((True, True), (False, False)):
            self.controls.solenoid_state(0, target, {"esp32_sol1": observed}, {})
        source.toggle_solenoid.assert_not_called()
        source.toggle_solenoid.return_value = True
        self.controls.solenoid_state(0, True, {"esp32_sol1": False}, {})
        source.toggle_solenoid.assert_called_once_with(0)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self.controls.solenoid_state(0, True, {}, {})
        source.toggle_solenoid.return_value = False
        with self.assertRaisesRegex(RuntimeError, "did not produce"):
            self.controls.solenoid_state(0, True, {"esp32_sol1": False}, {})

    def test_spreadsheet_mode_changes_use_firmware_command_and_telemetry(self):
        runtime = self.runtime()
        lines = (Path(__file__).resolve().parents[1] / "examples" / "move_stepper.csv").read_text().splitlines()
        program = "\n".join((lines[0], lines[1], lines[-2])) + "\n"
        for index, (mode, wire) in enumerate((("directional", 0), ("positional", 1))):
            runtime.load_experiment({"csv": program.replace("positional", mode)})
            runtime.start_experiment({})
            base = index * 0.5
            with runtime._condition:
                runtime._poll_locked(base + 0.1)
                runtime._poll_locked(base + 0.2)
            self.assertEqual(self.stepper.commands[-1], f"V1 M{wire}\n".encode())
            self.assertEqual(runtime.experiment_state()["state"], "running")
            with runtime._condition:
                runtime._poll_locked(base + 0.3)
            self.assertEqual(runtime.experiment_state()["state"], "completed")
            check = runtime.experiment_state()["results"][-1]["checks"]["requested_state"]
            self.assertEqual(check["observed"], mode)
            self.assertTrue(check["ok"])
        self.assertEqual(self.stepper.commands, [b"V1 M0\n", b"V1 M1\n"])

    def test_spreadsheet_mode_row_enables_following_move(self):
        self.stepper.payload.update(m=0, st=0)
        self.stepper.refresh()
        runtime = self.runtime()
        lines = (Path(__file__).resolve().parents[1] / "examples" / "move_stepper.csv").read_text().splitlines()
        runtime.load_experiment({"csv": "\n".join((lines[0], lines[1], lines[-2], lines[-1])) + "\n"})
        runtime.start_experiment({})
        for elapsed in (0.1, 0.2, 0.3, 0.4):
            with runtime._condition:
                runtime._poll_locked(elapsed)
        self.assertEqual(self.stepper.commands[0], b"V1 M1\n")
        self.assertTrue(self.stepper.commands[1].startswith(b"V1 G-2520,252,"))
        self.stepper.payload.update(mv=0, st=6, sps=0, p=self.stepper.payload["g"])
        self.stepper.refresh()
        with runtime._condition:
            runtime._poll_locked(0.5)
        self.assertEqual(runtime.experiment_state()["state"], "completed")

    def test_spreadsheet_move_sends_bounded_pulses_and_waits_for_firmware_completion(self):
        runtime = self.runtime()
        lines = (Path(__file__).resolve().parents[1] / "examples" / "move_stepper.csv").read_text().splitlines()
        csv_text = "\n".join((lines[0], lines[1], lines[-1])) + "\n"
        for index, (direction, pulses) in enumerate((("forward", -2520), ("reverse", 2520))):
            runtime.load_experiment({"csv": csv_text.replace("forward", direction)})
            runtime.start_experiment({})
            base = index * 0.5
            for elapsed in (base + 0.1, base + 0.2, base + 0.3):
                with runtime._condition:
                    runtime._poll_locked(elapsed)
            self.assertEqual(runtime.experiment_state()["state"], "running")
            moves = [command for command in self.stepper.commands if command.startswith(b"V1 G")]
            self.assertEqual(len(moves), index + 1)
            self.assertTrue(moves[-1].startswith(f"V1 G{pulses},252,".encode()))
            request = runtime.experiment_state()["results"][-1]["request"]
            self.assertEqual(request["distance_mm"], 10)
            self.assertEqual(request["signed_pulse_count"], pulses)
            self.assertEqual(request["pulse_rate_sps"], 252)
            self.stepper.payload.update(mv=0, st=6, sps=0, p=self.stepper.payload["g"])
            self.stepper.refresh()
            with runtime._condition:
                runtime._poll_locked(base + 0.4)
            self.assertEqual(runtime.experiment_state()["state"], "completed")

    def test_spreadsheet_move_timeout_dispatches_stop(self):
        runtime = self.runtime()
        lines = (Path(__file__).resolve().parents[1] / "examples" / "move_stepper.csv").read_text().splitlines()
        runtime.load_experiment({"csv": "\n".join((lines[0], lines[1], lines[-1])) + "\n"})
        runtime.start_experiment({})
        for elapsed in (0.1, 0.2, 15.2):
            with runtime._condition:
                runtime._poll_locked(elapsed)
        self.assertEqual(runtime.experiment_state()["state"], "failed")
        self.assertEqual(self.stepper.commands[-1], b"V1 X\n")
        self.assertFalse(self.stepper.status()["stepper_moving"])
        self.assertTrue(runtime.experiment_state()["results"][-1]["stop_requested"])

    def test_spreadsheet_move_rejects_travel_bounds_and_wrong_mode_before_motion(self):
        runtime = self.runtime()
        lines = (Path(__file__).resolve().parents[1] / "examples" / "move_stepper.csv").read_text().splitlines()
        csv_text = "\n".join((lines[0], lines[1], lines[-1])) + "\n"
        for index, case in enumerate(("travel", "mode")):
            program = csv_text.replace("1,10,forward", "10,20,forward") if case == "travel" else csv_text
            if case == "mode":
                self.stepper.payload.update(m=0, st=0)
                self.stepper.refresh()
            if case == "travel":
                with self.assertRaisesRegex(ValueError, "Row 2:.*max_travel_mm"):
                    runtime.load_experiment({"csv": program})
                self.assertEqual(self.stepper.commands, [])
                continue
            runtime.load_experiment({"csv": program})
            runtime.start_experiment({})
            with runtime._condition:
                runtime._poll_locked(index + 0.1)
                runtime._poll_locked(index + 0.2)
            self.assertEqual(runtime.experiment_state()["state"], "failed")
            self.assertFalse(any(command.startswith(b"V1 G") for command in self.stepper.commands))
            error = runtime.experiment_state()["results"][-1]["error"]
            self.assertIn("max_travel_mm" if case == "travel" else "Web Position", error)

    def test_spreadsheet_reverse_move_works_in_simulation(self):
        sources = core.make_sources(esp32_source="sim", dxmr90_source="sim", stepper_source="sim", esp32_auto_sequence=False)
        runtime = self.runtime(sources)
        runtime.set_stepper_control_mode({"web_position": True})
        initial_position = runtime.latest["stepper_position_mm"]
        lines = (Path(__file__).resolve().parents[1] / "examples" / "move_stepper.csv").read_text().splitlines()
        csv_text = "\n".join((lines[0], lines[1], lines[-1])) + "\n"
        runtime.load_experiment({"csv": csv_text.replace("1,10,forward", "1,1,reverse")})
        runtime.start_experiment({})
        for tick in range(1, 21):
            with runtime._condition:
                runtime._poll_locked(tick / 10)
        self.assertEqual(runtime.experiment_state()["state"], "completed")
        self.assertEqual(runtime.latest["stepper_position_mm"], initial_position - 1)


if __name__ == "__main__":
    unittest.main()
