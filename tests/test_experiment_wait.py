"""Timer-driven dwell and persistent motor command semantics."""
import unittest
from unittest.mock import Mock
from dashboard_app.experiment import ExperimentRunner, WaitAction, parse_program
from test_optional_reading_health import worksheet, row, evidence


def motor(rpm=250, **changes):
    fields = dict(action='requested_state', source='controllino', device='can_motor',
                  max_age_ms=500, health='ok', timeout_s=1, set_point='speed', rpm=rpm)
    fields.update(changes)
    return fields


class ExperimentWaitTests(unittest.TestCase):
    def test_wait_advances_by_clock_without_sensor_data_or_command_io(self):
        callback = Mock()
        runner = ExperimentRunner(request_state=callback)
        runner.load(worksheet(dict(action='wait', duration_s=5)), 'wait.csv', 0)
        self.assertIsInstance(runner.program[0], WaitAction)
        runner.start(0)
        runner.tick(4.999)
        self.assertEqual(runner.state, 'running')
        runner.tick(5)
        self.assertEqual(runner.state, 'completed')
        callback.assert_not_called()

    def test_wait_rejects_unrelated_operands_and_bad_duration(self):
        for extra in ({'source': 'controllino'}, {'timeout_s': 10}, {'health': 'ok'},
                      {'rpm': 1}, {'duration_s': 0}, {'duration_s': 'nan'}):
            fields = dict(action='wait', duration_s=5)
            fields.update(extra)
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                parse_program(worksheet(fields))

    def test_leased_motor_requires_explicit_stop_and_keeps_duration_blank(self):
        actions = parse_program(worksheet(motor(), motor(0)))
        self.assertIsNone(actions[0].duration_ms)
        self.assertEqual(actions[0].as_dict()['duration_s'], '')
        with self.assertRaisesRegex(ValueError, 'explicit rpm=0'):
            parse_program(worksheet(motor()))
        with self.assertRaisesRegex(ValueError, 'before a timed run'):
            parse_program(worksheet(motor(), motor(duration_s=1), motor(0)))

    def test_persistent_dro_check_remains_active_during_wait(self):
        runner = ExperimentRunner()
        runner.load(worksheet(row(set_point='on'), dict(action='wait', duration_s=5),
                              dict(action='reading_state', source='controllino', device='dro',
                                   set_point='off', timeout_s=1)), 'monitor.csv', 0)
        runner.start(0)
        runner.on_sample(evidence(), .1)
        self.assertEqual(runner.current_index, 1)
        runner.on_sample(evidence(stepper_dro_position_mm=101), .2)
        self.assertEqual(runner.state, 'failed')
        self.assertEqual(runner.results[-1]['reason'], 'persistent_reading_check_failed')

    def test_ramped_stop_deadline_ends_on_confirmed_acceptance(self):
        runner = ExperimentRunner(request_state=lambda a: {'before_sequence': 0, 'ramped_stop': True})
        runner.load(worksheet(motor(0)), 'stop.csv', 0)
        runner.start(0)
        sample = dict(stepper_age_ms=0, stepper_transport_error=None,
                      stepper_can_motor_target_rpm=0, stepper_can_motor_active=True,
                      stepper_can_motor_command_sequence=1, stepper_can_motor_error='running')
        runner.on_sample(sample, .1)
        runner.on_sample(sample, .2)
        self.assertTrue(runner.results[-1]['stop_started'])
        self.assertIsNone(runner.snapshot()['deadline_s'])
        runner.on_sample(dict(sample, stepper_age_ms=10000, stepper_transport_error='lag'), 100)
        self.assertEqual(runner.state, 'running')
        runner.on_sample(dict(sample, stepper_can_motor_active=False, stepper_can_motor_error='operator_stop'), 101)
        self.assertEqual(runner.state, 'completed')
