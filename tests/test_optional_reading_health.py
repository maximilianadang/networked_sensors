"""Reading comparisons include only worksheet-selected telemetry conditions."""
import csv
import io
import unittest
from unittest.mock import Mock
from dashboard_app.experiment import CSV_COLUMNS, ExperimentRunner, parse_program


def worksheet(*rows):
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=CSV_COLUMNS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue()


def row(**changes):
    fields = dict(action='reading_state', source='controllino', device='dro',
                  timeout_s=10, min_mm=0, max_mm=100)
    fields.update(changes)
    return fields


def evidence(**changes):
    values = dict(stepper_dro_position_mm=50, stepper_age_ms=10,
                  stepper_dro_sample_age_ms=10, stepper_transport_error=None,
                  stepper_dro_capable=True, stepper_connected=False)
    values.update(changes)
    return values


class OptionalReadingHealthTests(unittest.TestCase):
    def test_reading_set_points_replace_scope_in_schema_and_preview(self):
        self.assertNotIn('scope', CSV_COLUMNS)
        for point in ('', 'check'):
            action = parse_program(worksheet(row(set_point=point)))[0]
            self.assertEqual(action.as_dict()['set_point'], 'check')
            self.assertNotIn('scope', action.as_dict())
        actions = parse_program(worksheet(row(set_point='on'),
            dict(action='reading_state', source='controllino', device='dro',
                 set_point='off', timeout_s=10)))
        self.assertEqual([a.as_dict()['set_point'] for a in actions], ['on', 'off'])
        reading = dict(action='reading_state', source='ed593', device='tc0',
                       set_point='check', min_c=0, max_c=100, timeout_s=10)
        self.assertEqual(parse_program(worksheet(reading))[0].as_dict()['set_point'], 'check')
        for point in ('once', 'until_disabled', 'disabled', 'move'):
            with self.subTest(point=point), self.assertRaises(ValueError):
                parse_program(worksheet(row(set_point=point)))

    def test_value_only_requires_only_finite_in_bounds_measurement(self):
        action = parse_program(worksheet(row()))[0]
        self.assertEqual(action.as_dict()['max_age_ms'], '')
        self.assertEqual(action.as_dict()['health'], '')
        self.assertEqual(action.as_dict()['device'], 'dro')
        for value, ok in ((0, True), (100, True), (-1, False), (101, False),
                          (None, False), (True, False), (float('nan'), False), (float('inf'), False)):
            checks = action.evaluate({'stepper_dro_position_mm': value})
            self.assertEqual(set(checks), {'position'})
            self.assertEqual(checks['position']['ok'], ok)
        self.assertTrue(action.evaluate(evidence(stepper_age_ms=999999,
            stepper_dro_sample_age_ms=None, stepper_transport_error='offline',
            stepper_dro_capable=False))['position']['ok'])

    def test_freshness_and_health_can_each_be_selected_independently(self):
        choices = [({}, {'position'}),
                   ({'max_age_ms': 100}, {'position', 'freshness', 'dro_freshness'}),
                   ({'health': 'ok'}, {'position', 'health'}),
                   ({'max_age_ms': 100, 'health': 'ok'},
                    {'position', 'freshness', 'health', 'dro_freshness'})]
        for options, expected in choices:
            with self.subTest(options=options):
                action = parse_program(worksheet(row(**options)))[0]
                checks = action.evaluate(evidence())
                self.assertEqual(set(checks), expected)
                self.assertTrue(all(check['ok'] for check in checks.values()))
                for change, field in (({'stepper_age_ms': 101}, 'freshness'),
                                      ({'stepper_dro_sample_age_ms': 101}, 'dro_freshness'),
                                      ({'stepper_transport_error': 'lost'}, 'health'),
                                      ({'stepper_dro_capable': False}, 'dro')):
                    checks = action.evaluate(evidence(**change))
                    should_fail = (field == 'freshness' and 'max_age_ms' in options
                        or field == 'dro_freshness' and 'max_age_ms' in options
                        or field == 'health' and 'health' in options
)
                    self.assertEqual(all(check['ok'] for check in checks.values()), not should_fail)

    def test_zero_age_is_a_real_requirement_and_disabled_health_is_not_reported(self):
        action = parse_program(worksheet(row(max_age_ms=0)))[0]
        checks = action.evaluate(evidence(stepper_age_ms=0, stepper_dro_sample_age_ms=0))
        self.assertTrue(all(check['ok'] for check in checks.values()))
        self.assertNotIn('health', checks)
        self.assertFalse(action.evaluate(evidence(stepper_age_ms=.01))['freshness']['ok'])

    def test_temperature_value_only_vs_explicit_channel_readiness(self):
        reading = dict(action='reading_state', source='ed593', device='tc0',
                       min_c=0, max_c=100, timeout_s=10)
        sample = dict(ed593_tc0_temperature_c=25, ed593_tc0_ready=False,
                      ed593_tc0_enabled=False, ed593_tc0_fault=True,
                      ed593_age_ms=999999, ed593_transport_error='lost')
        action = parse_program(worksheet(reading))[0]
        self.assertEqual(set(action.evaluate(sample)), {'temperature'})
        self.assertTrue(action.evaluate(sample)['temperature']['ok'])
        action = parse_program(worksheet(dict(action='telemetry_health', source='ed593',
            device='tc0', max_age_ms=1000, health='ok', timeout_s=10)))[0]
        self.assertFalse(action.evaluate(sample)['tc0']['ok'])
        action = parse_program(worksheet({**reading, 'health': 'ok'}))[0]
        self.assertFalse(action.evaluate(sample)['health']['ok'])

    def test_persistent_value_only_monitor_has_no_hidden_age_or_health_failure(self):
        text = worksheet(row(set_point='on'),
            dict(action='requested_state', source='recording', set_point='on', timeout_s=60),
            dict(action='reading_state', source='controllino', device='dro', set_point='off', timeout_s=10))
        request = Mock()
        runner = ExperimentRunner(request_state=request)
        runner.load(text, 'value-only.csv', 0)
        runner.start(0)
        runner.on_sample({'stepper_dro_position_mm': 50}, .1)
        runner.on_sample({'stepper_dro_position_mm': 50}, .2)
        runner.tick(20)
        self.assertEqual(runner.state, 'running')
        self.assertEqual(set(runner.snapshot()['monitors']['controllino/dro']['checks']), {'position'})
        runner.on_sample({'stepper_dro_position_mm': 101}, 21)
        self.assertEqual(runner.state, 'failed')
        self.assertEqual(runner.results[-1]['reason'], 'persistent_reading_check_failed')
        request.assert_called_once()

    def test_age_threshold_only_gates_initial_dro_activation(self):
        reading = row(set_point='on', max_age_ms=100)
        text = worksheet(reading,
            dict(action='requested_state', source='recording', set_point='on', timeout_s=60),
            dict(action='reading_state', source='controllino', device='dro', set_point='off', timeout_s=10))
        runner = ExperimentRunner(request_state=Mock())
        runner.load(text, 'initial-age.csv', 0)
        runner.start(0)
        runner.on_sample(evidence(stepper_age_ms=101), .1)
        self.assertEqual(runner.state, 'failed')
        self.assertEqual(runner.results[-1]['reason'], 'reading_check_failed')
        runner.start(1)
        runner.on_sample(evidence(), 1.1)
        self.assertEqual(set(runner.snapshot()['monitors']['controllino/dro']['checks']), {'position'})
        runner.on_sample(evidence(stepper_age_ms=99999, stepper_dro_sample_age_ms=None), 1.2)
        runner.tick(20)
        self.assertEqual(runner.state, 'running')
        runner.on_sample(evidence(stepper_age_ms=99999, stepper_dro_position_mm=101), 21)
        self.assertEqual(runner.results[-1]['reason'], 'persistent_reading_check_failed')

    def test_persistent_optional_health_failure_remains_active(self):
        text = worksheet(row(set_point='on', health='ok'),
            dict(action='requested_state', source='recording', set_point='on', timeout_s=60),
            dict(action='reading_state', source='controllino', device='dro', set_point='off', timeout_s=10))
        runner = ExperimentRunner(request_state=Mock())
        runner.load(text, 'health.csv', 0)
        runner.start(0)
        runner.on_sample(evidence(), .1)
        runner.on_sample(evidence(stepper_transport_error='lost'), .2)
        self.assertEqual(runner.state, 'failed')
        self.assertFalse(runner.results[-1]['monitor_checks']['health']['ok'])

    def test_invalid_optional_conditions_rejected_before_execution(self):
        for options in ({'health': 'bad'}, {'max_age_ms': 'nan'}, {'max_age_ms': -1},
                        {'device': 'dro;dro'}, {'device': 'dro;'},
                        {'device': 'positive_limit'}, {'device': 'tc0'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                parse_program(worksheet(row(**options)))
        # Requested states retain their existing mandatory telemetry contract.
        with self.assertRaises(ValueError):
            parse_program(worksheet(dict(action='requested_state',source='controllino',
                device='brushless_motor',set_point='on',timeout_s=10)))
