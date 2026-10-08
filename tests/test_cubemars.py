"""CAN motor host contract tests. All controller and HTTP traffic stays in memory."""
import json
import threading
import time
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import supervisor_core as core
from dashboard_app.experiment import CSV_COLUMNS, ExperimentRunner, parse_program
from dashboard_app.http import build_handler
from dashboard_app.runtime import SourceMerger
import test_backend_boundary as boundary


class CanController(boundary.Controller):
    def __init__(self):
        super().__init__()
        for key in ('sv', 'sp', 'smin', 'smax', 'srel', 'sr'):
            self.payload.pop(key)
        self.payload.update(aux='cubemars', apin=9, cb=1, cid=104, cbr=500000,
                            cr=0, ce=0, ca=25, cv=-252, ci=-123, ct=28, cf=0,
                            cx='none', cd=0, cq=0, cmax=30, cdmax=5000, cstale=500)
        self.expire_immediately = False
        self.refresh()

    def enable_manual(self):
        self.payload.update(cm=0, cleasems=1500, cramp=0, cap=0, crmax=30, crdefault=30)
        self.refresh()

    def _write_command(self, command, description):
        if command.startswith((b'V1 M', b'V1 U', b'V1 K')) and 'cm' in self.payload and (b',' in command or command.startswith(b'V1 K')):
            self.commands.append(command)
            if self.apply:
                args = list(map(int, command[4:].strip().split(b',')))
                if command[3:4] != b'K':
                    rpm, ramp, token = args
                    self.payload.update(cr=rpm, cramp=ramp, cm=token, ce=1)
                    if command[3:4] == b'M':
                        self.payload['cap'] = self.payload['cv']
                self.payload.update(cd=1500, cq=(self.payload['cq'] + 1) & 0xFFFFFFFF)
                self.refresh()
        elif command.startswith(b'V1 C'):
            self.commands.append(command)
            if self.apply:
                args = list(map(int, command[4:].strip().split(b',')))
                rpm = args[0]
                self.payload.update(cr=rpm, cq=(self.payload['cq'] + 1) & 0xFFFFFFFF,
                                    ce=int(bool(rpm) and not self.expire_immediately),
                                    cd=args[1] if rpm and not self.expire_immediately else 0,
                                    cx='expired' if rpm and self.expire_immediately else 'none')
                if 'cm' in self.payload:
                    self.payload.update(cm=0, cramp=0, cap=rpm * 126 if self.payload['ce'] else 0)
                self.refresh()
        elif command == b'V1 E1\n':
            self.commands.append(command)
            self.payload.update(e=1, cr=0, ce=0, cd=0, mv=0, sol4=0)
            if 'cm' in self.payload:
                self.payload.update(cm=0, cramp=0, cap=0)
            self.refresh()
        else:
            super()._write_command(command, description)


class CubemarsTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stepper = CanController()
        self.esp32 = core.SimulatedEsp32Source(auto_sequence=False)
        self.sources = [self.esp32, core.SimulatedDxmr90Source(), self.stepper]
        self.controls = core.DeviceControls(self.sources)
        self.config_path = Path(self.directory.name) / 'system.json'

    runtime = boundary.BackendTests.runtime

    def current(self, **changes):
        return dict(self.stepper.status(), stepper_connected=True, stepper_age_ms=0, **changes)

    def worksheet(self, rpm='10', duration='1'):
        return ('action,source,device,max_age_ms,health,timeout_s,set_point,rpm,duration_s\n'
                f'requested_state,controllino,can_motor,500,ok,2,speed,{rpm},{duration}\n')

    def test_decoder_converts_actual_feedback_and_keeps_three_variants_exclusive(self):
        for source in (self.stepper, core.ControllinoUsbStepperSource('/dev/null')):
            value = source.decode_status_line(json.dumps(self.stepper.payload))
            self.assertTrue(value['stepper_can_motor_ready'])
            self.assertFalse(value['stepper_servo_capable'])
            self.assertFalse(value['stepper_brushless_motor_capable'])
            self.assertEqual(value['stepper_can_motor_rpm'], -2)
            self.assertEqual(value['stepper_can_motor_current_a'], -1.23)
            self.assertEqual(value['stepper_can_motor_target_rpm'], 0)
            self.assertEqual(value['stepper_can_motor_id'], 104)
            self.assertEqual(value['stepper_can_motor_bitrate'], 500000)
            self.assertEqual(value['stepper_can_motor_max_rpm'], 30)
            self.assertEqual(value['stepper_can_motor_max_duration_ms'], 5000)
            self.assertEqual(value['stepper_can_motor_stale_ms'], 500)
        servo = boundary.Controller()
        self.assertTrue(servo.status()['stepper_servo_capable'])
        self.assertFalse(servo.status()['stepper_can_motor_capable'])
        esc = dict(servo.payload, aux='esc', apin=12, bo=1, bp=1200)
        for key in ('sv', 'sp', 'smin', 'smax', 'srel', 'sr'):
            esc.pop(key)
        value = self.stepper.decode_status_line(json.dumps(esc))
        self.assertTrue(value['stepper_brushless_motor_capable'])
        self.assertFalse(value['stepper_can_motor_capable'])

    def test_decoder_rejects_malformed_or_ambiguous_motor_status(self):
        for change in ({'cv': 1.5}, {'ca': -2}, {'ci': 32768}, {'cf': float('nan')},
                       {'cmax': 794}, {'cdmax': 0}, {'ce': 1, 'e': 1}, {'sv': 0},
                       {'bo': 0}, {'cq': None}, {'aux': 'unknown'}, {'cx': []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.stepper.decode_status_line(json.dumps(dict(self.stepper.payload, **change)))
        value = self.stepper.decode_status_line(json.dumps(dict(self.stepper.payload, ca=-1)))
        self.assertFalse(value['stepper_can_motor_ready'])
        self.assertIsNone(value['stepper_can_motor_rpm'])
        self.assertIsNone(value['stepper_can_motor_current_a'])

    def test_manual_telemetry_requires_complete_schema_and_retains_old_firmware(self):
        self.assertFalse(self.current()['stepper_can_motor_manual_capable'])
        self.assertIsNone(self.current()['stepper_can_motor_manual_token'])
        self.stepper.enable_manual()
        self.stepper.payload.update(cm=123, ce=1, cr=10, cramp=3, cap=-63, cd=1500)
        self.stepper.refresh()
        current = self.current()
        for key, expected in (('manual_capable', True), ('manual_token', 123), ('lease_ms', 1500),
                              ('ramp_rpm_s', 3), ('applied_rpm', -0.5), ('max_ramp_rpm_s', 30),
                              ('default_ramp_rpm_s', 30)):
            self.assertEqual(current['stepper_can_motor_' + key], expected)
        self.assertEqual(current['stepper_can_motor_rpm'], -2)
        for key in ('cm', 'cleasems', 'cramp', 'cap', 'crmax', 'crdefault'):
            payload = dict(self.stepper.payload)
            payload.pop(key)
            with self.subTest(missing=key), self.assertRaises(ValueError):
                self.stepper.decode_status_line(json.dumps(payload))
        for change in ({'cm': True}, {'cm': 2147483648}, {'cleasems': 0}, {'cap': 100001},
                       {'crmax': 794}, {'cramp': 31}, {'cramp': 0}, {'ce': 0, 'cd': 0},
                       {'cm': 0}, {'cd': 1501}, {'crdefault': 0}, {'crdefault': 31},
                       {'crdefault': True}, {'crdefault': 1.5}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.stepper.decode_status_line(json.dumps(dict(self.stepper.payload, **change)))

    def test_optional_can_diagnostics_survive_normalization_and_history(self):
        old = self.current()
        self.assertIsNone(old['stepper_can_motor_diag_ef'])
        self.assertIsNone(old['stepper_can_motor_diag_sr'])
        live = {'ef': 11, 'tec': 8, 'rec': 128, 'rxfail': 3, 'rx': 99,
                'reg': '86F00003150B000F08800000000400', 'slow': '86F000031500000F',
                'id': 0x2968, 'ext': 1, 'rtr': 0, 'dlc': 8, 'data': '0000000000001D00'}
        self.stepper.payload['cdiag'] = live
        self.stepper.refresh()
        # Diagnostics report evidence without introducing another run policy.
        self.assertTrue(self.current()['stepper_can_motor_ready'])
        runtime = self.runtime()
        for key, expected in live.items():
            field = 'stepper_can_motor_diag_' + key
            self.assertIn(field, self.stepper.expected_fields)
            self.assertEqual(runtime.latest[field], expected)
            self.assertEqual(runtime.history[-1][field], expected)
        latched = dict(live, le=255, ls=11, lt=128, lr=255, lst=0, lsr=128,
                       let=0xFFFFFFFF, ec=3, sr='can_bus_error', se=255, ss=11,
                       st=128, srec=255, sst=0, ssrec=128, sms=123, sq=0xFFFFFFFF,
                       sc=2, mg=30000, sg=21000, spi=8000000)
        value = self.stepper.decode_status_line(json.dumps(dict(self.stepper.payload, cdiag=latched)))
        for key, expected in latched.items():
            self.assertEqual(value['stepper_can_motor_diag_' + key], expected)
        uninitialized = {'ef': -1, 'tec': -1, 'rec': -1, 'reg': '', 'slow': '', 'id': -1, 'data': '', 'sr': ''}
        value = self.stepper.decode_status_line(json.dumps(dict(self.stepper.payload, cdiag=uninitialized)))
        self.assertEqual(value['stepper_can_motor_diag_ef'], -1)
        self.assertEqual(value['stepper_can_motor_diag_sr'], '')
        self.assertIsNone(value['stepper_can_motor_diag_ec'])

    def test_can_diagnostics_validate_present_fields_and_allow_future_evidence(self):
        invalid = [None, [], 'invalid', {'ef': True}, {'tec': -2}, {'rec': 256},
                   {'rxfail': -1}, {'rx': 0x100000000}, {'reg': '00'}, {'slow': 'G' * 16},
                   {'data': '000'}, {'ext': 2}, {'dlc': 256}, {'id': 0x20000000},
                   {'le': -1}, {'ls': 256}, {'lst': 256}, {'ssrec': -1}, {'mg': 1.5},
                   {'let': 0x100000000}, {'sc': True}, {'sr': None}, {'sr': 'x' * 65}]
        for diagnostics in invalid:
            with self.subTest(diagnostics=diagnostics), self.assertRaises(ValueError):
                self.stepper.decode_status_line(json.dumps(dict(self.stepper.payload, cdiag=diagnostics)))
        value = self.stepper.decode_status_line(json.dumps(dict(self.stepper.payload, cdiag={'future': {'raw': 1}})))
        self.assertIsNone(value['stepper_can_motor_diag_ef'])

    def test_manual_commands_confirm_token_target_ramp_and_advancing_counter(self):
        self.stepper.enable_manual()
        self.stepper.payload['cq'] = 0xFFFFFFFF
        self.stepper.refresh()
        for values, command in (({'action': 'start', 'token': 7, 'rpm': 10, 'ramp_rpm_s': 3}, b'V1 M10,3,7\n'),
                                ({'action': 'update', 'token': 7, 'rpm': -20, 'ramp_rpm_s': 5}, b'V1 U-20,5,7\n'),
                                ({'action': 'renew', 'token': 7}, b'V1 K7\n')):
            before = self.current()
            receipt = self.controls.cubemars(before, values)
            self.assertEqual(self.stepper.commands[-1], command)
            self.assertTrue(receipt.confirmed(self.current()))
            self.assertFalse(receipt.confirmed(before))
            self.assertEqual(receipt.result['confirmation'], 'requested_state')
            changes = [('manual_token', 8), ('active', False), ('command_sequence', before['stepper_can_motor_command_sequence']),
                       ('command_sequence', (before['stepper_can_motor_command_sequence'] - 1) & 0xFFFFFFFF)]
            if values['action'] != 'renew':
                changes.extend((('target_rpm', 1), ('ramp_rpm_s', 1)))
            for key, value in changes:
                status = self.current()
                status['stepper_can_motor_' + key] = value
                with self.subTest(action=values['action'], field=key):
                    self.assertFalse(receipt.confirmed(status))
        self.assertEqual(len(self.stepper.commands), 3)

    def test_manual_validation_rejects_invalid_intent_without_sending(self):
        self.stepper.enable_manual()
        valid = {'action': 'start', 'token': 123, 'rpm': 10, 'ramp_rpm_s': 3}
        for change in ({'action': 'invalid'}, {'token': 0}, {'token': -1}, {'token': 2147483648},
                       {'token': True}, {'token': 1.5}, {'token': '123'}, {'rpm': 0}, {'rpm': 31},
                       {'rpm': -31}, {'rpm': True}, {'rpm': 0.5}, {'ramp_rpm_s': 0},
                       {'ramp_rpm_s': 31}, {'ramp_rpm_s': True}, {'ramp_rpm_s': 1.5},
                       {'duration_ms': 1000}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.controls.cubemars(self.current(), dict(valid, **change))
        for values in ({'action': 'renew', 'token': 123, 'rpm': None},
                       {'action': 'renew', 'token': 123, 'duration_ms': 1000},
                       {'action': 'start', 'token': 123, 'rpm': 10}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.controls.cubemars(self.current(), values)
        self.assertEqual(self.stepper.commands, [])
        self.stepper.payload.update(cmax=12, crmax=4, crdefault=4)
        self.stepper.refresh()
        self.controls.cubemars(self.current(), dict(valid, rpm=-12, ramp_rpm_s=4))
        self.assertEqual(self.stepper.commands, [b'V1 M-12,4,123\n'])

    def test_manual_uses_advertised_400_rpm_limit_and_fixed_80_rpm_s_default(self):
        self.stepper.enable_manual()
        self.stepper.payload.update(cmax=400, crmax=80, crdefault=80)
        self.stepper.refresh()
        current = self.current()
        self.assertEqual(current['stepper_can_motor_max_rpm'], 400)
        self.assertEqual(current['stepper_can_motor_max_ramp_rpm_s'], 80)
        self.assertEqual(current['stepper_can_motor_default_ramp_rpm_s'], 80)
        receipt = self.controls.cubemars(current, {
            'action': 'start', 'token': 123, 'rpm': 400,
            'ramp_rpm_s': current['stepper_can_motor_default_ramp_rpm_s'],
        })
        self.assertEqual(self.stepper.commands, [b'V1 M400,80,123\n'])
        self.assertTrue(receipt.confirmed(self.current()))
        for change in ({'rpm': 401}, {'ramp_rpm_s': 81}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.controls.cubemars(self.current(), dict(
                    {'action': 'update', 'token': 123, 'rpm': -400, 'ramp_rpm_s': 80}, **change))

    def test_manual_guards_legacy_firmware_health_ownership_and_expired_sessions(self):
        start = {'action': 'start', 'token': 123, 'rpm': 10, 'ramp_rpm_s': 3}
        with self.assertRaisesRegex(RuntimeError, 'does not support manual'):
            self.controls.cubemars(self.current(), start)
        self.stepper.enable_manual()
        original = dict(self.stepper.payload)
        for change in ({'ca': -1}, {'ca': 501}, {'cf': 1}, {'e': 1}, {'cb': 0}):
            self.stepper.payload = dict(original, **change)
            self.stepper.refresh()
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.controls.cubemars(self.current(), start)
        self.stepper.payload = original
        self.stepper.refresh()
        update = dict(start, action='update')
        renew = {'action': 'renew', 'token': 123}
        for values in (update, renew):
            with self.assertRaisesRegex(RuntimeError, 'inactive'):
                self.controls.cubemars(self.current(), values)
        self.controls.cubemars(self.current(), start)
        with self.assertRaisesRegex(RuntimeError, 'active'):
            self.controls.cubemars(self.current(), start)
        for values in (dict(update, token=124), dict(renew, token=124)):
            with self.assertRaisesRegex(RuntimeError, 'another token'):
                self.controls.cubemars(self.current(), values)
        self.stepper.payload.update(ca=-1, cf=7)
        self.stepper.refresh()
        self.controls.cubemars(self.current(), {'rpm': 0})
        self.stepper.payload.update(ca=25, cf=0)
        self.stepper.refresh()
        for values in (update, renew):
            with self.assertRaisesRegex(RuntimeError, 'inactive'):
                self.controls.cubemars(self.current(), values)
        self.assertEqual(self.stepper.commands, [b'V1 M10,3,123\n', b'V1 C0\n'])

    def test_manual_runtime_returns_confirmed_sample_without_polling_or_renewing(self):
        self.stepper.enable_manual()
        runtime = self.runtime()
        runtime.latest['unrelated_measurement'] = 1.25
        before = dict(runtime.latest)
        with patch.object(runtime, '_poll_locked', side_effect=AssertionError('unexpected sensor I/O')):
            result = runtime.set_cubemars_motor({'action': 'start', 'token': 123, 'rpm': 10, 'ramp_rpm_s': 3})
            self.assertTrue(result['confirmed'])
            self.assertEqual(result['sample']['unrelated_measurement'], 1.25)
            for key in ('manual_token', 'active', 'command_sequence', 'target_rpm', 'ramp_rpm_s'):
                self.assertEqual(result['sample']['stepper_can_motor_' + key], result['stepper']['stepper_can_motor_' + key])
            self.assertEqual(result['sample']['stepper_can_motor_manual_token'], 123)
            self.assertTrue(result['sample']['stepper_can_motor_active'])
            self.assertEqual(runtime.latest, before)
            self.assertEqual(result['motor_feedback']['rpm'], -2)
            self.assertEqual(result['motor_feedback']['applied_rpm'], -2)
            runtime.set_cubemars_motor({'action': 'renew', 'token': 123})
        for elapsed in (0.1, 0.2, 0.3, 0.4):
            with runtime._condition:
                runtime._poll_locked(elapsed)
        self.assertEqual(self.stepper.commands, [b'V1 M10,3,123\n', b'V1 K123\n'])

    def test_global_release_bypasses_pending_manual_confirmation_lock(self):
        self.stepper.enable_manual()
        runtime = self.runtime()
        self.controls.cubemars(self.current(), {'action': 'start', 'token': 123, 'rpm': 10, 'ramp_rpm_s': 3})
        finished = threading.Event()
        errors = []
        def release():
            try:
                runtime.set_cubemars_motor({'rpm': 0})
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()
        with runtime._cubemars_command_lock:
            thread = threading.Thread(target=release)
            thread.start()
            released = finished.wait(1)
        thread.join(2)
        self.assertTrue(released, 'release waited behind a pending manual command')
        self.assertEqual(errors, [])
        self.assertEqual(self.stepper.commands[-1], b'V1 C0\n')

    def test_signed_runs_and_release_encode_once_and_confirm_requested_state(self):
        for rpm in (10, -30, 30):
            receipt = self.controls.cubemars(self.current(), {'rpm': rpm, 'duration_ms': 20})
            self.assertEqual(self.stepper.commands[-1], f'V1 C{rpm},20\n'.encode())
            self.assertTrue(receipt.confirmed(self.current()))
            self.assertEqual(receipt.result['confirmation'], 'requested_state')
            self.assertEqual(self.stepper.status()['stepper_can_motor_rpm'], -2)
        for state in ({'ca': -1}, {'cf': 7}, {'e': 1}, {'cb': 0}):
            self.stepper.payload.update(cr=0, ce=0, **state)
            self.stepper.refresh()
            receipt = self.controls.cubemars(self.current(), {'rpm': 0, 'duration_ms': 'ignored'})
            self.assertEqual(self.stepper.commands[-1], b'V1 C0\n')
            self.assertTrue(receipt.confirmed(self.current()))

    def test_invalid_commands_do_not_send(self):
        for rpm, duration in ((31, 20), (-31, 20), (True, 20), (1.5, 20),
                              (float('nan'), 20), ('10', 20), (1, 19), (1, 5001),
                              (1, float('inf')), (1, True), (1, None)):
            with self.subTest(rpm=rpm, duration=duration), self.assertRaises(ValueError):
                self.controls.cubemars(self.current(), {'rpm': rpm, 'duration_ms': duration})
        self.assertEqual(self.stepper.commands, [])

    def test_fault_stale_estop_and_cached_status_guard_nonzero_runs(self):
        original = dict(self.stepper.payload)
        for change in ({'ca': -1}, {'ca': 501}, {'cf': 1}, {'e': 1}, {'cb': 0}):
            self.stepper.payload = dict(original, **change)
            self.stepper.refresh()
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.controls.cubemars(self.current(), {'rpm': 10, 'duration_ms': 1000})
        self.stepper.payload = dict(original, ca=400)
        self.stepper.refresh()
        current = self.current()
        current['stepper_age_ms'] = 101
        with self.assertRaises(RuntimeError):
            self.controls.cubemars(current, {'rpm': 10, 'duration_ms': 1000})
        self.stepper._cubemars_status_at = time.monotonic() - 1
        with self.assertRaises(RuntimeError):
            self.stepper.set_cubemars_speed(10, 1000)
        self.assertEqual(self.stepper.commands, [])
        # A historical reason is evidence, not a permanent software latch.
        self.stepper.payload = dict(original, cx='expired')
        self.stepper.refresh()
        self.controls.cubemars(self.current(), {'rpm': 10, 'duration_ms': 1000})
        self.assertEqual(len(self.stepper.commands), 1)

    def test_counter_confirms_short_expired_run_but_never_a_cached_echo(self):
        self.stepper.expire_immediately = True
        before = self.current()
        receipt = self.controls.cubemars(before, {'rpm': 10, 'duration_ms': 20})
        self.assertFalse(receipt.confirmed(before))
        current = self.current()
        self.assertFalse(current['stepper_can_motor_active'])
        self.assertTrue(receipt.confirmed(current))
        current['stepper_can_motor_command_sequence'] = before['stepper_can_motor_command_sequence']
        self.assertFalse(receipt.confirmed(current))
        self.stepper.pending_command_error = 'CAN transmit failed'
        with self.assertRaisesRegex(RuntimeError, 'rejected'):
            receipt.confirmed(self.current())

    def test_runtime_ack_and_estop_do_not_claim_measured_speed(self):
        runtime = self.runtime()
        self.stepper.expire_immediately = True
        result = runtime.set_cubemars_motor({'rpm': 10, 'duration_ms': 20})
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['requested_rpm'], 10)
        self.assertEqual(result['motor_feedback']['rpm'], -2)
        self.assertFalse(result['stepper']['stepper_can_motor_active'])
        runtime.emergency_stop_stepper()
        receipt = self.controls.emergency_stop(self.current())
        sample = self.current()
        sample['stepper_can_motor_active'] = True
        self.assertFalse(receipt.confirmed(sample))
        sample['stepper_can_motor_active'] = False
        self.assertTrue(receipt.confirmed(sample))

    def test_merger_ages_motor_feedback_even_when_source_transport_is_healthy(self):
        from datetime import datetime, timezone
        merger = SourceMerger([self.stepper], stale_after_s=5)
        sample = merger.poll(0, datetime.now(timezone.utc))
        self.assertTrue(sample['stepper_can_motor_ready'])
        self.stepper.emit = False
        sample = merger.poll(0.6, datetime.now(timezone.utc))
        self.assertTrue(sample['stepper_connected'])
        self.assertFalse(sample['stepper_can_motor_ready'])

    def test_csv_validates_limits_and_preserves_unified_schema(self):
        for rpm, duration in (('10', '0.02'), ('-30', '5'), ('31', '5.001'), ('1', '1.001'), ('0', '')):
            action, = parse_program(self.worksheet(rpm, duration))
            self.assertEqual(action.rpm, int(rpm))
            self.assertEqual(set(action.as_dict()), set(CSV_COLUMNS))
        for rpm, duration in (('794', '1'), ('NaN', '1'), ('1.5', '1'), ('10', '0.019'),
                              ('10', '2147483.648'), ('10', 'inf'), ('10', ''), ('10', '0.0201'),
                              ('1e999999999', '1'), ('10', '1e999999999')):
            with self.subTest(rpm=rpm, duration=duration), self.assertRaises(ValueError):
                parse_program(self.worksheet(rpm, duration))
        with self.assertRaises(ValueError):
            parse_program(self.worksheet().replace('can_motor', 'brushless_motor'))

    def test_csv_sends_once_and_uses_command_counter_not_speed_attainment(self):
        runtime = self.runtime()
        self.stepper.expire_immediately = True
        runtime.load_experiment({'csv': self.worksheet(duration='0.02')})
        runtime.start_experiment({})
        for elapsed in (0.1, 0.2, 0.3, 0.4):
            with runtime._condition:
                runtime._poll_locked(elapsed)
        self.assertEqual(runtime.experiment_state()['state'], 'completed')
        self.assertEqual(self.stepper.commands, [b'V1 C10,20\n'])
        check = runtime.experiment_state()['results'][-1]['checks']
        self.assertTrue(check['command_accepted']['ok'])
        self.assertEqual(check['requested_state']['measured_rpm'], -2)

    def test_advertised_firmware_limits_control_live_commands(self):
        for maximum, duration in ((12, 200), (60, 6000)):
            self.stepper.payload.update(cmax=maximum, cdmax=duration, cr=0, cd=0, ce=0, ca=600, cstale=1000)
            self.stepper.refresh()
            self.assertTrue(self.stepper.status()['stepper_can_motor_ready'])
            self.controls.cubemars(self.current(), {'rpm': maximum, 'duration_ms': duration})
            self.assertEqual(self.stepper.commands[-1], f'V1 C{maximum},{duration}\n'.encode())
            for rpm, ms in ((maximum + 1, duration), (maximum, duration + 1)):
                with self.assertRaises(ValueError):
                    self.controls.cubemars(self.current(), {'rpm': rpm, 'duration_ms': ms})
        for key in ('cmax', 'cdmax', 'cstale'):
            payload = dict(self.stepper.payload)
            payload.pop(key)
            with self.assertRaises(ValueError):
                self.stepper.decode_status_line(json.dumps(payload))

    def test_http_motor_route_validates_then_dispatches(self):
        runtime = self.runtime()
        handler = build_handler(runtime)
        def request(values):
            payload = json.dumps(values).encode()
            class Connection:
                response = bytearray()
                def makefile(self, *args):
                    return BytesIO(b'POST /api/stepper/motor/can HTTP/1.0\r\nContent-Type: application/json\r\n'
                                   + f'Content-Length: {len(payload)}\r\n\r\n'.encode() + payload)
                def sendall(self, data):
                    self.response.extend(data)
            connection = Connection()
            handler(connection, ('127.0.0.1', 0), None)
            headers, data = bytes(connection.response).split(b'\r\n\r\n', 1)
            return int(headers.split()[1]), json.loads(data)
        self.assertEqual(request({'rpm': 31, 'duration_ms': 1000})[0], 400)
        self.assertEqual(self.stepper.commands, [])
        code, result = request({'rpm': -10, 'duration_ms': 1000})
        self.assertEqual(code, 200)
        self.assertTrue(result['confirmed'])
        self.assertEqual(self.stepper.commands, [b'V1 C-10,1000\n'])
        self.assertEqual(request({'rpm': 0})[0], 200)
        self.stepper.enable_manual()
        code, result = request({'action': 'start', 'token': 42, 'rpm': 10, 'ramp_rpm_s': 3})
        self.assertEqual(code, 200)
        self.assertEqual(result['sample']['stepper_can_motor_manual_token'], 42)
        self.assertEqual(request({'action': 'renew', 'token': 42})[0], 200)
        self.assertEqual(request({'action': 'renew', 'token': 42, 'rpm': 10})[0], 400)
        self.assertEqual(self.stepper.commands[-2:], [b'V1 M10,3,42\n', b'V1 K42\n'])
