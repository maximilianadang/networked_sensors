"""Exercise the production shared framing and nonblocking Ethernet sender."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class StreamFirmwareTests(unittest.TestCase):
    def test_bounded_framing_backpressure_and_async_send(self):
        compiler = shutil.which('c++')
        if compiler is None:
            self.skipTest('A C++ compiler is required')
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'stream-test')
            result = subprocess.run([compiler, '-std=c++11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                                     '-I', str(root/'tests/native_stream'), '-I', str(root/'firmware'),
                                     str(root/'tests/stream_transport_test.cpp'), '-o', binary],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            result = subprocess.run([binary], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
