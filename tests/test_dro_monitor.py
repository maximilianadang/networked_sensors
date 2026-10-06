"""Persistent feedback must remain active through motion and quiet periods."""
import csv
import io
import unittest
from pathlib import Path
from unittest.mock import Mock

from dashboard_app.experiment import ExperimentRunner, parse_program
from test_motion_readings import sample
from test_experiment import healthy_sample
import test_backend_boundary as backend

EXAMPLE = (Path(__file__).resolve().parents[1] / 'examples' / 'dro_monitor.csv').read_text()


def program(*indices):
    rows = list(csv.reader(io.StringIO(EXAMPLE)))
    out = io.StringIO()
    csv.writer(out, lineterminator='\n').writerows([rows[0], *[rows[i] for i in indices]])
    return out.getvalue()


class DroMonitorTests(unittest.TestCase):
    def runner(self, text=None, request=None, abort=None):
        runner = ExperimentRunner(request_state=request or Mock(return_value={'command_id': 'move-1'}),
                                  abort_state=abort)
        runner.load(text or program(1, 3, 5, 6), 'persistent.csv', 0)
        runner.start(0)
        runner.on_sample(healthy_sample(), .1)
        runner.on_sample(sample(), .2)
        return runner

    def test_enable_advances_and_remains_active_until_disable(self):
        runner = self.runner(text=program(1, 3, 6))
        self.assertEqual(runner.current_index, 2)
        self.assertTrue(runner.snapshot()['monitors']['controllino/dro']['active'])
        runner.on_sample(sample(), .3)
        self.assertEqual(runner.state, 'completed')
        self.assertFalse(runner.snapshot()['monitors']['controllino/dro']['active'])
        self.assertEqual([e['event'] for e in runner.events].count('monitor_enabled'), 1)
        self.assertEqual([e['event'] for e in runner.events].count('monitor_disabled'), 1)
        runner.tick(100)
        self.assertEqual(runner.state, 'completed')

    def test_monitor_lifetime_is_independent_of_enable_row_timeout(self):
        runner = self.runner()
        runner.on_sample(sample(), .3)
        runner.on_sample(sample(), 11)
        self.assertEqual(runner.state, 'running')
        self.assertTrue(runner.snapshot()['monitors']['controllino/dro']['active'])

    def test_bound_failure_during_move_aborts_once_with_origin_evidence(self):
        abort = Mock()
        runner = self.runner(abort=abort)
        runner.on_sample(sample(), .3) # dispatch
        values = sample()
        values.update(stepper_dro_position_mm=101, stepper_command_id='move-1',
                      stepper_moving=True, stepper_state='moving')
        runner.on_sample(values, .4)
        self.assertEqual(runner.state, 'failed')
        result = runner.snapshot()['results'][-1]
        self.assertEqual(result['reason'], 'persistent_reading_check_failed')
        self.assertEqual(result['monitor_row'], 2)
        self.assertEqual(result['monitor_checks']['position']['observed'], 101)
        self.assertTrue(result['stop_requested'])
        abort.assert_called_once()
        runner.tick(10)
        runner.on_sample(values, 10)
        abort.assert_called_once()

    def test_failure_precedes_dispatch_and_disable(self):
        for indices in ((1, 3, 5, 6), (1, 3, 6)):
            request = Mock()
            runner = self.runner(text=program(*indices), request=request)
            values = sample()
            values['stepper_dro_position_mm'] = -1
            runner.on_sample(values, .3)
            self.assertEqual(runner.state, 'failed')
            request.assert_not_called()
            self.assertTrue(runner.snapshot()['monitors']['controllino/dro']['active'])

    def test_persistent_phase_does_not_expire_the_initial_freshness_threshold(self):
        abort = Mock()
        runner = self.runner(abort=abort)
        # Confirm start so the independent move handshake deadline is finished.
        runner.on_sample(sample(), .3)
        values = sample()
        values.update(stepper_command_id='move-1', stepper_moving=True,
                      stepper_state='moving')
        runner.on_sample(values, .4)
        runner.tick(100)
        self.assertEqual(runner.state, 'running')
        monitor = runner.snapshot()['monitors']['controllino/dro']
        self.assertEqual(set(monitor['checks']), {'health', 'position'})
        self.assertEqual(monitor['phase'], 'persistent')
        # Missing/old ages no longer matter to either started move or monitor.
        values.update(stepper_age_ms=None, stepper_dro_sample_age_ms=None)
        runner.on_sample(values, 101)
        self.assertEqual(runner.state, 'running')
        values.update(stepper_state='completed', stepper_moving=False)
        runner.on_sample(values, 102)
        runner.on_sample(values, 103)
        self.assertEqual(runner.state, 'completed')
        abort.assert_not_called()

    def test_new_sample_replaces_cached_timer_evidence_and_retains_bounds(self):
        runner = self.runner()
        # Near original sample expiration, a fresh event must be used first.
        runner.on_sample(sample(), 1.05)
        self.assertEqual(runner.state, 'running')
        values = sample()
        values.update(stepper_command_id='move-1', stepper_moving=False,
                      stepper_state='completed', stepper_dro_position_mm=70)
        runner.on_sample(values, 1.1)
        self.assertEqual(runner.current_index, 3)
        monitor = runner.snapshot()['monitors']['controllino/dro']
        self.assertEqual(monitor['checks']['position']['observed'], 70)
        self.assertTrue(monitor['active'])
        runner.on_sample(values, 1.2)
        self.assertEqual(runner.state, 'completed')

    def test_unhealthy_dro_and_transport_fail_persistent_check(self):
        for change in ({'stepper_transport_error': 'error'},
                       {'stepper_dro_position_mm': None}):
            with self.subTest(change=change):
                runner = self.runner()
                values = sample()
                values.update(change)
                runner.on_sample(values, .3)
                self.assertEqual(runner.state, 'failed')

    def test_parser_requires_balanced_monitor_states_and_rejects_ignored_operands(self):
        invalid = [program(1, 3), program(1, 6), program(1, 3, 3, 6),
                   program(1, 3, 6, 6), EXAMPLE.replace(',on,0,100', ',forever,0,100'),
                   EXAMPLE.replace('set_point', 'scope'),
                   EXAMPLE.replace(',on,0,100', ',off,0,100'),
                   EXAMPLE.replace('controllino', 'ed593').replace('dro', 'tc0')]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_program(text)
        self.assertEqual(len(parse_program(EXAMPLE)), 6)
        self.assertEqual(len(parse_program(program(1, 3, 6, 3, 6))), 5)

    def test_restart_and_load_clear_monitor_evidence(self):
        runner = self.runner(text=program(1, 3, 6))
        runner.on_sample(sample(), .3)
        runner.start(1)
        self.assertEqual(runner.snapshot()['monitors'], {})
        runner.on_sample(healthy_sample(), 1.1)
        runner.on_sample(sample(), 1.2)
        runner.on_sample(sample(), 1.3)
        runner.load(program(1), 'health.csv', 2)
        self.assertEqual(runner.snapshot()['monitors'], {})



