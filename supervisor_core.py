#!/usr/bin/env python3
"""Core schema, simulated sources, and merge loop for the bench supervisor."""

from __future__ import annotations

import json
import math
import os
import termios
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from typing import BinaryIO, Iterator, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import ProxyHandler, Request, build_opener

try:
    from .read_dxmr90_modbus import (
        CORE_NAMES,
        DEFAULT_HOST as DEFAULT_DXMR90_HOST,
        DEFAULT_PORT as DEFAULT_DXMR90_PORT,
        DEFAULT_UNIT_ID as DEFAULT_DXMR90_UNIT_ID,
        HEARTBEAT,
        MEASUREMENTS,
        Metric,
        ModbusError,
        ModbusTcpClient,
        read_direct_sick_values,
        read_metrics,
    )
except ImportError:  # pragma: no cover - direct script execution fallback
    from read_dxmr90_modbus import (
        CORE_NAMES,
        DEFAULT_HOST as DEFAULT_DXMR90_HOST,
        DEFAULT_PORT as DEFAULT_DXMR90_PORT,
        DEFAULT_UNIT_ID as DEFAULT_DXMR90_UNIT_ID,
        HEARTBEAT,
        MEASUREMENTS,
        Metric,
        ModbusError,
        ModbusTcpClient,
        read_direct_sick_values,
        read_metrics,
    )


ESP32_PERIOD_S = 0.1
ESP32_PAYLOAD_VERSION = 2
ESP32_SOLENOID_COUNT = 4
DEFAULT_ESP32_BASE_URL = "http://testbench.local"
DEFAULT_ESP32_TIMEOUT_S = 2.0
DEFAULT_ESP32_RECONNECT_S = 0.5
DXMR90_PERIOD_S = 1.0
STEPPER_PERIOD_S = 0.1
DEFAULT_DXMR90_REAL_RATE_HZ = 10.0
MERGE_PERIOD_S = 0.1
DEFAULT_DROP_AFTER_S = 2.0
DEFAULT_STALE_AFTER_S = 5.0
SIMULATION_SCENARIOS = (
    "healthy",
    "esp32_stale",
    "dxmr90_stale",
    "dxmr90_missing",
    "stepper_stale",
    "stepper_missing",
    "all_stale",
)
SOURCE_MODES = ("sim", "real", "off")
STEPPER_SOURCE_MODES = ("sim", "usb", "network", "off")
DXMR90_DATA_PATHS = ("direct", "republished")
DEFAULT_STEPPER_USB_PORT = "/dev/ttyACM0"
DEFAULT_STEPPER_USB_BAUD = 9600
DEFAULT_STEPPER_NETWORK_URL = "http://arduino.local:8080"
DEFAULT_STEPPER_NETWORK_TIMEOUT_S = 0.75
DEFAULT_STEPPER_MM_PER_PULSE = 0.00396875
DEFAULT_STEPPER_STEPS_PER_MM = 1.0 / DEFAULT_STEPPER_MM_PER_PULSE
DEFAULT_STEPPER_TRAVEL_MM = 137.18
DEFAULT_STEPPER_HOME_SPEED_MM_S = 1.5

# Quantity bounds only. They are not an absolute-position safety envelope;
# D6/D8 stop travel into their respective ends.
DEFAULT_STEPPER_MAX_DISTANCE_MM = DEFAULT_STEPPER_TRAVEL_MM
DEFAULT_STEPPER_MAX_SPEED_MM_S = 10.0
DEFAULT_STEPPER_ACCELERATION_MM_S2 = 5.0
DEFAULT_STEPPER_MIN_SPEED_SPS = round(0.1 * DEFAULT_STEPPER_STEPS_PER_MM)
DEFAULT_STEPPER_MAX_SPEED_SPS = round(
    DEFAULT_STEPPER_MAX_SPEED_MM_S * DEFAULT_STEPPER_STEPS_PER_MM
)

DXMR90_CORE_METRICS: tuple[Metric, ...] = tuple(
    metric for metric in (HEARTBEAT, *MEASUREMENTS) if metric.name in CORE_NAMES
)


@dataclass(frozen=True)
class SourceReading:
    """One source update, timestamped in supervisor elapsed time."""

    source: str
    mode: str
    elapsed_s: float
    values: Mapping[str, float | int | bool | str | None]


class SourceAdapter(Protocol):
    """Minimal synchronous source adapter contract for Step 1."""

    name: str
    mode: str
    period_s: float
    expected_fields: tuple[str, ...]

    def poll(self, elapsed_s: float) -> SourceReading | None:
        """Return a fresh reading if this source is due, otherwise None."""


class DisabledSource:
    """Schema-preserving source placeholder for absent hardware."""

    mode = "off"

    def __init__(
        self,
        name: str,
        period_s: float,
        expected_fields: tuple[str, ...],
    ) -> None:
        self.name = name
        self.period_s = period_s
        self.expected_fields = expected_fields

    def poll(self, elapsed_s: float) -> SourceReading | None:
        return None


class PeriodicSource:
    """Base class for deterministic simulation sources."""

    name: str
    mode = "sim"
    period_s: float
    expected_fields: tuple[str, ...] = ()

    def __init__(
        self,
        drop_after_s: float | None = None,
        start_after_s: float = 0.0,
    ) -> None:
        self.drop_after_s = drop_after_s
        self.start_after_s = start_after_s
        self._next_due_s = 0.0

    def poll(self, elapsed_s: float) -> SourceReading | None:
        if elapsed_s + 1e-9 < self.start_after_s:
            return None
        if self.drop_after_s is not None and elapsed_s + 1e-9 >= self.drop_after_s:
            return None
        if elapsed_s + 1e-9 < self._next_due_s:
            return None
        while self._next_due_s <= elapsed_s + 1e-9:
            self._next_due_s += self.period_s
        return SourceReading(
            source=self.name,
            mode=self.mode,
            elapsed_s=elapsed_s,
            values=self._read(elapsed_s),
        )

    def _read(self, elapsed_s: float) -> Mapping[str, float | int | bool | str | None]:
        raise NotImplementedError


class SimulatedEsp32Source(PeriodicSource):
    """Deterministic 10 Hz ESP32-like pressure, flow, and solenoid source."""

    name = "esp32"
    period_s = ESP32_PERIOD_S
    expected_fields = (
        "esp32_payload_version",
        "esp32_sample_ms",
        "esp32_p1_bar",
        "esp32_p2_bar",
        "esp32_p3_bar",
        "esp32_f1_gmin",
        "esp32_f2_gmin",
        "esp32_f3_gmin",
        "esp32_p_combined_bar",
        "esp32_f_combined_gmin",
        "esp32_sol1",
        "esp32_sol2",
        "esp32_sol3",
        "esp32_sol4",
        "esp32_p1_volt",
        "esp32_p2_volt",
        "esp32_p3_volt",
        "esp32_f1_volt",
        "esp32_f2_volt",
        "esp32_f3_volt",
        "esp32_transport_error",
    )

    def __init__(
        self,
        drop_after_s: float | None = None,
        start_after_s: float = 0.0,
        auto_sequence: bool = True,
    ) -> None:
        super().__init__(drop_after_s=drop_after_s, start_after_s=start_after_s)
        self._solenoids = [False] * ESP32_SOLENOID_COUNT
        self.auto_sequence = auto_sequence

    def set_solenoid(self, index: int, on: bool) -> None:
        if index < 0 or index >= len(self._solenoids):
            raise ValueError("solenoid index must be 0, 1, 2, or 3")
        self._solenoids[index] = on
        self._next_due_s = 0.0

    def toggle_solenoid(self, index: int) -> bool:
        if index < 0 or index >= len(self._solenoids):
            raise ValueError("solenoid index must be 0, 1, 2, or 3")
        self._solenoids[index] = not self._solenoids[index]
        self._next_due_s = 0.0
        return self._solenoids[index]

    def solenoid_states(self) -> tuple[bool, ...]:
        return tuple(self._solenoids)

    def _read(self, elapsed_s: float) -> Mapping[str, float | int | bool | str | None]:
        pulse_phase = elapsed_s % 6.0
        active = 0.8 <= pulse_phase <= 4.2
        if self.auto_sequence:
            if elapsed_s >= 2.0:
                self._solenoids[0] = True
            if elapsed_s >= 3.5:
                self._solenoids[1] = True

        pressures = [
            2.15 + 0.18 * math.sin(0.65 * elapsed_s),
            2.05 + 0.12 * math.sin(0.65 * elapsed_s + 0.7),
            2.22 + 0.10 * math.sin(0.65 * elapsed_s + 1.2),
        ]
        if active:
            envelope = math.sin(math.pi * (pulse_phase - 0.8) / 3.4)
            flows = [
                42.0 + 8.0 * envelope,
                37.0 + 6.0 * envelope,
                31.0 + 5.0 * envelope,
            ]
        else:
            flows = [0.0, 0.0, 0.0]

        p_combined = min(pressures)
        f_combined = sum(flows)
        values: dict[str, float | int | bool | str | None] = {
            "esp32_payload_version": ESP32_PAYLOAD_VERSION,
            "esp32_sample_ms": round(elapsed_s * 1000.0),
            "esp32_p1_bar": round(pressures[0], 3),
            "esp32_p2_bar": round(pressures[1], 3),
            "esp32_p3_bar": round(pressures[2], 3),
            "esp32_f1_gmin": round(flows[0], 2),
            "esp32_f2_gmin": round(flows[1], 2),
            "esp32_f3_gmin": round(flows[2], 2),
            "esp32_p_combined_bar": round(p_combined, 3),
            "esp32_f_combined_gmin": round(f_combined, 2),
        }
        for index, on in enumerate(self._solenoids, start=1):
            values[f"esp32_sol{index}"] = on
        for index, pressure in enumerate(pressures, start=1):
            values[f"esp32_p{index}_volt"] = round(0.5 + pressure / 10.0 * 4.0, 4)
        for index, flow in enumerate(flows, start=1):
            values[f"esp32_f{index}_volt"] = round(1.0 + flow / 258.58 * 4.0, 4)
        return values


