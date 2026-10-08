"""Exercise the production CAN codec and adapter without hardware.

The adapter test links autowp-mcp2515@1.3.1 from the firmware tooling's
.arduino-build/user/libraries/autowp-mcp2515 directory. Set MCP2515_LIBRARY_DIR
to use another installed copy. Missing dependency produces an explicit skip;
tests never download it. Run: python -m unittest discover -s tests -p 'test_cubemars_firmware.py' -v
"""

import shutil
import subprocess
import tempfile
import unittest
import os
from pathlib import Path


class CubeMarsFirmwareTests(unittest.TestCase):
    def compile_and_run(self, source, extra=()):
        compiler = shutil.which("c++")
        if compiler is None:
            self.skipTest("A C++ compiler is required for native firmware tests")
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "native_test"
            build = subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                 "-UNDEBUG", "-I", str(root / "firmware"),
                 str(root / "tests" / source), *map(str, extra), "-o", str(binary)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_protocol_and_run_state(self):
        self.compile_and_run("cubemars_protocol_test.cpp")

    def test_adapter_with_pinned_mcp2515(self):
        root = Path(__file__).resolve().parents[1]
        library = Path(os.environ.get("MCP2515_LIBRARY_DIR",
            root / ".arduino-build/user/libraries/autowp-mcp2515"))
        if not (library / "mcp2515.cpp").is_file():
            self.skipTest("Install autowp-mcp2515@1.3.1 with the firmware tooling, "
                          "or set MCP2515_LIBRARY_DIR to that library directory")
        self.assertIn("version=1.3.1", (library / "library.properties").read_text())
        self.compile_and_run("cubemars_adapter_test.cpp", (
            "-I", root / "tests/native_can", "-I", library, library / "mcp2515.cpp"))


if __name__ == "__main__":
    unittest.main()
