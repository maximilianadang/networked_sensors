"""USB identity discovery and recovery without opening real hardware."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import supervisor_core as core


@unittest.skipIf(core.termios is None, "USB discovery tests require Unix serial support")
class UsbDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.discovery = core.discover_controllino_usb
        self.sys = self.root / 'sys/class/tty'
        self.dev = self.root / 'dev'
        self.sys.mkdir(parents=True)
        (self.dev / 'serial/by-id').mkdir(parents=True)

    def device(self, serial='controller-one', name='ttyACM1', vid='2341', pid='0042'):
        usb = self.root / 'sys/devices' / serial
        interface = usb / 'interface'
        interface.mkdir(parents=True, exist_ok=True)
        for key, value in dict(idVendor=vid, idProduct=pid, serial=serial).items():
            (usb / key).write_text(value)
        tty = self.sys / name
        tty.mkdir()
        (tty / 'device').symlink_to(interface)
        (self.dev / name).touch()
        alias = self.dev / 'serial/by-id' / serial
        if alias.is_symlink():
            alias.unlink()
        alias.symlink_to(self.dev / name)
        return alias

    def discover(self):
        return self.discovery(sys_tty=self.sys, dev=self.dev)

    def source(self, port='auto'):
        source = core.ControllinoUsbStepperSource(port, baud=115200)
        self.addCleanup(source.close)
        return source

    def test_count_identities_not_aliases_and_filter_other_usb_devices(self):
        first = self.device()
        self.device(serial='unrelated', name='ttyUSB0', vid='1234')
        self.assertEqual(self.discover(), {'2341:0042:controller-one': str(first)})
        self.device(serial='controller-two', name='ttyACM2', vid='2a03', pid='0010')
        self.assertEqual(len(self.discover()), 2)

    def test_auto_waits_when_absent_or_ambiguous(self):
        source = self.source()
        with patch.object(core, 'discover_controllino_usb', self.discover):
            self.assertFalse(source._open(0))
            self.assertIn('No compatible', source.last_error)
            self.device()
            self.device(serial='controller-two', name='ttyACM2')
            self.assertFalse(source._open(1))
            self.assertIn('Multiple compatible', source.last_error)
            self.assertIsNone(source._fd)

    def test_reconnect_follows_same_identity_and_clears_cached_status(self):
        alias = self.device()
        source = self.source()
        with patch.object(core, 'discover_controllino_usb', self.discover), \
             patch.object(core.termios, 'tcgetattr', return_value=[0, 0, 0, 0, 0, 0, [0] * 32]), \
             patch.object(core.termios, 'tcsetattr'), patch.object(core.os, 'write') as write:
            self.assertTrue(source._open(0))
            self.assertEqual(source.resolved_port, str(alias))
            source._last_values = {'stepper_firmware_version': '1.5.0'}
            (self.dev / 'ttyACM1').unlink()
            self.assertFalse(source._open(1))
            self.assertIsNone(source._last_values)
            self.device(name='ttyACM3')
            self.assertTrue(source._open(2))
            self.assertEqual(source._opened_usb_device, str(self.dev / 'ttyACM3'))
            write.assert_not_called()

    def test_pinned_controller_does_not_switch_to_another_candidate(self):
        alias = self.device()
        source = self.source()
        with patch.object(core, 'discover_controllino_usb', self.discover):
            self.assertEqual(source._usb_port_to_open(), str(alias))
            (self.dev / 'ttyACM1').unlink()
            self.device(serial='replacement', name='ttyACM2')
            with self.assertRaisesRegex(OSError, 'Selected Controllino.*disconnected'):
                source._usb_port_to_open()

    def test_explicit_numbered_path_is_upgraded_to_identity(self):
        alias = self.device()
        self.device(serial='controller-two', name='ttyACM2')
        source = self.source(str(self.dev / 'ttyACM1'))
        with patch.object(core, 'discover_controllino_usb', self.discover):
            self.assertEqual(source._usb_port_to_open(), str(alias))
            self.assertEqual(source._usb_identity, '2341:0042:controller-one')

    def test_stale_numbered_path_recovers_but_explicit_identity_stays_selected(self):
        alias = self.device()
        with patch.object(core, 'discover_controllino_usb', self.discover), \
             patch.object(core.os.path, 'exists', side_effect=lambda p: False if p == '/dev/ttyACM0' else Path(p).exists()):
            self.assertEqual(self.source('/dev/ttyACM0')._usb_port_to_open(), str(alias))
            selected = '/dev/serial/by-id/missing-controller'
            self.assertEqual(self.source(selected)._usb_port_to_open(), selected)

    def test_discovery_survives_device_disappearing_and_uses_number_without_alias(self):
        alias = self.device()
        alias.unlink()
        self.assertEqual(next(iter(self.discover().values())), str(self.dev / 'ttyACM1'))
        (self.dev / 'ttyACM1').unlink()
        self.assertEqual(self.discover(), {})
