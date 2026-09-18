"""Lean startup/page serving must not depend on the legacy HTML file."""
from pathlib import Path
import subprocess
import sys
import unittest
import re
import runpy
from io import BytesIO
from urllib.parse import urljoin
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class LeanDependencyTests(unittest.TestCase):
    def test_direct_startup_preserves_launcher_settings_and_overrides(self):
        parse = runpy.run_path(str(ROOT/'dashboard-lean.py'))['parse_args']
        with patch.dict('os.environ', {}, clear=True):
            args = parse([])
            expected = dict(host='0.0.0.0', port=8000, esp32_source='real',
                            esp32_url='http://testbench.local', dxmr90_source='real',
                            dxmr90_host='192.168.0.1', dxmr90_data_path='direct',
                            dxmr90_rate_hz=10, stepper_source='controllino',
                            stepper_url='http://10.77.0.10', stepper_timeout=0.75)
            for name, value in expected.items():
                self.assertEqual(getattr(args, name), value, name)
        env = dict(CONTROLLINO_URL='http://controller.local', DASHBOARD_PORT='8123',
                   DASHBOARD_HOST='127.0.0.1', ESP32_SOURCE='off',
                   ESP32_URL='http://esp.local', DXMR90_SOURCE='sim',
                   DXMR90_HOST='192.168.1.10')
        with patch.dict('os.environ', env, clear=True):
            args = parse([])
            self.assertEqual((args.stepper_url, args.port, args.host),
                             ('http://controller.local', 8123, '127.0.0.1'))
            self.assertEqual((args.esp32_source, args.esp32_url,
                              args.dxmr90_source, args.dxmr90_host),
                             ('off', 'http://esp.local', 'sim', '192.168.1.10'))
            self.assertEqual(parse(['--port', '9000']).port, 9000)
        with patch.dict('os.environ', {'DASHBOARD_PORT': ''}, clear=True):
            self.assertEqual(parse([]).port, 8000)
        with patch.dict('os.environ', {'DASHBOARD_PORT': 'invalid'}, clear=True):
            with patch('sys.stderr'), self.assertRaises(SystemExit):
                parse([])

    def test_direct_entry_point_help_works_from_another_directory(self):
        subprocess.run([str(ROOT/'dashboard-lean.py'), '--help'], cwd='/tmp',
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_both_pages_serve_their_entire_local_module_graph(self):
        """Follow actual imports and request every dependency through its handler."""
        from dashboard_app.http import build_handler
        from functools import partial
        lean = partial(build_handler, lean=True)
        for factory in (lean, build_handler):
            handler_type = factory(None, quiet=True)
            seen = set()
            def request(url):
                if url in seen:
                    return
                seen.add(url)
                handler = object.__new__(handler_type)
                handler.path = url
                handler.wfile = BytesIO()
                status = []
                handler.send_response = status.append
                handler.send_header = lambda *args: None
                handler.end_headers = lambda: None
                handler.do_GET()
                self.assertEqual(status, [200], url)
                body = handler.wfile.getvalue().decode()
                if url == '/':
                    refs = re.findall(r'(?:src|href)="(/assets/[^"]+)"', body)
                elif url.endswith('.js'):
                    refs = re.findall(r'from\s+[\"\x27]([^\"\x27]+)', body)
                else:
                    refs = re.findall(r'@import\s+url\([\"\x27]?([^\"\x27)]+)', body)
                for ref in refs:
                    request(urljoin(url, ref))
            request('/')
            if factory is lean:
                self.assertEqual({url for url in seen if url.endswith('.js')},
                                 {'/assets/app-lean.js'})

    def test_lean_import_and_page_work_without_legacy_html(self):
        # Fresh interpreter covers import-time reads, not just cached modules.
        code = r'''
import runpy
from pathlib import Path
from unittest.mock import patch
original = Path.read_text
reads = []
def read(path, *args, **kwargs):
    if path.name == "index.html":
        raise AssertionError("Lean dashboard tried to read legacy HTML")
    reads.append(path.name)
    return original(path, *args, **kwargs)
with patch.object(Path, "read_text", read):
    app = runpy.run_path("dashboard-lean.py", run_name="dependency_test")
    handler_type = app["build_handler"](None, lean=True)
    handler = object.__new__(handler_type)
    handler.path = "/"
    pages = []
    handler._send_html = pages.append
    handler.do_GET()
    assert pages == [original(Path("dashboard_app/static/index-lean.html"))]
    assert "index-lean.html" in reads
'''
        subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True)

    def test_legacy_handler_still_serves_legacy_page(self):
        from dashboard_app.http import build_handler
        handler = object.__new__(build_handler(None, quiet=True))
        handler.path = '/'
        pages = []
        handler._send_html = pages.append
        handler.do_GET()
        self.assertEqual(pages, [(ROOT/'dashboard_app/static/index.html').read_text()])


if __name__ == '__main__':
    unittest.main()
