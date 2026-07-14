"""Tests for the simulation-first Yún stepper command/status contract."""

from __future__ import annotations

import math
import os
import pty
import select
import threading
import time
import unittest
from pathlib import Path

from networked_sensors.dashboard import DashboardRuntime, INDEX_HTML, parse_args
from networked_sensors.supervisor_core import (
    DEFAULT_STEPPER_MAX_DISTANCE_MM,
    DEFAULT_STEPPER_MAX_SPEED_MM_S,
    NetworkStepperSource,
    SimulatedStepperSource,
    SourceMerger,
    UsbStepperSource,
    make_sources,
)
from networked_sensors.yun_stepper_bridge import (
    CommandRejected,
    SerialBridgeState,
    StepperBridgeHandler,
    ThreadedHTTPServer,
    validate_command,
)


class SimulatedStepperSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stepper = SimulatedStepperSource()
        self.stepper.poll(0.0)

    def advance(self, stop_s: float, period_s: float = 0.1) -> None:
        tick = period_s
        while tick <= stop_s + 1e-9:
            self.stepper.poll(tick)
            tick += period_s

    def test_positive_move_reaches_exact_open_loop_target(self) -> None:
        accepted = self.stepper.move(2.5, 2.0, "positive-test")
        self.assertEqual(accepted["stepper_direction"], "positive")
        self.advance(3.0)
        status = self.stepper.status()
        self.assertEqual(status["stepper_state"], "completed")
        self.assertEqual(status["stepper_position_mm"], 71.09)
        self.assertEqual(status["stepper_remaining_mm"], 0.0)
        self.assertEqual(status["stepper_command_id"], "positive-test")
        self.assertEqual(status["stepper_position_semantics"], "commanded_open_loop")

    def test_negative_distance_selects_negative_direction(self) -> None:
        self.stepper.move(-1.0, 1.0, 5.0)
        self.assertEqual(self.stepper.status()["stepper_direction"], "negative")
        self.advance(2.0)
        self.assertEqual(self.stepper.status()["stepper_position_mm"], 67.59)

    def test_rejects_invalid_numeric_contract(self) -> None:
        invalid = (
            (0, 1),
            (math.nan, 1),
            (1, 0),
            (1, -1),
            (1, DEFAULT_STEPPER_MAX_SPEED_MM_S + 0.1),
            (DEFAULT_STEPPER_MAX_DISTANCE_MM + 0.1, 1),
        )
        for distance, speed in invalid:
            with self.subTest(
                distance=distance,
                speed=speed,
            ):
                with self.assertRaises(ValueError):
                    self.stepper.move(distance, speed)

    def test_rejects_second_move_while_busy(self) -> None:
        self.stepper.move(5.0, 1.0)
        with self.assertRaisesRegex(RuntimeError, "busy"):
            self.stepper.move(1.0, 1.0)

    def test_limit_blocks_motion_into_end_but_permits_motion_away(self) -> None:
        self.stepper.set_limits(positive=True)
        status = self.stepper.status()
        self.assertEqual(status["stepper_d6_raw"], "LOW")
        self.assertTrue(status["stepper_positive_limit_latched"])
        with self.assertRaisesRegex(RuntimeError, "positive limit"):
            self.stepper.move(1.0, 1.0)

        self.stepper.move(-1.0, 1.0)
        self.assertFalse(self.stepper.status()["stepper_positive_limit_latched"])
        self.advance(2.0)
        self.assertEqual(self.stepper.status()["stepper_position_mm"], 67.59)

    def test_limit_activation_during_motion_stops_immediately(self) -> None:
        self.stepper.move(5.0, 2.0, 10.0)
        self.stepper.poll(0.2)
        position_before_limit = self.stepper.status()["stepper_position_mm"]
        self.stepper.set_limits(positive=True)
        status = self.stepper.status()
        self.assertEqual(status["stepper_state"], "limit_blocked")
        self.assertFalse(status["stepper_moving"])
        self.stepper.poll(1.0)
        self.assertEqual(
            self.stepper.status()["stepper_position_mm"],
            position_before_limit,
        )

    def test_optional_d8_seek_and_control_mode_are_explicit(self) -> None:
        self.stepper.set_control_mode(False)
        with self.assertRaisesRegex(RuntimeError, "Web Position"):
            self.stepper.move(1.0, 1.0)
        self.stepper.set_control_mode(True)
        status = self.stepper.home()
        self.assertTrue(status["stepper_homed"])
        self.assertEqual(status["stepper_position_mm"], 0.0)
        self.assertEqual(status["stepper_state"], "homed")

    def test_operator_stop_and_local_disable_stop_motion(self) -> None:
        self.stepper.move(5.0, 2.0)
        self.stepper.poll(0.2)
        self.stepper.stop()
        self.assertEqual(self.stepper.status()["stepper_state"], "stopped")
        self.assertFalse(self.stepper.status()["stepper_moving"])

        self.stepper.move(-1.0, 1.0)
        self.stepper.set_local_enabled(False)
        status = self.stepper.status()
        self.assertFalse(status["stepper_local_enabled"])
        self.assertFalse(status["stepper_moving"])
        with self.assertRaisesRegex(RuntimeError, "enable"):
            self.stepper.move(-1.0, 1.0)

    def test_software_estop_latches_stops_and_requires_reset(self) -> None:
        self.stepper.move(5.0, 2.0)
        self.stepper.poll(0.2)
        stopped = self.stepper.emergency_stop()
        self.assertTrue(stopped["stepper_estop_latched"])
        self.assertTrue(stopped["stepper_blocked"])
        self.assertEqual(stopped["stepper_state"], "emergency_stop")
        self.assertFalse(stopped["stepper_moving"])
        with self.assertRaisesRegex(RuntimeError, "E-STOP"):
            self.stepper.move(1.0, 1.0)

        reset = self.stepper.reset_emergency_stop()
        self.assertFalse(reset["stepper_estop_latched"])
        self.assertEqual(reset["stepper_state"], "ready")
        self.assertTrue(self.stepper.move(1.0, 1.0)["stepper_moving"])

    def test_merger_keeps_stepper_health_and_status_shape(self) -> None:
        stepper = SimulatedStepperSource()
        merger = SourceMerger([stepper], stale_after_s=0.5)
        from datetime import datetime, timezone

        sample = merger.poll(0.0, datetime.now(timezone.utc))
        self.assertTrue(sample["stepper_connected"])
        self.assertEqual(sample["stepper_mode"], "sim")
        for field in stepper.expected_fields:
            self.assertIn(field, sample)