class DroMonitorIntegrationTests(unittest.TestCase):
    setUp = backend.BackendTests.setUp
    runtime = backend.BackendTests.runtime

    def test_real_runtime_monitor_failure_stops_motion(self):
        runtime = self.runtime()
        self.stepper.payload.update(dc=1, df=1, dr=5000, dd=0, da=0, dq=1, dx=0)
        self.stepper.refresh()
        runtime.load_experiment({'csv': EXAMPLE})
        runtime.start_experiment({})
        for time in (.1, .2, .3, .4, .5, .6):
            with runtime._condition:
                runtime._poll_locked(time)
        self.assertTrue(self.stepper.status()['stepper_moving'])
        self.stepper.payload['dr'] = 10100
        self.stepper.refresh()
        with runtime._condition:
            runtime._poll_locked(.7)
        result = runtime.experiment_state()['results'][-1]
        self.assertEqual(result['reason'], 'persistent_reading_check_failed')
        self.assertTrue(result['stop_requested'])
        self.assertEqual(self.stepper.commands[-1], b'V1 X\n')
        self.assertFalse(self.stepper.status()['stepper_moving'])

    def test_full_procedure_starts_with_recording_and_finishes_ordered_shutdown(self):
        self.stepper.payload.update(aux="esc", bo=0, bp=1200, dc=1, df=1,
                                    dr=5000, dd=0, da=0, dq=1, dx=0)
        self.stepper.refresh()
        runtime = self.runtime()
        text = (Path(__file__).resolve().parents[1] / 'examples' / 'full_test.csv').read_text()
        runtime.load_experiment({'csv': text})
        runtime.start_experiment({})
        transitions = []
        for index in range(1, 40):
            if self.stepper.payload['mv']:
                self.assertTrue(runtime.latest['solenoid1_on'])
                self.assertTrue(runtime.latest['solenoid2_on'])
                self.assertTrue(runtime.latest['stepper_brushless_motor_on'])
                self.stepper.payload.update(mv=0, st=6, sps=0, p=self.stepper.payload['g'])
                self.stepper.refresh()
            with runtime._condition:
                runtime._poll_locked(index / 10)
            state = (runtime.latest['solenoid1_on'], runtime.latest['solenoid2_on'],
                     runtime.latest['stepper_brushless_motor_on'])
            if not transitions or transitions[-1] != state:
                transitions.append(state)
            if runtime.experiment_state()['state'] != 'running':
                break
        snapshot = runtime.experiment_state()
        self.assertEqual(snapshot['state'], 'completed')
        self.assertEqual(len(snapshot['results']), 12)
        self.assertTrue(runtime.recording)
        self.assertFalse(snapshot['monitors']['controllino/dro']['active'])
        self.assertEqual(transitions, [(False, False, False), (False, False, True),
                         (True, False, True), (True, True, True), (True, False, True),
                         (False, False, True), (False, False, False)])
        self.assertEqual(self.stepper.commands[:3], [b'V1 P1200\n', b'V1 B1\n', b'V1 M1\n'])
        self.assertTrue(self.stepper.commands[3].startswith(b'V1 G'))
        self.assertEqual(self.stepper.commands[4], b'V1 B0\n')
