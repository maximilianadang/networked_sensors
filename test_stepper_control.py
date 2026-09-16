"""Tests for the simulation-first Yún stepper command/status contract."""

from __future__ import annotations

import errno
import json
import math
import os
import pty
import select
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from urllib.parse import parse_qs, urlparse

from networked_sensors.dashboard import (
    DashboardRuntime,
    INDEX_HTML,
    load_dashboard_asset,
    parse_args,
)
from networked_sensors.dashboard_app.system_config import SystemConfig
from networked_sensors.supervisor_core import (
    DEFAULT_STEPPER_HOME_SPEED_MM_S,
    DEFAULT_STEPPER_MAX_DISTANCE_MM,
    DEFAULT_STEPPER_MAX_SPEED_MM_S,
    DEFAULT_STEPPER_MIN_SPEED_MM_S,
    ControllinoStepperSource,
    ControllinoUsbStepperSource,
    NetworkStepperSource,
    SimulatedStepperSource,
    SourceMerger,
    UsbStepperSource,
    make_sources,
)


APP_JS = load_dashboard_asset("app.js")
API_JS = load_dashboard_asset("api.js")
DOM_JS = load_dashboard_asset("dom.js")
STEPPER_JS = load_dashboard_asset("components/stepper.js")
CONFIG_JS = load_dashboard_asset("config.js")
DASHBOARD_CSS = load_dashboard_asset("dashboard.css")
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

    def test_each_physical_limit_blocks_only_motion_into_that_endpoint(self) -> None:
        cases = (
            (True, False, 1.0, "positive limit"),
            (False, True, -1.0, "negative limit"),
        )
        for positive, negative, blocked_distance, message in cases:
            with self.subTest(
                positive=positive,
                negative=negative,
                blocked_distance=blocked_distance,
            ):
                stepper = SimulatedStepperSource()
                stepper.poll(0.0)
                stepper.set_limits(positive=positive, negative=negative)
                with self.assertRaisesRegex(RuntimeError, message):
                    stepper.move(blocked_distance, 1.0)
                allowed = stepper.move(-blocked_distance, 1.0)
                self.assertTrue(allowed["stepper_moving"])

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
        self.stepper.set_brushless_pulse_us(1750)
        self.stepper.set_brushless_motor(True)
        self.assertTrue(self.stepper.status()["stepper_brushless_motor_on"])
        self.assertEqual(
            self.stepper.status()["stepper_brushless_motor_pulse_us"],
            1750,
        )
        self.stepper.move(5.0, 2.0)
        self.stepper.poll(0.2)
        stopped = self.stepper.emergency_stop()
        self.assertTrue(stopped["stepper_estop_latched"])
        self.assertTrue(stopped["stepper_blocked"])
        self.assertEqual(stopped["stepper_state"], "emergency_stop")
        self.assertFalse(stopped["stepper_moving"])
        self.assertFalse(stopped["stepper_brushless_motor_on"])
        self.assertEqual(stopped["stepper_brushless_motor_pulse_us"], 1000)
        self.assertEqual(
            stopped["stepper_brushless_motor_setpoint_us"],
            1750,
        )
        with self.assertRaisesRegex(RuntimeError, "E-STOP"):
            self.stepper.set_brushless_motor(True)
        with self.assertRaisesRegex(RuntimeError, "E-STOP"):
            self.stepper.move(1.0, 1.0)

        reset = self.stepper.reset_emergency_stop()
        self.assertFalse(reset["stepper_estop_latched"])
        self.assertEqual(reset["stepper_state"], "ready")
        self.assertFalse(reset["stepper_brushless_motor_on"])
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


class SystemConfigTests(unittest.TestCase):
    def test_legacy_version_one_file_gets_default_geometry_on_next_write(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "system_config.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "stepper": {"dro_zero_raw_mm": 12.34},
                    }
                ),
                encoding="utf-8",
            )
            config = SystemConfig(path)
            self.assertEqual(
                config.powder_mass_per_stepper_travel_g_per_mm,
                2.4,
            )
            config.set_stepper_dro_zero_raw_mm(56.78)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["geometry"],
                {
                    "powder_mass_per_stepper_travel_g_per_mm": 2.4,
                },
            )

    def test_rejects_nonpositive_or_nonfinite_geometry(self) -> None:
        for value in (0, -1, float("inf"), True, "2.4"):
            with self.subTest(value=value), TemporaryDirectory() as directory:
                path = Path(directory) / "system_config.json"
                path.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "geometry": {
                                "powder_mass_per_stepper_travel_g_per_mm": value,
                            },
                            "stepper": {"dro_zero_raw_mm": None},
                        }
                    ),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "must be a positive finite number",
                ):
                    SystemConfig(path)


