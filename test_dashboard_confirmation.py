"""Behavioral regressions for shared confirmation and Controllino telemetry."""

import json
import unittest
from unittest.mock import MagicMock, Mock, patch

from networked_sensors.dashboard_app.runtime import DashboardRuntime
from networked_sensors.supervisor_core import ControllinoStepperSource
from networked_sensors import test_stepper_control as fixtures


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = object.__new__(DashboardRuntime)
        self.runtime._condition = Mock()
        self.runtime._stepper_payload_locked = Mock()
        self.stepper = Mock(pending_command_error=None)

    def test_waits_for_fresh_matching_status(self):
        stale = {"stepper_status_sequence": 1, "stepper_moving": False}
        wrong = {"stepper_status_sequence": 2, "stepper_moving": True}
        fresh = {"stepper_status_sequence": 3, "stepper_moving": False}
        self.runtime._stepper_payload_locked.side_effect = [stale, wrong, fresh]
        result = self.runtime._confirm_stepper_locked(
            self.stepper, 1, "Stop", lambda p: p["stepper_moving"] is False,
        )
        self.assertEqual(result, fresh)
        self.assertEqual(self.runtime._condition.wait.call_count, 2)

    def test_timeout_and_rejection_never_confirm(self):
        self.runtime._stepper_payload_locked.return_value = {"stepper_status_sequence": 1}
        with patch("networked_sensors.dashboard_app.runtime.time.monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(RuntimeError, "did not confirm Stop"):
                self.runtime._confirm_stepper_locked(self.stepper, 1, "Stop", lambda p: True)
        self.stepper.pending_command_error = "owned_by_usb"
        with self.assertRaisesRegex(RuntimeError, "owned_by_usb"):
            self.runtime._confirm_stepper_locked(self.stepper, 1, "Stop", lambda p: True)

    def test_reverse_requires_reverse_status(self):
        self.runtime._condition = MagicMock()
        self.runtime._stepper_local_run_locked = Mock(return_value=self.stepper)
        self.runtime.latest = {}
        self.runtime._stepper_payload_locked.side_effect = [
            {"stepper_status_sequence": 1},
            {"stepper_status_sequence": 2, "stepper_moving": True, "stepper_direction": "positive"},
            {"stepper_status_sequence": 3, "stepper_moving": True, "stepper_direction": "negative"},
        ]
        result = self.runtime.set_stepper_local_run({"direction": -1})
        self.assertEqual(result["stepper"]["stepper_direction"], "negative")
        self.runtime._condition.wait.assert_called_once()


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.source = ControllinoStepperSource("http://127.0.0.1")
        self.payload = json.loads(fixtures.ControllinoStepperSourceTests.STATUS)

    def test_absent_limits_and_actual_owner(self):
        for owner, expected in ((0, "none"), (1, "web_position_usb"), (2, "web_position_network")):
            self.payload["o"] = owner
            values = self.source._decode_network_status(json.dumps(self.payload))
            self.assertEqual(values["stepper_control_owner"], expected)
            self.assertIsNone(values["stepper_positive_limit_active"])
            self.assertIsNone(values["stepper_d6_raw"])

    def test_reported_limit_is_preserved_and_blocks_move(self):
        self.payload.update(d6=0, d8=1, lp=1)
        self.payload.pop("lx")
        values = self.source._decode_network_status(json.dumps(self.payload))
        self.assertTrue(values["stepper_positive_limit_active"])
        with self.assertRaisesRegex(RuntimeError, "positive limit"):
            self.source._validate_move_authority(values, 100)
        self.source._validate_move_authority(values, -100)

    def test_malformed_limit_is_rejected(self):
        self.payload["d6"] = None
        with self.assertRaises(ValueError):
            self.source._decode_network_status(json.dumps(self.payload))