class UsbStepperSourceTests(unittest.TestCase):
    FORWARD_BLOCKED = (
        '{"v":1,"t":"s","q":7,"d4":0,"d5":1,"d6":0,"d8":1,'
        '"lp":1,"ln":0,"b":1,"r":"positive_limit","sps":0,"csps":150,"ds":1}'
    )
    REVERSE_MOVING = (
        '{"v":1,"t":"s","q":8,"d4":0,"d5":0,"d6":0,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"none","sps":-150,"csps":150,"ds":1}'
    )
    STOPPED_SPEED_READY = (
        '{"v":1,"t":"s","q":9,"d4":1,"d5":0,"d6":1,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,"csps":150,"ds":1}'
    )
    INVERTED_REVERSE_MOVING = (
        '{"v":1,"t":"s","q":10,"d4":0,"d5":0,"d6":0,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"none","sps":150,"csps":150,"ds":-1}'
    )
    POSITION_LOCAL_OFF = (
        '{"v":1,"t":"s","q":20,"d4":1,"d5":0,"d6":1,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,"csps":150,"ds":1,'
        '"m":0,"h":0,"a":1,"e":0,"mv":0,"st":0,"p":0,"g":0,"c":0}'
    )
    WEB_UNHOMED_REVERSE_ARMED = (
        '{"v":1,"t":"s","q":21,"d4":0,"d5":0,"d6":1,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"none","sps":0,"csps":150,"ds":1,'
        '"m":1,"h":0,"a":1,"e":0,"mv":0,"st":2,"p":0,"g":0,"c":0}'
    )
    WEB_READY_FORWARD_ARMED = (
        '{"v":1,"t":"s","q":22,"d4":0,"d5":1,"d6":1,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"none","sps":0,"csps":150,"ds":1,'
        '"m":1,"h":1,"a":1,"e":0,"mv":0,"st":5,"p":1000,"g":1000,"c":0}'
    )
    ESTOP_LOCAL_ON = (
        '{"v":1,"t":"s","q":23,"d4":0,"d5":1,"d6":1,"d8":1,'
        '"lp":0,"ln":0,"b":1,"r":"emergency_stop","sps":0,"csps":150,"ds":1,'
        '"m":0,"h":0,"a":1,"e":1,"mv":0,"st":9,"p":0,"g":0,"c":0}'
    )

    def test_decodes_forward_blocked_by_positive_limit(self) -> None:
        status = UsbStepperSource.decode_status_line(self.FORWARD_BLOCKED)
        self.assertTrue(status["stepper_local_enabled"])
        self.assertEqual(status["stepper_manual_direction"], "forward")
        self.assertEqual(status["stepper_d5_raw"], "HIGH")
        self.assertTrue(status["stepper_positive_limit_active"])
        self.assertTrue(status["stepper_positive_limit_latched"])
        self.assertTrue(status["stepper_blocked"])
        self.assertEqual(status["stepper_blocked_reason"], "positive_limit")
        self.assertEqual(status["stepper_state"], "limit_blocked")
        self.assertFalse(status["stepper_moving"])
        self.assertFalse(status["stepper_command_capable"])
        self.assertTrue(status["stepper_speed_command_capable"])
        self.assertTrue(status["stepper_direction_command_capable"])
        self.assertEqual(status["stepper_direction_mapping"], "normal")
        self.assertEqual(status["stepper_command_speed_mm_s"], 1.5)

    def test_decodes_reverse_motion_away_from_positive_limit(self) -> None:
        status = UsbStepperSource.decode_status_line(self.REVERSE_MOVING)
        self.assertEqual(status["stepper_manual_direction"], "reverse")
        self.assertEqual(status["stepper_direction"], "negative")
        self.assertTrue(status["stepper_positive_limit_active"])
        self.assertFalse(status["stepper_positive_limit_latched"])
        self.assertFalse(status["stepper_blocked"])
        self.assertTrue(status["stepper_moving"])
        self.assertEqual(status["stepper_speed_mm_s"], -1.5)

    def test_inverted_electrical_sign_preserves_logical_reverse_status(self) -> None:
        status = UsbStepperSource.decode_status_line(
            self.INVERTED_REVERSE_MOVING
        )
        self.assertEqual(status["stepper_direction_mapping"], "inverted")
        self.assertEqual(status["stepper_manual_direction"], "reverse")
        self.assertEqual(status["stepper_direction"], "negative")
        self.assertEqual(status["stepper_speed_mm_s"], -1.5)

    def test_decodes_mode_and_suppresses_open_loop_coordinates(self) -> None:
        status = UsbStepperSource.decode_status_line(
            self.WEB_READY_FORWARD_ARMED
        )
        self.assertTrue(status["stepper_command_capable"])
        self.assertTrue(status["stepper_home_capable"])
        self.assertTrue(status["stepper_mode_command_capable"])
        self.assertEqual(status["stepper_control_mode"], "web_position")
        self.assertTrue(status["stepper_homed"])
        self.assertEqual(status["stepper_authorized_direction"], "forward")
        self.assertIsNone(status["stepper_position_mm"])
        self.assertIsNone(status["stepper_target_mm"])
        self.assertEqual(
            status["stepper_position_semantics"],
            "open_loop_counter_not_exposed",
        )
        self.assertEqual(status["stepper_state"], "ready")
        self.assertTrue(status["stepper_estop_capable"])
        self.assertFalse(status["stepper_estop_latched"])

        boot_disarmed = UsbStepperSource.decode_status_line(
            self.WEB_READY_FORWARD_ARMED.replace('"a":1', '"a":0')
        )
        self.assertFalse(boot_disarmed["stepper_boot_armed"])
        self.assertFalse(boot_disarmed["stepper_local_enabled"])

    def test_rejects_malformed_or_wrong_version_status(self) -> None:
        invalid_lines = (
            "not json",
            "[]",
            '{"v":2,"t":"s"}',
            self.FORWARD_BLOCKED.replace('"d6":0', '"d6":2'),
            self.FORWARD_BLOCKED.replace('"sps":0', '"sps":"fast"'),
            self.FORWARD_BLOCKED.replace('"csps":150', '"csps":1001'),
            self.FORWARD_BLOCKED.replace('"ds":1', '"ds":0'),
            self.WEB_READY_FORWARD_ARMED.replace('"st":5', '"st":10'),
            self.WEB_READY_FORWARD_ARMED.replace('"p":1000', '"p":true'),
        )
        for line in invalid_lines:
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    UsbStepperSource.decode_status_line(line)

    def test_reads_latest_status_from_usb_like_pseudo_terminal(self) -> None:
        master_fd, slave_fd = pty.openpty()
        port = os.ttyname(slave_fd)
        source = UsbStepperSource(port=port)
        try:
            self.assertIsNone(source.poll(0.0))
            os.write(master_fd, b"human startup line\r\n")
            os.write(master_fd, (self.REVERSE_MOVING + "\r\n").encode())
            reading = source.poll(0.1)
            self.assertIsNotNone(reading)
            assert reading is not None
            self.assertEqual(reading.mode, "usb")
            self.assertEqual(reading.values["stepper_status_sequence"], 8)
            self.assertEqual(reading.values["stepper_state"], "manual_moving")
        finally:
            source.close()
            os.close(master_fd)
            os.close(slave_fd)


    def test_missing_usb_port_is_disconnected_not_fatal(self) -> None:
        source = UsbStepperSource(port="/dev/this-stepper-port-does-not-exist")
        self.assertIsNone(source.poll(0.0))
        self.assertIsNotNone(source.last_error)
        from datetime import datetime, timezone

        merger = SourceMerger([source], stale_after_s=0.5)
        sample = merger.poll(0.1, datetime.now(timezone.utc))
        self.assertFalse(sample["stepper_connected"])
        self.assertIn("does-not-exist", str(sample["stepper_transport_error"]))

    def test_writes_bounded_speed_command_only_while_d4_is_off(self) -> None:
        master_fd, slave_fd = pty.openpty()
        port = os.ttyname(slave_fd)
        source = UsbStepperSource(port=port)
        try:
            self.assertIsNone(source.poll(0.0))
            os.write(master_fd, (self.STOPPED_SPEED_READY + "\r\n").encode())
            self.assertIsNotNone(source.poll(0.1))
            source.set_speed(3.25)
            self.assertEqual(os.read(master_fd, 32), b"V1 S325\n")
            source.set_direction_mapping(True)
            self.assertEqual(os.read(master_fd, 32), b"V1 D1\n")

            os.write(master_fd, (self.REVERSE_MOVING + "\r\n").encode())
            self.assertIsNotNone(source.poll(0.2))
            with self.assertRaisesRegex(RuntimeError, "D4 OFF"):
                source.set_speed(4.0)
        finally:
            source.close()
            os.close(master_fd)
            os.close(slave_fd)

    def test_rejects_invalid_manual_speed_without_writing(self) -> None:
        source = UsbStepperSource()
        for speed in (0, -1, 10.1, math.nan, True, "fast"):
            with self.subTest(speed=speed):
                with self.assertRaises(ValueError):
                    source.set_speed(speed)

    def test_rejects_non_boolean_direction_mapping(self) -> None:
        source = UsbStepperSource()
        for inverted in (0, 1, "true", None):
            with self.subTest(inverted=inverted):
                with self.assertRaises(ValueError):
                    source.set_direction_mapping(inverted)

    def test_writes_mode_home_move_and_stop_commands_with_guards(self) -> None:
        master_fd, slave_fd = pty.openpty()
        port = os.ttyname(slave_fd)
        source = UsbStepperSource(port=port)
        try:
            self.assertIsNone(source.poll(0.0))
            os.write(master_fd, (self.POSITION_LOCAL_OFF + "\r\n").encode())
            self.assertIsNotNone(source.poll(0.1))
            source.set_control_mode(True)
            self.assertEqual(os.read(master_fd, 32), b"V1 M1\n")

            os.write(
                master_fd,
                (self.WEB_UNHOMED_REVERSE_ARMED + "\r\n").encode(),
            )
            self.assertIsNotNone(source.poll(0.2))
            source.move(-2.5, 3.25, "unreferenced-command")
            self.assertEqual(os.read(master_fd, 64), b"V1 G-250,325,1\n")
            source.home()
            self.assertEqual(os.read(master_fd, 32), b"V1 H\n")

            os.write(
                master_fd,
                (self.WEB_READY_FORWARD_ARMED + "\r\n").encode(),
            )
            self.assertIsNotNone(source.poll(0.3))
            source.move(2.5, 3.25, "operator-command")
            self.assertEqual(os.read(master_fd, 64), b"V1 G250,325,2\n")
            source.stop()
            self.assertEqual(os.read(master_fd, 32), b"V1 X\n")
        finally:
            source.close()
            os.close(master_fd)
            os.close(slave_fd)

    def test_decodes_and_writes_latched_software_estop_contract(self) -> None:
        status = UsbStepperSource.decode_status_line(self.ESTOP_LOCAL_ON)
        self.assertTrue(status["stepper_estop_capable"])
        self.assertTrue(status["stepper_estop_latched"])
        self.assertFalse(status["stepper_local_enabled"])
        self.assertFalse(status["stepper_moving"])
        self.assertTrue(status["stepper_blocked"])
        self.assertEqual(status["stepper_state"], "emergency_stop")

        master_fd, slave_fd = pty.openpty()
        source = UsbStepperSource(port=os.ttyname(slave_fd))
        try:
            self.assertIsNone(source.poll(0.0))
            os.write(master_fd, (self.WEB_READY_FORWARD_ARMED + "\r\n").encode())
            self.assertIsNotNone(source.poll(0.1))
            source.emergency_stop()
            self.assertEqual(os.read(master_fd, 32), b"V1 E1\n")

            os.write(master_fd, (self.ESTOP_LOCAL_ON + "\r\n").encode())
            self.assertIsNotNone(source.poll(0.2))
            with self.assertRaisesRegex(RuntimeError, "D4 OFF"):
                source.reset_emergency_stop()

            reset_ready = self.ESTOP_LOCAL_ON.replace('"q":23', '"q":24').replace(
                '"d4":0', '"d4":1'
            )
            os.write(master_fd, (reset_ready + "\r\n").encode())
            self.assertIsNotNone(source.poll(0.3))
            source.reset_emergency_stop()
            self.assertEqual(os.read(master_fd, 32), b"V1 E0\n")
        finally:
            source.close()
            os.close(master_fd)
            os.close(slave_fd)