class UsbStepperSourceTests(unittest.TestCase):
    FORWARD_BLOCKED = (
        '{"v":1,"t":"s","q":7,"d4":0,"d5":1,"d6":0,"d8":1,'
        '"lp":1,"ln":0,"b":1,"r":"positive_limit","sps":0,"csps":378,'
        '"ds":1,"en":0}'
    )
    REVERSE_MOVING = (
        '{"v":1,"t":"s","q":8,"d4":0,"d5":0,"d6":0,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"none","sps":-378,"csps":378,'
        '"aps":350,"ds":1,"en":1}'
    )
    STOPPED_SPEED_READY = (
        '{"v":1,"t":"s","q":9,"d4":1,"d5":0,"d6":1,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,"csps":378,'
        '"ds":1,"en":0}'
    )
    INVERTED_REVERSE_MOVING = (
        '{"v":1,"t":"s","q":10,"d4":0,"d5":0,"d6":0,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"none","sps":378,"csps":378,"ds":-1}'
    )
    POSITION_LOCAL_OFF = (
        '{"v":1,"t":"s","q":20,"d4":1,"d5":0,"d6":1,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,"csps":378,"ds":1,"en":0,'
        '"m":0,"h":0,"a":1,"e":0,"mv":0,"st":0,"p":0,"g":0,"c":0}'
    )
    WEB_UNHOMED_REVERSE_ARMED = (
        '{"v":1,"t":"s","q":21,"d4":0,"d5":0,"d6":1,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"none","sps":0,"csps":378,"ds":1,"en":0,'
        '"m":1,"h":0,"a":1,"e":0,"mv":0,"st":2,"p":0,"g":0,"c":0}'
    )
    WEB_READY_FORWARD_ARMED = (
        '{"v":1,"t":"s","q":22,"d4":0,"d5":1,"d6":1,"d8":1,'
        '"lp":0,"ln":0,"b":0,"r":"none","sps":0,"csps":378,"ds":1,"en":0,'
        '"m":1,"h":1,"a":1,"e":0,"mv":0,"st":5,"p":1000,"g":1000,"c":0}'
    )
    ESTOP_LOCAL_ON = (
        '{"v":1,"t":"s","q":23,"d4":0,"d5":1,"d6":1,"d8":1,'
        '"lp":0,"ln":0,"b":1,"r":"emergency_stop","sps":0,"csps":378,"ds":1,"en":0,'
        '"m":0,"h":0,"a":1,"e":1,"mv":0,"st":9,"p":0,"g":0,"c":0}'
    )
    BRUSHLESS_OFF_READY = POSITION_LOCAL_OFF[:-1] + ',"bo":0}'
    BRUSHLESS_ON_READY = POSITION_LOCAL_OFF[:-1] + ',"bo":1}'
    BRUSHLESS_VARIABLE_OFF_READY = (
        POSITION_LOCAL_OFF[:-1] + ',"bo":0,"bp":1200}'
    )
    BRUSHLESS_VARIABLE_ON_READY = (
        POSITION_LOCAL_OFF[:-1] + ',"bo":1,"bp":1750}'
    )
    FILTERED_RAW_D6_GLITCH = (
        '{"v":1,"t":"s","q":24,"d4":1,"d5":1,"d6":0,"d8":1,'
        '"lx":515,"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,'
        '"csps":378,"ds":1,"en":0,"ut":1}'
    )

    def test_firmware_locks_direction_and_centralizes_physical_interlocks(self) -> None:
        firmware = Path(__file__).with_name("limit_switch_palas.ino").read_text()
        self.assertIn("const int FIXED_DIRECTION_SIGN = 1;", firmware)
        self.assertNotIn("#include <AccelStepper.h>", firmware)
        self.assertNotIn("stepper.run()", firmware)
        self.assertNotIn("V1 D0", firmware)
        self.assertNotIn("V1 D1", firmware)
        self.assertNotIn("directionSign", firmware)
        self.assertGreaterEqual(firmware.count("limitBlocksPhysicalDirection("), 5)
        self.assertIn("const int PIN_DRIVER_ENABLE_NEG = 9;",
                      Path(__file__).with_name("wiring_yun.h").read_text())
        self.assertIn("const unsigned long DRIVER_ENABLE_DELAY_MS = 200UL;", firmware)
        self.assertIn("activePhysicalDirection", firmware)
        self.assertIn("updatePhysicalEndpointLatches", firmware)
        latch_body = firmware.split(
            "void updatePhysicalEndpointLatches", 1
        )[1].split("void disableDriverOutput", 1)[0]
        self.assertIn("negativeLimitLatched = false;", latch_body)
        self.assertIn("positiveLimitLatched = false;", latch_body)
        self.assertIn(
            "positiveLimitActive && negativeLimitActive", latch_body
        )
        self.assertIn("const unsigned long STATUS_MOTION_MS = 100UL;", firmware)
        self.assertIn("networkTxActiveSharedWithUsb", firmware)
        self.assertIn("Serial.availableForWrite();", firmware)
        self.assertIn("Serial.flush();", firmware)
        self.assertIn("void serviceTransports()", firmware)
        self.assertNotIn("Serial.println(line);", firmware)
        self.assertIn("ISR(TIMER1_COMPA_vect)", firmware)
        self.assertIn("PULSE_ENGINE_LOCAL", firmware)
        self.assertIn("PULSE_ENGINE_POSITION", firmware)
        self.assertIn("PULSE_ENGINE_HOME", firmware)
        self.assertGreaterEqual(firmware.count("startPulseEngine("), 3)
        self.assertIn("pulseTimerPosition >= pulseTimerTarget", firmware)
        self.assertIn("pulseTimerPosition <= pulseTimerTarget", firmware)
        self.assertIn("OCR1A = pulseTimerPendingCompare;", firmware)
        self.assertIn("void updatePulseEngineRamp()", firmware)
        self.assertIn("2.0 * FIXED_ACCELERATION_SPS2", firmware)
        self.assertNotIn("startLocalPulseTimer", firmware)
        self.assertIn(
            "const unsigned long LIMIT_ASSERT_QUALIFY_US = 5000UL;",
            firmware,
        )
        qualifier_body = firmware.split("bool updateQualifiedLimit", 1)[1].split(
            "void updatePhysicalEndpointLatches", 1
        )[0]
        self.assertIn(
            "nowUs - input->assertionStartedUs >= LIMIT_ASSERT_QUALIFY_US",
            qualifier_body,
        )
        self.assertIn("input->rejectedGlitches < 255", qualifier_body)
        self.assertNotIn("delay(", qualifier_body)
        self.assertIn(
            "const unsigned long DIRECTION_QUALIFY_US = 10000UL;",
            firmware,
        )
        direction_qualifier_body = firmware.split(
            "bool updateQualifiedDirection", 1
        )[1].split("void updatePhysicalEndpointLatches", 1)[0]
        self.assertIn(
            "nowUs - input->transitionStartedUs >= DIRECTION_QUALIFY_US",
            direction_qualifier_body,
        )
        self.assertIn(
            "input->rejectedTransitions < 255",
            direction_qualifier_body,
        )
        self.assertNotIn("delay(", direction_qualifier_body)
        self.assertIn(
            "bool d5AuthorizesPositive = directionInput.qualifiedHigh;",
            firmware,
        )
        self.assertIn("bool d5Reverse = !directionHigh;", firmware)
        self.assertIn('\\"lx\\":%lu', firmware)
        self.assertIn("const unsigned int STATUS_FRAME_SIZE = 384;", firmware)
        self.assertIn("const int PIN_DRO_CLOCK = 10;",
                      Path(__file__).with_name("wiring_yun.h").read_text())
        self.assertIn("const int PIN_DRO_DATA = 11;",
                      Path(__file__).with_name("wiring_yun.h").read_text())
        self.assertIn("const int PIN_ESC_SIGNAL = 12;",
                      Path(__file__).with_name("wiring_yun.h").read_text())
        self.assertIn("const unsigned int ESC_OFF_PULSE_US = 1000U;", firmware)
        self.assertIn(
            "const unsigned int ESC_DEFAULT_ON_PULSE_US = 1200U;",
            firmware,
        )
        self.assertIn("const unsigned int ESC_MAX_PULSE_US = 2000U;", firmware)
        self.assertIn("ISR(TIMER3_OVF_vect)", firmware)
        self.assertIn("ISR(TIMER3_COMPA_vect)", firmware)
        self.assertIn("TCCR3B = _BV(WGM33) | _BV(WGM32) | _BV(CS31);", firmware)
        self.assertIn('strcmp(commandBuffer, "V1 B1") == 0', firmware)
        self.assertIn('strncmp(commandBuffer, "V1 P", 4) == 0', firmware)
        self.assertIn("strlen(commandBuffer) != 8", firmware)
        self.assertIn("void setBrushlessPulseWidth", firmware)
        self.assertIn("setBrushlessMotor(false);", firmware)
        self.assertIn('\\"bo\\":%d', firmware)
        self.assertIn('\\"bp\\":%u', firmware)
        self.assertIn("ISR(PCINT0_vect)", firmware)
        self.assertIn("PCMSK0 |= _BV(PCINT6);", firmware)
        self.assertIn("if (portB & _BV(PB6)) return;", firmware)
        self.assertIn("droNibble(frame, 11) != 2",
                      Path(__file__).with_name("absolute_dro_protocol.h").read_text())
        self.assertIn("droNibble(frame, 12) != 0",
                      Path(__file__).with_name("absolute_dro_protocol.h").read_text())
        self.assertIn('\\"dc\\":1,\\"df\\":%d', firmware)
        stop_body = firmware.split("void stopStepperImmediately()", 1)[1].split(
            "void abortWebMotion", 1
        )[0]
        self.assertIn("stopPulseEngine();", stop_body)
        self.assertIn("disableDriverOutput();", stop_body)

    def test_firmware_explicitly_owns_step_and_direction_outputs(self) -> None:
        repository = Path(__file__).parent
        firmware = (repository / "limit_switch_palas.ino").read_text()
        setup = firmware.split("void setup()", 1)[1].split("void loop()", 1)[0]

        required_once = (
            "digitalWrite(PIN_DRIVER_ENABLE_NEG, DRIVER_OUTPUT_DISABLED_LEVEL);",
            "pinMode(PIN_DRIVER_ENABLE_NEG, OUTPUT);",
            "digitalWrite(PIN_STEP, LOW);",
            "digitalWrite(PIN_DRIVER_DIR, LOW);",
            "pinMode(PIN_STEP, OUTPUT);",
            "pinMode(PIN_DRIVER_DIR, OUTPUT);",
        )
        for statement in required_once:
            with self.subTest(statement=statement):
                self.assertEqual(setup.count(statement), 1)

        # D9 must inhibit the DM542T before D2/D3 become driven outputs. Both
        # GPIO output latches must be LOW before either data-direction bit is
        # enabled, and Timer1 must remain disabled until all GPIO is configured.
        ordered_statements = (
            "digitalWrite(PIN_DRIVER_ENABLE_NEG, DRIVER_OUTPUT_DISABLED_LEVEL);",
            "pinMode(PIN_DRIVER_ENABLE_NEG, OUTPUT);",
            "digitalWrite(PIN_STEP, LOW);",
            "digitalWrite(PIN_DRIVER_DIR, LOW);",
            "pinMode(PIN_STEP, OUTPUT);",
            "pinMode(PIN_DRIVER_DIR, OUTPUT);",
            "TIMSK1 &= ~_BV(OCIE1A);",
        )
        positions = [setup.index(statement) for statement in ordered_statements]
        self.assertEqual(positions, sorted(positions))

        # Timer1 is the sole STEP-edge owner after setup. A software pulse
        # counter without these physical writes is not evidence of D3 output.
        timer_isr = firmware.split("ISR(TIMER1_COMPA_vect)", 1)[1].split(
            "void pollDroFrames", 1
        )[0]
        self.assertIn("digitalWrite(PIN_STEP, HIGH);", timer_isr)
        self.assertIn("delayMicroseconds(5);", timer_isr)
        self.assertIn("digitalWrite(PIN_STEP, LOW);", timer_isr)

        hardware_contract = (
            repository / "documentation" / "README.md"
        ).read_text()
        self.assertIn("pinMode(PIN_STEP, OUTPUT)", hardware_contract)
        self.assertIn("pinMode(PIN_DRIVER_DIR, OUTPUT)", hardware_contract)
        self.assertIn("must not depend on a library constructor", hardware_contract)

    def test_web_motion_continuously_clears_departed_endpoint_latch(self) -> None:
        firmware = Path(__file__).with_name("limit_switch_palas.ino").read_text()
        loop_body = firmware.split("void loop()", 1)[1]
        active_web_body = loop_body.split("if (activeWebMotion) {", 1)[1].split(
            "if (!activeWebMotion)", 1
        )[0]
        continuous_clear = (
            "if (d4MotionArmed) "
            "clearOppositeLimitLatch(activePhysicalDirection);"
        )
        qualified_destination_check = (
            "limitBlocksPhysicalDirection(\n"
            "                     activePhysicalDirection,\n"
            "                     positiveLimitActive,\n"
            "                     negativeLimitActive)"
        )
        self.assertIn(continuous_clear, active_web_body)
        self.assertIn(qualified_destination_check, active_web_body)
        self.assertLess(
            active_web_body.index(continuous_clear),
            active_web_body.index(qualified_destination_check),
        )
        self.assertIn(
            "if (d4MotionArmed) clearOppositeLimitLatch(physicalDirection);",
            loop_body,
        )

    def test_fixed_dir_output_polarity_matches_physical_limit_contract(self) -> None:
        repository = Path(__file__).parent
        firmware = (repository / "limit_switch_palas.ino").read_text()

        self.assertIn("const int FIXED_DIRECTION_SIGN = 1;", firmware)
        self.assertIn(
            "const int DRIVER_DIR_POSITIVE_LEVEL = LOW;",
            firmware,
        )
        self.assertIn(
            "const int DRIVER_DIR_NEGATIVE_LEVEL = HIGH;",
            firmware,
        )
        self.assertIn("DRIVER_DIR_POSITIVE_LEVEL == LOW", firmware)
        self.assertIn("DRIVER_DIR_NEGATIVE_LEVEL == HIGH", firmware)
        start_engine = firmware.split("void startPulseEngine(", 1)[1].split(
            "void queuePulseEngineSpeed", 1
        )[0]
        self.assertIn(
            "? DRIVER_DIR_POSITIVE_LEVEL\n"
            "          : DRIVER_DIR_NEGATIVE_LEVEL",
            start_engine,
        )
        self.assertNotIn(
            "digitalWrite(PIN_DRIVER_DIR, direction > 0 ? HIGH : LOW);",
            firmware,
        )

        hardware_contract = (
            repository / "documentation" / "README.md"
        ).read_text()
        self.assertIn("D2 LOW = Forward/positive toward D6", hardware_contract)
        self.assertIn("D2 HIGH = Reverse/negative toward D8", hardware_contract)

    def test_unified_timer_compare_quantization_matches_commanded_cruise(self) -> None:
        timer_hz = 16_000_000 // 64
        for requested_sps in (378, 504, 756, 1260, 2520):
            with self.subTest(requested_sps=requested_sps):
                ticks = (timer_hz + requested_sps // 2) // requested_sps
                emitted_sps = timer_hz / ticks
                self.assertLess(
                    abs(emitted_sps - requested_sps) / requested_sps,
                    0.0025,
                )

    def test_unified_timer_profile_keeps_short_and_long_targets_exact(self) -> None:
        acceleration_sps2 = 1260.0
        cruise_sps = 1260.0
        for target_pulses in (1, 25, 252, 10_000):
            with self.subTest(target_pulses=target_pulses):
                position = 0
                speed_sps = min(cruise_sps, 50.0)
                peak_sps = speed_sps
                while position < target_pulses:
                    remaining = target_pulses - position
                    braking_sps = math.sqrt(2.0 * acceleration_sps2 * remaining)
                    desired_sps = min(cruise_sps, braking_sps)
                    max_change_sps = acceleration_sps2 / max(speed_sps, 1.0)
                    if speed_sps < desired_sps:
                        speed_sps = min(desired_sps, speed_sps + max_change_sps)
                    else:
                        speed_sps = max(desired_sps, speed_sps - max_change_sps)
                    peak_sps = max(peak_sps, speed_sps)
                    position += 1
                self.assertEqual(position, target_pulses)
                self.assertLessEqual(peak_sps, cruise_sps)
                if target_pulses <= 252:
                    self.assertLess(peak_sps, cruise_sps)
                else:
                    self.assertAlmostEqual(peak_sps, cruise_sps)

    def test_qualified_limit_state_is_distinct_from_raw_and_counts_glitches(self) -> None:
        status = UsbStepperSource.decode_status_line(self.FILTERED_RAW_D6_GLITCH)
        self.assertEqual(status["stepper_d6_raw"], "LOW")
        self.assertFalse(status["stepper_positive_limit_active"])
        self.assertFalse(status["stepper_positive_limit_latched"])
        self.assertTrue(status["stepper_limit_filter_capable"])
        self.assertTrue(status["stepper_unified_timer_capable"])
        self.assertEqual(status["stepper_limit_qualification_ms"], 5.0)
        self.assertEqual(status["stepper_positive_limit_glitch_count"], 2)
        self.assertEqual(status["stepper_negative_limit_glitch_count"], 3)

        qualified = UsbStepperSource.decode_status_line(
            self.FILTERED_RAW_D6_GLITCH.replace('"lx":515', '"lx":131587')
        )
        self.assertTrue(qualified["stepper_positive_limit_active"])

    def test_qualified_d5_state_is_distinct_from_raw_and_counts_glitches(self) -> None:
        # Extended lx: capability bit 27, qualified-HIGH bit 26, four rejected
        # D5 transitions in bits 25..18, and the existing D6/D8 diagnostics.
        line = self.FILTERED_RAW_D6_GLITCH.replace(
            '"d5":1',
            '"d5":0',
        ).replace(
            '"lx":515',
            '"lx":202375683',
        )
        status = UsbStepperSource.decode_status_line(line)
        self.assertTrue(status["stepper_direction_filter_capable"])
        self.assertEqual(status["stepper_direction_qualification_ms"], 10.0)
        self.assertEqual(status["stepper_direction_glitch_count"], 4)
        self.assertEqual(status["stepper_d5_raw"], "LOW")
        self.assertEqual(status["stepper_d5_qualified"], "HIGH")
        self.assertEqual(status["stepper_manual_direction"], "forward")

    def test_compact_status_numeric_worst_case_fits_transport_frame(self) -> None:
        # Mirror the compact protocol's longest numeric representations. The
        # shared AVR buffer must still have room for newline and NUL.
        frame = (
            '{"v":1,"t":"s","q":4294967295,"d4":1,"d5":1,'
            '"d6":1,"d8":1,"lx":268435455,"lp":1,"ln":1,"b":1,'
            '"r":"negative_limit","sps":-2147483648,"csps":2520,'
            '"aps":2147483647,"ds":1,"en":1,"ut":1,"dc":1,"df":1,'
            '"dr":-2147483648,"dd":-2147483648,"da":2147483647,'
            '"dq":4294967295,"dx":65535,"m":1,"h":1,"a":1,'
            '"e":1,"bo":0,"bp":2000,"mv":1,"st":9,"p":-2147483648,"g":-2147483648,'
            '"c":65535,"o":2}'
        )
        self.assertLessEqual(len(frame) + 2, 384)

    def test_decodes_fresh_read_only_dro_telemetry(self) -> None:
        line = self.FILTERED_RAW_D6_GLITCH[:-1] + (
            ',"dc":1,"df":1,"dr":10660,"dd":-125,"da":17,'
            '"dq":1234,"dx":513}'
        )
        status = UsbStepperSource.decode_status_line(line)
        self.assertTrue(status["stepper_dro_capable"])
        self.assertTrue(status["stepper_dro_fresh"])
        self.assertEqual(status["stepper_dro_position_mm"], 106.60)
        self.assertEqual(status["stepper_dro_displacement_mm"], -1.25)
        self.assertEqual(status["stepper_dro_sample_age_ms"], 17)
        self.assertEqual(status["stepper_dro_valid_frame_count"], 1234)
        self.assertEqual(status["stepper_dro_rejected_frame_count"], 2)
        self.assertEqual(status["stepper_dro_dropped_frame_count"], 1)
        # DRO telemetry is deliberately not promoted into the existing
        # open-loop motion counter or any motion-authorization field.
        self.assertIsNone(status["stepper_position_mm"])
        self.assertFalse(status["stepper_blocked"])

    def test_rejects_partial_or_impossible_dro_status(self) -> None:
        partial = self.FILTERED_RAW_D6_GLITCH[:-1] + ',"dc":1}'
        impossible = self.FILTERED_RAW_D6_GLITCH[:-1] + (
            ',"dc":1,"df":1,"dr":0,"dd":0,"da":-1,"dq":0,"dx":0}'
        )
        with self.assertRaisesRegex(ValueError, "DRO status fields"):
            UsbStepperSource.decode_status_line(partial)
        with self.assertRaisesRegex(ValueError, "before its first valid frame"):
            UsbStepperSource.decode_status_line(impossible)

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
        self.assertFalse(status["stepper_direction_command_capable"])
        self.assertTrue(status["stepper_direction_calibration_safe"])
        self.assertEqual(status["stepper_direction_mapping"], "normal")
        self.assertTrue(status["stepper_driver_enable_capable"])
        self.assertFalse(status["stepper_driver_enabled"])
        self.assertEqual(status["stepper_command_speed_mm_s"], 1.5002)

    def test_decodes_reverse_motion_away_from_positive_limit(self) -> None:
        status = UsbStepperSource.decode_status_line(self.REVERSE_MOVING)
        self.assertEqual(status["stepper_manual_direction"], "reverse")
        self.assertEqual(status["stepper_direction"], "negative")
        self.assertTrue(status["stepper_positive_limit_active"])
        self.assertFalse(status["stepper_positive_limit_latched"])
        self.assertFalse(status["stepper_blocked"])
        self.assertTrue(status["stepper_moving"])
        self.assertTrue(status["stepper_driver_enabled"])
        self.assertEqual(status["stepper_speed_mm_s"], -1.5002)
        self.assertTrue(status["stepper_pulse_measurement_capable"])
        self.assertEqual(status["stepper_measured_pulse_rate_sps"], 350)
        self.assertEqual(status["stepper_measured_speed_mm_s"], 1.3891)

    def test_legacy_inverted_mapping_is_flagged_unsafe(self) -> None:
        status = UsbStepperSource.decode_status_line(
            self.INVERTED_REVERSE_MOVING
        )
        self.assertEqual(status["stepper_direction_mapping"], "inverted")
        self.assertEqual(status["stepper_manual_direction"], "reverse")
        self.assertEqual(status["stepper_direction"], "negative")
        self.assertEqual(status["stepper_speed_mm_s"], -1.5002)
        self.assertFalse(status["stepper_direction_calibration_safe"])
        self.assertEqual(status["stepper_fault"], "unsafe_direction_calibration")
        self.assertFalse(status["stepper_direction_command_capable"])
        self.assertFalse(status["stepper_driver_enable_capable"])
        self.assertIsNone(status["stepper_driver_enabled"])

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
            self.FORWARD_BLOCKED.replace('"csps":378', '"csps":2521'),
            self.FORWARD_BLOCKED.replace('"ds":1', '"ds":0'),
            self.FORWARD_BLOCKED.replace('"en":0', '"en":2'),
            self.FORWARD_BLOCKED.replace('"en":0', '"en":1'),
            self.REVERSE_MOVING.replace('"aps":350', '"aps":true'),
            self.REVERSE_MOVING.replace('"aps":350', '"aps":-1'),
            self.FILTERED_RAW_D6_GLITCH.replace('"lx":515', '"lx":true'),
            self.FILTERED_RAW_D6_GLITCH.replace('"lx":515', '"lx":262144'),
            self.FILTERED_RAW_D6_GLITCH.replace(
                '"lx":515',
                '"lx":268435456',
            ),
            self.FILTERED_RAW_D6_GLITCH.replace('"ut":1', '"ut":0'),
            self.WEB_READY_FORWARD_ARMED.replace('"st":5', '"st":10'),
            self.WEB_READY_FORWARD_ARMED.replace('"p":1000', '"p":true'),
        )
        for line in invalid_lines:
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    UsbStepperSource.decode_status_line(line)

        legacy = UsbStepperSource.decode_status_line(self.FORWARD_BLOCKED)
        self.assertFalse(legacy["stepper_pulse_measurement_capable"])
        self.assertIsNone(legacy["stepper_measured_pulse_rate_sps"])
        self.assertIsNone(legacy["stepper_measured_speed_mm_s"])
        self.assertFalse(legacy["stepper_limit_filter_capable"])
        self.assertIsNone(legacy["stepper_positive_limit_glitch_count"])
        self.assertFalse(legacy["stepper_direction_filter_capable"])
        self.assertIsNone(legacy["stepper_direction_glitch_count"])
        self.assertEqual(legacy["stepper_d5_qualified"], "HIGH")
        self.assertFalse(legacy["stepper_unified_timer_capable"])

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

    def test_captures_usb_firmware_command_rejection_text(self) -> None:
        master_fd, slave_fd = pty.openpty()
        source = UsbStepperSource(port=os.ttyname(slave_fd))
        try:
            self.assertIsNone(source.poll(0.0))
            os.write(
                master_fd,
                (self.WEB_UNHOMED_REVERSE_ARMED + "\r\n").encode(),
            )
            self.assertIsNotNone(source.poll(0.1))

            source.move(-1.0, 1.0)
            self.assertEqual(os.read(master_fd, 64), b"V1 G-252,252,1\n")
            os.write(
                master_fd,
                b"Command rejected: test interlock.\r\n"
                + (
                    self.WEB_UNHOMED_REVERSE_ARMED.replace(
                        '"q":21',
                        '"q":22',
                    )
                    + "\r\n"
                ).encode(),
            )
            self.assertIsNotNone(source.poll(0.2))
            self.assertEqual(
                source.pending_command_error,
                "test interlock.",
            )
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
            self.assertEqual(os.read(master_fd, 32), b"V1 S819\n")

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

    def test_runtime_direction_mapping_surface_is_removed(self) -> None:
        source = UsbStepperSource()
        self.assertFalse(hasattr(source, "set_direction_mapping"))
        self.assertNotIn("stepperDirectionMapping", INDEX_HTML)
        self.assertNotIn("/api/stepper/direction-mapping", API_JS)

    def test_unsafe_legacy_mapping_rejects_motion_commands(self) -> None:
        master_fd, slave_fd = pty.openpty()
        source = UsbStepperSource(port=os.ttyname(slave_fd))
        try:
            self.assertIsNone(source.poll(0.0))
            unsafe = self.WEB_UNHOMED_REVERSE_ARMED.replace('"ds":1', '"ds":-1')
            os.write(master_fd, (unsafe + "\r\n").encode())
            self.assertIsNotNone(source.poll(0.1))
            with self.assertRaisesRegex(RuntimeError, "unsafe legacy"):
                source.move(-1.0, 1.0)
            with self.assertRaisesRegex(RuntimeError, "unsafe legacy"):
                source.home()
            readable, _, _ = select.select([master_fd], [], [], 0.05)
            self.assertFalse(readable)
        finally:
            source.close()
            os.close(master_fd)
            os.close(slave_fd)

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
            self.assertEqual(os.read(master_fd, 64), b"V1 G-630,819,1\n")
            source.home()
            self.assertEqual(os.read(master_fd, 32), b"V1 H\n")

            os.write(
                master_fd,
                (self.WEB_READY_FORWARD_ARMED + "\r\n").encode(),
            )
            self.assertIsNotNone(source.poll(0.3))
            source.move(2.5, 3.25, "operator-command")
            self.assertEqual(os.read(master_fd, 64), b"V1 G630,819,2\n")
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

    def test_decodes_and_writes_fixed_brushless_motor_contract(self) -> None:
        off = UsbStepperSource.decode_status_line(self.BRUSHLESS_OFF_READY)
        self.assertTrue(off["stepper_brushless_motor_capable"])
        self.assertFalse(off["stepper_brushless_motor_on"])
        self.assertEqual(off["stepper_brushless_motor_pulse_us"], 1000)
        on = UsbStepperSource.decode_status_line(self.BRUSHLESS_ON_READY)
        self.assertTrue(on["stepper_brushless_motor_on"])
        self.assertEqual(on["stepper_brushless_motor_pulse_us"], 1200)
        self.assertFalse(on["stepper_brushless_motor_variable_capable"])
        self.assertEqual(
            on["stepper_brushless_motor_setpoint_us"],
            1200,
        )

        variable_off = UsbStepperSource.decode_status_line(
            self.BRUSHLESS_VARIABLE_OFF_READY
        )
        self.assertTrue(
            variable_off["stepper_brushless_motor_variable_capable"]
        )
        self.assertEqual(
            variable_off["stepper_brushless_motor_setpoint_us"],
            1200,
        )
        self.assertEqual(
            variable_off["stepper_brushless_motor_pulse_us"],
            1000,
        )
        variable_on = UsbStepperSource.decode_status_line(
            self.BRUSHLESS_VARIABLE_ON_READY
        )
        self.assertEqual(
            variable_on["stepper_brushless_motor_setpoint_us"],
            1750,
        )
        self.assertEqual(
            variable_on["stepper_brushless_motor_pulse_us"],
            1750,
        )
        for invalid in (
            self.POSITION_LOCAL_OFF[:-1] + ',"bp":1200}',
            self.BRUSHLESS_OFF_READY[:-1] + ',"bp":999}',
            self.BRUSHLESS_OFF_READY[:-1] + ',"bp":2001}',
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    UsbStepperSource.decode_status_line(invalid)

        impossible = self.ESTOP_LOCAL_ON[:-1] + ',"bo":1}'
        with self.assertRaisesRegex(ValueError, "while E-STOP is latched"):
            UsbStepperSource.decode_status_line(impossible)

        master_fd, slave_fd = pty.openpty()
        source = UsbStepperSource(port=os.ttyname(slave_fd))
        try:
            self.assertIsNone(source.poll(0.0))
            os.write(master_fd, (self.BRUSHLESS_OFF_READY + "\r\n").encode())
            self.assertIsNotNone(source.poll(0.1))
            source.set_brushless_motor(True)
            self.assertEqual(os.read(master_fd, 32), b"V1 B1\n")

            os.write(master_fd, (self.BRUSHLESS_ON_READY + "\r\n").encode())
            self.assertIsNotNone(source.poll(0.2))
            source.set_brushless_motor(False)
            self.assertEqual(os.read(master_fd, 32), b"V1 B0\n")

            os.write(
                master_fd,
                (self.BRUSHLESS_VARIABLE_OFF_READY + "\r\n").encode(),
            )
            self.assertIsNotNone(source.poll(0.3))
            source.set_brushless_pulse_us(1750)
            self.assertEqual(os.read(master_fd, 32), b"V1 P1750\n")
            for invalid in (999, 2001, 1200.5, True, "1200"):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):
                        source.set_brushless_pulse_us(invalid)
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
                "--system-config",
                "/tmp/test-system-config.json",
            ]
        )
        self.assertEqual(args.stepper_source, "network")
        self.assertEqual(args.stepper_url, "http://192.168.8.137:8080")
        self.assertEqual(args.stepper_timeout, 0.4)
        self.assertEqual(args.system_config, Path("/tmp/test-system-config.json"))
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
            '"csps":378', '"csps":819'
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
            self.assertEqual(received, [b"V1 S819\n"])

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                source.poll(elapsed)
                elapsed += 0.1
                status = source.status()
                if status.get("stepper_command_speed_mm_s") == 3.2504:
                    break
                time.sleep(0.02)
            status = source.status()
            self.assertEqual(status["stepper_command_speed_mm_s"], 3.2504)
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


