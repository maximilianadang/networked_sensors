"""Read-only ED-593 DCON ASCII transport (TCP 9500).

Configure channels and thermocouple types on the device. This reader never
writes configuration. Only Celsius temperature format is accepted; alternate
formats, rejected commands and ambiguous replies produce transport errors.
Protocol references and commissioning notes: devices/ed593/setup.txt.
"""
from __future__ import annotations

import math
import re
import socket
import threading
import time

DEFAULT_HOST = "10.77.0.11"  # Proposed bench address, not an assigned device IP.
DEFAULT_PORT = 9500
CHANNELS = tuple(f"tc{i}" for i in range(8))
EXPECTED_FIELDS = ("ed593_transport_error", "ed593_model", "ed593_format", "ed593_enabled_mask",
                   "ed593_diagnostic_mask") + tuple(
    f"ed593_{channel}_{suffix}" for channel in CHANNELS
    for suffix in ("temperature_c", "enabled", "fault", "ready")
)


class Ed593Client:
    def __init__(self, host: str, port: int = DEFAULT_PORT, address: int = 1,
                 timeout: float = 1.0, checksum: bool = False):
        if not host.strip() or not 1 <= port <= 65535 or not 0 <= address <= 255:
            raise ValueError("ED-593 requires host, port 1..65535, address 0..255")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("ED-593 timeout must be positive and finite")
        self.host, self.port, self.address = host, port, address
        self.timeout, self.checksum = timeout, checksum
        self.connection = None
        self.buffer = b""

    def __enter__(self):
        self.connection = socket.create_connection((self.host, self.port), self.timeout)
        return self

    def __exit__(self, *args):
        if self.connection is not None:
            self.connection.close()

    def query(self, command: str) -> str:
        # Keep the transport read-only even if a caller supplies another command.
        if command not in (f"${self.address:02X}2", f"${self.address:02X}6",
                           f"${self.address:02X}B", f"${self.address:02X}M0", f"#{self.address:02X}"):
            raise ValueError("Unsupported ED-593 read command")
        packet = command.encode("ascii")
        if self.checksum:
            packet += f"{sum(packet) & 255:02X}".encode("ascii")
        self.connection.sendall(packet + b"\r")
        deadline = time.monotonic() + self.timeout
        while b"\r" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ED-593 response deadline exceeded")
            self.connection.settimeout(remaining)
            chunk = self.connection.recv(512)
            if not chunk:
                raise ConnectionError("ED-593 connection closed before response")
            self.buffer += chunk
            if len(self.buffer) > 2048:
                raise ValueError("ED-593 response exceeds 2048 bytes")
        packet, self.buffer = self.buffer.split(b"\r", 1)
        packet = packet.lstrip(b"\n")
        if self.checksum:
            if len(packet) < 3 or packet[-2:] != f"{sum(packet[:-2]) & 255:02X}".encode():
                raise ValueError("Invalid ED-593 response checksum")
            packet = packet[:-2]
        reply = packet.decode("ascii")
        if reply.startswith("?"):
            raise ValueError(f"ED-593 rejected {command}: {reply}")
        return reply

    def read(self) -> dict[str, object]:
        prefix = f"!{self.address:02X}"
        model = self.query(f"${self.address:02X}M0")
        if model != prefix + "ED-593":
            raise ValueError(f"Expected ED-593 model reply, received {model!r}")
        config = self.query(f"${self.address:02X}2")
        if not re.fullmatch(re.escape(prefix) + r"[0-9A-F]{6}", config):
            raise ValueError("Invalid ED-593 configuration reply")
        flags = int(config[-2:], 16)
        if flags & 0x0F or bool(flags & 0x40) != self.checksum:
            raise ValueError("Configure ED-593 for Celsius temperature format and matching checksum setting")
        masks = []
        for suffix in ("6", "B"):
            reply = self.query(f"${self.address:02X}{suffix}")
            if not re.fullmatch(re.escape(prefix) + r"[0-9A-F]{2}", reply):
                raise ValueError("Invalid ED-593 channel mask reply")
            masks.append(int(reply[-2:], 16))
        reply = self.query(f"#{self.address:02X}")
        # Signed decimal fields are concatenated without separators.
        fields = re.findall(r"[+-]\d+\.\d+", reply[1:])
        if not reply.startswith(">") or len(fields) != 8 or "".join(fields) != reply[1:]:
            raise ValueError("Expected eight signed Celsius values from ED-593")
        return channel_values([float(value) for value in fields], *masks)


def channel_values(temperatures, enabled_mask, diagnostic_mask):
    if len(temperatures) != 8 or not all(math.isfinite(v) for v in temperatures):
        raise ValueError("ED-593 temperatures must contain eight finite numbers")
    values = {"ed593_model": "ED-593", "ed593_format": "celsius", "ed593_enabled_mask": enabled_mask,
              "ed593_diagnostic_mask": diagnostic_mask}
    for index, temperature in enumerate(temperatures):
        enabled = bool(enabled_mask & (1 << index))
        fault = bool(diagnostic_mask & (1 << index))
        values.update({f"ed593_tc{index}_temperature_c": temperature if enabled and not fault else None,
                       f"ed593_tc{index}_enabled": enabled,
                       f"ed593_tc{index}_fault": fault,
                       f"ed593_tc{index}_ready": enabled and not fault})
    return values


class RealEd593Source:
    name, mode = "ed593", "real"
    expected_fields = EXPECTED_FIELDS

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, address=1,
                 timeout=1.0, checksum=False, rate_hz=1.0):
        Ed593Client(host, port, address, timeout, checksum)  # Validate without I/O.
        if not math.isfinite(rate_hz) or rate_hz <= 0:
            raise ValueError("ED-593 rate must be positive and finite")
        self.settings = dict(host=host, port=port, address=address, timeout=timeout, checksum=checksum)
        self.period_s = 1.0 / rate_hz
        self.last_error = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._values = None
        self._generation = self._emitted = 0
        self._received_at = None

    def _fetch(self):
        try:
            with Ed593Client(**self.settings) as client:
                values = client.read()
        except (OSError, ValueError) as exc:
            with self._lock:
                self.last_error = str(exc)
            return
        with self._lock:
            self._values = values
            self._received_at = time.monotonic()
            self._generation += 1
            self.last_error = None

    def _run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            self._fetch()
            self._stop.wait(max(0, self.period_s - (time.monotonic() - started)))

    def poll(self, elapsed_s):
        from supervisor_core import SourceReading
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="ed593-ascii", daemon=True)
            self._thread.start()
        with self._lock:
            if self._values is None or self._generation == self._emitted:
                return None
            self._emitted = self._generation
            # Age starts at response reception, not when dashboard polling consumes it.
            age = max(0, time.monotonic() - self._received_at)
            return SourceReading(self.name, self.mode, elapsed_s - age, dict(self._values))

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5 * self.settings["timeout"] + 1)


class SimulatedEd593Source:
    name, mode = "ed593", "sim"
    expected_fields = EXPECTED_FIELDS
    period_s = 1.0
    last_error = None

    def __init__(self):
        self._next = 0

    def poll(self, elapsed_s):
        from supervisor_core import SourceReading
        if elapsed_s < self._next:
            return None
        self._next = elapsed_s + self.period_s
        values = channel_values([25 + i + math.sin(elapsed_s / 10) for i in range(8)], 0x0F, 0)
        return SourceReading(self.name, self.mode, elapsed_s, values)
