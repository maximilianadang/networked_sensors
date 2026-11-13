"""CLI delegates to the notebook helper; these tests never access hardware."""
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tools import firmware_upload as uploader


class FirmwareUploadCliTests(unittest.TestCase):
    def result(self, uploaded):
        return uploader.UploadResult(
            target=uploader.get_target("controllino-ak80-6"),
            payload=Path("controllino_motion_control.ino"), payload_sha256="test",
            staged_files=(), port="/dev/ttyACM1" if uploaded else None,
            compiled=True, uploaded=uploaded)

    def test_compile_only_and_upload_share_helper_and_report_different_outcomes(self):
        for uploaded in (False, True):
            with self.subTest(uploaded=uploaded):
                output = io.StringIO()
                flag = "--safety-confirmed" if uploaded else "--compile-only"
                with patch.object(uploader, "compile_and_upload", return_value=self.result(uploaded)) as run:
                    with redirect_stdout(output):
                        code = uploader.main(["--target", "controllino-ak80-6", "--port", "/dev/ttyACM1", flag])
                self.assertEqual(code, 0)
                run.assert_called_once_with(
                    "controllino-ak80-6", None, port="/dev/ttyACM1",
                    safety_confirmed=uploaded, compile_only=not uploaded,
                    executable=None, config_file=None)
                self.assertIn("Uploaded and verified" if uploaded else "Compiled (no upload)", output.getvalue())

    def test_missing_safety_confirmation_stops_before_toolchain_or_device_access(self):
        error = io.StringIO()
        with patch.object(uploader, "resolve_toolchain") as resolve:
            with redirect_stderr(error):
                code = uploader.main(["--target", "controllino-ak80-6"])
        self.assertEqual(code, 1)
        resolve.assert_not_called()
        self.assertIn("Upload blocked", error.getvalue())

    def test_list_ports_does_not_compile_or_upload(self):
        output = io.StringIO()
        with patch.object(uploader, "discover_ports", return_value=[]) as discover:
            with patch.object(uploader, "compile_and_upload") as run:
                with redirect_stdout(output):
                    code = uploader.main(["--list-ports"])
        self.assertEqual(code, 0)
        discover.assert_called_once_with(executable=None, config_file=None)
        run.assert_not_called()
        self.assertIn("No serial boards detected", output.getvalue())

    def test_failed_upload_exits_nonzero_without_success_message(self):
        output, error = io.StringIO(), io.StringIO()
        with patch.object(uploader, "compile_and_upload", side_effect=uploader.FirmwareUploadError("verification failed")):
            with redirect_stdout(output), redirect_stderr(error):
                code = uploader.main(["--target", "controllino-ak80-6", "--safety-confirmed"])
        self.assertEqual(code, 1)
        self.assertIn("verification failed", error.getvalue())
        self.assertNotIn("Uploaded and verified", output.getvalue())

    def test_direct_script_help_and_missing_arguments_do_not_silently_succeed(self):
        script = Path(uploader.__file__)
        help_result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--safety-confirmed", help_result.stdout)
        missing = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("choose --target", missing.stderr)
