"""Solenoid 4 follows Controllino telemetry/control, independently of the ESP32."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

from networked_sensors.dashboard_app.runtime import DashboardRuntime
from networked_sensors.supervisor_core import (
    ControllinoStepperSource, ControllinoUsbStepperSource, SimulatedEsp32Source,
    SimulatedDxmr90Source, SourceReading, SourceMerger,
)
from networked_sensors import test_stepper_control as fixtures


class FakeControllino(ControllinoStepperSource):
    def __init__(self):
        super().__init__("http://unused.invalid")
        self.payload = json.loads(fixtures.ControllinoStepperSourceTests.STATUS)
        self.payload.update(sol4=0)
        self.commands = []
        self.emit = True
        self.apply = True
        self.refresh()

    def refresh(self):
        self._last_values = self.decode_status_line(json.dumps(self.payload))

    def _require_connected(self):
        return self._last_values

    def _write_command(self, command, description):
        self.commands.append(command)
        if self.apply:
            self.payload['sol4'] = int(command.strip()[-1:])
            self.refresh()

    def poll(self, elapsed_s):
        if self.emit:
            return SourceReading(self.name, self.mode, elapsed_s, dict(self._last_values))


class SolenoidRoutingTests(unittest.TestCase):
    def setUp(self):
        self.stepper = FakeControllino()
        self.esp32 = SimulatedEsp32Source(auto_sequence=False)
        self.sources = [self.esp32, SimulatedDxmr90Source(), self.stepper]

    def test_usb_and_ethernet_decode_capability_and_send_identical_set_command(self):
        for cls in (ControllinoStepperSource, ControllinoUsbStepperSource):
            source = cls("http://unused.invalid")
            values = source.decode_status_line(json.dumps(self.stepper.payload))
            self.assertTrue(values['stepper_solenoid4_capable'])
            self.assertFalse(values['stepper_solenoid4_on'])
            source._require_connected = Mock(return_value=values)
            source._write_command = Mock()
            source.set_solenoid(3, True)
            self.assertEqual(source._write_command.call_args.args[0], b'V1 L4,1\n')
            values['stepper_estop_latched'] = True
            with self.assertRaisesRegex(RuntimeError, 'E-STOP'):
                source.set_solenoid(3, True)
            source.set_solenoid(3, False)
            self.assertEqual(source._write_command.call_args.args[0], b'V1 L4,0\n')
            old = dict(self.stepper.payload)
            del old['sol4']
            values = source.decode_status_line(json.dumps(old))
            source._require_connected.return_value = values
            with self.assertRaisesRegex(RuntimeError, 'needs solenoid'):
                source.set_solenoid(3, True)
            for invalid in (None, True, 2, '1'):
                with self.assertRaises(ValueError):
                    source.decode_status_line(json.dumps(dict(old, sol4=invalid)))

    def test_merge_uses_relay_not_esp32_for_button_and_open_flow(self):
        merger = SourceMerger(self.sources, stale_after_s=0.5)
        self.esp32.set_solenoid(3, True)
        sample = merger.poll(0, datetime.now(timezone.utc))
        self.assertTrue(sample['esp32_sol4'])  # Raw source identity is preserved.
        self.assertFalse(sample['solenoid4_on'])
        self.assertEqual(sample['solenoid4_source'], 'stepper')
        self.assertEqual(sample['dxmr90_open_total_mass_flow_g_min'], 0)
        self.stepper.set_solenoid(3, True)
        sample = merger.poll(0.1, datetime.now(timezone.utc))
        self.assertTrue(sample['solenoid4_on'])
        self.assertEqual(sample['dxmr90_open_total_mass_flow_g_min'], sample['dxmr90_total_mass_flow_g_min'])
        self.stepper.emit = False
        sample = merger.poll(1, datetime.now(timezone.utc))
        self.assertFalse(sample['solenoid4_connected'])
        self.assertIsNone(sample['solenoid4_on'])
        self.assertIsNone(sample['dxmr90_open_total_mass_flow_g_min'])

    def runtime(self, directory):
        with patch('networked_sensors.dashboard_app.runtime.make_sources', return_value=self.sources):
            return DashboardRuntime(
                scenario='healthy', rate_hz=10, drop_after_s=2, stale_after_s=1,
                history_limit=10, record_dir=Path(directory), esp32_source='sim',
                dxmr90_source='sim', stepper_source='controllino',
                stepper_port='/dev/null', stepper_baud=9600,
                dxmr90_host='unused', dxmr90_port=502, dxmr90_unit_id=1,
                dxmr90_timeout=0.1, dxmr90_addressing='one-based',
                dxmr90_word_order='high-low', dxmr90_data_path='direct', dxmr90_rate_hz=10,
            )

    def test_dashboard_routes_fourth_only_and_requires_confirmation(self):
        with TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            response = runtime.toggle_solenoid(3)
            self.assertTrue(response['state'])
            self.assertTrue(response['sample']['solenoid4_on'])
            self.assertEqual(self.stepper.commands, [b'V1 L4,1\n'])
            self.assertFalse(self.esp32.solenoid_states()[3])
            runtime.toggle_solenoid(0)
            self.assertTrue(self.esp32.solenoid_states()[0])
            self.assertEqual(len(self.stepper.commands), 1)
            self.stepper.apply = False
            with patch('networked_sensors.dashboard_app.runtime.time.monotonic', side_effect=[0, 0, 2]):
                with self.assertRaisesRegex(RuntimeError, 'did not confirm solenoid'):
                    runtime.toggle_solenoid(3)
            runtime.stop()

    def test_dashboard_direction_mapping_in_both_modes(self):
        with TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            self.stepper.set_local_run = Mock()
            self.stepper.move = Mock()
            runtime._confirm_stepper_locked = Mock(return_value={})
            for requested, wire in ((1, -1), (-1, 1), (0, 0)):
                runtime.set_stepper_local_run({'direction': requested})
                self.stepper.set_local_run.assert_called_with(wire)
            for label, distance in (('forward', -10), ('reverse', 10)):
                runtime.move_stepper({'direction':label, 'distance_mm':10, 'speed_mm_s':1})
                self.assertEqual(self.stepper.move.call_args.args[0], distance)
            runtime.stop()

    def test_each_solenoid_starts_recording_and_only_manual_stop_finishes(self):
        for index in range(4):
            with self.subTest(solenoid=index + 1), TemporaryDirectory() as directory:
                self.setUp()
                runtime = self.runtime(directory)
                self.assertFalse(runtime.recording)
                runtime.toggle_solenoid(index)
                self.assertTrue(runtime.recording)
                recorder = runtime.recorder
                # Relay confirmation performs one additional poll; each poll writes once.
                self.assertEqual(recorder._row_count, 2 if index == 3 else 1)
                runtime.toggle_solenoid((index + 1) % 4)
                self.assertIs(runtime.recorder, recorder)
                runtime.toggle_solenoid(index)  # Closing does not stop recording.
                self.assertTrue(runtime.recording)
                runtime.set_recording(False)
                self.assertIsNotNone(runtime.latest_recording)
                with runtime._condition:
                    runtime._poll_locked(0.2)
                self.assertFalse(runtime.recording)  # Other valve still open.
                runtime.toggle_solenoid(index)  # A new activation starts a new run.
                self.assertTrue(runtime.recording)
                self.assertIsNot(runtime.recorder, recorder)
                runtime.stop()

    def test_telemetry_activation_and_reconnect_do_not_require_browser_command(self):
        with TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            self.stepper.set_solenoid(3, True)
            with runtime._condition:
                runtime._poll_locked(0.1)
            self.assertTrue(runtime.recording)
            runtime.set_recording(False)
            self.stepper.emit = False
            with runtime._condition:
                runtime._poll_locked(2)
            self.stepper.emit = True
            with runtime._condition:
                runtime._poll_locked(2.1)
            self.assertFalse(runtime.recording)
            runtime.stop()

    def test_relay_remains_available_with_esp32_off_but_old_firmware_never_falls_back(self):
        from networked_sensors.supervisor_core import DisabledSource
        self.sources[0] = DisabledSource('esp32', 0.1, self.esp32.expected_fields)
        with TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            self.assertTrue(runtime.toggle_solenoid(3)['state'])
            with self.assertRaisesRegex(RuntimeError, 'not live'):
                runtime.toggle_solenoid(0)
            del self.stepper.payload['sol4']
            self.stepper.refresh()
            with runtime._condition:
                runtime._poll_locked(0.1)
            with self.assertRaisesRegex(RuntimeError, 'not live'):
                runtime.toggle_solenoid(3)
            self.assertEqual(len(self.stepper.commands), 1)
            runtime.stop()


if __name__ == '__main__':
    unittest.main()
