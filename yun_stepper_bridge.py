#!/usr/bin/env python
"""Trusted-LAN HTTP bridge for the Arduino Yun stepper firmware.

This service runs on the Yun's AR9331 Linux processor.  It owns the internal
UART (normally ``/dev/ttyATH0``), caches compact status lines emitted by the
ATmega32U4, and relays only the existing bounded ``V1`` command grammar.  It
does not generate STEP/DIR signals or make motion-safety decisions.

The file is deliberately compatible with the Yun image's Python 2.7 as well as
modern Python 3 so it can be exercised by the laptop test suite.
"""

from __future__ import print_function

import argparse
import json
import os
import re
import select
import signal
import sys
import termios
import threading
import time
import tty

try:  # Python 2 on the archived Yun Linux image.
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn
except ImportError:  # Python 3 laptop tests.
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn


DEFAULT_DEVICE = "/dev/ttyATH0"
DEFAULT_BAUD = 115200
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_ACK_TIMEOUT_S = 1.0
MAX_COMMAND_BYTES = 48
MAX_SERIAL_BUFFER_BYTES = 8192

COMMAND_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^V1 E[01]$",
        r"^V1 S[0-9]{1,4}$",
        r"^V1 D[01]$",
        r"^V1 M[01]$",
        r"^V1 H$",
        r"^V1 X$",
        r"^V1 G-?[0-9]{1,6},[0-9]{1,4},[0-9]{1,5}$",
    )
)


def _to_bytes(value):
    if isinstance(value, bytes):
        return value
    return value.encode("ascii")


def _to_text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def validate_command(command):
    """Return normalized ASCII command text or raise ``ValueError``."""

    command = _to_text(command).strip()
    try:
        encoded = command.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("command must contain ASCII characters only")
    if not encoded or len(encoded) >= MAX_COMMAND_BYTES:
        raise ValueError("command must contain 1 to 47 bytes")
    if not any(pattern.match(command) for pattern in COMMAND_PATTERNS):
        raise ValueError("command is not part of the supported V1 grammar")
    return command


