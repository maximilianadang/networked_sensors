"""Persistent transport against a local simulated controller; no hardware I/O."""
import json
import socket
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

import supervisor_core as core
from test_backend_boundary import Controller


def wait_for(predicate, timeout=2):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('Timed out waiting for simulated controller')


class FirmwarePeer:
    def __init__(self, *, drop_ack=False, delay_ack=0, wrong_ack=False, reject=False,
                 disconnect=False, fragment=False, stream_capable=True):
        self.drop_ack, self.delay_ack, self.wrong_ack = drop_ack, delay_ack, wrong_ack
        self.reject, self.disconnect, self.fragment = reject, disconnect, fragment
        self.payload = dict(Controller().payload, fw='1.6.0', m=0, st=0, sol5=0, sol6=0)
        if stream_capable:
            self.payload['net'] = 'tcp'
        self.requests = []
        self.connections = 0
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen()
        self.listener.settimeout(.1)
        self.url = f'tcp://127.0.0.1:{self.listener.getsockname()[1]}'
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def send(self, connection, payload):
        frame = (json.dumps(payload)+'\n').encode()
        if self.fragment:
            connection.sendall(frame[:11])
            connection.sendall(frame[11:79])
            frame = frame[79:]
        connection.sendall(frame)

    def run(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.connections += 1
            connection.settimeout(.01)
            buffer = b''
            replies = []
            next_status = 0
            try:
                while not self.stopped.is_set():
                    now = time.monotonic()
                    if now >= next_status:
                        self.send(connection, self.payload)
                        next_status = now + .025
                    for due, ack in list(replies):
                        if now >= due:
                            self.send(connection, ack)
                            replies.remove((due, ack))
                    try:
                        data = connection.recv(4096)
                        if not data:
                            break
                        buffer += data
                    except socket.timeout:
                        continue
                    while b'\n' in buffer:
                        raw, buffer = buffer.split(b'\n', 1)
                        identity, command = raw.decode().split(' ', 1)
                        identity = int(identity[1:])
                        self.requests.append((identity, command))
                        rejected = self.reject(command) if callable(self.reject) else self.reject
                        if not rejected:
                            if command in ('V1 M0', 'V1 M1'):
                                self.payload.update(m=int(command[-1]), st=5 if command[-1]=='1' else 0)
                            elif command.startswith('V1 L'):
                                target, state = command[4:].split(',')
                                self.payload['sol'+target] = int(state)
                            elif command.startswith('V1 G'):
                                delta, speed, identity_move = map(int, command[4:].split(','))
                                self.payload.update(c=identity_move, g=delta, csps=speed, mv=1, st=4,
                                                    sps=speed if delta > 0 else -speed)
                        if self.disconnect:
                            connection.close()
                            break
                        ack = dict(v=1, t='a', id=identity, ok=int(not rejected), e='busy' if rejected else 'none')
                        if self.wrong_ack:
                            self.send(connection, dict(ack, id=identity+10000))
                        if not self.drop_ack:
                            replies.append((now+self.delay_ack, ack))
                    if self.disconnect and self.requests:
                        break
            except OSError:
                pass
            finally:
                connection.close()

    def close(self):
        self.stopped.set()
        self.listener.close()
        self.thread.join(timeout=1)


class TcpStepperTests(unittest.TestCase):
    def setup_source(self, **options):
        peer = FirmwarePeer(**options)
        source = core.ControllinoStepperSource(peer.url, timeout=.6)
        self.addCleanup(peer.close)
        self.addCleanup(source.close)
        source.poll(0)
        wait_for(source._transport_connected)
        return source, peer

    def test_persistent_stream_reuses_connection_and_serial_command_semantics(self):
        source, peer = self.setup_source(fragment=True)
        for enabled in (True, False, True):
            source.set_control_mode(enabled)
            wait_for(lambda: source.status()['stepper_control_mode']==('web_position' if enabled else 'local_velocity'))
        source.set_solenoid(0, True)
        wait_for(lambda: source.status()['stepper_solenoid1_on'])
        self.assertEqual(peer.connections, 1)
        self.assertEqual([command for _, command in peer.requests], ['V1 M1','V1 M0','V1 M1','V1 L5,1'])
        self.assertEqual(len({identity for identity, _ in peer.requests}), 4)
        self.assertIsNone(source.status()['stepper_transport_error'])

    def test_reader_continues_while_ack_is_delayed_and_wrong_ids_are_ignored(self):
        source, peer = self.setup_source(delay_ack=.2, wrong_ack=True)
        before = source.status()['stepper_status_sequence']
        start = time.monotonic()
        source.set_control_mode(True)
        self.assertGreaterEqual(time.monotonic()-start, .19)
        self.assertGreater(source.status()['stepper_status_sequence'], before+2)
        self.assertEqual(len(peer.requests), 1)

    def test_send_observer_fires_after_complete_write_before_ack(self):
        source, peer = self.setup_source(delay_ack=.2)
        events = []
        with core.observe_command_sends(lambda: events.append((time.monotonic(), len(peer.requests)))):
            start = time.monotonic()
            source.set_control_mode(True)
        self.assertEqual(len(events), 1)
        self.assertLess(events[0][0]-start, .15)

    def test_move_receipt_keeps_identity_when_telemetry_precedes_ack(self):
        source, _ = self.setup_source(delay_ack=.15)
        source.set_control_mode(True)
        wait_for(lambda: source.status()['stepper_control_mode']=='web_position')
        controls = core.DeviceControls([source])
        receipt = controls.move(controls.status(), dict(distance_mm=1, speed_mm_s=1,
                                                        direction='forward', command_id='test-move'))
        self.assertIsNone(source.pending_command_id)
        self.assertTrue(receipt.confirmed(controls.status()))
        self.assertEqual(source.status()['stepper_command_id'], 'test-move')

    def test_lost_ack_is_unknown_and_does_not_discard_confirmed_actual_state(self):
        source, peer = self.setup_source(drop_ack=True)
        with self.assertRaises(core.CommandNotConfirmedError):
            source.set_control_mode(True)
        self.assertEqual(source.status()['stepper_control_mode'], 'web_position')
        self.assertTrue(source._transport_connected())
        self.assertIsNone(source.pending_command_error)
        self.assertEqual(len(peer.requests), 1)

    def test_explicit_rejection_preserves_mode(self):
        source, _ = self.setup_source(reject=True)
        with self.assertRaisesRegex(RuntimeError, 'rejected command: busy'):
            source.set_control_mode(True)
        self.assertEqual(source.status()['stepper_control_mode'], 'local_velocity')
        self.assertIsNone(source.status()['stepper_transport_error'])

    def test_rejected_request_does_not_poison_an_accepted_state_receipt(self):
        source, _ = self.setup_source(delay_ack=.1, reject=lambda command: command=='V1 M0')
        controls = core.DeviceControls([source])
        accepted = controls.control_mode(controls.status(), {'web_position':True})
        with self.assertRaisesRegex(RuntimeError, 'rejected command: busy'):
            controls.control_mode(controls.status(), {'web_position':False})
        self.assertTrue(accepted.confirmed(controls.status()))

    def test_disconnect_fails_inflight_command_and_never_replays_it(self):
        source, peer = self.setup_source(disconnect=True)
        with self.assertRaises(core.CommandNotConfirmedError):
            source.set_control_mode(True)
        wait_for(lambda: peer.connections >= 2)
        wait_for(source._transport_connected)
        time.sleep(.08)
        self.assertEqual(len(peer.requests), 1)

    def test_disconnect_retains_measured_values_without_authorizing_commands(self):
        source, peer = self.setup_source()
        peer.payload.update(dc=1, df=1, dr=1234, dd=10, da=1, dq=2, dkind='linace')
        wait_for(lambda: source.status()['stepper_dro_position_mm'] is not None)
        position = source.status()['stepper_dro_position_mm']
        peer.close()
        wait_for(lambda: not source._transport_connected())
        self.assertEqual(source.status()['stepper_dro_position_mm'], position)
        self.assertIsNotNone(source.status()['stepper_transport_error'])
        with self.assertRaisesRegex(RuntimeError, 'not connected'):
            source.set_control_mode(True)

    def test_queue_is_bounded_and_shutdown_releases_pending_callers(self):
        source, peer = self.setup_source(drop_ack=True)
        source.MAX_PENDING = 2
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(source.set_control_mode, True)
            second = executor.submit(source.set_control_mode, True)
            wait_for(lambda: len(peer.requests)==2)
            with self.assertRaisesRegex(RuntimeError, 'queue is full'):
                source.set_control_mode(True)
            source.close()
            for future in (first,second):
                with self.assertRaises(core.CommandNotConfirmedError):
                    future.result(timeout=.3)
        self.assertEqual(len(peer.requests),2)

    def test_concurrent_commands_match_their_own_acceptance_or_rejection(self):
        source, peer = self.setup_source(reject=lambda command: command=='V1 M0')

        def requests(enabled):
            for _ in range(20):
                if enabled:
                    source.set_control_mode(True)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'rejected command: busy'):
                        source.set_control_mode(False)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(requests, enabled) for enabled in (True, False, True, False)]
            for future in futures:
                future.result(timeout=5)
        self.assertEqual(len(peer.requests), 80)
        self.assertEqual(len({identity for identity, _ in peer.requests}), 80)
        self.assertEqual(peer.connections, 1)

    def test_old_firmware_is_identified_without_sending_commands(self):
        peer = FirmwarePeer(stream_capable=False)
        source = core.ControllinoStepperSource(peer.url, timeout=.5)
        self.addCleanup(peer.close); self.addCleanup(source.close)
        source.poll(0)
        wait_for(lambda: 'requires Controllino firmware' in str(source.last_error))
        self.assertFalse(source._transport_connected())
        self.assertEqual(peer.requests, [])

    def test_address_validation_and_legacy_address_alias(self):
        source = core.ControllinoStepperSource('http://10.77.0.10')
        self.assertEqual(source.address, ('10.77.0.10',80))
        source.close()
        for address in ('https://host','tcp://host/status','tcp://host?x=y','tcp://user:secret@host',
                        'tcp://host:0','tcp://host:65536','host'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                core.ControllinoStepperSource(address)
