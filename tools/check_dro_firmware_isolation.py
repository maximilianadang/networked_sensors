#!/usr/bin/env python3
"""Explicit USB isolation test: upload input-only sketch, log, restore firmware.

Only run with equipment safe for controller resets. No motion commands sent.
Uses the existing upload workflow and stdlib serial I/O; no new dependencies.
"""
import json
import argparse
import os
from pathlib import Path
import select
import termios
import time
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.firmware_upload import compile_and_upload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, help='Verified controller USB port')
    parser.add_argument('--payload', default='controllino_dro_input_test.ino')
    parser.add_argument('--results', default='documentation/dro-input-isolation-results.json')
    args = parser.parse_args()
    port = str(Path(args.port).resolve(strict=True))
    results = []
    try:
        compile_and_upload('controllino', payload=args.payload,
                           port=port, safety_confirmed=True)
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attr = termios.tcgetattr(fd)
            attr[0] = attr[1] = attr[3] = 0
            attr[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attr[4] = attr[5] = termios.B115200
            attr[6][termios.VMIN], attr[6][termios.VTIME] = 0, 0
            termios.tcsetattr(fd, termios.TCSANOW, attr)
            deadline, buffer = time.monotonic() + 12, b''
            while time.monotonic() < deadline:
                if not select.select([fd], [], [], .5)[0]:
                    continue
                buffer += os.read(fd, 4096)
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    results.append(row)
                    if len(results) <= 3:
                        print('input-only:', row, flush=True)
        finally:
            os.close(fd)
        if not results:
            raise RuntimeError('No diagnostic samples received')
        Path(args.results).write_text(
            json.dumps({'samples': results}, indent=2) + '\n')
        print('RESULT:', len(results), 'samples; last:', results[-1], flush=True)
    finally:
        print('Restoring current motion firmware', flush=True)
        print(compile_and_upload('controllino', payload='controllino_motion_control.ino',
                                 port=port, safety_confirmed=True), flush=True)


if __name__ == '__main__':
    main()
