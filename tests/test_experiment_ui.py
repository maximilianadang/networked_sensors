"""Exercise experiment controls with mocked DOM/HTTP; never contact hardware."""
import shutil
import subprocess
import unittest
from pathlib import Path


class ExperimentUiTests(unittest.TestCase):
    def test_thermocouple_readings_and_component_names(self):
        runtime = shutil.which("gjs")
        if runtime is None:
            self.skipTest("GJS is required for the isolated JavaScript component checks")
        result = subprocess.run([runtime, "-m", str(Path(__file__).with_name("thermocouple_ui_test.mjs"))],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_positional_move_uses_controller_specific_arming(self):
        runtime = shutil.which("gjs")
        if runtime is None:
            self.skipTest("GJS is required for the isolated JavaScript component checks")
        result = subprocess.run([runtime, "-m", str(Path(__file__).with_name("stepper_ui_test.mjs"))],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_completed_motor_experiment_can_be_started_again(self):
        runtime = shutil.which("gjs")
        if runtime is None:
            self.skipTest("GJS is required for the isolated JavaScript component checks")
        fixture = Path(__file__).with_name("experiment_ui_test.mjs")
        result = subprocess.run([runtime, "-m", str(fixture)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
