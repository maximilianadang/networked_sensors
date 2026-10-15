"""Feedback deadlines start at transmission, excluding queue/send preparation."""
from concurrent.futures import Future, ThreadPoolExecutor
import threading
import unittest
from unittest.mock import Mock
from dashboard_app.experiment import ExperimentRunner, parse_program
from test_optional_reading_health import worksheet
import supervisor_core as core
import test_backend_boundary as boundary


def valve():
    return dict(action='requested_state', source='controllino', device='solenoid1',
                max_age_ms=500, health='ok', timeout_s=5, set_point='open')


def sample(**changes):
    data = dict(stepper_age_ms=0, stepper_transport_error=None, stepper_solenoid1_on=False)
    data.update(changes)
    return data


class FeedbackTimeoutTests(unittest.TestCase):
    def test_no_deadline_while_waiting_for_freshness_before_send(self):
        callback = Mock()
        runner = ExperimentRunner(request_state=callback)
        runner.load(worksheet(valve()), 'valve.csv', 0)
        runner.start(0)
        runner.on_sample(sample(stepper_age_ms=501), 100)
        runner.tick(1000)
        self.assertEqual(runner.state, 'running')
        self.assertIsNone(runner.snapshot()['deadline_s'])
        callback.assert_not_called()
        runner.on_sample(sample(), 1000)
        self.assertEqual(runner.snapshot()['deadline_s'], 1005)
        runner.on_sample(sample(stepper_solenoid1_on=True), 1000.1)
        self.assertEqual(runner.state, 'completed')

    def test_pending_http_response_is_timed_from_send_not_worker_completion(self):
        future = Future()
        future.set_running_or_notify_cancel()
        future.dispatch_state = {'sent_s': None}
        runner = ExperimentRunner(request_state=lambda a: future)
        runner.load(worksheet(valve()), 'valve.csv', 0)
        runner.start(0)
        runner.on_sample(sample(), .1)
        runner.tick(100)
        self.assertEqual(runner.state, 'running')
        self.assertIsNone(runner.snapshot()['deadline_s'])
        future.dispatch_state['sent_s'] = 100
        runner.tick(104.99)
        self.assertEqual(runner.snapshot()['deadline_s'], 105)
        runner.tick(105)
        self.assertEqual(runner.state, 'failed')
        self.assertFalse(future.done())
        self.assertEqual(runner.results[-1]['reason'], 'timeout')

    def test_slow_worker_has_full_feedback_window_after_transmission(self):
        future = Future()
        future.set_running_or_notify_cancel()
        future.dispatch_state = {'sent_s': None}
        runner = ExperimentRunner(request_state=lambda a: future)
        runner.load(worksheet(valve()), 'valve.csv', 0)
        runner.start(0)
        runner.on_sample(sample(), .1)
        runner.tick(200)
        future.dispatch_state['sent_s'] = 200
        future.set_result(None)
        runner.on_sample(sample(), 201)
        self.assertEqual(runner.snapshot()['deadline_s'], 205)
        runner.on_sample(sample(stepper_solenoid1_on=True), 204)
        self.assertEqual(runner.state, 'completed')

    def test_group_feedback_clock_starts_after_last_target_transmission(self):
        fixture = boundary.BackendTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        runtime = fixture.runtime()
        runtime._experiment_executor = ThreadPoolExecutor(max_workers=1)
        first_sent, continue_send = threading.Event(), threading.Event()
        def dispatch(action, sample):
            core._command_sent()
            first_sent.set()
            if not continue_send.wait(2):
                raise RuntimeError('test release was not delivered')
            core._command_sent()
        runtime._request_experiment_state = dispatch
        fields = valve()
        fields['device'] = 'solenoid1;solenoid2'
        action, = parse_program(worksheet(fields))
        future = runtime._submit_experiment_command(action)
        try:
            self.assertTrue(first_sent.wait(1))
            self.assertIsNone(future.dispatch_state['sent_s'])
            continue_send.set()
            future.result(timeout=1)
            self.assertIsNotNone(future.dispatch_state['sent_s'])
        finally:
            continue_send.set()

    def test_usb_only_marks_complete_writes(self):
        from unittest.mock import patch
        source = core.UsbStepperSource('/dev/null')
        for written, expected in ((5, 1), (4, 0)):
            source._fd = 99
            observer = Mock()
            with core.observe_command_sends(observer), patch.object(core.os, 'write', return_value=written), patch.object(core.os, 'close'):
                if written == 5:
                    source._write_command(b'V1 X\n', 'stop')
                else:
                    with self.assertRaises(RuntimeError): source._write_command(b'V1 X\n', 'stop')
                self.assertEqual(observer.call_count, expected)
            source._fd = None

    def test_transport_send_event_is_context_local(self):
        observer = Mock()
        with core.observe_command_sends(observer):
            thread = threading.Thread(target=core._command_sent)
            thread.start(); thread.join()
            observer.assert_not_called()
            core._command_sent()
        core._command_sent()
        observer.assert_called_once()

    def test_http_event_follows_complete_request_and_precedes_response_wait(self):
        events = []
        class Socket:
            def sendall(self, data): events.append('bytes sent')
            def close(self): pass
        connection = core._ObservedHTTPConnection('unused.invalid')
        connection.sock = Socket()
        try:
            with core.observe_command_sends(lambda: events.append('command sent')):
                connection.request('POST', '/command', body=b'V1 X')
                events.append('response can now be awaited')
            self.assertEqual(events, ['bytes sent', 'bytes sent', 'command sent', 'response can now be awaited'])
        finally: connection.close()

    def test_failed_http_transmission_does_not_start_feedback_timer(self):
        observer = Mock()
        class Socket:
            def sendall(self, data): raise OSError('send failed')
            def close(self): pass
        connection = core._ObservedHTTPConnection('unused.invalid')
        connection.sock = Socket()
        try:
            with core.observe_command_sends(observer), self.assertRaises(OSError):
                connection.request('POST', '/command', body=b'V1 X')
            observer.assert_not_called()
        finally: connection.close()
