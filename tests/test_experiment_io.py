"""Real worker isolation with deliberately blocked command transports."""
import threading
import csv
import io
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import Mock

import test_backend_boundary as backend
from test_dro_monitor import program
from test_experiment import healthy_sample
from test_motion_readings import sample
from dashboard_app.experiment import ExperimentRunner


class ExperimentIoTests(unittest.TestCase):
    setUp = backend.BackendTests.setUp

    def runtime(self):
        runtime = backend.BackendTests.runtime(self)
        runtime._experiment_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='test-experiment-io')
        self.stepper.payload.update(dc=1, df=1, dr=5000, dd=0, da=0, dq=1, dx=0)
        self.stepper.refresh()
        runtime.load_experiment({'csv': program(1, 3, 5, 6)})
        runtime.start_experiment({})
        self.poll(runtime, .1)
        self.poll(runtime, .2)
        return runtime

    def poll(self, runtime, time):
        # A separate thread lets a regression fail with a bounded wait rather
        # than hanging the test inside a condition held by blocked command I/O.
        done = threading.Event()
        errors = []
        def run():
            try:
                with runtime._condition:
                    runtime._poll_locked(time)
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(done.wait(1), 'telemetry poll blocked behind command I/O')
        if errors:
            raise errors[0]

    def blocked_move(self):
        started, release = threading.Event(), threading.Event()
        original = self.stepper._write_command
        def send(command, description):
            if command.startswith(b'V1 G'):
                started.set()
                if not release.wait(3):
                    raise RuntimeError('test command release timed out')
            original(command, description)
        self.stepper._write_command = send
        self.addCleanup(release.set)
        return started, release

    def test_monitor_fails_while_move_dispatch_is_blocked_then_stop_is_ordered(self):
        runtime = self.runtime()
        started, release = self.blocked_move()
        self.poll(runtime, .3)
        self.assertTrue(started.wait(1))
        self.assertTrue(runtime.experiment_state()['results'][-1]['request_pending'])
        self.stepper.payload['dr'] = 10100
        self.stepper.refresh()
        self.poll(runtime, .4)
        result = runtime.experiment_state()['results'][-1]
        self.assertEqual(result['reason'], 'persistent_reading_check_failed')
        self.assertTrue(result['stop_pending'])
        self.assertNotIn('stop_requested', result)
        self.assertEqual(result['monitor_checks']['position']['observed'], 101)
        for call in (lambda: runtime.start_experiment({}),
                     lambda: runtime.load_experiment({'csv': program(1)})):
            with self.assertRaisesRegex(RuntimeError, 'still in flight'):
                call()
        stop = runtime.experiment._pending_stop
        release.set()
        stop.result(timeout=1)
        self.poll(runtime, .5)
        result = runtime.experiment_state()['results'][-1]
        self.assertTrue(result['stop_requested'])
        self.assertFalse(result['stop_pending'])
        self.assertTrue(result['request_finished_after_failure'])
        self.assertTrue(self.stepper.commands[0].startswith(b'V1 G'))
        self.assertEqual(self.stepper.commands[1:], [b'V1 X\n'])
        self.assertFalse(self.stepper.status()['stepper_moving'])
        self.assertEqual(runtime.experiment_state()['state'], 'failed')

    def test_samples_continue_and_acknowledgement_is_not_motion_completion(self):
        runtime = self.runtime()
        started, release = self.blocked_move()
        self.poll(runtime, .3)
        self.assertTrue(started.wait(1))
        future = runtime.experiment._pending_request
        sequence = runtime.sequence
        self.stepper.payload['dr'] = 6000
        self.stepper.refresh()
        self.poll(runtime, .4)
        self.assertGreater(runtime.sequence, sequence)
        self.assertFalse(future.done())
        monitor = runtime.experiment_state()['monitors']['controllino/dro']
        self.assertEqual(monitor['checks']['position']['observed'], 60)
        self.assertEqual(runtime.experiment_state()['current_row'], 3)
        release.set()
        future.result(timeout=1)
        self.poll(runtime, .5) # consume completion event
        self.assertTrue(runtime.experiment_state()['results'][-1]['requested'])
        self.assertEqual(runtime.experiment_state()['current_row'], 3)
        self.poll(runtime, .6) # telemetry says still moving
        self.assertEqual(runtime.experiment_state()['current_row'], 3)
        self.stepper.payload.update(mv=0, st=6, sps=0)
        self.stepper.refresh()
        self.poll(runtime, .7)
        self.assertEqual(runtime.experiment_state()['current_row'], 4)
        self.poll(runtime, .8)
        self.assertEqual(runtime.experiment_state()['state'], 'completed')

    def test_stop_io_does_not_block_failure_reporting_and_errors_are_retained(self):
        runtime = self.runtime()
        original = self.stepper._write_command
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def send(command, description):
            if command == b'V1 X\n':
                started.set()
                release.wait(3)
                raise RuntimeError('stop transport unavailable')
            original(command, description)
        self.stepper._write_command = send
        self.poll(runtime, .3)
        pending = runtime.experiment._pending_request
        if pending is not None:
            pending.result(timeout=1)
        self.poll(runtime, .4)
        self.stepper.payload['dr'] = 10100
        self.stepper.refresh()
        self.poll(runtime, .5)
        self.assertTrue(started.wait(1))
        self.assertEqual(runtime.experiment_state()['state'], 'failed')
        stop = runtime.experiment._pending_stop
        self.poll(runtime, .6)
        self.assertTrue(runtime.experiment_state()['results'][-1]['stop_pending'])
        release.set()
        with self.assertRaisesRegex(RuntimeError, 'stop transport unavailable'):
            stop.result(timeout=1)
        self.poll(runtime, .7)
        result = runtime.experiment_state()['results'][-1]
        self.assertFalse(result['stop_pending'])
        self.assertEqual(result['stop_error'], 'stop transport unavailable')

    def test_queued_command_is_cancelled_and_not_retried_on_failure(self):
        future = Future()
        request = Mock(return_value=future)
        stop = Mock()
        runner = ExperimentRunner(request_state=request, abort_state=stop)
        runner.load(program(1, 3, 5, 6), 'queued.csv', 0)
        runner.start(0)
        runner.on_sample(healthy_sample(), .1)
        runner.on_sample(sample(), .2)
        runner.on_sample(sample(), .3)
        values = sample()
        values['stepper_dro_position_mm'] = 101
        runner.on_sample(values, .4)
        self.assertTrue(future.cancelled())
        self.assertTrue(runner.snapshot()['results'][-1]['request_cancelled'])
        request.assert_called_once()
        stop.assert_called_once()
        runner.on_sample(values, .5)
        request.assert_called_once()

    def test_manual_command_transport_releases_monitoring_lock(self):
        runtime = self.runtime()
        started, release = threading.Event(), threading.Event()
        original = self.stepper._write_command
        self.addCleanup(release.set)
        def send(command, description):
            if command.startswith(b'V1 S'):
                started.set()
                release.wait(3)
            original(command, description)
        self.stepper._write_command = send
        errors = []
        def manual():
            try:
                runtime.set_stepper_speed({'speed_mm_s': 1})
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=manual, daemon=True)
        thread.start()
        self.assertTrue(started.wait(1))
        self.stepper.payload['dr'] = 10100
        self.stepper.refresh()
        self.poll(runtime, .3)
        self.assertEqual(runtime.experiment_state()['state'], 'failed')
        release.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_monitor_failure_cancels_remaining_group_targets(self):
        runtime = self.runtime()
        # Replace the already-loaded program before starting its dispatch.
        runtime.experiment.fail('test_reset', .2)
        rows = list(csv.DictReader(io.StringIO(program(1, 3, 5, 6))))
        rows[2].update(source='esp32', set_point='open', device='solenoid1;solenoid2;solenoid3',
                       speed_mm_s='', duration_s='', direction='')
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
        text = buffer.getvalue()
        runtime.load_experiment({'csv': text})
        runtime.experiment.start(0)
        self.poll(runtime, .1)
        self.poll(runtime, .2)
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = runtime.devices.solenoid_state
        targets = []
        def send(index, *args):
            targets.append(index)
            started.set()
            release.wait(3)
            return original(index, *args)
        runtime.devices.solenoid_state = send
        self.poll(runtime, .3)
        self.assertTrue(started.wait(1))
        future = runtime.experiment._pending_request
        self.stepper.payload['dr'] = 10100
        self.stepper.refresh()
        self.poll(runtime, .4)
        self.assertEqual(runtime.experiment_state()['state'], 'failed')
        release.set()
        future.result(timeout=1)
        self.poll(runtime, .5)
        self.assertEqual(targets, [0])
        results = runtime.experiment_state()['results'][-1]['request']['targets']
        self.assertEqual(results['solenoid1']['status'], 'requested')
        self.assertEqual(results['solenoid2']['status'], 'failed')
        self.assertEqual(results['solenoid3']['status'], 'failed')
