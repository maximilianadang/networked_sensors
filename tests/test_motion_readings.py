"""Read-only DRO checks; limit switches remain outside spreadsheet control."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from dashboard_app.experiment import ExperimentRunner, parse_program
from test_backend_boundary import Controller
from test_experiment import healthy_sample

EXAMPLE = (Path(__file__).resolve().parents[1] / 'examples' / 'motion_readings.csv').read_text()


def sample(**changes):
    return healthy_sample('stepper', stepper_dro_capable=True,
        stepper_dro_sample_age_ms=100, stepper_dro_position_mm=50,
        stepper_d6_raw='HIGH', stepper_d8_raw='HIGH',
        stepper_positive_limit_active=False, stepper_negative_limit_active=False,
        **changes)


class MotionReadingTests(unittest.TestCase):
    def test_dro_health_uses_explicit_age_and_ignores_limit_states(self):
        health = parse_program(EXAMPLE)[1]
        values = sample()
        values.update(stepper_connected=False, stepper_dro_fresh=False,
                      stepper_d6_raw=None, stepper_positive_limit_active=None,
                      stepper_negative_limit_active=True, stepper_negative_limit_latched=True)
        self.assertTrue(all(c['ok'] for c in health.evaluate(values).values()))
        values['stepper_dro_sample_age_ms'] = 1001
        self.assertFalse(health.evaluate(values)['dro']['ok'])

    def test_dro_finite_inclusive_raw_position_bounds_and_explicit_freshness(self):
        action = parse_program(EXAMPLE)[2]
        for value, ok in [(0, True), (100, True), (-.01, False), (100.01, False),
                          (None, False), (True, False), (float('nan'), False), (float('inf'), False)]:
            values = sample()
            values.update(stepper_dro_position_mm=value, stepper_dro_display_mm=50)
            with self.subTest(value=value):
                self.assertEqual(action.evaluate(values)['position']['ok'], ok)
        values = sample()
        values['stepper_dro_sample_age_ms'] = 1001
        self.assertFalse(action.evaluate(values)['dro_freshness']['ok'])
        values['stepper_dro_sample_age_ms'] = None
        self.assertFalse(action.evaluate(values)['dro_freshness']['ok'])
        values['stepper_dro_sample_age_ms'] = 100
        values['stepper_transport_error'] = 'disconnected'
        self.assertFalse(action.evaluate(values)['health']['ok'])

    def test_spreadsheet_rejects_limit_devices_and_removed_state_column(self):
        for device in ('positive_limit', 'negative_limit'):
            with self.subTest(device=device):
                with self.assertRaises(ValueError):
                    parse_program(EXAMPLE.replace('10,dro,', f'10,dro;{device},'))
                with self.assertRaises(ValueError):
                    parse_program(EXAMPLE.replace(',10,dro,0,100', f',10,{device},0,100'))
        lines = EXAMPLE.splitlines()
        legacy = '\n'.join([lines[0] + ',expected_state',
                             *(line + ',' for line in lines[1:])])
        with self.assertRaises(ValueError):
            parse_program(legacy)

    def test_parser_rejects_ignored_or_ambiguous_operands(self):
        for invalid in [EXAMPLE.replace('dro,0,100', 'dro,100,0'),
                        EXAMPLE.replace('dro,0,100', 'dro,nan,100'),
                        EXAMPLE.replace('min_mm,max_mm', 'min_c,max_c')]:
            with self.subTest(csv=invalid), self.assertRaises(ValueError):
                parse_program(invalid)
        negative = parse_program(EXAMPLE.replace('dro,0,100', 'dro,-100,100'))[2]
        self.assertEqual(negative.min_mm, -100)
        for action in parse_program(EXAMPLE):
            self.assertEqual(set(action.as_dict()), set(parse_program(EXAMPLE)[0].as_dict()))

    def test_runner_reads_all_without_dispatch_and_stops_on_failed_reading(self):
        request = Mock()
        runner = ExperimentRunner(request_state=request)
        runner.load(EXAMPLE, 'motion.csv', 0)
        runner.start(0)
        runner.on_sample(healthy_sample(), .1)
        for time in (.2, .3):
            runner.on_sample(sample(), time)
        self.assertEqual(runner.state, 'completed')
        request.assert_not_called()
        # Skip the readiness row to prove unavailable DRO readings fail themselves.
        lines = EXAMPLE.splitlines()
        runner.load('\n'.join([lines[0], lines[1], lines[3]]) + '\n', 'absent.csv', 1)
        runner.start(1)
        runner.on_sample(healthy_sample(), 1.1)
        values = sample()
        values.update(stepper_dro_capable=False, stepper_dro_position_mm=None)
        runner.on_sample(values, 1.2)
        self.assertEqual(runner.state, 'failed')
        self.assertFalse(runner.snapshot()['results'][-1]['checks']['position']['ok'])
        request.assert_not_called()

    def test_production_decoder_keeps_limit_evidence_outside_dro_checks(self):
        controller = Controller()
        health = parse_program(EXAMPLE)[1]
        self.assertIsNone(controller._last_values['stepper_positive_limit_active'])
        self.assertIsNone(controller._last_values['stepper_negative_limit_active'])
        controller.payload.pop('lx')
        controller.payload.update(d6=0, d8=1, dc=1, df=1, dr=5000, dd=0, da=0, dq=1, dx=0)
        values = healthy_sample('stepper', **controller.decode_status_line(json.dumps(controller.payload)))
        self.assertTrue(all(c['ok'] for c in health.evaluate(values).values()))
        self.assertTrue(values['stepper_positive_limit_active'])
        self.assertFalse(values['stepper_negative_limit_active'])
        self.assertTrue(parse_program(EXAMPLE)[2].evaluate(values)['position']['ok'])
        self.assertEqual(set(health.evaluate(values)), {'freshness', 'health', 'dro'})
