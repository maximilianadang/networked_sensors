#!/usr/bin/env python3
"""Read republished DXMR90-4k sensor measurements over Modbus TCP.

The DXM script described in the exported notes republishes useful SICK flow
sensor values into local registers 13001, 13003, ... as 32-bit floats. This
reader uses only Python's standard library.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import socket
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence


DEFAULT_HOST = "192.168.0.1"
DEFAULT_PORT = 502
DEFAULT_UNIT_ID = 1
SICK_PROCESS_BASE_REGISTERS = (1002, 2002)
SICK_PROCESS_WORD_COUNT = 16
PSI_PER_BAR = 14.503773773


@dataclass(frozen=True)
class Metric:
    name: str
    register: int
    unit: str
    kind: str = "float32"

    @property
    def register_span(self) -> str:
        if self.kind == "uint16":
            return str(self.register)
        return f"{self.register}-{self.register + 1}"


HEARTBEAT = Metric("heartbeat", 12001, "count", "uint16")

MEASUREMENTS: tuple[Metric, ...] = (
    Metric("port1_mass_flow_g_min", 13001, "g/min"),
    Metric("port2_mass_flow_g_min", 13003, "g/min"),
    Metric("total_mass_flow_g_min", 13005, "g/min"),
    Metric("port1_pressure_psi", 13007, "psi"),
    Metric("port2_pressure_psi", 13009, "psi"),
    Metric("pressure_delta_p1_minus_p2_psi", 13011, "psi"),
    Metric("port1_pressure_bar", 13013, "bar"),
    Metric("port2_pressure_bar", 13015, "bar"),
    Metric("pressure_delta_p1_minus_p2_bar", 13017, "bar"),
    Metric("port1_temperature_c", 13019, "C"),
    Metric("port2_temperature_c", 13021, "C"),
    Metric("temperature_delta_p1_minus_p2_c", 13023, "C"),
    Metric("port1_volumetric_flow_l_min", 13025, "L/min"),
    Metric("port2_volumetric_flow_l_min", 13027, "L/min"),
    Metric("total_volumetric_flow_l_min", 13029, "L/min"),
    Metric("port1_flow_velocity_m_s", 13031, "m/s"),
    Metric("port2_flow_velocity_m_s", 13033, "m/s"),
    Metric("port1_mass_counter_g", 13035, "g"),
    Metric("port2_mass_counter_g", 13037, "g"),
    Metric("total_mass_counter_g", 13039, "g"),
    Metric("port1_volume_counter_l", 13041, "L"),
    Metric("port2_volume_counter_l", 13043, "L"),
    Metric("total_volume_counter_l", 13045, "L"),
    Metric("port1_energy_counter_wh", 13047, "Wh"),
    Metric("port2_energy_counter_wh", 13049, "Wh"),
    Metric("total_energy_counter_wh", 13051, "Wh"),
    Metric("temperature_delta_p2_minus_p1_c", 13053, "C"),
    Metric("absolute_temperature_delta_c", 13055, "C"),
    Metric("temperature_delta_p1_minus_p2_x10", 13057, "C x10"),
)

CORE_NAMES = {
    "heartbeat",
    "port1_mass_flow_g_min",
    "port2_mass_flow_g_min",
    "total_mass_flow_g_min",
    "port1_pressure_psi",
    "port2_pressure_psi",
    "pressure_delta_p1_minus_p2_psi",
    "port1_pressure_bar",
    "port2_pressure_bar",
    "pressure_delta_p1_minus_p2_bar",
    "port1_temperature_c",
    "port2_temperature_c",
    "total_volumetric_flow_l_min",
}


class ModbusError(RuntimeError):
    pass


class ModbusTcpClient:
    def __init__(
        self,
        host: str,
        port: int,
        unit_id: int,
        timeout: float,
        addressing: str,
    ) -> None:
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.addressing = addressing
        self._transaction_id = 0
        self._sock: socket.socket | None = None

    def __enter__(self) -> "ModbusTcpClient":
        self._sock = socket.create_connection((self.host, self.port), self.timeout)
        self._sock.settimeout(self.timeout)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._sock is not None:
            self._sock.close()
        self._sock = None

    def read_holding_registers(self, register: int, quantity: int) -> list[int]:
        if self._sock is None:
            raise ModbusError("client is not connected")
        if quantity < 1 or quantity > 125:
            raise ValueError("Modbus quantity must be 1..125 registers")

        address = register_to_protocol_address(register, self.addressing)
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        function_code = 3
        pdu = struct.pack(">BHH", function_code, address, quantity)
        mbap_length = 1 + len(pdu)
        request = struct.pack(
            ">HHHB",
            self._transaction_id,
            0,
            mbap_length,
            self.unit_id,
        ) + pdu

        self._sock.sendall(request)
        header = recv_exact(self._sock, 7)
        response_transaction, protocol_id, length, response_unit = struct.unpack(
            ">HHHB", header
        )
        if response_transaction != self._transaction_id:
            raise ModbusError(
                f"transaction mismatch: sent {self._transaction_id}, "
                f"got {response_transaction}"
            )
        if protocol_id != 0:
            raise ModbusError(f"unexpected protocol id {protocol_id}")
        if response_unit != self.unit_id:
            raise ModbusError(
                f"unit id mismatch: requested {self.unit_id}, got {response_unit}"
            )

        pdu_response = recv_exact(self._sock, length - 1)
        response_function = pdu_response[0]
        if response_function & 0x80:
            exception_code = pdu_response[1] if len(pdu_response) > 1 else None
            raise ModbusError(
                f"Modbus exception for register {register}: code {exception_code}"
            )
        if response_function != function_code:
            raise ModbusError(f"unexpected function code {response_function}")

        byte_count = pdu_response[1]
        expected_count = quantity * 2
        if byte_count != expected_count:
            raise ModbusError(
                f"expected {expected_count} data bytes, got {byte_count}"
            )
        payload = pdu_response[2 : 2 + byte_count]
        return list(struct.unpack(f">{quantity}H", payload))


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ModbusError("connection closed while reading Modbus response")
        chunks.extend(chunk)
    return bytes(chunks)


def register_to_protocol_address(register: int, addressing: str) -> int:
    address = register - 1 if addressing == "one-based" else register
    if address < 0 or address > 0xFFFF:
        raise ValueError(f"register {register} maps to invalid address {address}")
    return address


def decode_float32(words: Sequence[int], word_order: str) -> float:
    if len(words) != 2:
        raise ValueError("float32 decode requires exactly two registers")
    first, second = words
    if word_order == "low-high":
        first, second = second, first
    return struct.unpack(">f", struct.pack(">HH", first, second))[0]


def read_direct_sick_values(
    client: ModbusTcpClient,
    word_order: str,
) -> dict[str, float | int]:
    """Read both live SICK IO-Link process windows and return dashboard units."""

    heartbeat = client.read_holding_registers(HEARTBEAT.register, 1)[0]
    ports: list[dict[str, float]] = []
    process_names = (
        "mass_flow_kg_h",
        "flow_velocity_m_s",
        "volume_m3",
        "volumetric_flow_m3_h",
        "mass_kg",
        "energy_kwh",
        "temperature_c",
        "pressure_bar",
    )
    for base_register in SICK_PROCESS_BASE_REGISTERS:
        words = client.read_holding_registers(
            base_register,
            SICK_PROCESS_WORD_COUNT,
        )
        ports.append(
            {
                name: decode_float32(words[offset : offset + 2], word_order)
                for name, offset in zip(process_names, range(0, 16, 2))
            }
        )

    port1, port2 = ports
    p1_bar = port1["pressure_bar"]
    p2_bar = port2["pressure_bar"]
    p1_mass_g_min = port1["mass_flow_kg_h"] * 1000.0 / 60.0
    p2_mass_g_min = port2["mass_flow_kg_h"] * 1000.0 / 60.0
    p1_volume_l_min = port1["volumetric_flow_m3_h"] * 1000.0 / 60.0
    p2_volume_l_min = port2["volumetric_flow_m3_h"] * 1000.0 / 60.0

    return {
        "heartbeat": heartbeat,
        "port1_mass_flow_g_min": p1_mass_g_min,
        "port2_mass_flow_g_min": p2_mass_g_min,
        "total_mass_flow_g_min": p1_mass_g_min + p2_mass_g_min,
        "port1_pressure_psi": p1_bar * PSI_PER_BAR,
        "port2_pressure_psi": p2_bar * PSI_PER_BAR,
        "pressure_delta_p1_minus_p2_psi": (p1_bar - p2_bar) * PSI_PER_BAR,
        "port1_pressure_bar": p1_bar,
        "port2_pressure_bar": p2_bar,
        "pressure_delta_p1_minus_p2_bar": p1_bar - p2_bar,
        "port1_temperature_c": port1["temperature_c"],
        "port2_temperature_c": port2["temperature_c"],
        "total_volumetric_flow_l_min": p1_volume_l_min + p2_volume_l_min,
    }


def select_metrics(group: str, names: Sequence[str]) -> list[Metric]:
    by_name = {metric.name: metric for metric in (HEARTBEAT, *MEASUREMENTS)}
    if names:
        missing = [name for name in names if name not in by_name]
        if missing:
            available = ", ".join(sorted(by_name))
            raise SystemExit(
                f"Unknown metric(s): {', '.join(missing)}\nAvailable: {available}"
            )
        return [by_name[name] for name in names]

    if group == "all":
        return [HEARTBEAT, *MEASUREMENTS]
    return [metric for metric in (HEARTBEAT, *MEASUREMENTS) if metric.name in CORE_NAMES]


def read_metrics(
    client: ModbusTcpClient,
    metrics: Sequence[Metric],
    word_order: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    if any(metric.kind == "uint16" for metric in metrics):
        heartbeat_words = client.read_holding_registers(HEARTBEAT.register, 1)
    else:
        heartbeat_words = []

    float_metrics = [metric for metric in metrics if metric.kind == "float32"]
    float_words_by_register: dict[int, int] = {}
    if float_metrics:
        start = min(metric.register for metric in float_metrics)
        end = max(metric.register + 1 for metric in float_metrics)
        words = client.read_holding_registers(start, end - start + 1)
        float_words_by_register = {start + index: word for index, word in enumerate(words)}

    for metric in metrics:
        if metric.kind == "uint16":
            value: int | float = heartbeat_words[0]
            raw_words = heartbeat_words
        else:
            raw_words = [
                float_words_by_register[metric.register],
                float_words_by_register[metric.register + 1],
            ]
            value = decode_float32(raw_words, word_order)
        rows.append(
            {
                "name": metric.name,
                "register": metric.register,
                "register_span": metric.register_span,
                "value": value,
                "unit": metric.unit,
                "raw_words": raw_words,
            }
        )
    return rows


def format_value(value: object) -> str:
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        return f"{value:.6g}"
    return str(value)


def print_table(timestamp: str, rows: Sequence[dict[str, object]], raw: bool) -> None:
    name_width = max(len(str(row["name"])) for row in rows)
    print(timestamp)
    for row in rows:
        raw_text = ""
        if raw:
            raw_text = " raw=" + ",".join(str(word) for word in row["raw_words"])
        print(
            f"{str(row['name']):<{name_width}}  "
            f"{format_value(row['value']):>12}  "
            f"{str(row['unit']):<8}  "
            f"reg {row['register_span']}{raw_text}"
        )


def emit_rows(
    output_format: str,
    timestamp: str,
    rows: Sequence[dict[str, object]],
    raw: bool,
    csv_writer: csv.DictWriter | None,
) -> None:
    if output_format == "table":
        print_table(timestamp, rows, raw)
        return

    if output_format == "json":
        payload = {
            "timestamp": timestamp,
            "measurements": rows if raw else strip_raw(rows),
        }
        print(json.dumps(payload, separators=(",", ":")))
        return

    if csv_writer is None:
        raise RuntimeError("CSV writer was not initialized")
    for row in rows:
        csv_writer.writerow(
            {
                "timestamp": timestamp,
                "name": row["name"],
                "register": row["register"],
                "register_span": row["register_span"],
                "value": row["value"],
                "unit": row["unit"],
                "raw_words": ",".join(str(word) for word in row["raw_words"])
                if raw
                else "",
            }
        )


def strip_raw(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {key: value for key, value in row.items() if key != "raw_words"} for row in rows
    ]


def metric_names() -> str:
    names = [HEARTBEAT.name, *(metric.name for metric in MEASUREMENTS)]
    return "\n".join(f"  {name}" for name in names)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read DXMR90-4k republished measurements over Modbus TCP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available metrics:\n{metric_names()}",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="DXM IP address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Modbus TCP port")
    parser.add_argument(
        "--unit-id",
        type=int,
        default=DEFAULT_UNIT_ID,
        help="Modbus unit id/slave id",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="socket timeout in seconds",
    )
    parser.add_argument(
        "--addressing",
        choices=("one-based", "zero-based"),
        default="one-based",
        help="how documented register numbers map to Modbus protocol addresses",
    )
    parser.add_argument(
        "--word-order",
        choices=("high-low", "low-high"),
        default="high-low",
        help="32-bit float word order",
    )
    parser.add_argument(
        "--group",
        choices=("core", "all"),
        default="core",
        help="metric group to read when --metric is not used",
    )
    parser.add_argument(
        "--metric",
        action="append",
        default=[],
        help="specific metric name to read; may be repeated",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="poll interval in seconds; omit or set 0 to read once",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json", "csv"),
        default="table",
        help="output format",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="include raw 16-bit register words in output",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    metrics = select_metrics(args.group, args.metric)

    csv_writer = None
    if args.format == "csv":
        fieldnames = [
            "timestamp",
            "name",
            "register",
            "register_span",
            "value",
            "unit",
            "raw_words",
        ]
        csv_writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        csv_writer.writeheader()

    while True:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        try:
            with ModbusTcpClient(
                args.host,
                args.port,
                args.unit_id,
                args.timeout,
                args.addressing,
            ) as client:
                rows = read_metrics(client, metrics, args.word_order)
        except (OSError, ModbusError, ValueError) as exc:
            print(f"DXMR90 read failed: {exc}", file=sys.stderr)
            return 1

        emit_rows(args.format, timestamp, rows, args.raw, csv_writer)
        sys.stdout.flush()

        if args.interval <= 0:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
