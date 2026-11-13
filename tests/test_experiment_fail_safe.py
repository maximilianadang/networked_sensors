"""Two spreadsheet sections share one runner and preserve ordered failure outcomes."""
import csv
import io
import json
import threading
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import supervisor_core as core
import test_backend_boundary as boundary
from test_cubemars import CanController
from dashboard_app.system_config import DEFAULT_SYSTEM_CONFIG
from dashboard_app.experiment import (
    CSV_COLUMNS, ExperimentRunner, PressureReadingAction, parse_program, parse_worksheet,
)


def worksheet(main, fail_safe=()):
    output = io.StringIO()
    columns = [*CSV_COLUMNS, *(f'fail_safe_{column}' for column in CSV_COLUMNS)]
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    for index in range(max(len(main), len(fail_safe))):
        row = dict(main[index]) if index < len(main) else {}
        if index < len(fail_safe):
            row.update({f'fail_safe_{key}': value for key, value in fail_safe[index].items()})
        writer.writerow(row)
    return output.getvalue()


def sequence_csv(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def pressure(**changes):
    return dict(dict(action='reading_state', source='controllino', device='AI10',
                     min_mbar=0, max_mbar=10, timeout_s=1), **changes)


def off():
    return dict(action='reading_state', source='controllino', device='AI10', set_point='off', timeout_s=1)


def command(device='stepper', set_point='halt', **changes):
    return dict(dict(action='requested_state', source='controllino', device=device,
                     set_point=set_point, max_age_ms=1000, health='ok', timeout_s=1), **changes)


def evidence(**changes):
    return dict(dict(stepper_pressure1_reading=5, stepper_age_ms=10,
                     stepper_pressure_sample_age_ms=10, stepper_transport_error=None,
                     stepper_moving=False, stepper_solenoid1_on=False,
                     stepper_solenoid2_on=False, stepper_solenoid3_on=False,
                     stepper_can_motor_target_rpm=0, stepper_can_motor_active=False,
                     stepper_can_motor_command_sequence=11), **changes)


class FailSafeTests(unittest.TestCase):
    def runner(self, main, fail_safe=(), request=None, abort=None):
        runner = ExperimentRunner(request_state=request, abort_state=abort)
        runner.load(worksheet(main, fail_safe), 'two-sections.csv', 0)
        runner.start(0)
        return runner

    def test_both_sections_linted_even_when_fail_safe_would_not_run(self):
        text = worksheet([pressure()], [command()])
        sheet = parse_worksheet(text)
        self.assertEqual(len(sheet.main), 1)
        self.assertEqual(len(sheet.fail_safe), 1)
        self.assertEqual(parse_program(text), sheet.main)
        for bad in (command(speed_mm_s=1), command(set_point='typo'), command(timeout_s=-1),
                    pressure(min_psi=0), pressure(max_mbar=-1)):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'Fail safe:'):
                parse_worksheet(worksheet([pressure()], [bad]))
        runner = ExperimentRunner()
        runner.load(worksheet([pressure()]), 'ready.csv', 0)
        original = runner.snapshot()
        with self.assertRaises(ValueError):
            runner.load(worksheet([pressure()], [command(duration_s=1)]), 'bad.csv', 1)
        self.assertEqual(runner.snapshot(), original)

    def test_two_csv_fields_use_same_actions_as_combined_format_and_lint_atomically(self):
        main, shutdown = [pressure()], [command()]
        self.assertEqual(parse_worksheet(sequence_csv(main), sequence_csv(shutdown)),
                         parse_worksheet(worksheet(main, shutdown)))
        runner = ExperimentRunner()
        runner.load(sequence_csv(main), 'main.csv', 0, fail_safe_csv=sequence_csv(shutdown))
        self.assertEqual(len(runner.fail_safe_program), 1)
        original = runner.snapshot()
        with self.assertRaisesRegex(ValueError, 'Fail safe:'):
            runner.load(sequence_csv(main), 'invalid.csv', 1,
                        fail_safe_csv=sequence_csv([command(speed_mm_s=1)]))
        self.assertEqual(runner.snapshot(), original)
        runner.load(sequence_csv(main), 'main.csv', 2, fail_safe_csv='\n ')
        self.assertFalse(runner.fail_safe_program)
        for invalid in (True, 42, {}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'fail_safe_csv'):
                parse_worksheet(sequence_csv(main), invalid)
        with self.assertRaisesRegex(ValueError, 'already contains fail-safe columns'):
            parse_worksheet(worksheet(main, shutdown), sequence_csv(shutdown))

    def test_separate_inputs_share_total_size_and_action_limits(self):
        with self.assertRaisesRegex(ValueError, '256 KiB'):
            parse_worksheet('x' * (150 * 1024), 'y' * (150 * 1024))
        wait = dict(action='wait', duration_s=1)
        with self.assertRaisesRegex(ValueError, '1000 action rows'):
            parse_worksheet(sequence_csv([wait] * 500), sequence_csv([wait] * 501))

    def test_columns_must_be_two_contiguous_independent_groups(self):
        text = worksheet([pressure()], [command()])
        for invalid in (text.replace('fail_safe_action', 'fail_safe_typo', 1),
                        text.replace('fail_safe_timeout_s', 'timeout_s', 1),
                        text.replace('fail_safe_health', 'fail_safe_action', 1)):
            with self.subTest(invalid=invalid[:200]), self.assertRaises(ValueError):
                parse_worksheet(invalid)

    def test_normal_completion_never_runs_fail_safe_and_can_restart(self):
        request = Mock()
        runner = self.runner([pressure()], [command()], request)
        for start in (0, 2):
            if start:
                runner.start(start)
            runner.on_sample(evidence(), start + .1)
            self.assertEqual(runner.state, 'completed')
            self.assertEqual(runner.sequence, 'main')
        request.assert_not_called()

    def test_threshold_failure_executes_response_in_order_once_without_resuming_main(self):
        requests, abort = [], Mock()
        def request(action):
            requests.append(action.as_dict())
            return {'before_sequence': 10}
        shutdown = [command('solenoid1;solenoid2;solenoid3', 'closed'), command(),
                    command('can_motor', 'speed', rpm=0)]
        runner = self.runner([pressure(), command('solenoid1', 'open')], shutdown, request, abort)
        runner.on_sample(evidence(stepper_pressure1_reading=11), .1)
        self.assertEqual(runner.sequence, 'fail_safe')
        self.assertEqual(runner.state, 'running')
        self.assertEqual(requests, [])
        for index in range(2, 8):
            runner.on_sample(evidence(stepper_pressure1_reading=11), index / 10)
        self.assertEqual(runner.state, 'failed')
        self.assertEqual([(r['device'], r['set_point']) for r in requests],
                         [('solenoid1;solenoid2;solenoid3', 'closed'), ('stepper', 'halt'), ('can_motor', 'speed')])
        self.assertEqual([r['status'] for r in runner.results], ['failed', 'passed', 'passed', 'passed'])
        self.assertEqual(runner.failure['checks']['pressure']['observed'], 11)
        abort.assert_not_called()  # Explicit fail-safe rows determine output order.
        runner.tick(10)
        runner.on_sample(evidence(), 11)
        self.assertEqual(len(requests), 3)

    def test_fail_safe_continues_after_dispatch_error_and_keeps_both_causes(self):
        def request(action):
            if action.as_dict()['device'].startswith('solenoid'):
                raise OSError('transport write failed')
        runner = self.runner([pressure()], [command('solenoid1', 'closed'), command()], request)
        for index in range(1, 5):
            runner.on_sample(evidence(stepper_pressure1_reading=11), index / 10)
        self.assertEqual(runner.state, 'failed')
        self.assertEqual(runner.failure['reason'], 'reading_check_failed')
        self.assertEqual(runner.results[1]['reason'], 'request_failed')
        self.assertEqual(runner.results[1]['error'], 'transport write failed')
        self.assertEqual(runner.results[2]['status'], 'passed')

    def test_fail_safe_timeout_advances_and_late_io_is_attached_to_its_own_row(self):
        future = Future()
        future.set_running_or_notify_cancel()
        future.dispatch_state = {'sent_s': .2}
        requests = Mock(side_effect=[future, None])
        runner = self.runner([pressure()], [command('solenoid1', 'closed'), command()], requests)
        runner.on_sample(evidence(stepper_pressure1_reading=11), .1)
        runner.on_sample(evidence(), .2)
        runner.tick(1.2)
        self.assertEqual(runner.results[1]['reason'], 'timeout')
        self.assertEqual(runner.current_index, 1)
        future.set_exception(OSError('late acknowledgement failure'))
        runner.on_sample(evidence(), 1.3)
        runner.on_sample(evidence(), 1.4)
        self.assertEqual(runner.state, 'failed')
        self.assertEqual(runner.results[1]['request_error'], 'late acknowledgement failure')
        self.assertEqual(runner.results[2]['status'], 'passed')
        self.assertNotIn('request_error', runner.results[2])

    def test_unhealthy_fail_safe_requirement_does_not_block_later_local_action(self):
        request = Mock()
        shutdown = [command(), dict(action='requested_state', source='recording', set_point='off', timeout_s=1)]
        runner = self.runner([pressure()], shutdown, request)
        runner.on_sample(evidence(stepper_pressure1_reading=11), .1)
        values = evidence(stepper_age_ms=2000, stepper_transport_error='reset')
        runner.on_sample(values, .2)
        runner.on_sample(values, .3)
        runner.on_sample(values, .4, {'recording': False})
        self.assertEqual(runner.state, 'failed')
        self.assertEqual(runner.results[1]['reason'], 'command_requirements_failed')
        self.assertEqual(runner.results[2]['status'], 'passed')
        request.assert_called_once()

    def test_persistent_monitor_stops_main_during_in_flight_motion_without_implicit_abort(self):
        pending = Future()
        pending.set_running_or_notify_cancel()
        pending.dispatch_state = {'sent_s': .2}
        request, abort = Mock(side_effect=[pending, None]), Mock()
        main = [pressure(set_point='on'), command(set_point='move', speed_mm_s=1, duration_s=10, direction='forward'), off()]
        runner = self.runner(main, [command()], request, abort)
        runner.on_sample(evidence(), .1)
        runner.on_sample(evidence(), .2)
        runner.on_sample(evidence(stepper_pressure1_reading=11), .3)
        self.assertEqual(runner.sequence, 'fail_safe')
        self.assertFalse(runner.monitors['controllino/pressure1']['active'])
        runner.on_sample(evidence(stepper_pressure1_reading=11), .4)
        pending.set_result({'command_id': 'late-move'})
        runner.on_sample(evidence(stepper_pressure1_reading=11), .5)
        self.assertEqual(runner.state, 'failed')
        self.assertEqual(runner.results[1]['request']['command_id'], 'late-move')
        self.assertTrue(runner.results[1]['request_finished_after_failure'])
        self.assertEqual(runner.results[2]['status'], 'passed')
        abort.assert_not_called()

    def test_runtime_shutdown_does_not_start_fail_safe(self):
        runner = self.runner([pressure()], [command()], Mock())
        runner.fail('runtime_stopped', .1, run_fail_safe=False)
        self.assertEqual(runner.state, 'failed')
        self.assertEqual(runner.sequence, 'main')
        self.assertFalse(any(e['event'] == 'fail_safe_started' for e in runner.events))

    def test_pressure_reading_uses_only_selected_requirements_and_finite_bounds(self):
        action = parse_program(worksheet([pressure()]))[0]
        self.assertIsInstance(action, PressureReadingAction)
        for value in (0, 5, 10):
            self.assertTrue(action.evaluate(evidence(stepper_pressure1_reading=value,
                stepper_age_ms=9999, stepper_transport_error='reset'))['pressure']['ok'])
        for value in (None, float('nan'), float('inf'), True, '5', -1, 11):
            self.assertFalse(action.evaluate(evidence(stepper_pressure1_reading=value))['pressure']['ok'])
        action = parse_program(worksheet([pressure(max_age_ms=100, health='ok')]))[0]
        checks = action.evaluate(evidence(stepper_pressure_sample_age_ms=101))
        self.assertFalse(checks['pressure_freshness']['ok'])
        self.assertTrue(checks['freshness']['ok'])
        self.assertTrue(checks['health']['ok'])

    def test_persistent_pressure_freshness_is_activation_only_and_multiple_sensors_are_independent(self):
        main = [pressure(set_point='on', max_age_ms=100),
                pressure(device='AI11', set_point='on'), dict(action='wait', duration_s=10),
                off(), dict(off(), device='AI11')]
        runner = self.runner(main, [command()], Mock())
        runner.on_sample(evidence(stepper_pressure2_reading=5), .1)
        runner.on_sample(evidence(stepper_pressure2_reading=5), .2)
        runner.on_sample(evidence(stepper_pressure2_reading=5, stepper_age_ms=9000,
                                  stepper_pressure_sample_age_ms=9000), 2)
        self.assertEqual(runner.sequence, 'main')
        self.assertEqual(len(runner.monitors), 2)
        runner.on_sample(evidence(stepper_pressure2_reading=11), 3)
        self.assertEqual(runner.sequence, 'fail_safe')
        self.assertEqual(runner.failure['monitor_device'], 'AI11')

    def test_aliases_cannot_enable_duplicate_monitors_and_units_are_explicit(self):
        for rows in ([pressure(set_point='on'), pressure(device='pressure1', set_point='on'), off()],
                     [pressure(set_point='on')], [off()], [pressure(min_psi=0)],
                     [pressure(device='AI0')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                parse_program(worksheet(rows))
        high, = parse_program(worksheet([dict(action='reading_state', source='controllino', device='AI0',
                                               timeout_s=1, min_psi=0, max_psi=100)]))
        self.assertEqual(high.as_dict()['max_psi'], 100)


class FailSafeRuntimeTests(unittest.TestCase):
    runtime = boundary.BackendTests.runtime

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stepper = CanController()
        self.stepper.enable_manual()
        self.stepper.payload.update(fw='1.6.4', cmax=793, crmax=80, crdefault=80, cstop=1,
                                    sol5=0, sol6=0, adc=[100] * 9, adcage=10, adcseq=1)
        self.stepper.refresh()
        self.esp32 = core.SimulatedEsp32Source(auto_sequence=False)
        self.sources = [self.esp32, core.SimulatedDxmr90Source(), self.stepper]
        self.config_path = Path(self.directory.name) / 'system.json'
        config = deepcopy(DEFAULT_SYSTEM_CONFIG)
        config['pressure_inputs']['1'].update(volts_per_count=.01, calibration=dict(
            voltage_min=0, voltage_max=10, pressure_min=0, pressure_max=20, unit='mbar', reference='differential'))
        self.config_path.write_text(json.dumps(config))

    def setup_experiment(self):
        runtime = self.runtime()
        main = [dict(action='requested_state', source='recording', set_point='on', timeout_s=1),
                command('can_motor', 'speed', rpm=250),
                command('solenoid1;solenoid2;solenoid3', 'open'), pressure(set_point='on'),
                command(set_point='move', speed_mm_s=1, duration_s=100, direction='forward'),
                off(), command('can_motor', 'speed', rpm=0),
                dict(action='requested_state', source='recording', set_point='off', timeout_s=1)]
        shutdown = [command('solenoid1;solenoid2;solenoid3', 'closed'), command(),
                    command('can_motor', 'speed', rpm=0),
                    dict(action='requested_state', source='recording', set_point='off', timeout_s=1)]
        runtime.load_experiment({'csv': worksheet(main, shutdown)})
        runtime.start_experiment({})
        return runtime

    def poll(self, runtime, elapsed):
        self.stepper.refresh()
        with runtime._condition:
            runtime._poll_locked(elapsed)

    def test_backend_loads_both_fields_and_preserves_program_on_invalid_fail_safe(self):
        runtime = self.runtime()
        state = runtime.load_experiment({'csv': sequence_csv([pressure()]),
                                          'fail_safe_csv': sequence_csv([command()])})
        self.assertEqual(len(state['program']), 1)
        self.assertEqual(len(state['fail_safe_program']), 1)
        self.assertEqual(state['csv_input_fields'], ['csv', 'fail_safe_csv'])
        with self.assertRaisesRegex(ValueError, 'Fail safe:'):
            runtime.load_experiment({'csv': sequence_csv([pressure()]),
                                     'fail_safe_csv': sequence_csv([command(duration_s=1)])})
        self.assertEqual(runtime.experiment_state(), state)

    def test_pressure_trip_closes_then_halts_then_ramps_and_records_valve_failure(self):
        runtime = self.setup_experiment()
        for index in range(1, 20):
            self.poll(runtime, index / 10)
            if self.stepper.payload['mv']:
                break
        self.assertTrue(self.stepper.payload['mv'])
        self.assertEqual(self.stepper.payload['cr'], 250)
        before = len(self.stepper.commands)
        original_write = self.stepper._write_command
        def write(command, description):
            if command == b'V1 L5,0\n':
                self.stepper.commands.append(command)
                raise OSError('solenoid 1 write rejected')
            return original_write(command, description)
        self.stepper._write_command = write
        self.stepper.payload['adc'][0] = 1000
        self.poll(runtime, 2)
        self.assertEqual(runtime.experiment.sequence, 'fail_safe')
        self.assertTrue(self.stepper.payload['mv'])  # No hidden stepper stop before valve rows.
        runtime._renew_cubemars_lease()
        self.assertTrue(self.stepper.commands[-1].startswith(b'V1 K'))
        stop_at = None
        for index in range(21, 45):
            elapsed = index / 10
            if self.stepper.payload['cr'] == 0:
                stop_at = stop_at if stop_at is not None else elapsed
                if elapsed - stop_at >= .3:
                    self.stepper.payload.update(ce=0, cm=0, cap=0, cramp=0, cd=0, cx='operator_stop')
            self.poll(runtime, elapsed)
            if runtime.experiment.state != 'running':
                break
        wires = [wire for wire in self.stepper.commands[before:] if not wire.startswith(b'V1 K')]
        self.assertEqual(wires[:4], [b'V1 L5,0\n', b'V1 L6,0\n', b'V1 L4,0\n', b'V1 X\n'])
        self.assertTrue(wires[4].startswith(b'V1 U0,80,'))
        self.assertNotIn(b'V1 C0\n', wires)
        self.assertFalse(self.stepper.payload['mv'])
        self.assertFalse(runtime.recording)
        self.assertEqual(runtime.experiment.state, 'failed')
        self.assertEqual(runtime.experiment.failure['monitor_checks']['pressure']['observed'], 20)
        results = runtime.experiment.results
        self.assertEqual([r['status'] for r in results if r['sequence'] == 'fail_safe'],
                         ['failed', 'passed', 'passed', 'passed'])
        self.assertIn('solenoid 1 write rejected', results[-4]['error'])

    def test_in_flight_main_dispatch_finishes_before_any_fail_safe_wire_write(self):
        runtime = self.setup_experiment()
        entered, release = threading.Event(), threading.Event()
        original_write = self.stepper._write_command
        def write(command, description):
            if command.startswith(b'V1 G'):
                entered.set()
                if not release.wait(2):
                    raise TimeoutError('test did not release move')
            return original_write(command, description)
        self.stepper._write_command = write
        try:
            # Use the real command queue for dispatch/monitor independence.
            runtime._experiment_executor = ThreadPoolExecutor(max_workers=1)
            for index in range(1, 60):
                self.poll(runtime, index / 100)
                if entered.wait(.005):
                    break
            self.assertTrue(entered.is_set())
            self.stepper.payload['adc'][0] = 1000
            self.poll(runtime, 1)
            self.poll(runtime, 1.1)  # Queue first fail-safe row behind the in-flight move.
            self.assertEqual(runtime.experiment.sequence, 'fail_safe')
            self.assertTrue(runtime.experiment.results[-1]['request_pending'])
            self.assertNotIn(b'V1 X\n', self.stepper.commands)
            self.assertNotIn(b'V1 L5,0\n', self.stepper.commands)
        finally:
            release.set()
            runtime._experiment_executor.shutdown(wait=True)
        self.assertTrue(any(w.startswith(b'V1 G') for w in self.stepper.commands))
        move_index = next(i for i, w in enumerate(self.stepper.commands) if w.startswith(b'V1 G'))
        valve_index = self.stepper.commands.index(b'V1 L5,0\n')
        self.assertLess(move_index, valve_index)
        runtime.experiment.on_io_event(1.2)
        move_result = next(r for r in runtime.experiment.results if r.get('request_finished_after_failure'))
        self.assertEqual(move_result['sequence'], 'main')
