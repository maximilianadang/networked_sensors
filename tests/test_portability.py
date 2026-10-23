"""Exercise network workflows without Unix serial support; no bench hardware I/O."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import supervisor_core as core


class PortabilityTests(unittest.TestCase):
    def test_network_workflows_without_unix_serial(self):
        # Use a fresh interpreter: patching an already-imported module would
        # miss Unix-only dependencies evaluated during import/class creation.
        script = r'''
import runpy
import sys
import unittest

sys.modules["termios"] = None
sys.path.insert(0, "tests")
sys.argv = ["dashboard.py", "--help"]
try:
    runpy.run_path("dashboard.py", run_name="__main__")
except SystemExit as exc:
    if exc.code != 0:
        raise
sys.argv = ["network-portability-checks"]

suite = unittest.defaultTestLoader.loadTestsFromNames([
    "test_cli_defaults", "test_tcp_stepper", "test_backend_boundary",
    "test_ed593", "test_dxmr90_flow",
])
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unavailable_usb_reports_transport_error_without_opening_a_device(self):
        with patch.object(core, "termios", None), \
             patch.object(core.os, "open") as open_device, \
             patch.object(core, "discover_controllino_usb") as discover:
            for source in (core.UsbStepperSource("COM3"), core.ControllinoUsbStepperSource()):
                with self.subTest(source=type(source).__name__):
                    try:
                        self.assertIsNone(source.poll(0))
                        self.assertIn("use Ethernet", source.last_error)
                        self.assertIsNone(source._fd)
                    finally:
                        source.close()
            open_device.assert_not_called()
            discover.assert_not_called()