class ControllinoStepperSourceTests(unittest.TestCase):
    STATUS = (
        '{"v":1,"t":"s","d4":1,"d5":1,"d6":-1,"d8":-1,'
        '"lx":0,"lp":0,"ln":0,"b":0,"r":"none","sps":0,'
        '"csps":1000,"aps":0,"ds":1,"en":0,"ut":1,"dc":0,'
        '"df":0,"dr":0,"dd":0,"da":-1,"dq":0,"dx":0,'
        '"m":1,"h":0,"a":1,"e":0,"bo":0,"bp":1200,"mv":0,'
        '"st":5,"p":0,"g":0,"c":0,"o":2}'
    )

    def test_controllino_usb_status_and_acknowledgements(self) -> None:
        master_fd, slave_fd = pty.openpty()
        sources = make_sources(esp32_source="off", dxmr90_source="off",
                               stepper_source="controllino-usb",
                               stepper_port=os.ttyname(slave_fd), stepper_baud=9600)
        source = next(source for source in sources if source.name == "stepper")
        self.assertIsInstance(source, ControllinoUsbStepperSource)
        try:
            source.poll(0.0)
            os.write(master_fd, (self.STATUS + "\n").encode())
            reading = source.poll(0.1)
            self.assertIsNotNone(reading)
            self.assertEqual(reading.mode, "controllino")
            self.assertIsNone(reading.values["stepper_positive_limit_active"])
            self.assertFalse(reading.values["stepper_dro_capable"])
            os.write(master_fd, b'{"v":1,"t":"a","ok":0,"e":"blocked"}\n')
            source.poll(0.2)
            self.assertEqual(source.pending_command_error, "blocked")
            os.write(master_fd, b'{"v":1,"t":"a","ok":1}\n')
            source.poll(0.3)
            self.assertIsNone(source.pending_command_error)
        finally:
            source.close()
            os.close(master_fd)
            os.close(slave_fd)

    def test_cli_and_factory_enable_direct_controllino_mode(self) -> None:
        args = parse_args(
            [
                "--stepper-source",
                "controllino",
                "--stepper-url",
                "http://10.77.0.10",
            ]
        )
        sources = make_sources(
            esp32_source="off",
            dxmr90_source="off",
            stepper_source=args.stepper_source,
            stepper_network_url=args.stepper_url,
        )
        stepper = next(source for source in sources if source.name == "stepper")
        self.assertIsInstance(stepper, ControllinoStepperSource)
        stepper.close()

    def test_direct_status_and_signed_move_use_controllino_wire_contract(self) -> None:
        commands: list[str] = []
        status = self.STATUS

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                return

            def _reply(self, body: str) -> None:
                payload = body.encode("ascii")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802
                self._reply(status)

            def do_POST(self) -> None:  # noqa: N802
                query = parse_qs(urlparse(self.path).query)
                commands.append(query["value"][0])
                self._reply('{"v":1,"t":"a","ok":1,"e":"none"}')

        server = ThreadedHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        source = ControllinoStepperSource(f"http://{host}:{port}", timeout=0.5)
        try:
            source._fetch_status()
            values = source.status()
            self.assertFalse(values["stepper_home_capable"])
            self.assertFalse(values["stepper_dro_capable"])
            self.assertEqual(values["stepper_authorized_direction"], "both")
            source.move(-1.0, 1.0, "return")
            self.assertEqual(commands, ["V1 G-252,252,1"])
        finally:
            source.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=1.0)

    def test_compact_rejection_surfaces_firmware_reason(self) -> None:
        source = ControllinoStepperSource("http://10.77.0.10")
        self.assertEqual(
            source._acknowledgement_error(
                {"v": 1, "t": "a", "ok": 0, "e": "wrong_mode"}
            ),
            "wrong_mode",
        )

    def test_software_switch_commands_replace_absent_d4_d5_inputs(self) -> None:
        source = ControllinoStepperSource("http://10.77.0.10")
        local_status = self.STATUS.replace('"m":1', '"m":0').replace(
            '"st":5', '"st":0'
        )
        source._last_values = source._decode_network_status(local_status)
        with mock.patch.object(source, "_write_command") as write:
            source.set_local_run(-1)
            write.assert_called_once_with(b"V1 R-1\n", "software run")
        self.assertIn('stepperLocalRun: "/api/stepper/local-run"', API_JS)
        self.assertIn('id="stepperRunReverse"', INDEX_HTML)
        self.assertIn('id="stepperRunStop"', INDEX_HTML)
        self.assertIn('id="stepperRunForward"', INDEX_HTML)

