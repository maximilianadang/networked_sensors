"""Native tests of production LinACE capture, ADC snapshots and relay outputs."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class ControllinoIoFirmwareTests(unittest.TestCase):
    def test_acquisition_and_outputs(self):
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("A C++ compiler is required for native firmware tests")
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "io_test"
            result = subprocess.run([
                compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                "-UNDEBUG", "-I", str(root / "tests/native_io"),
                "-I", str(root / "firmware"), str(root / "tests/controllino_io_test.cpp"),
                "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
