#!/usr/bin/env python3
"""Stopped-controller diagnostic: reset USB and capture telemetry during HTTP reads.

Sends no motion commands. USB reset requires the connected equipment to be
safe. --abandon adds early client disconnects. With temporary trace firmware,
the capture also retains processing-stage markers; normal firmware emits JSON.
"""
import argparse
import os
import select
import socket
import struct
import termios
import threading
import time
import fcntl

from check_controllino_http import request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/ttyACM0')
    parser.add_argument('--host', default='10.77.0.10')
    parser.add_argument('--reset-confirmed', action='store_true', required=True)
    parser.add_argument('--abandon', action='store_true')
    args = parser.parse_args()
    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[0] = attrs[1] = attrs[3] = 0
    attrs[4] = attrs[5] = termios.B9600
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    received = bytearray()
    done = threading.Event()

    def read():
        while not done.is_set():
            if select.select([fd], [], [], .1)[0]:
                received.extend(os.read(fd, 4096))

    reader = threading.Thread(target=read)
    reader.start()
    try:
        dtr = struct.pack('I', termios.TIOCM_DTR)
        fcntl.ioctl(fd, termios.TIOCMBIC, dtr)
        time.sleep(.1)
        fcntl.ioctl(fd, termios.TIOCMBIS, dtr)
        time.sleep(4)
        initial = request(args.host, .75)
        print('INITIAL', initial, 'USB', bytes(received[-500:]), flush=True)
        if 'error' in initial:
            return 1
        for index in range(100):
            try:
                if args.abandon:
                    with socket.create_connection((args.host, 80), timeout=.75) as client:
                        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                          struct.pack('ii', 1, 0))
                        client.sendall(b'GET /status HTTP/1.1\r\nHost: test\r\n\r\n')
                time.sleep(.1)
                result = request(args.host, .75)
            except OSError as exc:
                result = {'error': str(exc)}
            print(index, result, flush=True)
            if 'error' in result:
                break
        offset = len(received)
        print('TRACE TAIL', bytes(received[-1000:]), flush=True)
        time.sleep(3)
        print('SUBSEQUENT USB', bytes(received[offset:]), flush=True)
        final = request(args.host, .75)
        print('FINAL', final, flush=True)
        return int('error' in result or 'error' in final)
    finally:
        done.set()
        reader.join()
        os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