class NetworkStepperSourceTests(unittest.TestCase):
    def test_cli_and_source_factory_enable_network_mode(self) -> None:
        args = parse_args(
            [
                "--stepper-source",
                "network",
                "--stepper-url",
                "http://192.168.8.137:8080",
                "--stepper-timeout",
                "0.4",
            ]
        )
        self.assertEqual(args.stepper_source, "network")
        self.assertEqual(args.stepper_url, "http://192.168.8.137:8080")
        self.assertEqual(args.stepper_timeout, 0.4)
        sources = make_sources(
            esp32_source="off",
            dxmr90_source="off",
            stepper_source="network",
            stepper_network_url=args.stepper_url,
            stepper_network_timeout=args.stepper_timeout,
        )
        stepper = next(source for source in sources if source.name == "stepper")
        self.assertIsInstance(stepper, NetworkStepperSource)
        stepper.close()

    def test_bridge_service_and_network_adapter_share_v1_contract(self) -> None:
        master_fd, slave_fd = pty.openpty()
        bridge = SerialBridgeState(
            device=os.ttyname(slave_fd),
            ack_timeout=0.5,
        )
        bridge.open()
        server = ThreadedHTTPServer(("127.0.0.1", 0), StepperBridgeHandler)
        server.bridge = bridge
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        host, port = server.server_address
        source = NetworkStepperSource(f"http://{host}:{port}", timeout=0.5)
        initial = UsbStepperSourceTests.POSITION_LOCAL_OFF[:-1] + ',"o":0}'
        updated = initial.replace('"q":20', '"q":21').replace(
            '"csps":150', '"csps":325'
        ).replace('"o":0', '"o":2')
        received: list[bytes] = []
        try:
            os.write(master_fd, (initial + "\n").encode("ascii"))
            deadline = time.monotonic() + 2.0
            reading = None
            elapsed = 0.0
            while time.monotonic() < deadline and reading is None:
                reading = source.poll(elapsed)
                elapsed += 0.1
                time.sleep(0.02)
            self.assertIsNotNone(reading)
            assert reading is not None
            self.assertEqual(reading.mode, "network")
            self.assertEqual(reading.values["stepper_control_owner"], "none")

            def acknowledge() -> None:
                received.append(os.read(master_fd, 64))
                os.write(
                    master_fd,
                    b'{"v":1,"t":"a","ok":1,"e":"none"}\n',
                )
                os.write(master_fd, (updated + "\n").encode("ascii"))

            responder = threading.Thread(target=acknowledge)
            responder.start()
            source.set_speed(3.25)
            responder.join(timeout=1.0)
            self.assertFalse(responder.is_alive())
            self.assertEqual(received, [b"V1 S325\n"])

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                source.poll(elapsed)
                elapsed += 0.1
                status = source.status()
                if status.get("stepper_command_speed_mm_s") == 3.25:
                    break
                time.sleep(0.02)
            status = source.status()
            self.assertEqual(status["stepper_command_speed_mm_s"], 3.25)
            self.assertEqual(
                status["stepper_control_owner"],
                "manual_d4_d5+network_control",
            )
        finally:
            source.close()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1.0)
            bridge.close()
            os.close(master_fd)
            os.close(slave_fd)

    def test_dashboard_requires_fresh_network_estop_status(self) -> None:
        master_fd, slave_fd = pty.openpty()
        bridge = SerialBridgeState(os.ttyname(slave_fd), ack_timeout=0.5)
        bridge.open()
        server = ThreadedHTTPServer(("127.0.0.1", 0), StepperBridgeHandler)
        server.bridge = bridge
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        host, port = server.server_address
        initial = UsbStepperSourceTests.POSITION_LOCAL_OFF[:-1] + ',"o":0}'
        estopped = (
            UsbStepperSourceTests.ESTOP_LOCAL_ON[:-1] + ',"o":0}'
        ).replace('"q":23', '"q":24')
        os.write(master_fd, (initial + "\n").encode("ascii"))
        runtime = DashboardRuntime(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=1.0,
            history_limit=10,
            record_dir=Path("/tmp/stepper-dashboard-network-test-recordings"),
            esp32_source="off",
            dxmr90_source="off",
            stepper_source="network",
            stepper_port="/dev/null",
            stepper_baud=9600,
            stepper_network_url=f"http://{host}:{port}",
            stepper_network_timeout=0.5,
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
                if runtime.stepper_status()["stepper"].get(
                    "stepper_status_sequence"
                ) == 20:
                    break
                time.sleep(0.02)
            self.assertEqual(
                runtime.stepper_status()["stepper"]["stepper_status_sequence"],
                20,
            )

            def acknowledge() -> None:
                self.assertEqual(os.read(master_fd, 64), b"V1 E1\n")
                os.write(
                    master_fd,
                    b'{"v":1,"t":"a","ok":1,"e":"none"}\n',
                )
                os.write(master_fd, (estopped + "\n").encode("ascii"))

            responder = threading.Thread(target=acknowledge)
            responder.start()
            result = runtime.emergency_stop_stepper()
            responder.join(timeout=1.0)
            self.assertFalse(responder.is_alive())
            self.assertTrue(result["confirmed"])
            self.assertTrue(result["stepper"]["stepper_estop_latched"])
            self.assertEqual(result["stepper"]["stepper_status_sequence"], 24)
        finally:
            runtime.stop()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1.0)
            bridge.close()
            os.close(master_fd)
            os.close(slave_fd)

    def test_bridge_rejects_unknown_or_oversized_grammar_before_uart(self) -> None:
        # The archived Yun image uses Python 2.7, which rejects non-ASCII source
        # without an encoding declaration. Keep this small deployment script
        # ASCII-only so copying it verbatim cannot recreate that startup fault.
        bridge_source = Path(__file__).with_name("yun_stepper_bridge.py").read_bytes()
        bridge_source.decode("ascii")
        self.assertEqual(validate_command(" V1 E1\n"), "V1 E1")
        for command in ("V1 Q", "V1 G1,2", "V2 E1", "V1 S" + "1" * 60):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    validate_command(command)

    def test_bridge_propagates_firmware_rejection_and_ack_timeout(self) -> None:
        master_fd, slave_fd = pty.openpty()
        bridge = SerialBridgeState(os.ttyname(slave_fd), ack_timeout=0.2)
        bridge.open()
        try:
            def reject() -> None:
                self.assertEqual(os.read(master_fd, 64), b"V1 D1\n")
                os.write(
                    master_fd,
                    b'{"v":1,"t":"a","ok":0,"e":"owned_by_usb"}\n',
                )

            responder = threading.Thread(target=reject)
            responder.start()
            with self.assertRaisesRegex(CommandRejected, "owned_by_usb"):
                bridge.command("V1 D1")
            responder.join(timeout=1.0)
            self.assertFalse(responder.is_alive())

            bridge.ack_timeout = 0.05
            with self.assertRaisesRegex(RuntimeError, "acknowledgement timed out"):
                bridge.command("V1 E1")
            self.assertEqual(os.read(master_fd, 64), b"V1 E1\n")
            self.assertFalse(bridge.health()["command_synchronized"])
            with self.assertRaisesRegex(RuntimeError, "unsynchronized"):
                bridge.command("V1 X")
            readable, _, _ = select.select([master_fd], [], [], 0.05)
            self.assertFalse(readable)
        finally:
            bridge.close()
            os.close(master_fd)
            os.close(slave_fd)

    def test_network_decode_reports_explicit_usb_owner(self) -> None:
        line = UsbStepperSourceTests.POSITION_LOCAL_OFF[:-1] + ',"o":1}'
        status = NetworkStepperSource.decode_status_line(line)
        self.assertEqual(
            status["stepper_control_owner"],
            "manual_d4_d5+usb_control",
        )