class SimulatedDxmr90Source(PeriodicSource):
    """Deterministic 1 Hz DXMR90-like Modbus metrics source."""

    name = "dxmr90"
    period_s = DXMR90_PERIOD_S
    expected_fields = tuple(f"dxmr90_{metric.name}" for metric in DXMR90_CORE_METRICS)

    def _read(self, elapsed_s: float) -> Mapping[str, float | int | bool | str | None]:
        heartbeat = int(elapsed_s // self.period_s) + 1
        flow_wave = max(0.0, math.sin(0.35 * elapsed_s))
        values: dict[str, float | int | bool | str | None] = {}
        for metric in DXMR90_CORE_METRICS:
            key = f"dxmr90_{metric.name}"
            if metric.name == "heartbeat":
                values[key] = heartbeat
            elif metric.name == "port1_mass_flow_g_min":
                values[key] = round(28.0 + 12.0 * flow_wave, 3)
            elif metric.name == "port2_mass_flow_g_min":
                values[key] = round(24.0 + 10.0 * flow_wave, 3)
            elif metric.name == "total_mass_flow_g_min":
                values[key] = round(52.0 + 22.0 * flow_wave, 3)
            elif metric.name == "port1_pressure_psi":
                values[key] = round(30.0 + 2.0 * math.sin(0.2 * elapsed_s), 3)
            elif metric.name == "port2_pressure_psi":
                values[key] = round(28.0 + 1.5 * math.sin(0.2 * elapsed_s + 0.5), 3)
            elif metric.name == "pressure_delta_p1_minus_p2_psi":
                p1 = float(values.get("dxmr90_port1_pressure_psi", 0.0))
                p2 = float(values.get("dxmr90_port2_pressure_psi", 0.0))
                values[key] = round(p1 - p2, 3)
            elif metric.name == "port1_pressure_bar":
                values[key] = round(
                    float(values.get("dxmr90_port1_pressure_psi", 0.0)) / 14.503773773,
                    4,
                )
            elif metric.name == "port2_pressure_bar":
                values[key] = round(
                    float(values.get("dxmr90_port2_pressure_psi", 0.0)) / 14.503773773,
                    4,
                )
            elif metric.name == "pressure_delta_p1_minus_p2_bar":
                p1 = float(values.get("dxmr90_port1_pressure_bar", 0.0))
                p2 = float(values.get("dxmr90_port2_pressure_bar", 0.0))
                values[key] = round(p1 - p2, 4)
            elif metric.name == "port1_temperature_c":
                values[key] = round(22.0 + 0.3 * math.sin(0.1 * elapsed_s), 3)
            elif metric.name == "port2_temperature_c":
                values[key] = round(22.4 + 0.2 * math.sin(0.1 * elapsed_s + 0.4), 3)
            elif metric.name == "total_volumetric_flow_l_min":
                values[key] = round(44.0 + 18.0 * flow_wave, 3)
        return values


class SimulatedStepperSource:
    """Bounded open-loop stepper model with the future Yún status shape."""

    name = "stepper"
    mode = "sim"
    period_s = STEPPER_PERIOD_S
    expected_fields = (
        "stepper_local_enabled",
        "stepper_command_capable",
        "stepper_speed_command_capable",
        "stepper_direction_command_capable",
        "stepper_mode_command_capable",
        "stepper_home_capable",
        "stepper_estop_capable",
        "stepper_estop_latched",
        "stepper_control_owner",
        "stepper_control_mode",
        "stepper_homed",
        "stepper_boot_armed",
        "stepper_authorized_direction",
        "stepper_moving",
        "stepper_state",
        "stepper_direction",
        "stepper_position_mm",
        "stepper_target_mm",
        "stepper_remaining_mm",
        "stepper_speed_mm_s",
        "stepper_command_speed_mm_s",
        "stepper_acceleration_mm_s2",
        "stepper_command_id",
        "stepper_positive_limit_active",
        "stepper_negative_limit_active",
        "stepper_positive_limit_latched",
        "stepper_negative_limit_latched",
        "stepper_d6_raw",
        "stepper_d8_raw",
        "stepper_d4_raw",
        "stepper_d5_raw",
        "stepper_manual_direction",
        "stepper_direction_mapping",
        "stepper_blocked",
        "stepper_blocked_reason",
        "stepper_status_sequence",
        "stepper_transport_error",
        "stepper_position_semantics",
        "stepper_travel_min_mm",
        "stepper_travel_max_mm",
        "stepper_fault",
    )

    def __init__(
        self,
        *,
        drop_after_s: float | None = None,
        max_distance_mm: float = DEFAULT_STEPPER_MAX_DISTANCE_MM,
        max_speed_mm_s: float = DEFAULT_STEPPER_MAX_SPEED_MM_S,
    ) -> None:
        self.drop_after_s = drop_after_s
        self.max_distance_mm = max_distance_mm
        self.max_speed_mm_s = max_speed_mm_s
        self._next_due_s = 0.0
        self._last_elapsed_s = 0.0
        self._position_mm = DEFAULT_STEPPER_TRAVEL_MM / 2.0
        self._target_mm = self._position_mm
        self._velocity_mm_s = 0.0
        self._command_speed_mm_s = 0.0
        self._acceleration_mm_s2 = DEFAULT_STEPPER_ACCELERATION_MM_S2
        self._moving = False
        self._state = "idle"
        self._command_id: str | None = None
        self._command_counter = 0
        self._local_enabled = True
        self._positive_limit_active = False
        self._negative_limit_active = False
        self._positive_limit_latched = False
        self._negative_limit_latched = False
        self._fault: str | None = None
        self._control_mode = "web_position"
        self._homed = False
        self._estop_latched = False

    @staticmethod
    def _finite_number(value: object, field: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{field} must be a finite number")
        return number

    def move(
        self,
        distance_mm: object,
        speed_mm_s: object,
        command_id: object | None = None,
    ) -> dict[str, float | int | bool | str | None]:
        distance = self._finite_number(distance_mm, "distance_mm")
        speed = self._finite_number(speed_mm_s, "speed_mm_s")
        if self._estop_latched:
            raise RuntimeError("reset the software E-STOP before moving")
        if distance == 0:
            raise ValueError("distance_mm must be non-zero")
        if abs(distance) > self.max_distance_mm:
            raise ValueError(
                f"abs(distance_mm) must not exceed {self.max_distance_mm:g}"
            )
        if speed <= 0 or speed > self.max_speed_mm_s:
            raise ValueError(
                f"speed_mm_s must be greater than 0 and at most {self.max_speed_mm_s:g}"
            )
        if self._control_mode != "web_position":
            raise RuntimeError("select Web Position mode before moving")
        if self._moving:
            raise RuntimeError("stepper is busy")
        if not self._local_enabled:
            raise RuntimeError("local motion enable is off")
        if distance > 0 and (
            self._positive_limit_active or self._positive_limit_latched
        ):
            raise RuntimeError("positive limit blocks positive motion")
        if distance < 0 and (
            self._negative_limit_active or self._negative_limit_latched
        ):
            raise RuntimeError("negative limit blocks negative motion")
        target = self._position_mm + distance

        if command_id is None:
            self._command_counter += 1
            resolved_id = f"sim-{self._command_counter:06d}"
        else:
            resolved_id = str(command_id).strip()
            if not resolved_id or len(resolved_id) > 64:
                raise ValueError("command_id must contain 1 to 64 characters")

        # A command away from an end clears that end's transient latch. The raw
        # active input remains visible and still blocks movement into the end.
        if distance < 0:
            self._positive_limit_latched = False
        else:
            self._negative_limit_latched = False
        self._target_mm = target
        self._velocity_mm_s = 0.0
        self._command_speed_mm_s = speed
        self._acceleration_mm_s2 = DEFAULT_STEPPER_ACCELERATION_MM_S2
        self._moving = True
        self._state = "moving"
        self._command_id = resolved_id
        self._fault = None
        self._next_due_s = 0.0
        return self.status()

    def set_control_mode(
        self,
        web_position: object,
    ) -> dict[str, float | int | bool | str | None]:
        if not isinstance(web_position, bool):
            raise ValueError("web_position must be true or false")
        if self._estop_latched:
            raise RuntimeError("reset the software E-STOP before changing control mode")
        if self._moving:
            raise RuntimeError("stop motion before changing control mode")
        self._control_mode = "web_position" if web_position else "local_velocity"
        self._state = "ready" if web_position else "manual_stopped"
        self._next_due_s = 0.0
        return self.status()

    def home(self) -> dict[str, float | int | bool | str | None]:
        if self._estop_latched:
            raise RuntimeError("reset the software E-STOP before Home")
        if self._control_mode != "web_position":
            raise RuntimeError("select Web Position mode before Home")
        if self._moving:
            raise RuntimeError("stepper is busy")
        if not self._local_enabled:
            raise RuntimeError("local motion enable is off")
        self._position_mm = 0.0
        self._target_mm = 0.0
        self._velocity_mm_s = 0.0
        self._homed = True
        self._negative_limit_active = True
        self._negative_limit_latched = True
        self._state = "homed"
        self._fault = None
        self._next_due_s = 0.0
        return self.status()

    def stop(self, reason: str = "operator_stop") -> dict[str, float | int | bool | str | None]:
        self._moving = False
        self._velocity_mm_s = 0.0
        self._target_mm = self._position_mm
        self._state = "stopped"
        self._fault = reason
        self._next_due_s = 0.0
        return self.status()

    def emergency_stop(self) -> dict[str, float | int | bool | str | None]:
        """Latch the simulated software stop and inhibit all motion."""

        self._estop_latched = True
        self._moving = False
        self._velocity_mm_s = 0.0
        self._target_mm = self._position_mm
        self._state = "emergency_stop"
        self._fault = "emergency_stop"
        self._next_due_s = 0.0
        return self.status()

    def reset_emergency_stop(self) -> dict[str, float | int | bool | str | None]:
        """Reset the simulated latch; real USB firmware additionally requires D4 OFF."""

        if self._moving:
            raise RuntimeError("stop motion before resetting the software E-STOP")
        self._estop_latched = False
        self._state = "ready" if self._control_mode == "web_position" else "manual_stopped"
        self._fault = None
        self._next_due_s = 0.0
        return self.status()

    def set_local_enabled(self, enabled: bool) -> None:
        self._local_enabled = bool(enabled)
        self._next_due_s = 0.0
        if not self._local_enabled and self._moving:
            self.stop("local_enable_off")

    def set_limits(
        self,
        *,
        positive: bool | None = None,
        negative: bool | None = None,
    ) -> None:
        direction = self._direction()
        if positive is not None:
            active = bool(positive)
            if active and not self._positive_limit_active:
                self._positive_limit_latched = True
            self._positive_limit_active = active
        if negative is not None:
            active = bool(negative)
            if active and not self._negative_limit_active:
                self._negative_limit_latched = True
            self._negative_limit_active = active
        self._next_due_s = 0.0
        if self._moving and (
            (direction == "positive" and self._positive_limit_active)
            or (direction == "negative" and self._negative_limit_active)
        ):
            self.stop(f"{direction}_limit")
            self._state = "limit_blocked"

    def _direction(self) -> str:
        remaining = self._target_mm - self._position_mm
        if not self._moving or abs(remaining) < 1e-9:
            return "none"
        return "positive" if remaining > 0 else "negative"

    def _advance(self, elapsed_s: float) -> None:
        dt = max(0.0, elapsed_s - self._last_elapsed_s)
        self._last_elapsed_s = max(self._last_elapsed_s, elapsed_s)
        if self._estop_latched or not self._moving or dt <= 0:
            return
        direction = 1.0 if self._target_mm > self._position_mm else -1.0
        if (direction > 0 and self._positive_limit_active) or (
            direction < 0 and self._negative_limit_active
        ):
            self.stop("positive_limit" if direction > 0 else "negative_limit")
            self._state = "limit_blocked"
            return
        speed = min(
            self._command_speed_mm_s,
            abs(self._velocity_mm_s) + self._acceleration_mm_s2 * dt,
        )
        self._velocity_mm_s = direction * speed
        remaining = self._target_mm - self._position_mm
        travel = direction * min(abs(remaining), speed * dt)
        self._position_mm += travel
        if abs(self._target_mm - self._position_mm) < 1e-9:
            self._position_mm = self._target_mm
            self._velocity_mm_s = 0.0
            self._moving = False
            self._state = "completed"

    def status(self) -> dict[str, float | int | bool | str | None]:
        remaining = self._target_mm - self._position_mm
        return {
            "stepper_local_enabled": self._local_enabled,
            "stepper_command_capable": True,
            "stepper_speed_command_capable": False,
            "stepper_direction_command_capable": False,
            "stepper_mode_command_capable": True,
            "stepper_home_capable": True,
            "stepper_estop_capable": True,
            "stepper_estop_latched": self._estop_latched,
            "stepper_control_owner": "supervisor",
            "stepper_control_mode": self._control_mode,
            "stepper_homed": self._homed,
            "stepper_boot_armed": True,
            "stepper_authorized_direction": "both",
            "stepper_moving": self._moving,
            "stepper_state": self._state,
            "stepper_direction": self._direction(),
            "stepper_position_mm": round(self._position_mm, 4),
            "stepper_target_mm": round(self._target_mm, 4),
            "stepper_remaining_mm": round(remaining, 4),
            "stepper_speed_mm_s": round(self._velocity_mm_s, 4),
            "stepper_command_speed_mm_s": round(self._command_speed_mm_s, 4),
            "stepper_acceleration_mm_s2": round(self._acceleration_mm_s2, 4),
            "stepper_command_id": self._command_id,
            "stepper_positive_limit_active": self._positive_limit_active,
            "stepper_negative_limit_active": self._negative_limit_active,
            "stepper_positive_limit_latched": self._positive_limit_latched,
            "stepper_negative_limit_latched": self._negative_limit_latched,
            "stepper_d6_raw": "LOW" if self._positive_limit_active else "HIGH",
            "stepper_d8_raw": "LOW" if self._negative_limit_active else "HIGH",
            "stepper_d4_raw": "LOW" if self._local_enabled else "HIGH",
            "stepper_d5_raw": "HIGH",
            "stepper_manual_direction": "not_applicable",
            "stepper_direction_mapping": "not_applicable",
            "stepper_blocked": self._state in ("limit_blocked", "emergency_stop"),
            "stepper_blocked_reason": (
                self._fault
                if self._state in ("limit_blocked", "emergency_stop")
                else "none"
            ),
            "stepper_status_sequence": None,
            "stepper_transport_error": None,
            "stepper_position_semantics": "commanded_open_loop",
            "stepper_travel_min_mm": None,
            "stepper_travel_max_mm": None,
            "stepper_fault": self._fault,
        }

    def poll(self, elapsed_s: float) -> SourceReading | None:
        self._advance(elapsed_s)
        if self.drop_after_s is not None and elapsed_s + 1e-9 >= self.drop_after_s:
            return None
        if elapsed_s + 1e-9 < self._next_due_s:
            return None
        while self._next_due_s <= elapsed_s + 1e-9:
            self._next_due_s += self.period_s
        return SourceReading(self.name, self.mode, elapsed_s, self.status())


class UsbStepperSource:
    """Read Yún status and send guarded local/position USB commands."""

    name = "stepper"
    mode = "usb"
    period_s = STEPPER_PERIOD_S
    expected_fields = SimulatedStepperSource.expected_fields
    supports_commands = True
    supports_speed_command = True

    _BAUD_CONSTANTS = {
        9600: termios.B9600,
        19200: termios.B19200,
        38400: termios.B38400,
        57600: termios.B57600,
        115200: termios.B115200,
    }

    def __init__(
        self,
        port: str = DEFAULT_STEPPER_USB_PORT,
        baud: int = DEFAULT_STEPPER_USB_BAUD,
    ) -> None:
        if baud not in self._BAUD_CONSTANTS:
            allowed = ", ".join(str(value) for value in self._BAUD_CONSTANTS)
            raise ValueError(f"unsupported stepper USB baud {baud}; expected {allowed}")
        self.port = port
        self.baud = baud
        self.last_error: str | None = None
        self._fd: int | None = None
        self._buffer = b""
        self._next_open_attempt_s = 0.0
        self._last_values: dict[str, float | int | bool | str | None] | None = None
        self._next_command_id = 1
        self._command_names: dict[int, str] = {}
        self.pending_command_id: str | None = None
        self._command_lock = threading.Lock()

    @staticmethod
    def _wire_level(payload: Mapping[str, object], key: str) -> int:
        value = payload.get(key)
        if isinstance(value, bool) or value not in (0, 1):
            raise ValueError(f"USB stepper field {key} must be 0 or 1")
        return int(value)

    @classmethod
    def decode_status_line(
        cls,
        line: str,
    ) -> dict[str, float | int | bool | str | None]:
        """Expand one version-1 compact firmware status line."""

        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("USB stepper status is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("USB stepper status must be a JSON object")
        if payload.get("v") != 1 or payload.get("t") != "s":
            raise ValueError("unsupported USB stepper status version or type")

        sequence = payload.get("q")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("USB stepper field q must be a non-negative integer")
        d4 = cls._wire_level(payload, "d4")
        d5 = cls._wire_level(payload, "d5")
        d6 = cls._wire_level(payload, "d6")
        d8 = cls._wire_level(payload, "d8")
        positive_latched = cls._wire_level(payload, "lp") == 1
        negative_latched = cls._wire_level(payload, "ln") == 1
        blocked = cls._wire_level(payload, "b") == 1
        reason_value = payload.get("r")
        if not isinstance(reason_value, str) or len(reason_value) > 40:
            raise ValueError("USB stepper field r must be a short string")
        speed_value = payload.get("sps")
        if isinstance(speed_value, bool) or not isinstance(speed_value, (int, float)):
            raise ValueError("USB stepper field sps must be numeric")
        speed_sps = float(speed_value)
        if not math.isfinite(speed_sps):
            raise ValueError("USB stepper field sps must be finite")
        configured_speed_value = payload.get("csps")
        speed_command_capable = configured_speed_value is not None
        if speed_command_capable:
            if (
                isinstance(configured_speed_value, bool)
                or not isinstance(configured_speed_value, (int, float))
            ):
                raise ValueError("USB stepper field csps must be numeric")
            configured_speed_sps = float(configured_speed_value)
            if (
                not math.isfinite(configured_speed_sps)
                or configured_speed_sps < DEFAULT_STEPPER_MIN_SPEED_SPS
                or configured_speed_sps > DEFAULT_STEPPER_MAX_SPEED_SPS
            ):
                raise ValueError(
                    "USB stepper field csps must be in "
                    f"{DEFAULT_STEPPER_MIN_SPEED_SPS}.."
                    f"{DEFAULT_STEPPER_MAX_SPEED_SPS}"
                )
        else:
            # Backward-compatible decoding keeps the currently uploaded T4A
            # firmware observable, but speed commands remain disabled until a
            # T4B status containing csps proves the new firmware is running.
            configured_speed_sps = abs(speed_sps)

        direction_sign_value = payload.get("ds")
        direction_command_capable = direction_sign_value is not None
        if direction_command_capable:
            if (
                isinstance(direction_sign_value, bool)
                or not isinstance(direction_sign_value, int)
                or direction_sign_value not in (-1, 1)
            ):
                raise ValueError("USB stepper field ds must be -1 or 1")
            direction_sign = int(direction_sign_value)
        else:
            # T4A/T4B firmware without a ds field used the normal mapping.
            direction_sign = 1

        estop_capable = "e" in payload
        estop_latched = (
            cls._wire_level(payload, "e") == 1 if estop_capable else False
        )

        owner_value = payload.get("o")
        owner_capable = owner_value is not None
        if owner_capable:
            if (
                isinstance(owner_value, bool)
                or not isinstance(owner_value, int)
                or owner_value not in (0, 1, 2)
            ):
                raise ValueError("USB stepper field o must be 0, 1, or 2")
            transport_owner = {0: "none", 1: "usb", 2: "network"}[owner_value]
        else:
            transport_owner = None

        position_keys = ("m", "h", "a", "mv", "st", "p", "g", "c")
        position_command_capable = all(key in payload for key in position_keys)
        if any(key in payload for key in position_keys) and not position_command_capable:
            raise ValueError("USB position status fields must be provided together")
        if position_command_capable:
            mode_wire = cls._wire_level(payload, "m")
            homed = cls._wire_level(payload, "h") == 1
            boot_armed = cls._wire_level(payload, "a") == 1
            moving_wire = cls._wire_level(payload, "mv") == 1
            state_wire = payload.get("st")
            position_steps = payload.get("p")
            target_steps = payload.get("g")
            command_number = payload.get("c")
            if (
                isinstance(state_wire, bool)
                or not isinstance(state_wire, int)
                or state_wire not in range(10)
            ):
                raise ValueError("USB stepper field st must be in 0..9")
            for key, value in (("p", position_steps), ("g", target_steps)):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"USB stepper field {key} must be an integer")
            if (
                isinstance(command_number, bool)
                or not isinstance(command_number, int)
                or command_number < 0
                or command_number > 65535
            ):
                raise ValueError("USB stepper field c must be in 0..65535")
            control_mode = "web_position" if mode_wire == 1 else "local_velocity"
            state = {
                0: "manual_stopped",
                1: "manual_moving",
                2: "unhomed",
                3: "homing",
                4: "moving",
                5: "ready",
                6: "completed",
                7: "aborted",
                8: "limit_blocked",
                9: "emergency_stop",
            }[int(state_wire)]
            moving = moving_wire
            # The firmware counters are intentionally not promoted as physical
            # position. Relative motion is open-loop; only D6/D8 are used for
            # travel safety decisions.
            position_mm = None
            target_mm = None
            remaining_mm = None
            command_id = (
                f"{cls.mode}-{int(command_number):05d}"
                if int(command_number)
                else None
            )
        else:
            control_mode = "local_velocity"
            homed = False
            moving_wire = False
            position_mm = None
            target_mm = None
            remaining_mm = None
            command_id = None
            boot_armed = True

        local_enabled = d4 == 0 and boot_armed and not estop_latched
        manual_direction = "reverse" if d5 == 0 else "forward"
        positive_active = d6 == 0
        negative_active = d8 == 0
        logical_speed_sps = speed_sps * direction_sign
        if estop_latched:
            moving = False
            state = "emergency_stop"
            logical_speed_sps = 0.0
            blocked = True
            reason_value = "emergency_stop"
        if not position_command_capable:
            moving = local_enabled and not blocked and abs(logical_speed_sps) > 1e-9
            if blocked:
                state = "limit_blocked"
            elif moving:
                state = "manual_moving"
            else:
                state = "manual_stopped"
        if logical_speed_sps > 0:
            direction = "positive"
        elif logical_speed_sps < 0:
            direction = "negative"
        elif (
            position_command_capable
            and moving
            and isinstance(position_mm, (int, float))
            and isinstance(target_mm, (int, float))
            and target_mm != position_mm
        ):
            direction = "positive" if target_mm > position_mm else "negative"
        elif position_command_capable and state == "homing":
            direction = "negative"
        else:
            direction = "none"

        return {
            "stepper_local_enabled": local_enabled,
            "stepper_command_capable": position_command_capable,
            "stepper_speed_command_capable": speed_command_capable,
            "stepper_direction_command_capable": direction_command_capable,
            "stepper_mode_command_capable": position_command_capable,
            "stepper_home_capable": position_command_capable,
            "stepper_estop_capable": estop_capable,
            "stepper_estop_latched": estop_latched,
            "stepper_control_owner": (
                (
                    f"web_position_{transport_owner}"
                    if control_mode == "web_position" and transport_owner != "none"
                    else f"manual_d4_d5+{transport_owner}_control"
                    if transport_owner != "none"
                    else "none"
                )
                if owner_capable
                else "web_position_usb"
                if position_command_capable and control_mode == "web_position"
                else "manual_d4_d5+usb_control"
                if position_command_capable
                else "manual_d4_d5+usb_calibration"
                if speed_command_capable and direction_command_capable
                else "manual_d4_d5+usb_speed"
                if speed_command_capable
                else "manual_switches"
            ),
            "stepper_control_mode": control_mode,
            "stepper_homed": homed,
            "stepper_boot_armed": boot_armed,
            "stepper_authorized_direction": manual_direction,
            "stepper_moving": moving,
            "stepper_state": state,
            "stepper_direction": direction,
            "stepper_position_mm": position_mm,
            "stepper_target_mm": target_mm,
            "stepper_remaining_mm": remaining_mm,
            "stepper_speed_mm_s": round(
                logical_speed_sps / DEFAULT_STEPPER_STEPS_PER_MM,
                4,
            ),
            "stepper_command_speed_mm_s": round(
                configured_speed_sps / DEFAULT_STEPPER_STEPS_PER_MM,
                4,
            ),
            "stepper_acceleration_mm_s2": (
                DEFAULT_STEPPER_ACCELERATION_MM_S2
                if position_command_capable
                else None
            ),
            "stepper_command_id": command_id,
            "stepper_positive_limit_active": positive_active,
            "stepper_negative_limit_active": negative_active,
            "stepper_positive_limit_latched": positive_latched,
            "stepper_negative_limit_latched": negative_latched,
            "stepper_d6_raw": "LOW" if d6 == 0 else "HIGH",
            "stepper_d8_raw": "LOW" if d8 == 0 else "HIGH",
            "stepper_d4_raw": "LOW" if d4 == 0 else "HIGH",
            "stepper_d5_raw": "LOW" if d5 == 0 else "HIGH",
            "stepper_manual_direction": manual_direction,
            "stepper_direction_mapping": (
                "inverted" if direction_sign == -1 else "normal"
            ),
            "stepper_blocked": blocked,
            "stepper_blocked_reason": reason_value,
            "stepper_status_sequence": sequence,
            "stepper_transport_error": None,
            "stepper_position_semantics": (
                "open_loop_counter_not_exposed"
                if position_command_capable
                else "unavailable_manual_velocity"
            ),
            "stepper_travel_min_mm": None,
            "stepper_travel_max_mm": None,
            "stepper_fault": (
                reason_value
                if blocked or state in ("aborted", "limit_blocked", "emergency_stop")
                else None
            ),
        }

    def _open(self, elapsed_s: float) -> bool:
        if self._fd is not None:
            return True
        if elapsed_s + 1e-9 < self._next_open_attempt_s:
            return False
        self._next_open_attempt_s = elapsed_s + 1.0
        fd: int | None = None
        try:
            fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            attributes = termios.tcgetattr(fd)
            attributes[0] = 0
            attributes[1] = 0
            attributes[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attributes[3] = 0
            attributes[4] = self._BAUD_CONSTANTS[self.baud]
            attributes[5] = self._BAUD_CONSTANTS[self.baud]
            attributes[6][termios.VMIN] = 0
            attributes[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, attributes)
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            self.last_error = f"{self.port}: {exc.strerror or exc}"
            return False
        assert fd is not None
        self._fd = fd
        self._buffer = b""
        self.last_error = None
        return True

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _disconnect(self, message: str) -> None:
        self.close()
        self.last_error = message

    def status(self) -> Mapping[str, float | int | bool | str | None]:
        if self._last_values is not None:
            values = dict(self._last_values)
        else:
            values = {field: None for field in self.expected_fields}
            values["stepper_command_capable"] = False
            values["stepper_speed_command_capable"] = False
            values["stepper_direction_command_capable"] = False
            values["stepper_mode_command_capable"] = False
            values["stepper_home_capable"] = False
            values["stepper_estop_capable"] = False
            values["stepper_estop_latched"] = False
            values["stepper_control_owner"] = "manual_switches"
        values["stepper_transport_error"] = self.last_error
        return values

    @staticmethod
    def _manual_speed_sps(speed_mm_s: object) -> int:
        if isinstance(speed_mm_s, bool):
            raise ValueError("speed_mm_s must be a finite number")
        try:
            speed = float(speed_mm_s)
        except (TypeError, ValueError) as exc:
            raise ValueError("speed_mm_s must be a finite number") from exc
        if not math.isfinite(speed):
            raise ValueError("speed_mm_s must be a finite number")
        if speed < 0.1 or speed > DEFAULT_STEPPER_MAX_SPEED_MM_S:
            raise ValueError(
                "speed_mm_s must be from 0.1 through "
                f"{DEFAULT_STEPPER_MAX_SPEED_MM_S:g}"
            )
        return int(round(speed * DEFAULT_STEPPER_STEPS_PER_MM))

    @staticmethod
    def _finite_number(value: object, field: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{field} must be a finite number")
        return number

    def _require_connected(self) -> dict[str, float | int | bool | str | None]:
        if not self._transport_connected() or self._last_values is None:
            raise RuntimeError(f"{self.mode} stepper is not connected")
        return self._last_values

    def _transport_connected(self) -> bool:
        return self._fd is not None

    def _write_command(self, command: bytes, description: str) -> None:
        # Serialize complete command lines. E-STOP bypasses the dashboard's
        # cross-source poll lock, so it may race another HTTP command at this
        # boundary; each line must still reach the firmware intact.
        with self._command_lock:
            if self._fd is None:
                raise RuntimeError("USB stepper is not connected")
            try:
                written = os.write(self._fd, command)
            except (BlockingIOError, OSError) as exc:
                self._disconnect(f"{self.port}: {getattr(exc, 'strerror', None) or exc}")
                raise RuntimeError(f"USB stepper {description} command failed") from exc
            if written != len(command):
                self._disconnect(f"{self.port}: incomplete USB command write")
                raise RuntimeError(f"USB stepper {description} command was incomplete")

    def set_speed(self, speed_mm_s: object) -> Mapping[str, float | int | bool | str | None]:
        """Set manual-mode speed without starting or choosing motion."""

        speed_sps = self._manual_speed_sps(speed_mm_s)
        values = self._require_connected()
        if values.get("stepper_estop_latched"):
            raise RuntimeError("reset the software E-STOP before changing speed")
        if not values.get("stepper_speed_command_capable"):
            raise RuntimeError(
                f"Yún firmware does not support {self.mode} speed tuning"
            )
        if values.get("stepper_d4_raw") != "HIGH":
            raise RuntimeError("turn D4 OFF before changing manual speed")
        self._write_command(f"V1 S{speed_sps}\n".encode("ascii"), "speed")
        return self.status()

    def set_direction_mapping(
        self,
        inverted: object,
    ) -> Mapping[str, float | int | bool | str | None]:
        """Invert electrical DIR mapping without starting or selecting motion."""

        if not isinstance(inverted, bool):
            raise ValueError("inverted must be true or false")
        values = self._require_connected()
        if values.get("stepper_estop_latched"):
            raise RuntimeError("reset the software E-STOP before changing direction mapping")
        if not values.get("stepper_direction_command_capable"):
            raise RuntimeError("Yún firmware does not support direction mapping")
        if values.get("stepper_d4_raw") != "HIGH":
            raise RuntimeError("turn D4 OFF before changing direction mapping")
        self._write_command(
            f"V1 D{1 if inverted else 0}\n".encode("ascii"),
            "direction",
        )
        return self.status()

    def set_control_mode(
        self,
        web_position: object,
    ) -> Mapping[str, float | int | bool | str | None]:
        if not isinstance(web_position, bool):
            raise ValueError("web_position must be true or false")
        values = self._require_connected()
        if values.get("stepper_estop_latched"):
            raise RuntimeError("reset the software E-STOP before changing control mode")
        if not values.get("stepper_mode_command_capable"):
            raise RuntimeError("Yún firmware does not support control modes")
        if values.get("stepper_d4_raw") != "HIGH" or values.get("stepper_moving"):
            raise RuntimeError("turn D4 OFF and stop motion before changing mode")
        self._write_command(
            f"V1 M{1 if web_position else 0}\n".encode("ascii"),
            "mode",
        )
        return self.status()

    def home(self) -> Mapping[str, float | int | bool | str | None]:
        values = self._require_connected()
        if values.get("stepper_estop_latched"):
            raise RuntimeError("reset the software E-STOP before Home")
        if not values.get("stepper_home_capable"):
            raise RuntimeError("Yún firmware does not support Home")
        if values.get("stepper_control_mode") != "web_position":
            raise RuntimeError("select Web Position mode before Home")
        if not values.get("stepper_local_enabled"):
            raise RuntimeError("turn D4 ON to arm Home")
        if values.get("stepper_authorized_direction") != "reverse":
            raise RuntimeError("D5 must authorize Reverse for Home")
        if values.get("stepper_moving"):
            raise RuntimeError("stepper is busy")
        self._write_command(b"V1 H\n", "Home")
        return self.status()

    def emergency_stop(self) -> Mapping[str, float | int | bool | str | None]:
        """Latch the firmware software E-STOP in either control mode."""

        values = self._require_connected()
        if not values.get("stepper_estop_capable"):
            raise RuntimeError("Yún firmware does not support software E-STOP")
        self._write_command(b"V1 E1\n", "software E-STOP")
        return self.status()

    def reset_emergency_stop(self) -> Mapping[str, float | int | bool | str | None]:
        """Reset the firmware latch only with physical D4 OFF."""

        values = self._require_connected()
        if not values.get("stepper_estop_capable"):
            raise RuntimeError("Yún firmware does not support software E-STOP")
        if values.get("stepper_d4_raw") != "HIGH":
            raise RuntimeError("turn D4 OFF before resetting the software E-STOP")
        if values.get("stepper_moving"):
            raise RuntimeError("stop motion before resetting the software E-STOP")
        self._write_command(b"V1 E0\n", "software E-STOP reset")
        return self.status()

    def move(
        self,
        distance_mm: object,
        speed_mm_s: object,
        command_id: object | None = None,
    ) -> Mapping[str, float | int | bool | str | None]:
        distance = self._finite_number(distance_mm, "distance_mm")
        speed_sps = self._manual_speed_sps(speed_mm_s)
        if distance == 0:
            raise ValueError("distance_mm must be non-zero")
        if abs(distance) > DEFAULT_STEPPER_MAX_DISTANCE_MM:
            raise ValueError(
                f"abs(distance_mm) must not exceed {DEFAULT_STEPPER_MAX_DISTANCE_MM:g}"
            )
        delta_steps = int(round(distance * DEFAULT_STEPPER_STEPS_PER_MM))
        if delta_steps == 0:
            raise ValueError("distance_mm is smaller than one provisional step")

        values = self._require_connected()
        if values.get("stepper_estop_latched"):
            raise RuntimeError("reset the software E-STOP before moving")
        if not values.get("stepper_command_capable"):
            raise RuntimeError("Yún firmware does not support bounded moves")
        if values.get("stepper_control_mode") != "web_position":
            raise RuntimeError("select Web Position mode before moving")
        if not values.get("stepper_local_enabled"):
            raise RuntimeError("turn D4 ON to arm the move")
        if values.get("stepper_moving"):
            raise RuntimeError("stepper is busy")
        requested_direction = "forward" if delta_steps > 0 else "reverse"
        if values.get("stepper_authorized_direction") != requested_direction:
            raise RuntimeError(
                f"D5 must authorize {requested_direction.title()} for this move"
            )
        if delta_steps > 0 and (
            values.get("stepper_positive_limit_active")
            or values.get("stepper_positive_limit_latched")
        ):
            raise RuntimeError("positive limit blocks positive motion")
        if delta_steps < 0 and (
            values.get("stepper_negative_limit_active")
            or values.get("stepper_negative_limit_latched")
        ):
            raise RuntimeError("negative limit blocks negative motion")

        if command_id is None:
            resolved_name = f"{self.mode}-{self._next_command_id:05d}"
        else:
            resolved_name = str(command_id).strip()
            if not resolved_name or len(resolved_name) > 64:
                raise ValueError("command_id must contain 1 to 64 characters")
        wire_id = self._next_command_id
        self._next_command_id = 1 if wire_id >= 65535 else wire_id + 1
        self._command_names[wire_id] = resolved_name
        self.pending_command_id = resolved_name
        command = f"V1 G{delta_steps},{speed_sps},{wire_id}\n".encode("ascii")
        self._write_command(command, "move")
        return self.status()

    def stop(self) -> Mapping[str, float | int | bool | str | None]:
        values = self._require_connected()
        if not values.get("stepper_command_capable"):
            raise RuntimeError("Yún firmware does not support web Stop")
        if values.get("stepper_control_mode") != "web_position":
            raise RuntimeError("web Stop is available only in Web Position mode")
        self._write_command(b"V1 X\n", "Stop")
        return self.status()

    def poll(self, elapsed_s: float) -> SourceReading | None:
        if not self._open(elapsed_s):
            return None
        assert self._fd is not None
        latest: dict[str, float | int | bool | str | None] | None = None
        try:
            while True:
                chunk = os.read(self._fd, 4096)
                if not chunk:
                    break
                self._buffer += chunk
                if len(self._buffer) > 16384:
                    self._buffer = self._buffer[-8192:]
        except BlockingIOError:
            pass
        except OSError as exc:
            self._disconnect(f"{self.port}: {exc.strerror or exc}")
            return None

        while b"\n" in self._buffer:
            raw_line, self._buffer = self._buffer.split(b"\n", 1)
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("{"):
                continue
            try:
                latest = self.decode_status_line(line)
            except ValueError as exc:
                self.last_error = str(exc)
                continue
            decoded_command_id = latest.get("stepper_command_id")
            if isinstance(decoded_command_id, str) and decoded_command_id.startswith(
                "usb-"
            ):
                try:
                    wire_id = int(decoded_command_id[4:])
                except ValueError:
                    wire_id = 0
                resolved_name = self._command_names.get(wire_id)
                if resolved_name is not None:
                    latest["stepper_command_id"] = resolved_name
                    if resolved_name == self.pending_command_id:
                        self.pending_command_id = None
        if latest is None:
            return None
        self.last_error = None
        self._last_values = latest
        return SourceReading(self.name, self.mode, elapsed_s, latest)


class NetworkStepperSource(UsbStepperSource):
    """Poll and command the Yún Linux UART bridge over its trusted-LAN API.

    The Linux service only relays the existing bounded ``V1`` command lines and
    compact status JSON.  Motion, D4/D5 authority, limits, and the E-STOP latch
    remain entirely in the ATmega firmware.
    """

    mode = "network"

    def __init__(
        self,
        base_url: str = DEFAULT_STEPPER_NETWORK_URL,
        timeout: float = DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        parsed = urlparse(normalized_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                "stepper network URL must be an absolute http:// or https:// URL"
            )
        if timeout <= 0:
            raise ValueError("stepper network timeout must be positive")

        self.base_url = normalized_url
        self.timeout = timeout
        self.last_error: str | None = None
        self._last_values: dict[str, float | int | bool | str | None] | None = None
        self._next_command_id = 1
        self._command_names: dict[int, str] = {}
        self.pending_command_id: str | None = None
        self._command_lock = threading.Lock()
        self._opener = build_opener(ProxyHandler({}))
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._emitted_generation = 0

    def _transport_connected(self) -> bool:
        with self._state_lock:
            return self._last_values is not None and self.last_error is None

    def _ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="yun-stepper-network-source",
            daemon=True,
        )
        self._thread.start()

    def _set_error(self, message: str | None) -> None:
        with self._state_lock:
            self.last_error = message

    def _fetch_status(self) -> None:
        url = f"{self.base_url}/v1/status"
        request = Request(
            url,
            headers={"Accept": "application/json", "Cache-Control": "no-store"},
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(4096).decode("utf-8", errors="replace").strip()
            values = self.decode_status_line(body)
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            HTTPException,
            ValueError,
        ) as exc:
            self._set_error(f"{url}: {exc}")
            return

        decoded_command_id = values.get("stepper_command_id")
        if isinstance(decoded_command_id, str) and decoded_command_id.startswith(
            "network-"
        ):
            try:
                wire_id = int(decoded_command_id[8:])
            except ValueError:
                wire_id = 0
            resolved_name = self._command_names.get(wire_id)
            if resolved_name is not None:
                values["stepper_command_id"] = resolved_name
                if resolved_name == self.pending_command_id:
                    self.pending_command_id = None
        with self._state_lock:
            self._last_values = values
            self.last_error = None
            self._generation += 1

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._fetch_status()
            if self._stop_event.wait(self.period_s):
                break

    def status(self) -> Mapping[str, float | int | bool | str | None]:
        with self._state_lock:
            if self._last_values is not None:
                values = dict(self._last_values)
            else:
                values = {field: None for field in self.expected_fields}
                values["stepper_command_capable"] = False
                values["stepper_speed_command_capable"] = False
                values["stepper_direction_command_capable"] = False
                values["stepper_mode_command_capable"] = False
                values["stepper_home_capable"] = False
                values["stepper_estop_capable"] = False
                values["stepper_estop_latched"] = False
                values["stepper_control_owner"] = "none"
            values["stepper_transport_error"] = self.last_error
        return values

    def _write_command(self, command: bytes, description: str) -> None:
        command_text = command.decode("ascii").strip()
        url = f"{self.base_url}/v1/command"
        request = Request(
            url,
            data=command_text.encode("ascii"),
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "Content-Type": "text/plain; charset=us-ascii",
            },
            method="POST",
        )
        with self._command_lock:
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    body = response.read(1024).decode(
                        "utf-8", errors="replace"
                    ).strip()
                acknowledgement = json.loads(body)
            except HTTPError as exc:
                error_body = exc.read(1024).decode("utf-8", errors="replace").strip()
                try:
                    payload = json.loads(error_body)
                    detail = payload.get("error") if isinstance(payload, dict) else None
                except json.JSONDecodeError:
                    detail = None
                message = str(detail or exc)
                self._set_error(f"{url}: {message}")
                raise RuntimeError(
                    f"network stepper {description} command rejected: {message}"
                ) from exc
            except (
                URLError,
                TimeoutError,
                OSError,
                HTTPException,
                UnicodeError,
                json.JSONDecodeError,
            ) as exc:
                self._set_error(f"{url}: {exc}")
                raise RuntimeError(
                    f"network stepper {description} command failed: {exc}"
                ) from exc

            if (
                not isinstance(acknowledgement, dict)
                or acknowledgement.get("v") != 1
                or acknowledgement.get("type") != "ack"
                or acknowledgement.get("accepted") is not True
            ):
                raise RuntimeError(
                    f"network stepper {description} returned an invalid acknowledgement"
                )
            self._set_error(None)

    def poll(self, elapsed_s: float) -> SourceReading | None:
        self._ensure_started()
        with self._state_lock:
            if (
                self._last_values is None
                or self._generation == self._emitted_generation
            ):
                return None
            values = dict(self._last_values)
            self._emitted_generation = self._generation
        return SourceReading(self.name, self.mode, elapsed_s, values)

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.timeout + self.period_s))


class RealEsp32Source:
    """Background SSE client for the ESP32 sensor and solenoid API.

    The headless firmware sends complete version-2 readings and immediate
    solenoid state-change events. The background reader keeps the HTTP stream
    from blocking the supervisor's fixed-rate merge loop; ``poll`` only
    projects a newly received event into the common source schema.
    """

    name = "esp32"
    mode = "real"
    period_s = ESP32_PERIOD_S
    expected_fields = SimulatedEsp32Source.expected_fields

    def __init__(
        self,
        base_url: str = DEFAULT_ESP32_BASE_URL,
        timeout: float = DEFAULT_ESP32_TIMEOUT_S,
        reconnect_s: float = DEFAULT_ESP32_RECONNECT_S,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        parsed = urlparse(normalized_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("ESP32 URL must be an absolute http:// or https:// URL")
        if timeout <= 0:
            raise ValueError("ESP32 timeout must be positive")
        if reconnect_s < 0:
            raise ValueError("ESP32 reconnect delay must be non-negative")

        self.base_url = normalized_url
        self.timeout = timeout
        self.reconnect_s = reconnect_s
        self.last_error: str | None = None
        # Bench devices are local endpoints and must not be sent through an
        # HTTP proxy inherited from the laptop environment.
        self._opener = build_opener(ProxyHandler({}))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._values: dict[str, float | int | bool | str | None] | None = None
        self._solenoids: list[bool | None] = [None] * ESP32_SOLENOID_COUNT
        self._generation = 0
        self._emitted_generation = 0

    @staticmethod
    def _numeric_triplet(payload: object, key: str) -> tuple[float, float, float]:
        if not isinstance(payload, dict):
            raise ValueError("ESP32 reading must be a JSON object")
        values = payload.get(key)
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError(f"ESP32 reading {key!r} must contain exactly 3 values")
        decoded: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"ESP32 reading {key!r} values must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"ESP32 reading {key!r} values must be finite")
            decoded.append(numeric)
        return decoded[0], decoded[1], decoded[2]

    @classmethod
    def decode_reading_data(
        cls,
        data: str,
    ) -> dict[str, float | int | bool | str | None]:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid ESP32 reading JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("ESP32 reading must be a JSON object")
        if "v" not in payload:
            raise ValueError(
                f"ESP32 reading is missing required payload version "
                f"{ESP32_PAYLOAD_VERSION}"
            )
        version = payload["v"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("ESP32 reading version must be an integer")
        if version != ESP32_PAYLOAD_VERSION:
            raise ValueError(f"unsupported ESP32 reading version {version}")

        pressures = cls._numeric_triplet(payload, "p")
        flows = cls._numeric_triplet(payload, "f")
        values: dict[str, float | int | bool | str | None] = {
            "esp32_payload_version": version,
            "esp32_sample_ms": None,
            "esp32_p1_bar": pressures[0],
            "esp32_p2_bar": pressures[1],
            "esp32_p3_bar": pressures[2],
            "esp32_f1_gmin": flows[0],
            "esp32_f2_gmin": flows[1],
            "esp32_f3_gmin": flows[2],
            "esp32_p_combined_bar": min(pressures),
            "esp32_f_combined_gmin": sum(flows),
        }
        sample_ms = payload.get("sample_ms")
        if (
            isinstance(sample_ms, bool)
            or not isinstance(sample_ms, int)
            or sample_ms < 0
        ):
            raise ValueError("ESP32 reading sample_ms must be a non-negative integer")
        pressure_volts = cls._numeric_triplet(payload, "p_v")
        flow_volts = cls._numeric_triplet(payload, "f_v")
        solenoids = cls._boolean_solenoid_states(payload, "sol")
        values["esp32_sample_ms"] = sample_ms
        for index, voltage in enumerate(pressure_volts, start=1):
            values[f"esp32_p{index}_volt"] = voltage
        for index, voltage in enumerate(flow_volts, start=1):
            values[f"esp32_f{index}_volt"] = voltage
        for index, state in enumerate(solenoids, start=1):
            values[f"esp32_sol{index}"] = state
        return values

    @staticmethod
    def _boolean_solenoid_states(payload: object, key: str) -> tuple[bool, ...]:
        if not isinstance(payload, dict):
            raise ValueError("ESP32 reading must be a JSON object")
        values = payload.get(key)
        if (
            not isinstance(values, list)
            or len(values) != ESP32_SOLENOID_COUNT
            or any(not isinstance(value, bool) for value in values)
        ):
            raise ValueError(
                f"ESP32 reading {key!r} must contain exactly "
                f"{ESP32_SOLENOID_COUNT} booleans"
            )
        return tuple(values)

    @staticmethod
    def decode_solenoid_data(data: str) -> tuple[bool, ...]:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid ESP32 solenoid JSON: {exc.msg}") from exc
        if not isinstance(payload, list):
            raise ValueError(
                f"ESP32 solenoid event must contain exactly "
                f"{ESP32_SOLENOID_COUNT} booleans"
            )
        return RealEsp32Source._boolean_solenoid_states({"sol": payload}, "sol")

    def _ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="esp32-sse-source",
            daemon=True,
        )
        self._thread.start()

    def _set_error(self, message: str | None) -> None:
        with self._lock:
            self.last_error = message

    def _apply_event(self, event_name: str, data: str) -> None:
        try:
            if event_name == "reading":
                reading = self.decode_reading_data(data)
                with self._lock:
                    self._solenoids[:] = [
                        bool(reading[f"esp32_sol{index}"])
                        for index in range(1, ESP32_SOLENOID_COUNT + 1)
                    ]
                    self._values = reading
                    self._generation += 1
                    self.last_error = None
            elif event_name == "sol":
                states = self.decode_solenoid_data(data)
                with self._lock:
                    self._solenoids[:] = states
                    if self._values is not None:
                        for index, state in enumerate(states, start=1):
                            self._values[f"esp32_sol{index}"] = state
                        self._generation += 1
                    self.last_error = None
        except ValueError as exc:
            self._set_error(str(exc))

    def _consume_sse(self, response: BinaryIO) -> None:
        event_name = "message"
        data_lines: list[str] = []
        while not self._stop_event.is_set():
            raw_line = response.readline()
            if not raw_line:
                raise ConnectionError("ESP32 SSE stream closed")
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    self._apply_event(event_name, "\n".join(data_lines))
                event_name = "message"
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value
            elif field == "data":
                data_lines.append(value)

    def _run(self) -> None:
        events_url = f"{self.base_url}/events"
        while not self._stop_event.is_set():
            response: BinaryIO | None = None
            try:
                request = Request(
                    events_url,
                    headers={"Accept": "text/event-stream"},
                    method="GET",
                )
                response = self._opener.open(request, timeout=self.timeout)
                self._consume_sse(response)
            except (
                HTTPError,
                URLError,
                TimeoutError,
                OSError,
                ConnectionError,
                HTTPException,
                ValueError,
            ) as exc:
                if not self._stop_event.is_set():
                    self._set_error(f"{events_url}: {exc}")
            finally:
                if response is not None:
                    response.close()
            if self._stop_event.wait(self.reconnect_s):
                break

    def poll(self, elapsed_s: float) -> SourceReading | None:
        self._ensure_started()
        with self._lock:
            if self._values is None or self._generation == self._emitted_generation:
                return None
            values = dict(self._values)
            self._emitted_generation = self._generation
        return SourceReading(self.name, self.mode, elapsed_s, values)

    def toggle_solenoid(self, index: int) -> bool:
        if index < 0 or index >= ESP32_SOLENOID_COUNT:
            raise ValueError("solenoid index must be 0, 1, 2, or 3")
        url = f"{self.base_url}/solenoid/toggle?{urlencode({'n': index})}"
        request = Request(url, data=b"", method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(32).decode("utf-8", errors="replace").strip()
        except (HTTPError, URLError, TimeoutError, OSError, HTTPException) as exc:
            self._set_error(f"{url}: {exc}")
            raise RuntimeError(f"ESP32 solenoid command failed: {exc}") from exc
        if body not in ("ON", "OFF"):
            raise RuntimeError(f"unexpected ESP32 solenoid response: {body!r}")
        state = body == "ON"
        with self._lock:
            self._solenoids[index] = state
            if self._values is not None:
                self._values[f"esp32_sol{index + 1}"] = state
                self._generation += 1
            self.last_error = None
        return state

    def solenoid_states(self) -> tuple[bool | None, ...]:
        with self._lock:
            return tuple(self._solenoids)

    def close(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.timeout + self.reconnect_s))


class RealDxmr90Source:
    """DXMR90/Banner Modbus adapter for live or republished SICK values."""

    name = "dxmr90"
    mode = "real"
    expected_fields = SimulatedDxmr90Source.expected_fields

    def __init__(
        self,
        host: str = DEFAULT_DXMR90_HOST,
        port: int = DEFAULT_DXMR90_PORT,
        unit_id: int = DEFAULT_DXMR90_UNIT_ID,
        timeout: float = 1.0,
        addressing: str = "one-based",
        word_order: str = "high-low",
        data_path: str = "direct",
        rate_hz: float = DEFAULT_DXMR90_REAL_RATE_HZ,
    ) -> None:
        if data_path not in DXMR90_DATA_PATHS:
            allowed = ", ".join(DXMR90_DATA_PATHS)
            raise ValueError(f"unknown DXMR90 data path {data_path!r}; expected {allowed}")
        if rate_hz <= 0:
            raise ValueError("DXMR90 rate must be positive")
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.addressing = addressing
        self.word_order = word_order
        self.data_path = data_path
        self.rate_hz = rate_hz
        self.period_s = 1.0 / rate_hz
        self.last_error: str | None = None
        self._next_due_s = 0.0

    def poll(self, elapsed_s: float) -> SourceReading | None:
        if elapsed_s + 1e-9 < self._next_due_s:
            return None
        while self._next_due_s <= elapsed_s + 1e-9:
            self._next_due_s += self.period_s
        try:
            with ModbusTcpClient(
                self.host,
                self.port,
                self.unit_id,
                self.timeout,
                self.addressing,
            ) as client:
                if self.data_path == "direct":
                    direct_values = read_direct_sick_values(client, self.word_order)
                    rows = None
                else:
                    rows = read_metrics(client, DXMR90_CORE_METRICS, self.word_order)
                    direct_values = None
        except (OSError, ModbusError, ValueError) as exc:
            self.last_error = str(exc)
            return None

        self.last_error = None
        values: dict[str, float | int | bool | str | None]
        if direct_values is not None:
            values = {f"dxmr90_{name}": value for name, value in direct_values.items()}
        else:
            assert rows is not None
            values = {}
            for row in rows:
                values[f"dxmr90_{row['name']}"] = row["value"]  # type: ignore[assignment]
        return SourceReading(
            source=self.name,
            mode=self.mode,
            elapsed_s=elapsed_s,
            values=values,
        )


class SourceMerger:
    """Latest-value merge with per-source health and age fields."""

    def __init__(
        self,
        sources: list[SourceAdapter],
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
    ) -> None:
        self.sources = sources
        self.stale_after_s = stale_after_s
        self._latest: dict[str, SourceReading] = {}
        self._fresh_readings: list[SourceReading] = []

    def poll(self, elapsed_s: float, timestamp: datetime) -> dict[str, object]:
        self._fresh_readings = []
        for source in self.sources:
            reading = source.poll(elapsed_s)
            if reading is not None:
                self._latest[source.name] = reading
                self._fresh_readings.append(reading)

        sample: dict[str, object] = {
            "timestamp_iso": timestamp.isoformat(timespec="milliseconds"),
            "elapsed_s": round(elapsed_s, 3),
        }
        for source in self.sources:
            reading = self._latest.get(source.name)
            if reading is None:
                sample[f"{source.name}_mode"] = source.mode
                sample[f"{source.name}_connected"] = False
                sample[f"{source.name}_age_ms"] = None
                sample.update({field: None for field in source.expected_fields})
                transport_error_field = f"{source.name}_transport_error"
                if transport_error_field in source.expected_fields:
                    sample[transport_error_field] = getattr(source, "last_error", None)
                continue
            age_s = max(0.0, elapsed_s - reading.elapsed_s)
            sample[f"{source.name}_mode"] = reading.mode
            sample[f"{source.name}_connected"] = age_s <= self.stale_after_s
            sample[f"{source.name}_age_ms"] = round(age_s * 1000.0, 1)
            sample.update({field: None for field in source.expected_fields})
            sample.update(reading.values)
            transport_error_field = f"{source.name}_transport_error"
            if transport_error_field in source.expected_fields:
                sample[transport_error_field] = getattr(source, "last_error", None)
        return sample

    def fresh_readings(self) -> tuple[SourceReading, ...]:
        return tuple(self._fresh_readings)


def make_simulated_sources(
    scenario: str = "healthy",
    drop_after_s: float = DEFAULT_DROP_AFTER_S,
    esp32_auto_sequence: bool = True,
) -> list[SourceAdapter]:
    """Create simulated sources for a named scenario."""

    if scenario not in SIMULATION_SCENARIOS:
        allowed = ", ".join(SIMULATION_SCENARIOS)
        raise ValueError(f"unknown scenario {scenario!r}; expected one of {allowed}")

    esp32_drop_after_s: float | None = None
    dxmr90_drop_after_s: float | None = None
    stepper_drop_after_s: float | None = None
    if scenario == "esp32_stale":
        esp32_drop_after_s = drop_after_s
    elif scenario == "dxmr90_stale":
        dxmr90_drop_after_s = drop_after_s
    elif scenario == "dxmr90_missing":
        dxmr90_drop_after_s = 0.0
    elif scenario == "stepper_stale":
        stepper_drop_after_s = drop_after_s
    elif scenario == "stepper_missing":
        stepper_drop_after_s = 0.0
    elif scenario == "all_stale":
        esp32_drop_after_s = drop_after_s
        dxmr90_drop_after_s = drop_after_s
        stepper_drop_after_s = drop_after_s

    return [
        SimulatedEsp32Source(
            drop_after_s=esp32_drop_after_s,
            auto_sequence=esp32_auto_sequence,
        ),
        SimulatedDxmr90Source(drop_after_s=dxmr90_drop_after_s),
        SimulatedStepperSource(drop_after_s=stepper_drop_after_s),
    ]


def make_sources(
    *,
    esp32_source: str = "sim",
    esp32_base_url: str = DEFAULT_ESP32_BASE_URL,
    esp32_timeout: float = DEFAULT_ESP32_TIMEOUT_S,
    dxmr90_source: str = "sim",
    stepper_source: str = "sim",
    stepper_port: str = DEFAULT_STEPPER_USB_PORT,
    stepper_baud: int = DEFAULT_STEPPER_USB_BAUD,
    stepper_network_url: str = DEFAULT_STEPPER_NETWORK_URL,
    stepper_network_timeout: float = DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
    scenario: str = "healthy",
    drop_after_s: float = DEFAULT_DROP_AFTER_S,
    esp32_auto_sequence: bool = True,
    dxmr90_host: str = DEFAULT_DXMR90_HOST,
    dxmr90_port: int = DEFAULT_DXMR90_PORT,
    dxmr90_unit_id: int = DEFAULT_DXMR90_UNIT_ID,
    dxmr90_timeout: float = 1.0,
    dxmr90_addressing: str = "one-based",
    dxmr90_word_order: str = "high-low",
    dxmr90_data_path: str = "direct",
    dxmr90_rate_hz: float = DEFAULT_DXMR90_REAL_RATE_HZ,
) -> list[SourceAdapter]:
    """Create source arms with independent sim/real/off selection."""

    if esp32_source not in SOURCE_MODES:
        allowed = ", ".join(SOURCE_MODES)
        raise ValueError(f"unknown ESP32 source {esp32_source!r}; expected {allowed}")
    if dxmr90_source not in SOURCE_MODES:
        allowed = ", ".join(SOURCE_MODES)
        raise ValueError(f"unknown DXMR90 source {dxmr90_source!r}; expected {allowed}")
    if stepper_source not in STEPPER_SOURCE_MODES:
        allowed = ", ".join(STEPPER_SOURCE_MODES)
        raise ValueError(f"unknown stepper source {stepper_source!r}; expected {allowed}")
    sim_sources = make_simulated_sources(
        scenario=scenario,
        drop_after_s=drop_after_s,
        esp32_auto_sequence=esp32_auto_sequence,
    )
    sim_by_name = {source.name: source for source in sim_sources}

    if esp32_source == "sim":
        esp32: SourceAdapter = sim_by_name["esp32"]
    elif esp32_source == "real":
        esp32 = RealEsp32Source(
            base_url=esp32_base_url,
            timeout=esp32_timeout,
        )
    else:
        esp32 = DisabledSource("esp32", ESP32_PERIOD_S, SimulatedEsp32Source.expected_fields)

    if dxmr90_source == "sim":
        dxmr90: SourceAdapter = sim_by_name["dxmr90"]
    elif dxmr90_source == "real":
        dxmr90 = RealDxmr90Source(
            host=dxmr90_host,
            port=dxmr90_port,
            unit_id=dxmr90_unit_id,
            timeout=dxmr90_timeout,
            addressing=dxmr90_addressing,
            word_order=dxmr90_word_order,
            data_path=dxmr90_data_path,
            rate_hz=dxmr90_rate_hz,
        )
    else:
        dxmr90 = DisabledSource(
            "dxmr90",
            DXMR90_PERIOD_S,
            SimulatedDxmr90Source.expected_fields,
        )

    if stepper_source == "sim":
        stepper: SourceAdapter = sim_by_name["stepper"]
    elif stepper_source == "usb":
        stepper = UsbStepperSource(port=stepper_port, baud=stepper_baud)
    elif stepper_source == "network":
        stepper = NetworkStepperSource(
            base_url=stepper_network_url,
            timeout=stepper_network_timeout,
        )
    else:
        stepper = DisabledSource(
            "stepper",
            STEPPER_PERIOD_S,
            SimulatedStepperSource.expected_fields,
        )

    return [esp32, dxmr90, stepper]


def iter_merged_samples(
    samples: int,
    rate_hz: float = 10.0,
    sources: list[SourceAdapter] | None = None,
    scenario: str = "healthy",
    drop_after_s: float = DEFAULT_DROP_AFTER_S,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    start_time: datetime | None = None,
) -> Iterator[dict[str, object]]:
    """Yield deterministic merged samples without requiring wall-clock sleeps."""

    if samples < 1:
        return
    if rate_hz <= 0:
        raise ValueError("rate_hz must be positive")

    period_s = 1.0 / rate_hz
    source_list = (
        make_simulated_sources(scenario=scenario, drop_after_s=drop_after_s)
        if sources is None
        else sources
    )
    merger = SourceMerger(source_list, stale_after_s=stale_after_s)
    timestamp0 = start_time or datetime.now(timezone.utc)
    for index in range(samples):
        elapsed_s = index * period_s
        yield merger.poll(elapsed_s, timestamp0 + timedelta(seconds=elapsed_s))
