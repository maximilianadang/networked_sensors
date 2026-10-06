"""Reject invalid command operands throughout a worksheet before execution."""
import unittest
from unittest.mock import Mock, PropertyMock, patch

import supervisor_core as core
from dashboard_app.experiment import ExperimentRunner, parse_program
from test_optional_reading_health import worksheet
import test_backend_boundary as backend


def request(**changes):
    fields = dict(action='requested_state', source='controllino', device='stepper',
                  max_age_ms=1000, health='ok', timeout_s=10, set_point='move',
                  speed_mm_s=1, duration_s=10, direction='forward')
    fields.update(changes)
    return fields


def pulse(value):
    return request(device='brushless_motor', set_point='pulse', pulse_us=value,
                   speed_mm_s='', duration_s='', direction='')


class CommandLintTests(unittest.TestCase):
    def test_esc_limits_rejected_before_any_row_runs(self):
        for value in (999, 2001, 1200.5):
            with self.subTest(value=value):
                callback = Mock()
                runner = ExperimentRunner(request_state=callback)
                program = worksheet(dict(action='requested_state', source='recording',
                                         timeout_s=10, set_point='on'), pulse(value))
                with self.assertRaisesRegex(ValueError, 'Row 2:.*pulse_us'):
                    runner.load(program, 'invalid.csv', 0)
                with self.assertRaises(RuntimeError):
                    runner.start(0)
                callback.assert_not_called()

    def test_esc_endpoints_share_execution_validator(self):
        for value in (1000, 2000):
            action, = parse_program(worksheet(pulse(value)))
            self.assertEqual(action.pulse_us, core.UsbStepperSource._brushless_pulse_us(value))

    def test_move_limits_and_pulse_resolution(self):
        cases = [(request(speed_mm_s=0.09), 'speed_mm_s'),
                 (request(speed_mm_s=10.01), 'speed_mm_s'),
                 (request(speed_mm_s=10, duration_s=20), 'max_travel_mm'),
                 (request(duration_s=0.0001), 'one provisional step')]
        for fields, error in cases:
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(ValueError, 'Row 1:.*' + error):
                    parse_program(worksheet(fields))
        for speed in (0.1, 10):
            parse_program(worksheet(request(speed_mm_s=speed, duration_s=1)))

    def test_configured_travel_limit_does_not_override_controller_limit(self):
        with self.assertRaisesRegex(ValueError, 'max_travel_mm'):
            parse_program(worksheet(request()), max_travel_mm=5)
        with self.assertRaisesRegex(ValueError, 'must not exceed'):
            parse_program(worksheet(request(speed_mm_s=10, duration_s=20)), max_travel_mm=500)

    def test_runtime_checks_configuration_at_load_and_again_at_start(self):
        fixture = backend.BackendTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        runtime = fixture.runtime()
        with patch.object(type(runtime.system_config), 'stepper_max_travel_mm',
                          new_callable=PropertyMock, return_value=5):
            with self.assertRaisesRegex(ValueError, 'Row 1:.*max_travel_mm'):
                runtime.load_experiment({'csv': worksheet(request())})
        runtime.load_experiment({'csv': worksheet(request())})
        with patch.object(type(runtime.system_config), 'stepper_max_travel_mm',
                          new_callable=PropertyMock, return_value=5):
            with self.assertRaisesRegex(ValueError, 'Row 1:.*max_travel_mm'):
                runtime.start_experiment({})
        self.assertEqual(runtime.experiment_state()['state'], 'ready')
        self.assertEqual(fixture.stepper.commands, [])