class SerialBridgeState(object):
    """Own the Yun UART, latest status, and serialized command acknowledgements."""

    def __init__(self, device=DEFAULT_DEVICE, baud=DEFAULT_BAUD, ack_timeout=1.0):
        if baud != 115200:
            raise ValueError("this service currently supports 115200 baud only")
        if ack_timeout <= 0:
            raise ValueError("ack timeout must be positive")
        self.device = device
        self.baud = baud
        self.ack_timeout = ack_timeout
        self.fd = None
        self.last_status = None
        self.last_status_at = None
        self.last_error = None
        self._buffer = b""
        self._stop = threading.Event()
        self._reader = None
        self._state_lock = threading.Lock()
        self._ack_condition = threading.Condition(self._state_lock)
        self._ack_generation = 0
        self._last_ack = None
        self._command_lock = threading.Lock()
        # A timed-out command may still produce a late, uncorrelated ACK. Once
        # that happens we must not risk treating it as the ACK for a later
        # motion command. Restarting the service flushes the UART and restores
        # a known command/ACK boundary.
        self._command_synchronized = True

    def open(self):
        fd = os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(fd)
        attributes = termios.tcgetattr(fd)
        attributes[4] = termios.B115200
        attributes[5] = termios.B115200
        attributes[6][termios.VMIN] = 0
        attributes[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attributes)
        termios.tcflush(fd, termios.TCIOFLUSH)
        self.fd = fd
        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop)
        self._reader.daemon = True
        self._reader.name = "yun-stepper-uart-reader"
        self._reader.start()

    def close(self):
        self._stop.set()
        if self._reader is not None:
            self._reader.join(1.0)
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def _handle_line(self, line):
        line = _to_text(line).strip()
        if not line.startswith("{"):
            return
        try:
            payload = json.loads(line)
        except (TypeError, ValueError) as exc:
            with self._state_lock:
                self.last_error = "invalid ATmega JSON: %s" % exc
            return
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return
        message_type = payload.get("t")
        with self._ack_condition:
            if message_type == "s":
                self.last_status = payload
                self.last_status_at = time.time()
                self.last_error = None
            elif message_type == "a":
                self._last_ack = payload
                self._ack_generation += 1
                self._ack_condition.notify_all()

    def _read_loop(self):
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([self.fd], [], [], 0.2)
                if not readable:
                    continue
                chunk = os.read(self.fd, 4096)
                if not chunk:
                    continue
                self._buffer += chunk
                if len(self._buffer) > MAX_SERIAL_BUFFER_BYTES:
                    self._buffer = self._buffer[-MAX_SERIAL_BUFFER_BYTES // 2 :]
                while b"\n" in self._buffer:
                    line, self._buffer = self._buffer.split(b"\n", 1)
                    self._handle_line(line)
            except (OSError, select.error) as exc:
                if not self._stop.is_set():
                    with self._state_lock:
                        self.last_error = "%s: %s" % (self.device, exc)
                    time.sleep(0.1)

    def status(self):
        with self._state_lock:
            if self.last_status is None:
                raise RuntimeError("no ATmega status has been received")
            return dict(self.last_status)

    def health(self):
        with self._state_lock:
            age = (
                None
                if self.last_status_at is None
                else max(0.0, time.time() - self.last_status_at)
            )
            return {
                "v": 1,
                "service": "yun-stepper-bridge",
                "device": self.device,
                "status_age_s": age,
                "error": self.last_error,
                "command_synchronized": self._command_synchronized,
            }

    def _write_all(self, payload, deadline):
        offset = 0
        while offset < len(payload):
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RuntimeError("timed out writing command to ATmega")
            _, writable, _ = select.select([], [self.fd], [], remaining)
            if not writable:
                raise RuntimeError("timed out writing command to ATmega")
            written = os.write(self.fd, payload[offset:])
            if written <= 0:
                raise RuntimeError("ATmega UART accepted no command bytes")
            offset += written

    def command(self, command):
        normalized = validate_command(command)
        with self._command_lock:
            if not self._command_synchronized:
                raise RuntimeError(
                    "ATmega command channel is unsynchronized after a timeout; "
                    "restart yun-stepper-bridge before sending another command"
                )
            with self._state_lock:
                prior_generation = self._ack_generation
            deadline = time.time() + self.ack_timeout
            try:
                self._write_all(_to_bytes(normalized + "\n"), deadline)
                with self._ack_condition:
                    while self._ack_generation == prior_generation:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            raise RuntimeError(
                                "ATmega command acknowledgement timed out"
                            )
                        self._ack_condition.wait(remaining)
                    acknowledgement = dict(self._last_ack or {})
            except RuntimeError:
                self._command_synchronized = False
                raise
        if acknowledgement.get("ok") != 1:
            raise CommandRejected(
                str(acknowledgement.get("e") or "ATmega rejected command")
            )
        return {
            "v": 1,
            "type": "ack",
            "accepted": True,
            "command": normalized,
        }


class CommandRejected(RuntimeError):
    pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class StepperBridgeHandler(BaseHTTPRequestHandler):
    server_version = "YunStepperBridge/1"

    def _send_json(self, status, payload):
        body = _to_bytes(json.dumps(payload, separators=(",", ":")))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def bridge(self):
        return self.server.bridge

    def do_GET(self):
        if self.path == "/v1/status":
            try:
                self._send_json(200, self.bridge.status())
            except RuntimeError as exc:
                self._send_json(503, {"v": 1, "error": str(exc)})
            return
        if self.path == "/v1/health":
            self._send_json(200, self.bridge.health())
            return
        self._send_json(404, {"v": 1, "error": "not found"})

    def do_POST(self):
        if self.path != "/v1/command":
            self._send_json(404, {"v": 1, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 1 or length >= MAX_COMMAND_BYTES:
            self._send_json(400, {"v": 1, "error": "invalid command length"})
            return
        body = self.rfile.read(length)
        try:
            acknowledgement = self.bridge.command(body)
        except ValueError as exc:
            self._send_json(400, {"v": 1, "error": str(exc)})
        except CommandRejected as exc:
            self._send_json(409, {"v": 1, "error": str(exc)})
        except RuntimeError as exc:
            self._send_json(504, {"v": 1, "error": str(exc)})
        else:
            self._send_json(200, acknowledgement)

    def log_message(self, message_format, *args):
        sys.stderr.write(
            "%s - - [%s] %s\n"
            % (self.address_string(), self.log_date_time_string(), message_format % args)
        )


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--ack-timeout", type=float, default=DEFAULT_ACK_TIMEOUT_S
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    bridge = SerialBridgeState(args.device, args.baud, args.ack_timeout)
    bridge.open()
    server = ThreadedHTTPServer((args.host, args.port), StepperBridgeHandler)
    server.bridge = bridge

    def stop(_signum, _frame):
        # ``shutdown`` must run outside the serve_forever thread.
        thread = threading.Thread(target=server.shutdown)
        thread.daemon = True
        thread.start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        "Yun stepper bridge listening on http://%s:%d using %s"
        % (args.host, args.port, args.device),
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        bridge.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