class UsbStepperDashboardTests(unittest.TestCase):
    def test_dashboard_exposes_and_latches_simulated_software_estop(self) -> None:
        self.assertIn('id="emergencyStop"', INDEX_HTML)
        self.assertIn('id="emergencyReset"', INDEX_HTML)
        self.assertIn('/api/stepper/estop', INDEX_HTML)
        runtime = DashboardRuntime(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=5.0,
            history_limit=10,
            record_dir=Path("/tmp/stepper-dashboard-estop-test-recordings"),
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
        stopped = runtime.emergency_stop_stepper()
        self.assertTrue(stopped["confirmed"])
        self.assertTrue(stopped["stepper"]["stepper_estop_latched"])
        self.assertFalse(stopped["stepper"]["stepper_moving"])
        with self.assertRaisesRegex(RuntimeError, "E-STOP"):
            runtime.move_stepper({"distance_mm": 1.0, "speed_mm_s": 1.0})
        reset = runtime.reset_stepper_emergency_stop()
        self.assertTrue(reset["confirmed"])
        self.assertFalse(reset["stepper"]["stepper_estop_latched"])

    def test_runtime_requires_fresh_usb_estop_and_reset_acknowledgements(self) -> None:
        master_fd, slave_fd = pty.openpty()
        runtime = DashboardRuntime(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=5.0,
            history_limit=10,
            record_dir=Path("/tmp/stepper-dashboard-estop-usb-test-recordings"),
            esp32_source="sim",
            dxmr90_source="sim",
            stepper_source="usb",
            stepper_port=os.ttyname(slave_fd),
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
            os.write(
                master_fd,
                (UsbStepperSourceTests.POSITION_LOCAL_OFF + "\r\n").encode(),
            )
            stepper = next(
                source for source in runtime.sources if source.name == "stepper"
            )
            self.assertIsNotNone(stepper.poll(0.1))
            runtime.start()

            stop_command: list[bytes] = []

            def acknowledge_stop() -> None:
                stop_command.append(os.read(master_fd, 32))
                status = (
                    '{"v":1,"t":"s","q":21,"d4":1,"d5":0,"d6":1,"d8":1,'
                    '"lp":0,"ln":0,"b":1,"r":"emergency_stop","sps":0,'
                    '"csps":150,"ds":1,"m":0,"h":0,"a":1,"e":1,'
                    '"mv":0,"st":9,"p":0,"g":0,"c":0}\r\n'
                )
                os.write(master_fd, status.encode())

            responder = threading.Thread(target=acknowledge_stop)
            responder.start()
            stopped = runtime.emergency_stop_stepper()
            responder.join(timeout=1.0)
            self.assertEqual(stop_command, [b"V1 E1\n"])
            self.assertTrue(stopped["confirmed"])
            self.assertTrue(stopped["stepper"]["stepper_estop_latched"])

            reset_command: list[bytes] = []

            def acknowledge_reset() -> None:
                reset_command.append(os.read(master_fd, 32))
                status = (
                    '{"v":1,"t":"s","q":22,"d4":1,"d5":0,"d6":1,"d8":1,'
                    '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,'
                    '"csps":150,"ds":1,"m":0,"h":0,"a":1,"e":0,'
                    '"mv":0,"st":0,"p":0,"g":0,"c":0}\r\n'
                )
                os.write(master_fd, status.encode())

            responder = threading.Thread(target=acknowledge_reset)
            responder.start()
            reset = runtime.reset_stepper_emergency_stop()
            responder.join(timeout=1.0)
            self.assertEqual(reset_command, [b"V1 E0\n"])
            self.assertTrue(reset["confirmed"])
            self.assertFalse(reset["stepper"]["stepper_estop_latched"])
        finally:
            runtime.stop()
            os.close(master_fd)
            os.close(slave_fd)

    def test_runtime_forwards_manual_speed_without_enabling_move(self) -> None:
        master_fd, slave_fd = pty.openpty()
        port = os.ttyname(slave_fd)
        stopped_status = (
            '{"v":1,"t":"s","q":9,"d4":1,"d5":0,"d6":1,"d8":1,'
            '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,"csps":150,"ds":1}\r\n'
        )
        runtime = DashboardRuntime(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=5.0,
            history_limit=10,
            record_dir=Path("/tmp/stepper-dashboard-test-recordings"),
            esp32_source="sim",
            dxmr90_source="sim",
            stepper_source="usb",
            stepper_port=port,
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
            os.write(master_fd, stopped_status.encode())
            stepper = next(
                source for source in runtime.sources if source.name == "stepper"
            )
            self.assertIsNotNone(stepper.poll(0.1))
            runtime.start()

            speed_command: list[bytes] = []

            def acknowledge_speed() -> None:
                speed_command.append(os.read(master_fd, 32))
                confirmed_status = (
                    '{"v":1,"t":"s","q":10,"d4":1,"d5":0,"d6":1,"d8":1,'
                    '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,'
                    '"csps":400,"ds":1}\r\n'
                )
                os.write(master_fd, confirmed_status.encode())

            responder = threading.Thread(target=acknowledge_speed)
            responder.start()
            payload = runtime.set_stepper_speed({"speed_mm_s": 4.0})
            responder.join(timeout=1.0)
            self.assertFalse(responder.is_alive())
            self.assertEqual(speed_command, [b"V1 S400\n"])
            self.assertTrue(payload["confirmed"])
            self.assertEqual(
                payload["sample"]["stepper_command_speed_mm_s"],
                4.0,
            )
            self.assertFalse(payload["stepper"]["stepper_command_capable"])
            self.assertTrue(
                payload["stepper"]["stepper_speed_command_capable"]
            )

            direction_command: list[bytes] = []

            def acknowledge_direction() -> None:
                direction_command.append(os.read(master_fd, 32))
                confirmed_status = (
                    '{"v":1,"t":"s","q":11,"d4":1,"d5":0,"d6":1,"d8":1,'
                    '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,'
                    '"csps":400,"ds":-1}\r\n'
                )
                os.write(master_fd, confirmed_status.encode())

            responder = threading.Thread(target=acknowledge_direction)
            responder.start()
            payload = runtime.set_stepper_direction_mapping({"inverted": True})
            responder.join(timeout=1.0)
            self.assertFalse(responder.is_alive())
            self.assertEqual(direction_command, [b"V1 D1\n"])
            self.assertTrue(payload["confirmed"])
            self.assertEqual(
                payload["sample"]["stepper_direction_mapping"],
                "inverted",
            )
            self.assertTrue(
                payload["stepper"]["stepper_direction_command_capable"]
            )
        finally:
            runtime.stop()
            os.close(master_fd)
            os.close(slave_fd)

    def test_runtime_confirms_control_mode_d8_seek_and_unreferenced_move(self) -> None:
        master_fd, slave_fd = pty.openpty()
        port = os.ttyname(slave_fd)
        local_off = UsbStepperSourceTests.POSITION_LOCAL_OFF + "\r\n"
        runtime = DashboardRuntime(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=5.0,
            history_limit=10,
            record_dir=Path("/tmp/stepper-dashboard-mode-test-recordings"),
            esp32_source="sim",
            dxmr90_source="sim",
            stepper_source="usb",
            stepper_port=port,
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
            os.write(master_fd, local_off.encode())
            stepper = next(
                source for source in runtime.sources if source.name == "stepper"
            )
            self.assertIsNotNone(stepper.poll(0.1))
            runtime.start()

            mode_command: list[bytes] = []

            def acknowledge_mode() -> None:
                mode_command.append(os.read(master_fd, 32))
                status = (
                    '{"v":1,"t":"s","q":21,"d4":1,"d5":0,"d6":1,"d8":1,'
                    '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,"csps":150,"ds":1,'
                    '"m":1,"h":0,"a":1,"mv":0,"st":2,"p":0,"g":0,"c":0}\r\n'
                )
                os.write(master_fd, status.encode())

            responder = threading.Thread(target=acknowledge_mode)
            responder.start()
            payload = runtime.set_stepper_control_mode({"web_position": True})
            responder.join(timeout=1.0)
            self.assertEqual(mode_command, [b"V1 M1\n"])
            self.assertTrue(payload["confirmed"])
            self.assertEqual(
                payload["stepper"]["stepper_control_mode"],
                "web_position",
            )

            armed_reverse = (
                '{"v":1,"t":"s","q":22,"d4":0,"d5":0,"d6":1,"d8":1,'
                '"lp":0,"ln":0,"b":0,"r":"none","sps":0,"csps":150,"ds":1,'
                '"m":1,"h":0,"a":1,"mv":0,"st":2,"p":0,"g":0,"c":0}\r\n'
            )
            os.write(master_fd, armed_reverse.encode())
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if stepper.status().get("stepper_status_sequence") == 22:
                    break
                time.sleep(0.02)
            self.assertEqual(stepper.status()["stepper_status_sequence"], 22)

            home_command: list[bytes] = []

            def acknowledge_home() -> None:
                home_command.append(os.read(master_fd, 32))
                status = (
                    '{"v":1,"t":"s","q":23,"d4":0,"d5":0,"d6":1,"d8":1,'
                    '"lp":0,"ln":0,"b":0,"r":"none","sps":-1,"csps":150,"ds":1,'
                    '"m":1,"h":0,"a":1,"mv":1,"st":3,"p":0,"g":0,"c":1}\r\n'
                )
                os.write(master_fd, status.encode())

            responder = threading.Thread(target=acknowledge_home)
            responder.start()
            payload = runtime.home_stepper()
            responder.join(timeout=1.0)
            self.assertEqual(home_command, [b"V1 H\n"])
            self.assertTrue(payload["confirmed"])
            self.assertEqual(payload["stepper"]["stepper_state"], "homing")

            with self.assertRaisesRegex(ValueError, "positive finite travel"):
                runtime.move_stepper(
                    {"distance_mm": -2.5, "speed_mm_s": 2.0}
                )

            # At a homed mid-stroke position, D5 Reverse must turn the positive
            # operator magnitude into a negative low-level step delta.
            ready_reverse = (
                '{"v":1,"t":"s","q":24,"d4":0,"d5":0,"d6":1,"d8":1,'
                '"lp":0,"ln":0,"b":0,"r":"none","sps":0,"csps":150,"ds":1,'
                '"m":1,"h":0,"a":1,"mv":0,"st":5,"p":10000,"g":10000,"c":0}\r\n'
            )
            os.write(master_fd, ready_reverse.encode())
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if stepper.status().get("stepper_status_sequence") == 24:
                    break
                time.sleep(0.02)
            self.assertEqual(stepper.status()["stepper_status_sequence"], 24)

            move_command: list[bytes] = []

            def acknowledge_move() -> None:
                move_command.append(os.read(master_fd, 64))
                status = (
                    '{"v":1,"t":"s","q":25,"d4":0,"d5":0,"d6":1,"d8":1,'
                    '"lp":0,"ln":0,"b":0,"r":"none","sps":-1,"csps":200,"ds":1,'
                    '"m":1,"h":0,"a":1,"mv":1,"st":4,"p":10000,"g":9750,"c":1}\r\n'
                )
                os.write(master_fd, status.encode())

            responder = threading.Thread(target=acknowledge_move)
            responder.start()
            payload = runtime.move_stepper(
                {
                    "distance_mm": 2.5,
                    "speed_mm_s": 2.0,
                    "command_id": "d5-selected",
                }
            )
            responder.join(timeout=1.0)
            self.assertFalse(responder.is_alive())
            self.assertEqual(move_command, [b"V1 G-250,200,1\n"])
            self.assertEqual(payload["travel_mm"], 2.5)
            self.assertEqual(payload["resolved_direction"], "reverse")
            self.assertEqual(payload["signed_distance_mm"], -2.5)
            self.assertEqual(payload["stepper"]["stepper_command_id"], "d5-selected")
        finally:
            runtime.stop()
            os.close(master_fd)
            os.close(slave_fd)


if __name__ == "__main__":
    unittest.main()
    NetworkStepperSource,