class NetworkStepperSourceRuntimeTests(unittest.TestCase):
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
        self.assertEqual(validate_command(" V1 B0\n"), "V1 B0")
        self.assertEqual(validate_command("V1 B1"), "V1 B1")
        self.assertEqual(validate_command("V1 P1000"), "V1 P1000")
        self.assertEqual(validate_command("V1 P2000"), "V1 P2000")
        for command in (
            "V1 Q",
            "V1 D0",
            "V1 D1",
            "V1 G1,2",
            "V2 E1",
            "V1 P999",
            "V1 P2001",
            "V1 P+1000",
            "V1 S" + "1" * 60,
        ):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    validate_command(command)

    def test_bridge_ignores_nonblocking_uart_eagain(self) -> None:
        bridge = SerialBridgeState("/dev/fake")
        bridge.fd = 123
        read_attempts = 0

        def fake_select(*_args):
            return ([123], [], [])

        def fake_read(*_args):
            nonlocal read_attempts
            read_attempts += 1
            if read_attempts == 1:
                raise OSError(errno.EAGAIN, "temporarily unavailable")
            bridge._stop.set()
            return b""

        with mock.patch(
            "networked_sensors.yun_stepper_bridge.select.select", fake_select
        ):
            with mock.patch("networked_sensors.yun_stepper_bridge.os.read", fake_read):
                bridge._read_loop()

        self.assertEqual(read_attempts, 2)
        self.assertIsNone(bridge.last_error)

    def test_bridge_propagates_firmware_rejection_and_ack_timeout(self) -> None:
        master_fd, slave_fd = pty.openpty()
        bridge = SerialBridgeState(os.ttyname(slave_fd), ack_timeout=0.2)
        bridge.open()
        try:
            def reject() -> None:
                self.assertEqual(os.read(master_fd, 64), b"V1 M1\n")
                os.write(
                    master_fd,
                    b'{"v":1,"t":"a","ok":0,"e":"owned_by_usb"}\n',
                )

            responder = threading.Thread(target=reject)
            responder.start()
            with self.assertRaisesRegex(CommandRejected, "owned_by_usb"):
                bridge.command("V1 M1")
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
    def test_control_mode_uses_explicit_radio_choices(self) -> None:
        self.assertIn('id="stepperModeLocal"', INDEX_HTML)
        self.assertIn('value="local_velocity" checked', INDEX_HTML)
        self.assertIn(">Local Speed</label>", INDEX_HTML)
        self.assertIn('id="stepperModeWeb"', INDEX_HTML)
        self.assertIn('value="web_position"', INDEX_HTML)
        self.assertIn(
            'id="stepperApplySpeed" type="button">Apply Motor Speed</button>',
            INDEX_HTML,
        )
        self.assertIn(
            'class="visually-hidden" id="stepperMessage"',
            INDEX_HTML,
        )
        self.assertNotIn('class="pill" id="stepperMessage"', INDEX_HTML)
        self.assertNotIn("Local Velocity", INDEX_HTML)
        self.assertNotIn("Local Velocity", STEPPER_JS)
        self.assertEqual(INDEX_HTML.count('name="stepper_control_mode"'), 2)
        self.assertNotIn('role="switch"', INDEX_HTML)
        self.assertIn(
            "const modeInputs = [els.stepperModeLocal, els.stepperModeWeb]",
            STEPPER_JS,
        )
        self.assertIn("async function requestControlMode()", STEPPER_JS)

    def test_web_position_spacebar_uses_guarded_move_stop_actions(self) -> None:
        self.assertIn('id="stepperMove"', INDEX_HTML)
        self.assertIn('id="stepperStop"', INDEX_HTML)
        self.assertEqual(INDEX_HTML.count('aria-keyshortcuts="Space"'), 2)
        self.assertIn('const spacePressed = event.code === "Space" || event.key === " ";', APP_JS)
        self.assertIn('latest?.stepper_control_mode !== "web_position"', STEPPER_JS)
        self.assertIn('const actionButton = moving ? els.stepperStop : els.stepperMove;', STEPPER_JS)
        self.assertIn('if (!actionButton || actionButton.hidden || actionButton.disabled) return false;', STEPPER_JS)
        self.assertIn('void requestMove()', STEPPER_JS)
        self.assertIn('void requestStop()', STEPPER_JS)
        self.assertIn('["INPUT", "TEXTAREA", "SELECT"].includes(tagName)', DOM_JS)
        self.assertIn('const activatingControl = ["BUTTON", "A"].includes(tagName);', DOM_JS)
        self.assertIn('event.defaultPrevented || event.repeat', DOM_JS)
        self.assertIn('event.ctrlKey || event.altKey || event.metaKey || event.shiftKey', DOM_JS)
        self.assertIn('let motionRequestPending = false;', STEPPER_JS)
        self.assertIn('motionRequestPending = "move";', STEPPER_JS)
        self.assertIn('motionRequestPending = "stop";', STEPPER_JS)
        self.assertIn('id="stepperCommandFeedback"', INDEX_HTML)
        self.assertIn("function setCommandFeedback", STEPPER_JS)
        self.assertIn(
            "setCommandFeedback(`Move failed: ${error.message}`)",
            STEPPER_JS,
        )
        self.assertIn(
            "if (motionRequestPending === \"move\") "
            "motionRequestPending = false;",
            STEPPER_JS,
        )
        self.assertIn(".stepper-command-feedback", DASHBOARD_CSS)
        self.assertIn("async function responseError(response)", API_JS)
        self.assertIn(
            'typeof payload.error === "string"',
            API_JS,
        )

    def test_brushless_motor_has_compact_guarded_m_toggle(self) -> None:
        self.assertIn('id="brushlessMotorToggle"', INDEX_HTML)
        self.assertIn('aria-keyshortcuts="M"', INDEX_HTML)
        self.assertIn('id="brushlessMotorState"', INDEX_HTML)
        self.assertIn("D12 Pulse Timer: --", INDEX_HTML)
        self.assertIn(
            "`D12 Pulse Timer: ${brushlessPulseUs.toFixed(0)} µs`",
            STEPPER_JS,
        )
        control_mode_start = INDEX_HTML.index('<fieldset class="mode-options">')
        control_mode_end = INDEX_HTML.index("</fieldset>", control_mode_start)
        brushless_control = INDEX_HTML.index('class="brushless-control"')
        self.assertLess(control_mode_start, brushless_control)
        self.assertLess(control_mode_end, brushless_control)
        self.assertLess(
            INDEX_HTML.index('id="stepperApplySpeed"'),
            brushless_control,
        )
        self.assertLess(brushless_control, INDEX_HTML.index("<h3>Interlocks</h3>"))
        self.assertIn('id="brushlessPulseWidth"', INDEX_HTML)
        self.assertIn('min="1000" max="2000" step="1" value="1200"', INDEX_HTML)
        self.assertIn('id="brushlessApplyPulse"', INDEX_HTML)
        self.assertIn('stepperMotorToggle: "/api/stepper/motor/toggle"', API_JS)
        self.assertIn('stepperMotorPulse: "/api/stepper/motor/pulse"', API_JS)
        self.assertIn("function requestBrushlessPulse()", STEPPER_JS)
        self.assertIn("{pulse_us: pulseUs}", STEPPER_JS)
        self.assertIn(
            'const motorPressed = event.code === "KeyM" || '
            'event.key.toLowerCase() === "m";',
            APP_JS,
        )
        self.assertLess(
            APP_JS.index("if (shortcutTargetIsGuarded(event)) return;"),
            APP_JS.index("const motorPressed"),
        )
        self.assertIn("function handleMotorShortcut()", STEPPER_JS)
        self.assertIn("void requestBrushlessToggle();", STEPPER_JS)
        self.assertIn(
            "applyMotionPlan",
            STEPPER_JS,
        )
        self.assertIn(".brushless-control", DASHBOARD_CSS)
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) auto auto",
            DASHBOARD_CSS,
        )

    def test_dashboard_separates_scheduled_and_measured_step_output(self) -> None:
        self.assertIn("Scheduled speed", INDEX_HTML)
        self.assertIn('id="stepperMeasuredSpeed"', INDEX_HTML)
        self.assertIn('id="stepperPrimaryPulseOutput"', INDEX_HTML)
        self.assertIn('id="stepperDroVelocity"', INDEX_HTML)
        self.assertIn("Pulse timer", INDEX_HTML)
        self.assertIn("DRO velocity", INDEX_HTML)
        self.assertIn(
            'id="stepperPrimaryPulseOutput" class="piston-secondary-value"',
            INDEX_HTML,
        )
        self.assertIn(
            'id="stepperDroVelocity" class="piston-secondary-value"',
            INDEX_HTML,
        )
        self.assertNotIn('id="stepperPulseMonitor"', INDEX_HTML)
        self.assertLess(
            INDEX_HTML.index('id="stepperDroPosition"'),
            INDEX_HTML.index('id="stepperPrimaryPulseOutput"'),
        )
        self.assertLess(
            INDEX_HTML.index('id="stepperDroVelocity"'),
            INDEX_HTML.index('id="stepperPrimaryPulseOutput"'),
        )
        self.assertIn(
            "stepperPrimaryPulseOutput,\n"
            "        hasMeasuredPulseOutput ?",
            STEPPER_JS,
        )
        self.assertIn("stepper_dro_velocity_mm_s", STEPPER_JS)
        self.assertIn("stepper_dro_velocity_window_ms", STEPPER_JS)
        self.assertIn(".piston-secondary-value", DASHBOARD_CSS)
        self.assertIn("stepper_measured_pulse_rate_sps", STEPPER_JS)
        self.assertIn("stepper_measured_speed_mm_s", STEPPER_JS)
        self.assertIn('id="stepperPulseEngine"', INDEX_HTML)
        self.assertIn("stepper_unified_timer_capable", STEPPER_JS)
        self.assertIn('id="stepperLimitFilter"', INDEX_HTML)
        self.assertIn("stepper_limit_qualification_ms", STEPPER_JS)
        self.assertIn("stepper_positive_limit_glitch_count", STEPPER_JS)
        self.assertIn("stepper_negative_limit_glitch_count", STEPPER_JS)
        self.assertIn("stepper_direction_qualification_ms", STEPPER_JS)
        self.assertIn("stepper_direction_glitch_count", STEPPER_JS)
        self.assertIn("/ raw ${latest.stepper_d6_raw", STEPPER_JS)
        self.assertIn(
            'aria-label="Read-only piston head position measured by the DRO"',
            INDEX_HTML,
        )
        self.assertIn('<span class="piston-eyebrow">DRO position</span>', INDEX_HTML)
        self.assertIn('id="stepperDroPosition"', INDEX_HTML)
        self.assertNotIn('id="stepperDroDisplacement"', INDEX_HTML)
        self.assertIn("stepper_dro_fresh", STEPPER_JS)
        self.assertIn("stepper_dro_valid_frame_count", STEPPER_JS)

    def test_dashboard_does_not_call_a_disconnected_yun_unsafe_legacy(self) -> None:
        message_block = STEPPER_JS.split(
            'if (!messageSticky && ["usb", "network", "controllino"].includes',
            1,
        )[1].split("updateControls();", 1)[0]
        self.assertIn(
            '!connected\n        ? "Motion controller disconnected"',
            message_block,
        )
        self.assertIn("!directionCalibrationSafe", message_block)
        self.assertLess(
            message_block.index(
                '!connected\n        ? "Motion controller disconnected"'
            ),
            message_block.index("!directionCalibrationSafe"),
        )

    def test_dashboard_promotes_live_dro_piston_visual_and_compacts_readouts(self) -> None:
        self.assertIn('class="control-panel stepper-panel"', INDEX_HTML)
        self.assertIn('class="stepper-layout"', INDEX_HTML)
        self.assertIn('id="stepperPistonVisual"', INDEX_HTML)
        self.assertIn('class="piston-readout"', INDEX_HTML)
        self.assertNotIn('id="stepperDroState"', INDEX_HTML)
        self.assertNotIn('id="stepperDroDirection"', INDEX_HTML)
        self.assertNotIn("Boot travel", INDEX_HTML)
        self.assertNotIn("System zero not set", INDEX_HTML)
        self.assertNotIn('class="piston-live-strip"', INDEX_HTML)
        self.assertNotIn('class="piston-range-note"', INDEX_HTML)
        self.assertIn('class="stepper-console"', INDEX_HTML)
        self.assertIn('class="source-row stepper-interlocks"', INDEX_HTML)
        self.assertNotIn("<header><h3>Motion</h3></header>", INDEX_HTML)
        self.assertIn(
            "<summary>Stepper diagnostics and motion telemetry</summary>",
            INDEX_HTML,
        )
        self.assertLess(
            INDEX_HTML.index('<details class="source-panel source-drawer">'),
            INDEX_HTML.index("<summary>Stepper diagnostics and motion telemetry</summary>"),
        )
        self.assertIn('<div class="interlock-item"><dt title="Local enable input">Enable (D4)</dt>', INDEX_HTML)
        self.assertIn(
            '<dt title="Positive/bottom travel limit">+ Limit (D6 · bottom)</dt>',
            INDEX_HTML,
        )
        self.assertIn(
            '<dt title="Negative/top travel limit">− Limit (D8 · top)</dt>',
            INDEX_HTML,
        )
        self.assertLess(
            INDEX_HTML.index('id="stepperPistonVisual"'),
            INDEX_HTML.index('class="stepper-console"'),
        )
        self.assertNotIn("droVisualRange", CONFIG_JS)
        self.assertIn(
            "const droVisualMinMm = -limits.max_distance_mm",
            STEPPER_JS,
        )
        self.assertIn("const droVisualMaxMm = 0", STEPPER_JS)
        self.assertIn(
            "const droVisualEndpointToleranceMm = 0.1",
            STEPPER_JS,
        )
        self.assertIn(
            "positionMm < droVisualMinMm - droVisualEndpointToleranceMm",
            STEPPER_JS,
        )
        self.assertIn(
            'setText(els.stepperDroMaxLabel, "D8 top 0 mm")',
            STEPPER_JS,
        )
        self.assertIn("D6 bottom ${droVisualMinMm.toFixed(2)} mm", STEPPER_JS)
        self.assertIn("D8 top 0 mm", INDEX_HTML)
        self.assertIn("D6 bottom −137.18 mm", INDEX_HTML)
        self.assertIn(
            "positive is upward and negative is downward",
            STEPPER_JS,
        )
        self.assertIn('style.setProperty(\n        "--piston-position"', STEPPER_JS)
        self.assertIn('classList.toggle("is-stale", hasPosition && !fresh)', STEPPER_JS)
        self.assertIn("!hasPosition || !hasTrustworthyVisualPosition", STEPPER_JS)
        self.assertIn('classList.toggle("is-out-of-range", outOfRange)', STEPPER_JS)
        self.assertIn('if (fresh) {', STEPPER_JS)
        self.assertIn(".stepper-layout", DASHBOARD_CSS)
        self.assertIn(
            ".stepper-layout {\n  display: grid;\n  grid-template-columns: repeat(2, minmax(0, 1fr));",
            DASHBOARD_CSS,
        )
        self.assertIn(".piston-head", DASHBOARD_CSS)
        self.assertIn('id="stepperDirectionIndicator"', INDEX_HTML)
        self.assertIn('id="stepperDirectionArrow"', INDEX_HTML)
        self.assertIn('id="stepperDirectionLabel"', INDEX_HTML)
        self.assertIn('id="stepperDirectionBlocked"', INDEX_HTML)
        self.assertIn('id="stepperDirectionBlockLabel"', INDEX_HTML)
        self.assertIn(".piston-direction-indicator", DASHBOARD_CSS)
        self.assertIn(
            'arrowDirection === "down" ? "↓" : arrowDirection === "up" ? "↑" : ""',
            STEPPER_JS,
        )
        self.assertIn('"D5 FWD · D6 BOTTOM"', STEPPER_JS)
        self.assertIn('"D5 REV · D8 TOP"', STEPPER_JS)
        self.assertIn(
            "const hasD5Direction = connected &&",
            STEPPER_JS,
        )
        self.assertIn(
            'latest.stepper_positive_limit_active === true',
            STEPPER_JS,
        )
        self.assertIn(
            'latest.stepper_negative_limit_active === true',
            STEPPER_JS,
        )
        self.assertIn(
            '"is-blocked",\n      blockedLimit !== null',
            STEPPER_JS,
        )
        self.assertIn(
            ".piston-direction-indicator.is-blocked "
            ".piston-direction-blocked",
            DASHBOARD_CSS,
        )
        self.assertIn(
            ".piston-direction-indicator.is-unavailable "
            ".piston-direction-block-label",
            DASHBOARD_CSS,
        )
        self.assertIn("min-height: 500px", DASHBOARD_CSS)
        self.assertIn("grid-template-rows: auto minmax(320px, 1fr)", DASHBOARD_CSS)
        self.assertNotIn(".piston-live-strip", DASHBOARD_CSS)
        self.assertNotIn(".piston-range-note", DASHBOARD_CSS)
        self.assertIn("top: var(--piston-position)", DASHBOARD_CSS)
        self.assertIn("height: calc(var(--piston-position) - 4%)", DASHBOARD_CSS)
        self.assertIn("const positionPercent = 96 - ratio * 92", STEPPER_JS)
        self.assertNotIn("bottom: var(--piston-position)", DASHBOARD_CSS)
        self.assertNotIn("writing-mode: vertical-rl", DASHBOARD_CSS)

    def test_dashboard_derives_signed_velocity_only_from_fresh_dro_frames(
        self,
    ) -> None:
        config_directory = TemporaryDirectory()
        self.addCleanup(config_directory.cleanup)
        runtime = DashboardRuntime(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=5.0,
            history_limit=10,
            record_dir=Path("/tmp/stepper-dashboard-dro-velocity-test-recordings"),
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
            system_config_path=Path(config_directory.name) / "system_config.json",
        )

        def dro_sample(
            position_mm: float,
            frame_count: int,
            *,
            fresh: bool = True,
        ) -> dict[str, object]:
            return {
                "stepper_connected": True,
                "stepper_dro_capable": True,
                "stepper_dro_fresh": fresh,
                "stepper_dro_position_mm": position_mm,
                "stepper_dro_valid_frame_count": frame_count,
                "stepper_dro_sample_age_ms": 0,
            }

        try:
            first = dro_sample(10.0, 1)
            runtime._apply_stepper_dro_velocity_locked(first, 0.0)
            self.assertIsNone(first["stepper_dro_velocity_mm_s"])

            positive = dro_sample(10.2, 2)
            runtime._apply_stepper_dro_velocity_locked(positive, 0.2)
            self.assertEqual(positive["stepper_dro_velocity_mm_s"], 1.0)
            self.assertEqual(positive["stepper_dro_velocity_window_ms"], 200)

            positive_smoothed = dro_sample(10.4, 3)
            runtime._apply_stepper_dro_velocity_locked(positive_smoothed, 0.4)
            self.assertEqual(
                positive_smoothed["stepper_dro_velocity_mm_s"],
                1.0,
            )

            stale = dro_sample(10.4, 3, fresh=False)
            runtime._apply_stepper_dro_velocity_locked(stale, 0.6)
            self.assertIsNone(stale["stepper_dro_velocity_mm_s"])

            stopped_first = dro_sample(10.4, 4)
            runtime._apply_stepper_dro_velocity_locked(stopped_first, 0.8)
            stopped = dro_sample(10.4, 5)
            runtime._apply_stepper_dro_velocity_locked(stopped, 1.0)
            self.assertEqual(stopped["stepper_dro_velocity_mm_s"], 0.0)

            reset = dro_sample(10.4, 5, fresh=False)
            runtime._apply_stepper_dro_velocity_locked(reset, 1.2)
            negative_first = dro_sample(10.4, 6)
            runtime._apply_stepper_dro_velocity_locked(negative_first, 1.4)
            negative = dro_sample(10.2, 7)
            runtime._apply_stepper_dro_velocity_locked(negative, 1.6)
            self.assertEqual(negative["stepper_dro_velocity_mm_s"], -1.0)
        finally:
            runtime.stop()

    def test_dashboard_zeroes_fresh_dro_without_enabling_return_motion(self) -> None:
        self.assertIn('id="stepperSetDroZero"', INDEX_HTML)
        self.assertIn('id="stepperMoveToDroZero"', INDEX_HTML)
        self.assertIn(
            'id="stepperMoveToDroZero" type="button" disabled',
            INDEX_HTML,
        )
        self.assertIn('id="stepperDroRawPosition"', INDEX_HTML)
        self.assertIn('id="stepperDroZeroDiagnostic"', INDEX_HTML)
        self.assertIn('stepperDroZero: "/api/stepper/dro-zero"', API_JS)
        self.assertIn("els.stepperMoveToDroZero.disabled = true", STEPPER_JS)
        zero_handler = STEPPER_JS.split(
            'els.stepperSetDroZero.addEventListener("click"',
            1,
        )[1].split('els.stepperForm.addEventListener("input"', 1)[0]
        self.assertIn("postJson(API.stepperDroZero)", zero_handler)
        self.assertNotIn("API.stepperMove", zero_handler)
        self.assertNotIn("requestMove", zero_handler)
        self.assertIn("payload.zero?.motion_commanded !== false", zero_handler)

        config_directory = TemporaryDirectory()
        self.addCleanup(config_directory.cleanup)
        system_config_path = Path(config_directory.name) / "system_config.json"
        runtime_arguments = dict(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=5.0,
            history_limit=10,
            record_dir=Path("/tmp/stepper-dashboard-dro-zero-test-recordings"),
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
            system_config_path=system_config_path,
        )
        runtime = DashboardRuntime(**runtime_arguments)
        try:
            self.assertFalse(runtime.latest["stepper_dro_zero_set"])
            self.assertIsNone(runtime.latest["stepper_dro_zero_raw_mm"])
            self.assertIsNone(runtime.latest["stepper_dro_zeroed_position_mm"])
            with runtime._condition:
                runtime.latest.update(
                    {
                        "stepper_connected": True,
                        "stepper_moving": False,
                        "stepper_dro_capable": True,
                        "stepper_dro_fresh": True,
                        "stepper_dro_position_mm": -2.81,
                        "stepper_dro_valid_frame_count": 10,
                    }
                )
            stepper = next(
                source for source in runtime.sources if source.name == "stepper"
            )
            with mock.patch.object(stepper, "move") as move:
                payload = runtime.set_stepper_dro_zero()
                move.assert_not_called()
            self.assertEqual(
                payload["zero"],
                {
                    "set": True,
                    "raw_position_mm": -2.81,
                    "scope": "system_config",
                    "motion_commanded": False,
                },
            )
            self.assertEqual(
                json.loads(system_config_path.read_text(encoding="utf-8")),
                {
                    "version": 1,
                    "geometry": {
                        "powder_mass_per_stepper_travel_g_per_mm": 2.4,
                    },
                    "stepper": {
                        "dro_zero_raw_mm": -2.81,
                    },
                },
            )
            self.assertEqual(payload["sample"]["stepper_dro_zeroed_position_mm"], 0.0)

            next_sample = dict(payload["sample"])
            next_sample["stepper_dro_position_mm"] = 1.69
            runtime._apply_stepper_dro_zero_locked(next_sample)
            self.assertEqual(next_sample["stepper_dro_zeroed_position_mm"], 4.5)
            self.assertEqual(next_sample["stepper_dro_position_mm"], 1.69)

            runtime.latest["stepper_moving"] = True
            with self.assertRaisesRegex(RuntimeError, "stop motion"):
                runtime.set_stepper_dro_zero()
            runtime.latest["stepper_moving"] = False
            runtime.latest["stepper_dro_fresh"] = False
            with self.assertRaisesRegex(RuntimeError, "fresh connected DRO"):
                runtime.set_stepper_dro_zero()
        finally:
            runtime.stop()

        restarted_runtime = DashboardRuntime(**runtime_arguments)
        try:
            self.assertTrue(restarted_runtime.latest["stepper_dro_zero_set"])
            self.assertEqual(
                restarted_runtime.latest["stepper_dro_zero_raw_mm"],
                -2.81,
            )
            reconnected_sample = dict(restarted_runtime.latest)
            reconnected_sample["stepper_dro_position_mm"] = 1.69
            restarted_runtime._apply_stepper_dro_zero_locked(reconnected_sample)
            self.assertEqual(
                reconnected_sample["stepper_dro_zeroed_position_mm"],
                4.5,
            )
        finally:
            restarted_runtime.stop()

    def test_dashboard_exposes_and_latches_simulated_software_estop(self) -> None:
        self.assertIn('id="emergencyStop"', INDEX_HTML)
        self.assertIn('id="emergencyReset"', INDEX_HTML)
        self.assertIn('/api/stepper/estop', API_JS)
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
        config = runtime.dashboard_config()
        self.assertEqual(config["history_limit"], 10)
        self.assertEqual(config["solenoid_count"], 4)
        self.assertEqual(
            config["geometry"],
            {
                "powder_mass_per_stepper_travel_g_per_mm": 2.4,
            },
        )
        self.assertEqual(
            config["stepper"],
            {
                "max_distance_mm": DEFAULT_STEPPER_MAX_DISTANCE_MM,
                "min_speed_mm_s": DEFAULT_STEPPER_MIN_SPEED_MM_S,
                "max_speed_mm_s": DEFAULT_STEPPER_MAX_SPEED_MM_S,
                "default_speed_mm_s": DEFAULT_STEPPER_HOME_SPEED_MM_S,
                "home_speed_mm_s": DEFAULT_STEPPER_HOME_SPEED_MM_S,
            },
        )
        saved_metadata = runtime.update_metadata(
            {
                "powder_flow_rate_g_per_s": "6.0",
                "test_duration_s": "4.0",
            }
        )
        self.assertEqual(saved_metadata["powder_flow_rate_g_per_s"], "6.0")
        self.assertEqual(saved_metadata["test_duration_s"], "4.0")
        motor_on = runtime.toggle_stepper_brushless_motor()
        self.assertTrue(motor_on["confirmed"])
        self.assertTrue(motor_on["stepper"]["stepper_brushless_motor_on"])
        self.assertEqual(motor_on["pulse_us"], 1200)
        stopped = runtime.emergency_stop_stepper()
        self.assertTrue(stopped["confirmed"])
        self.assertTrue(stopped["stepper"]["stepper_estop_latched"])
        self.assertFalse(stopped["stepper"]["stepper_moving"])
        self.assertFalse(stopped["stepper"]["stepper_brushless_motor_on"])
        self.assertEqual(
            stopped["stepper"]["stepper_brushless_motor_pulse_us"],
            1000,
        )
        with self.assertRaisesRegex(RuntimeError, "E-STOP"):
            runtime.toggle_stepper_brushless_motor()
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
                    '"csps":378,"ds":1,"m":0,"h":0,"a":1,"e":1,'
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
                    '"csps":378,"ds":1,"m":0,"h":0,"a":1,"e":0,'
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

    def test_runtime_requires_fresh_usb_brushless_motor_acknowledgement(self) -> None:
        master_fd, slave_fd = pty.openpty()
        runtime = DashboardRuntime(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=5.0,
            history_limit=10,
            record_dir=Path("/tmp/stepper-dashboard-brushless-usb-test-recordings"),
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
                (UsbStepperSourceTests.BRUSHLESS_OFF_READY + "\r\n").encode(),
            )
            stepper = next(
                source for source in runtime.sources if source.name == "stepper"
            )
            self.assertIsNotNone(stepper.poll(0.1))
            runtime.start()

            command: list[bytes] = []

            def acknowledge_motor_on() -> None:
                command.append(os.read(master_fd, 32))
                status = UsbStepperSourceTests.BRUSHLESS_ON_READY.replace(
                    '"q":20',
                    '"q":21',
                )
                os.write(master_fd, (status + "\r\n").encode())

            responder = threading.Thread(target=acknowledge_motor_on)
            responder.start()
            result = runtime.toggle_stepper_brushless_motor()
            responder.join(timeout=1.0)
            self.assertEqual(command, [b"V1 B1\n"])
            self.assertTrue(result["confirmed"])
            self.assertTrue(result["on"])
            self.assertEqual(result["pulse_us"], 1200)
            self.assertTrue(result["stepper"]["stepper_brushless_motor_on"])
        finally:
            runtime.stop()
            os.close(master_fd)
            os.close(slave_fd)

    def test_runtime_sets_and_confirms_usb_brushless_pulse_width(self) -> None:
        master_fd, slave_fd = pty.openpty()
        runtime = DashboardRuntime(
            scenario="healthy",
            rate_hz=10.0,
            drop_after_s=2.0,
            stale_after_s=5.0,
            history_limit=10,
            record_dir=Path("/tmp/stepper-dashboard-brushless-pulse-test-recordings"),
            esp32_source="off",
            dxmr90_source="off",
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
                (
                    UsbStepperSourceTests.BRUSHLESS_VARIABLE_OFF_READY
                    + "\r\n"
                ).encode(),
            )
            stepper = next(
                source for source in runtime.sources if source.name == "stepper"
            )
            self.assertIsNotNone(stepper.poll(0.1))
            runtime.start()

            command: list[bytes] = []

            def acknowledge_pulse() -> None:
                command.append(os.read(master_fd, 32))
                status = (
                    UsbStepperSourceTests.BRUSHLESS_VARIABLE_OFF_READY
                    .replace('"q":20', '"q":21')
                    .replace('"bp":1200', '"bp":1750')
                )
                os.write(master_fd, (status + "\r\n").encode())

            responder = threading.Thread(target=acknowledge_pulse)
            responder.start()
            result = runtime.set_stepper_brushless_pulse(
                {"pulse_us": 1750}
            )
            responder.join(timeout=1.0)
            self.assertEqual(command, [b"V1 P1750\n"])
            self.assertTrue(result["confirmed"])
            self.assertEqual(result["setpoint_us"], 1750)
            self.assertEqual(result["pulse_us"], 1000)
            self.assertEqual(
                result["stepper"]["stepper_brushless_motor_setpoint_us"],
                1750,
            )
        finally:
            runtime.stop()
            os.close(master_fd)
            os.close(slave_fd)

    def test_runtime_forwards_manual_speed_without_enabling_move(self) -> None:
        master_fd, slave_fd = pty.openpty()
        port = os.ttyname(slave_fd)
        stopped_status = (
            '{"v":1,"t":"s","q":9,"d4":1,"d5":0,"d6":1,"d8":1,'
            '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,"csps":378,"ds":1}\r\n'
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
                    '"csps":1008,"ds":1}\r\n'
                )
                os.write(master_fd, confirmed_status.encode())

            responder = threading.Thread(target=acknowledge_speed)
            responder.start()
            payload = runtime.set_stepper_speed({"speed_mm_s": 4.0})
            responder.join(timeout=1.0)
            self.assertFalse(responder.is_alive())
            self.assertEqual(speed_command, [b"V1 S1008\n"])
            self.assertTrue(payload["confirmed"])
            self.assertEqual(
                payload["sample"]["stepper_command_speed_mm_s"],
                4.0005,
            )
            self.assertFalse(payload["stepper"]["stepper_command_capable"])
            self.assertTrue(
                payload["stepper"]["stepper_speed_command_capable"]
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
                    '"lp":0,"ln":0,"b":0,"r":"run_off","sps":0,"csps":378,"ds":1,'
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
                '"lp":0,"ln":0,"b":0,"r":"none","sps":0,"csps":378,"ds":1,'
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
                    '"lp":0,"ln":0,"b":0,"r":"none","sps":-1,"csps":378,"ds":1,'
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
                '"lp":0,"ln":0,"b":0,"r":"none","sps":0,"csps":378,"ds":1,'
                '"m":1,"h":0,"a":1,"mv":0,"st":5,"p":10000,"g":10000,"c":0}\r\n'
            )
            os.write(master_fd, ready_reverse.encode())
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if stepper.status().get("stepper_status_sequence") == 24:
                    break
                time.sleep(0.02)
            self.assertEqual(stepper.status()["stepper_status_sequence"], 24)

            rejected_move_command: list[bytes] = []

            def reject_move() -> None:
                rejected_move_command.append(os.read(master_fd, 64))
                status = ready_reverse.replace('"q":24', '"q":25')
                os.write(
                    master_fd,
                    b"Command rejected: test interlock.\r\n"
                    + status.encode(),
                )

            responder = threading.Thread(target=reject_move)
            responder.start()
            with self.assertRaisesRegex(
                RuntimeError,
                "motion controller rejected the move: test interlock",
            ):
                runtime.move_stepper(
                    {
                        "distance_mm": 1.0,
                        "speed_mm_s": 1.0,
                        "command_id": "expected-rejection",
                    }
                )
            responder.join(timeout=1.0)
            self.assertFalse(responder.is_alive())
            self.assertEqual(
                rejected_move_command,
                [b"V1 G-252,252,1\n"],
            )

            move_command: list[bytes] = []

            def acknowledge_move() -> None:
                move_command.append(os.read(master_fd, 64))
                status = (
                    '{"v":1,"t":"s","q":26,"d4":0,"d5":0,"d6":1,"d8":1,'
                    '"lp":0,"ln":0,"b":0,"r":"none","sps":-1,"csps":504,"ds":1,'
                    '"m":1,"h":0,"a":1,"mv":1,"st":4,"p":10000,"g":9750,"c":2}\r\n'
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
            self.assertEqual(move_command, [b"V1 G-630,504,2\n"])
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
