"""One target column across health, readings, and requested states."""
import csv
import io
import unittest
from pathlib import Path
from dashboard_app.experiment import CSV_COLUMNS, parse_program
from test_optional_reading_health import evidence, worksheet, row


class UnifiedTargetTests(unittest.TestCase):
    def test_single_target_column_across_all_actions(self):
        actions = parse_program(worksheet(
            dict(action='telemetry_health', source='controllino', device='stepper;dro',
                 max_age_ms=1000, health='ok', timeout_s=10),
            row(),
            dict(action='requested_state', source='esp32', device='solenoid1;solenoid2',
                 max_age_ms=1000, health='ok', timeout_s=10, set_point='open'),
            dict(action='requested_state', source='controllino', device='stepper',
                 max_age_ms=1000, health='ok', timeout_s=1, set_point='move',
                 speed_mm_s=1, duration_s=60, direction='forward')))
        self.assertEqual([action.as_dict()['device'] for action in actions],
                         ['stepper;dro', 'dro', 'solenoid1;solenoid2', 'stepper'])
        for action in actions:
            self.assertEqual(set(action.as_dict()), set(CSV_COLUMNS))
        self.assertNotIn('required_devices', CSV_COLUMNS)
        self.assertNotIn('require_ready', CSV_COLUMNS)
        self.assertEqual(actions[0].devices, ('stepper', 'dro'))

    def test_readiness_is_a_health_row_target_without_broadening_health(self):
        health, reading = parse_program(worksheet(
            dict(action='telemetry_health', source='controllino', device='dro',
                 max_age_ms=1000, health='ok', timeout_s=10),
            row(health='ok')))
        sample = evidence(stepper_dro_capable=False)
        self.assertFalse(health.evaluate(sample)['dro']['ok'])
        self.assertTrue(health.evaluate(sample)['health']['ok'])
        self.assertTrue(all(check['ok'] for check in reading.evaluate(sample).values()))
        self.assertEqual(set(reading.evaluate(sample)), {'health', 'position'})
        sample['stepper_transport_error'] = 'lost'
        self.assertFalse(reading.evaluate(sample)['health']['ok'])

    def test_ambiguous_legacy_or_new_flag_headers_rejected(self):
        text = worksheet(row())
        rows = list(csv.reader(io.StringIO(text)))
        for column in ('required_devices', 'require_ready'):
            out = io.StringIO()
            csv.writer(out).writerows([rows[0] + [column], rows[1] + ['dro']])
            with self.subTest(column=column), self.assertRaises(ValueError):
                parse_program(out.getvalue())

    def test_every_local_example_uses_unique_unified_target_column(self):
        for path in (Path(__file__).resolve().parents[1] / 'examples').glob('*.csv'):
            with self.subTest(path=path.name):
                text = path.read_text()
                header = next(csv.reader(io.StringIO(text)))
                self.assertEqual(header.count('device'), 1)
                self.assertNotIn('required_devices', header)
                self.assertNotIn('require_ready', header)
                parse_program(text)
