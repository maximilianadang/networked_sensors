"""Production limit decoding and command guards, without hardware I/O."""
import json
import unittest
from unittest.mock import Mock, patch

import supervisor_core as core
from dashboard_app.experiment import ExperimentRunner
from test_backend_boundary import Controller


class ControllinoLimitTests(unittest.TestCase):
    def controller(self, **changes):
        controller = Controller()
        controller.payload.update(fw='1.5.5', m=1, d6=1, d8=1, lx=0,
                                  lppin=67, lnpin=66)
        controller.payload.update(changes)
        controller.refresh()
        return controller

    def test_five_times_microstepping_commands_and_telemetry_agree(self):
        controller = self.controller(fw='1.5.7', csps=295, aps=1000)
        self.assertEqual(controller.status()['stepper_command_speed_mm_s'], 1.4986)
        self.assertEqual(controller.status()['stepper_measured_speed_mm_s'], 5.08)
        controls = core.DeviceControls([controller])
        controls.move(dict(controller.status()), {'distance_mm': 10, 'speed_mm_s': 1.4,
                                                  'direction': 'forward'})
        self.assertTrue(controller.commands[-1].startswith(b'V1 G1969,276,'))
        controller.payload.update(m=0, mv=0, st=0, sps=0)
        controller.refresh()
        receipt = controls.speed(dict(controller.status()), {'speed_mm_s': 1.4})
        self.assertEqual(controller.commands[-1], b'V1 S276\n')
        self.assertAlmostEqual(receipt.result['requested_speed_mm_s'], 1.40208)
        self.assertEqual(controller.status()['stepper_command_speed_mm_s'], 1.4021)

    def test_encoder_acquisition_timestamp_is_optional_and_validated(self):
        controller = self.controller(dt=0xFFFFFFFE)
        self.assertEqual(controller.status()['stepper_dro_acquired_ms'], 0xFFFFFFFE)
        for stamp in (-1, 2**32, True, 1.5):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                controller.decode_status_line(json.dumps(dict(controller.payload, dt=stamp)))
        controller.payload.pop('dt')
        controller.refresh()
        self.assertIsNone(controller.status()['stepper_dro_acquired_ms'])

    def test_real_usb_poll_resolves_experiment_move_start_and_completion(self):
        source = core.ControllinoUsbStepperSource()
        source._fd = 123
        command_id = 'experiment-regression'
        source._command_names[7] = command_id
        source.pending_command_id = command_id
        payload = dict(self.controller().payload, c=7, csps=394)
        aborted = Mock()
        runner = ExperimentRunner(lambda action: {'command_id': command_id}, aborted)
        runner.load('action,source,device,max_age_ms,health,timeout_s,set_point,speed_mm_s,duration_s,direction\n'
                    'requested_state,controllino,stepper,1000,ok,1,move,2,5,forward\n', 'move.csv', 0)
        runner.start(0)

        def receive(now, **state):
            payload.update(state)
            packet = (json.dumps(payload) + '\n').encode()
            with patch.object(source, '_open', return_value=True), patch('supervisor_core.os.read', side_effect=[packet, BlockingIOError()]):
                reading = source.poll(now)
            self.assertEqual(reading.values['stepper_command_id'], command_id)
            runner.on_sample(dict(reading.values, stepper_connected=True, stepper_age_ms=0), now)

        try:
            receive(.1, st=5, mv=0, sps=0)
            receive(.2, st=4, mv=1, sps=394)
            self.assertTrue(runner.results[0]['motion_started'])
            self.assertIsNone(source.pending_command_id)
            self.assertIsNone(runner.snapshot()['deadline_s'])
            receive(5.3, st=6, mv=0, sps=0)  # Beyond the start-confirmation timeout.
            self.assertEqual(runner.state, 'completed')
            aborted.assert_not_called()
        finally:
            source._fd = None  # The descriptor is a test sentinel, never real hardware.

    def test_command_id_resolution_preserves_unknown_ids_and_legacy_usb(self):
        for source in (core.ControllinoUsbStepperSource(), core.UsbStepperSource()):
            source._command_names[2] = 'caller-move'
            values = {'stepper_command_id': f'{source.mode}-00002'}
            source._resolve_command_id(values)
            self.assertEqual(values['stepper_command_id'], 'caller-move')
            for value in (f'{source.mode}-00003', f'{source.mode}-bad', None):
                values = {'stepper_command_id': value}
                source._resolve_command_id(values)
                self.assertEqual(values['stepper_command_id'], value)

    def test_legacy_usb_calibration_is_unchanged(self):
        controller = self.controller(csps=1000, aps=1000)
        values = core.UsbStepperSource.decode_status_line(json.dumps(dict(controller.payload, q=1, dc=1)))
        self.assertEqual(values['stepper_command_speed_mm_s'], 3.9688)
        self.assertEqual(values['stepper_measured_speed_mm_s'], 3.9688)
        self.assertEqual(core.UsbStepperSource._manual_speed_sps(1.5), 378)

    def test_reports_real_inputs_and_qualified_states(self):
        controller = self.controller(d6=0, d8=0, lx=1 << 16)
        values = controller.status()
        self.assertEqual(values['stepper_positive_limit_input'], 'DI1 / A13')
        self.assertEqual(values['stepper_negative_limit_input'], 'DI0 / A12')
        self.assertEqual(values['stepper_d6_raw'], 'LOW')
        self.assertFalse(values['stepper_positive_limit_active'])  # raw transient, not qualified
        self.assertTrue(values['stepper_negative_limit_active'])
        self.assertFalse(values['stepper_negative_limit_latched'])
        self.assertEqual(values['stepper_limit_qualification_ms'], 5)
        self.assertFalse(values['stepper_home_capable'])

    def test_home_uses_requested_speed_and_requires_firmware_capability(self):
        controller = self.controller(hc=1)
        controls = core.DeviceControls([controller])
        self.assertTrue(controller.status()['stepper_home_capable'])
        controls.home(dict(controller.status()), {'speed_mm_s': 2})
        self.assertEqual(controller.commands[-1], b'V1 H394\n')
        with self.assertRaisesRegex(RuntimeError, "busy"):
            controls.home(dict(controller.status()), {"speed_mm_s": 1})
        controller.payload.update(lx=1 << 16, mv=0, st=5, sps=0)
        controller.refresh()
        controls.home(dict(controller.status()), {'speed_mm_s': 1})
        self.assertEqual(controller.commands[-1], b'V1 H197\n')
        for speed in (None, True, 0, 11, float('nan')):
            with self.subTest(speed=speed), self.assertRaises((ValueError, TypeError)):
                controls.home(dict(controller.status()), {'speed_mm_s': speed})
        controller.payload.pop('hc')
        controller.refresh()
        with self.assertRaises(RuntimeError):
            controls.home(dict(controller.status()), {'speed_mm_s': 1})

    def test_move_blocks_toward_each_endpoint_and_allows_retreat(self):
        for side, blocked, retreat, wire in (
                ('negative', 'reverse', 'forward', b'V1 G197,'),
                ('positive', 'forward', 'reverse', b'V1 G-197,')):
            with self.subTest(side=side):
                controller = self.controller(lx=1 << (16 if side == 'negative' else 17))
                controls = core.DeviceControls([controller])
                with self.assertRaisesRegex(RuntimeError, side + ' limit'):
                    controls.move(dict(controller.status()), {'distance_mm': 1, 'speed_mm_s': 1,
                                                               'direction': blocked})
                self.assertEqual(controller.commands, [])
                controls.move(dict(controller.status()), {'distance_mm': 1, 'speed_mm_s': 1,
                                                           'direction': retreat})
                self.assertTrue(controller.commands[-1].startswith(wire))

    def test_high_releases_both_motion_modes_without_a_reset_or_retreat(self):
        for side in ('positive', 'negative'):
            for positional in (False, True):
                with self.subTest(side=side, positional=positional):
                    controller = self.controller()
                    controller.payload.update(m=int(positional), lx=1 << (17 if side == 'positive' else 16))
                    controller.refresh()
                    controls = core.DeviceControls([controller])
                    direction = 'forward' if side == 'positive' else 'reverse'
                    wire = 1 if side == 'positive' else -1
                    def request():
                        if positional:
                            return controls.move(dict(controller.status()), {'distance_mm': 1, 'speed_mm_s': 1,
                                                                             'direction': direction})
                        return controls.local_run(dict(controller.status()), {'direction': wire})
                    with self.assertRaises(RuntimeError):
                        request()
                    controller.payload.update(lx=0, d6=1, d8=1, lp=1, ln=1)
                    controller.refresh()  # Ignore historical flags from older Controllino packets.
                    request()
                    self.assertEqual(len(controller.commands), 1)

    def test_directional_commands_respect_the_same_limits_and_stop_is_available(self):
        for side, blocked, retreat in (('negative', -1, 1), ('positive', 1, -1)):
            with self.subTest(side=side):
                controller = self.controller()
                controller.payload.update(m=0, lx=1 << (16 if side == 'negative' else 17))
                controller.refresh()
                controls = core.DeviceControls([controller])
                with self.assertRaisesRegex(RuntimeError, side + ' limit'):
                    controls.local_run(dict(controller.status()), {'direction': blocked})
                self.assertEqual(controller.commands, [])
                controls.local_run(dict(controller.status()), {'direction': 0})
                self.assertEqual(controller.commands[-1], b'V1 R0\n')
                controls.local_run(dict(controller.status()), {'direction': retreat})
                self.assertEqual(controller.commands[-1], f'V1 R{retreat}\n'.encode())

    def test_rejects_incomplete_or_conflicting_input_metadata(self):
        controller = self.controller()
        for changes in ({'lppin': None}, {'lnpin': 67}, {'lppin': -1},
                        {'lnpin': True}, {'d6': -1}, {'lppin': 80}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                controller.decode_status_line(json.dumps(dict(controller.payload, **changes)))

    def test_older_firmware_keeps_absent_limits_unavailable(self):
        values = Controller().status()
        self.assertIsNone(values['stepper_positive_limit_input'])
        self.assertIsNone(values['stepper_negative_limit_input'])
        self.assertIsNone(values['stepper_positive_limit_active'])
        self.assertIsNone(values['stepper_negative_limit_active'])

    def test_limit_trip_during_confirmed_spreadsheet_motion_fails_the_move(self):
        controller = self.controller()
        controller._command_names[1] = 'move'
        aborted = Mock()
        runner = ExperimentRunner(lambda action: {'command_id': 'move'}, aborted)
        runner.load('action,source,device,max_age_ms,health,timeout_s,set_point,speed_mm_s,duration_s,direction\n'
                    'requested_state,controllino,stepper,1000,ok,1,move,1,10,forward\n', 'move.csv', 0)
        runner.start(0)

        def sample(now):
            controller.refresh()
            runner.on_sample(dict(controller.status(), stepper_age_ms=0), now)

        sample(.1)
        controller.payload.update(c=1, st=4, mv=1, g=2520, sps=252)
        sample(.2)
        self.assertTrue(runner.results[0]['motion_started'])
        self.assertIsNone(runner.snapshot()['deadline_s'])
        controller.payload.update(d6=0, lx=1 << 17, lp=1, b=1, r='positive_limit', st=8, mv=0, sps=0)
        sample(2)
        self.assertEqual(runner.state, 'failed')
        self.assertEqual(runner.results[0]['reason'], 'move_aborted')
        aborted.assert_called_once()
