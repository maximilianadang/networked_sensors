"""Execute Yún firmware behavior without a controller or serial connection."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class YunFirmwareTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('c++'), 'native C++ compiler required')
    def test_motion_qualification_ownership_and_transport_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'yun-test'
            subprocess.run(['c++', '-std=c++11', '-I', str(ROOT/'tests/yun_stubs'),
                            '-I', str(ROOT/'tests/dro_stubs'), '-I', str(ROOT/'firmware'),
                            str(ROOT/'tests/yun_behavior_test.cpp'), '-o', str(binary)],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)
