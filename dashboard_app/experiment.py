"""Sequential experiment actions driven by telemetry and application events.

Start is an external action. The CSV contains only the actions after Start,
in the order chosen by the operator. Every threshold is explicit in
the row; there are no default check requirements hidden in the runner.

Runtime owns synchronization and supplies monotonic time in seconds. This
module has no device I/O. Requested states are dispatched through a runtime
callback and verified on a subsequent application-state event.
"""

from __future__ import annotations

import csv
import math
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
from io import StringIO
from typing import Callable, Mapping

try:
    from .. import supervisor_core as core
except ImportError:  # Direct dashboard-lean.py execution.
    import supervisor_core as core


BASE_CSV_COLUMNS = (
    "action", "source", "max_age_ms", "health", "timeout_s",
)
MOVE_COLUMNS = ("speed_mm_s", "duration_s", "direction")
DEVICE_COLUMNS = ("device", "pulse_us")
READING_COLUMNS = ("min_c", "max_c", "min_mm", "max_mm")
CSV_COLUMNS = (
    "action", "source", "device", "max_age_ms", "health", "timeout_s",
    "set_point", "min_mm", "max_mm", "min_c", "max_c",
    "speed_mm_s", "duration_s", "direction", "pulse_us",
)
MAX_CSV_BYTES = 256 * 1024
MAX_ROWS = 1000
STEPPER_MODES = {"positional": "web_position", "directional": "local_velocity"}

# Each device names the booleans that establish its readiness/capability.
# These are telemetry checks, not physical-state or interlock checks.
DEVICE_REQUIREMENTS = {
    "esp32": {
        "pressure_adc": ("esp32_pressure_adc_ready",),
        "flow_adc": ("esp32_flow_adc_ready",),
    },
    "ed593": {f"tc{i}": (f"ed593_tc{i}_ready",) for i in range(8)},
    "dxmr90": {},  # No individual SICK readiness/capability flags exposed.
    "controllino": {
        "stepper": ("stepper_command_capable",),
        "dro": ("stepper_dro_capable",),
        "pulse_measurement": ("stepper_pulse_measurement_capable",),
        "servo": ("stepper_servo_capable",),
        "brushless_motor": ("stepper_brushless_motor_capable",),
        "solenoid4": ("stepper_solenoid4_capable",),
    },
}


def _number(text: str, column: str, row: int, *, positive: bool) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"Row {row}: {column} must be a finite number") from exc
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        bound = "positive" if positive else "non-negative"
        raise ValueError(f"Row {row}: {column} must be {bound} and finite")
    return value


def freshness_check(age: object, max_age_ms: float) -> dict[str, object]:
    """A received sample is fresh solely according to the worksheet bound."""
    return {
        "ok": type(age) in (int, float) and math.isfinite(age)
        and 0 <= age <= max_age_ms,
        "age_ms": age,
        "max_age_ms": max_age_ms,
    }


def source_adapter_name(source: str) -> str:
    """Translate spreadsheet communicator names at the existing adapter boundary.

    The shared motion adapter and its telemetry fields retain their established
    stepper names; the spreadsheet names the communicating controller.
    """
    return "stepper" if source == "controllino" else source


def source_checks(sample: Mapping[str, object], source: str,
                  max_age_ms: float | None, health: str) -> dict[str, object]:
    source = source_adapter_name(source)
    error_field = f"{source}_transport_error"
    error = sample.get(error_field)
    available = error_field in sample
    checks = {}
    if max_age_ms is not None:
        checks["freshness"] = freshness_check(sample.get(f"{source}_age_ms"), max_age_ms)
    if health:
        checks["health"] = {
            "ok": available and error in (None, ""), "required": health,
            "transport_error_available": available, "transport_error": error,
            "connected": sample.get(f"{source}_connected"),
            "mode": sample.get(f"{source}_mode"),
        }
    return checks


def device_check(sample: Mapping[str, object], source: str, device: str,
                 max_age_ms: float | None) -> dict[str, object]:
    """Use the same device availability evidence for health and reading rows."""
    fields = DEVICE_REQUIREMENTS[source][device]
    evidence = {field: sample.get(field) for field in fields}
    ok = all(value is True for value in evidence.values())
    if source == "ed593":
        evidence.update({f"ed593_{device}_enabled": sample.get(f"ed593_{device}_enabled"),
                         f"ed593_{device}_fault": sample.get(f"ed593_{device}_fault")})
        ok = ok and evidence[f"ed593_{device}_enabled"] is True and evidence[f"ed593_{device}_fault"] is False
    if source == "controllino" and device == "dro" and max_age_ms is not None:
        freshness = dro_freshness_check(sample, max_age_ms)
        evidence.update(freshness["observed"])
        ok = ok and freshness["ok"]
    return {"ok": ok, "observed": evidence}


def dro_freshness_check(sample: Mapping[str, object], max_age_ms: float) -> dict[str, object]:
    evidence = {}
    age = sample.get("stepper_dro_sample_age_ms")
    evidence["stepper_dro_sample_age_ms"] = age
    source_age = sample.get("stepper_age_ms")
    # Firmware da is the age at status acquisition; cached source age must
    # continue aging that measurement even if no new status arrives.
    effective_age = (age + source_age
                     if type(age) in (int, float) and type(source_age) in (int, float)
                     and math.isfinite(age) and math.isfinite(source_age)
                     and age >= 0 and source_age >= 0 else None)
    evidence["stepper_age_ms"] = source_age
    evidence["effective_age_ms"] = effective_age
    evidence["max_age_ms"] = max_age_ms
    return {"ok": freshness_check(effective_age, max_age_ms)["ok"], "observed": evidence}


def reading_checks(sample: Mapping[str, object], source: str, device: str,
                   max_age_ms: float | None, health: str) -> dict[str, object]:
    checks = source_checks(sample, source, max_age_ms, health)
    if source == "controllino" and device == "dro" and max_age_ms is not None:
        checks["dro_freshness"] = dro_freshness_check(sample, max_age_ms)
    return checks


@dataclass(frozen=True)
class TelemetryHealthAction:
    source: str
    max_age_ms: float
    health: str
    devices: tuple[str, ...]
    timeout_s: float

    def as_dict(self) -> dict[str, object]:
        return {
            "action": "telemetry_health",
            "source": self.source,
            "max_age_ms": self.max_age_ms,
            "health": self.health,
            "device": ";".join(self.devices),
            "timeout_s": self.timeout_s,
            "set_point": "",
            **dict.fromkeys((*MOVE_COLUMNS, "pulse_us", *READING_COLUMNS), ""),
        }

    def evaluate(self, sample: Mapping[str, object]) -> dict[str, object]:
        """Keep the observed evidence alongside each comparison result."""
        checks = source_checks(sample, self.source, self.max_age_ms, self.health)
        for device in self.devices:
            checks[device] = device_check(sample, self.source, device, self.max_age_ms)
        return checks


@dataclass(frozen=True)
class RequestedStateAction:
    source: str
    set_point: str
    timeout_s: float

    def as_dict(self) -> dict[str, object]:
        return {
            "action": "requested_state", "source": self.source,
            "max_age_ms": "", "health": "",
            "timeout_s": self.timeout_s, "set_point": self.set_point,
            **dict.fromkeys((*MOVE_COLUMNS, *DEVICE_COLUMNS, *READING_COLUMNS), ""),
        }

    def evaluate(self, actual_states: Mapping[str, object]) -> dict[str, object]:
        observed = actual_states.get(self.source)
        return {"requested_state": {
            "ok": observed is True, "requested": self.set_point, "observed": observed,
        }}


@dataclass(frozen=True)
class StepperModeAction:
    set_point: str
    max_age_ms: float
    health: str
    timeout_s: float
    source: str = "controllino"

    @property
    def firmware_mode(self) -> str:
        return STEPPER_MODES[self.set_point]

    def as_dict(self) -> dict[str, object]:
        return {
            "action": "requested_state", "source": self.source,
            "max_age_ms": self.max_age_ms, "health": self.health,
            "timeout_s": self.timeout_s,
            "set_point": self.set_point, **dict.fromkeys((*MOVE_COLUMNS, *DEVICE_COLUMNS, *READING_COLUMNS), ""),
            "device": "stepper",
        }

    def evaluate(self, sample: Mapping[str, object]) -> dict[str, object]:
        checks = source_checks(sample, self.source, self.max_age_ms, self.health)
        reported = sample.get("stepper_control_mode")
        observed = next((name for name, mode in STEPPER_MODES.items() if mode == reported), reported)
        checks["requested_state"] = {
            "ok": observed == self.set_point,
            "requested": self.set_point, "observed": observed,
        }
        return checks


@dataclass(frozen=True)
class MoveStepperAction:
    speed_mm_s: float
    duration_s: float
    direction: str
    max_age_ms: float
    health: str
    timeout_s: float
    source: str = "controllino"
    set_point: str = "move"

    @property
    def distance_mm(self) -> float:
        return self.speed_mm_s * self.duration_s

    def as_dict(self) -> dict[str, object]:
        return {
            "action": "requested_state", "source": self.source,
            "max_age_ms": self.max_age_ms, "health": self.health,
            "timeout_s": self.timeout_s, "set_point": self.set_point,
            "speed_mm_s": self.speed_mm_s, "duration_s": self.duration_s,
            "direction": self.direction, **dict.fromkeys((*DEVICE_COLUMNS, *READING_COLUMNS), ""),
            "device": "stepper",
        }

    def evaluate(self, sample: Mapping[str, object], command_id: str, *, started: bool = False) -> dict[str, object]:
        checks = source_checks(sample, self.source, self.max_age_ms, self.health)
        if started:
            checks = {}  # Completion is established by the matching pulse command.
        observed_id = sample.get("stepper_command_id")
        state = sample.get("stepper_state")
        moving = sample.get("stepper_moving")
        checks["move"] = {
            "ok": observed_id == command_id and state == "completed" and moving is False,
            "expected_command_id": command_id, "command_id": observed_id,
            "state": state, "moving": moving,
            "commanded_position_mm": sample.get("stepper_position_mm"),
            "commanded_target_mm": sample.get("stepper_target_mm"),
            "remaining_mm": sample.get("stepper_remaining_mm"),
        }
        return checks


@dataclass(frozen=True)
class ActuatorAction:
    source: str
    device: str
    set_point: str
    max_age_ms: float
    health: str
    timeout_s: float
    pulse_us: int | None = None

    @property
    def devices(self) -> tuple[str, ...]:
        return tuple(self.device.split(";"))

    @property
    def output_on(self) -> bool:
        # Installed solenoids are normally closed: energize to open.
        return self.set_point == ("on" if self.device == "brushless_motor" else "open")

    def as_dict(self) -> dict[str, object]:
        return {
            "action": "requested_state", "source": self.source, "device": self.device,
            "set_point": self.set_point, "max_age_ms": self.max_age_ms,
            "health": self.health, "timeout_s": self.timeout_s,
            "pulse_us": self.pulse_us if self.pulse_us is not None else "",
            **dict.fromkeys((*MOVE_COLUMNS, *READING_COLUMNS), ""),
        }

    def evaluate(self, sample: Mapping[str, object]) -> dict[str, object]:
        checks = source_checks(sample, self.source, self.max_age_ms, self.health)
        for device in self.devices:
            if device == "brushless_motor":
                field = ("stepper_brushless_motor_setpoint_us" if self.set_point == "pulse"
                         else "stepper_brushless_motor_on")
            else:
                field = ("stepper_solenoid4_on" if self.source == "controllino"
                         else f"esp32_sol{device[-1]}")
            observed = sample.get(field)
            requested = self.pulse_us if self.set_point == "pulse" else self.output_on
            check = {
                "ok": (type(observed) is int and observed == requested) if self.set_point == "pulse"
                      else observed is requested,
                "requested": requested if self.set_point == "pulse" else self.set_point,
                "observed": observed, "field": field,
            }
            if device != "brushless_motor":
                check.update(
                    observed=("open" if observed else "closed") if type(observed) is bool else None,
                    output_on=observed, valve_type="normally_closed",
                    evidence="electrical_output",
                )
            checks[device if len(self.devices) > 1 else "requested_state"] = check
        return checks


@dataclass(frozen=True)
class TemperatureReadingAction:
    device: str
    min_c: float
    max_c: float
    max_age_ms: float | None
    health: str
    timeout_s: float
    source: str = "ed593"

    def as_dict(self) -> dict[str, object]:
        return {
            "action": "reading_state", "source": self.source, "device": self.device,
            "max_age_ms": self.max_age_ms if self.max_age_ms is not None else "", "health": self.health,
            "timeout_s": self.timeout_s, "min_c": self.min_c, "max_c": self.max_c,
            "set_point": "check", "pulse_us": "", **dict.fromkeys((*MOVE_COLUMNS, "min_mm", "max_mm"), ""),
        }

    def evaluate(self, sample: Mapping[str, object]) -> dict[str, object]:
        checks = reading_checks(sample, self.source, self.device, self.max_age_ms, self.health)
        value = sample.get(f"ed593_{self.device}_temperature_c")
        available = type(value) in (int, float) and math.isfinite(value)
        enabled = sample.get(f"ed593_{self.device}_enabled")
        fault = sample.get(f"ed593_{self.device}_fault")
        checks["temperature"] = {
            "ok": available and self.min_c <= value <= self.max_c,
            "observed": value, "unit": "C", "min_c": self.min_c, "max_c": self.max_c,
            "enabled": enabled, "fault": fault,
        }
        return checks


@dataclass(frozen=True)
class DroReadingAction:
    device: str
    max_age_ms: float | None
    health: str
    timeout_s: float
    min_mm: float
    max_mm: float
    source: str = "controllino"
    set_point: str = "check"

    def as_dict(self) -> dict[str, object]:
        return {
            **dict.fromkeys(CSV_COLUMNS, ""),
            "action": "reading_state", "source": self.source, "device": self.device,
            "max_age_ms": self.max_age_ms if self.max_age_ms is not None else "", "health": self.health, "timeout_s": self.timeout_s,
            "min_mm": self.min_mm, "max_mm": self.max_mm, "set_point": self.set_point,
        }

    def evaluate(self, sample: Mapping[str, object], *, persistent: bool = False) -> dict[str, object]:
        checks = reading_checks(sample, self.source, self.device,
                                None if persistent else self.max_age_ms, self.health)
        value = sample.get("stepper_dro_position_mm")
        finite = type(value) in (int, float) and math.isfinite(value)
        checks["position"] = {
            "ok": finite and self.min_mm <= value <= self.max_mm,
            "observed": value, "min_mm": self.min_mm, "max_mm": self.max_mm,
            "unit": "mm", "datum": "dro_raw_position",
        }
        return checks


@dataclass(frozen=True)
class DisableDroCheckAction:
    timeout_s: float
    source: str = "controllino"
    device: str = "dro"

    def as_dict(self) -> dict[str, object]:
        return {**dict.fromkeys(CSV_COLUMNS, ""), "action": "reading_state",
                "source": self.source, "device": self.device,
                "set_point": "off", "timeout_s": self.timeout_s}


ExperimentAction = TelemetryHealthAction | RequestedStateAction | MoveStepperAction | StepperModeAction | ActuatorAction | TemperatureReadingAction | DroReadingAction | DisableDroCheckAction


def parse_program(csv_text: object, *, max_travel_mm: float = core.DEFAULT_STEPPER_MAX_DISTANCE_MM) -> tuple[ExperimentAction, ...]:
    """Reject ambiguous or unsupported worksheets before anything starts."""
    if not isinstance(csv_text, str):
        raise ValueError("csv must be spreadsheet text exported as CSV")
    if len(csv_text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError("Spreadsheet exceeds 256 KiB")
    try:
        reader = csv.reader(StringIO(csv_text.lstrip("\ufeff")), strict=True)
        header = next(reader, [])
        header = [cell.strip() for cell in header]
        if (len(header) != len(set(header)) or not set(BASE_CSV_COLUMNS) <= set(header)
                or not set(header) <= set(CSV_COLUMNS)):
            raise ValueError(f"CSV columns must be: {', '.join(BASE_CSV_COLUMNS)}; optional: set_point, {', '.join((*MOVE_COLUMNS, *DEVICE_COLUMNS, *READING_COLUMNS))}")
        actions = []
        for cells in reader:
            if not cells or all(not cell.strip() for cell in cells):
                continue
            row = len(actions) + 1
            if row > MAX_ROWS:
                raise ValueError("Spreadsheet exceeds 1000 action rows")
            if len(cells) != len(header):
                raise ValueError(f"Row {row}: expected {len(header)} cells")
            values = dict(zip(header, (cell.strip() for cell in cells)))
            timeout_s = _number(values["timeout_s"], "timeout_s", row, positive=True)
            if values["action"] == "reading_state":
                source, device = values["source"], values.get("device")
                set_point = values.get("set_point", "") or "check"
                if set_point not in ("check", "on", "off"):
                    raise ValueError(f"Row {row}: reading_state set_point must be check, on, or off")
                if set_point != "check" and (source, device) != ("controllino", "dro"):
                    raise ValueError(f"Row {row}: persistent reading is supported only for controllino/dro")
                if set_point == "off":
                    if any(values.get(column, "") for column in CSV_COLUMNS
                           if column not in ("action", "source", "device", "set_point", "timeout_s")):
                        raise ValueError(f"Row {row}: off requires blank check/request columns")
                    actions.append(DisableDroCheckAction(timeout_s))
                    continue
                if values["health"] not in ("", "ok") or any(values.get(column, "") for column in ("pulse_us", *MOVE_COLUMNS)):
                    raise ValueError(f"Row {row}: reading_state allows health blank or ok and requires blank pulse/motion columns")
                age = (_number(values["max_age_ms"], "max_age_ms", row, positive=False)
                       if values["max_age_ms"] else None)
                if source == "ed593" and device in DEVICE_REQUIREMENTS["ed593"]:
                    bounds = ("min_c", "max_c")
                elif source == "controllino" and device == "dro":
                    bounds = ("min_mm", "max_mm")
                else:
                    raise ValueError(f"Row {row}: reading_state supports ed593/tc0 through tc7 or controllino/dro")
                if any(values.get(column, "") for column in READING_COLUMNS if column not in bounds):
                    raise ValueError(f"Row {row}: reading columns do not apply to {device}")
                try:
                    lower, upper = (float(values.get(column, "")) for column in bounds)
                except ValueError as exc:
                    raise ValueError(f"Row {row}: {' and '.join(bounds)} must be finite numbers") from exc
                if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
                    raise ValueError(f"Row {row}: {' and '.join(bounds)} must be finite with minimum <= maximum")
                if source == "ed593":
                    actions.append(TemperatureReadingAction(device, lower, upper, age, values["health"], timeout_s))
                else:
                    actions.append(DroReadingAction(device, age, values["health"], timeout_s, lower, upper, set_point=set_point))
                continue
            if any(values.get(column, "") for column in READING_COLUMNS):
                raise ValueError(f"Row {row}: reading columns are only applicable to reading_state")
            if (values["action"] == "requested_state" and values.get("device", "")
                    and not (values["source"] == "controllino"
                             and values.get("set_point") in (*STEPPER_MODES, "move"))):
                devices = tuple(part.strip() for part in values["device"].split(";"))
                if any(not part for part in devices) or len(devices) != len(set(devices)):
                    raise ValueError(f"Row {row}: device targets must be nonempty and unique")
                device = ";".join(devices)
                source = values["source"]
                set_point = values.get("set_point", "")
                solenoids = {"solenoid1", "solenoid2", "solenoid3", "solenoid4"}
                compatible = set(devices) <= solenoids or devices == ("brushless_motor",)
                valid_source = compatible and all(
                    (target in solenoids and source == "esp32")
                    or (target in ("solenoid4", "brushless_motor") and source == "controllino")
                    for target in devices
                )
                if not valid_source:
                    raise ValueError(f"Row {row}: unsupported actuator/source combination")
                if set_point not in (("on", "off", "pulse") if device == "brushless_motor" else ("open", "closed")):
                    raise ValueError(f"Row {row}: solenoid set_point must be open/closed; brushless_motor supports on/off/pulse")
                if values["health"] != "ok":
                    raise ValueError(f"Row {row}: actuator requires health=ok")
                if any(values.get(column, "") for column in MOVE_COLUMNS):
                    raise ValueError(f"Row {row}: actuator requires blank motion columns")
                pulse = None
                if set_point == "pulse":
                    number = _number(values.get("pulse_us", ""), "pulse_us", row, positive=True)
                    if not number.is_integer():
                        raise ValueError(f"Row {row}: pulse_us must be an integer")
                    pulse = int(number)
                    try:
                        core.UsbStepperSource._brushless_pulse_us(pulse)
                    except ValueError as exc:
                        raise ValueError(f"Row {row}: {exc}") from exc
                elif values.get("pulse_us", ""):
                    raise ValueError(f"Row {row}: pulse_us is only applicable to set_point=pulse")
                actions.append(ActuatorAction(source, device, set_point,
                    _number(values["max_age_ms"], "max_age_ms", row, positive=False),
                    values["health"], timeout_s, pulse))
                continue
            if values.get("pulse_us", ""):
                raise ValueError(f"Row {row}: pulse_us is only applicable to actuator requests")
            if values["action"] == "requested_state":
                if values["source"] == "controllino" and values.get("set_point") in STEPPER_MODES:
                    if values.get("device", "") not in ("", "stepper"):
                        raise ValueError(f"Row {row}: mode change targets device=stepper")
                    if values["health"] != "ok":
                        raise ValueError(f"Row {row}: mode change requires health=ok")
                    if any(values.get(column, "") for column in MOVE_COLUMNS):
                        raise ValueError(f"Row {row}: mode change requires blank motion columns")
                    actions.append(StepperModeAction(
                        values["set_point"],
                        _number(values["max_age_ms"], "max_age_ms", row, positive=False),
                        values["health"], timeout_s,
                    ))
                    continue
                if values["source"] == "controllino" and values.get("set_point") == "move":
                    if values.get("device", "") not in ("", "stepper"):
                        raise ValueError(f"Row {row}: move targets device=stepper")
                    if values["health"] != "ok":
                        raise ValueError(f"Row {row}: move requires health=ok")
                    if values.get("direction") not in ("forward", "reverse"):
                        raise ValueError(f"Row {row}: direction must be forward or reverse")
                    move = MoveStepperAction(
                        _number(values.get("speed_mm_s", ""), "speed_mm_s", row, positive=True),
                        _number(values.get("duration_s", ""), "duration_s", row, positive=True),
                        values["direction"],
                        _number(values["max_age_ms"], "max_age_ms", row, positive=False),
                        values["health"], timeout_s,
                    )
                    if not math.isfinite(move.distance_mm) or move.distance_mm <= 0:
                        raise ValueError(f"Row {row}: speed × duration must give positive finite distance")
                    try:
                        core.validate_stepper_move(move.distance_mm, move.speed_mm_s,
                                                   max_travel_mm=max_travel_mm)
                    except ValueError as exc:
                        raise ValueError(f"Row {row}: {exc}") from exc
                    actions.append(move)
                    continue
                if values["source"] != "recording" or values.get("set_point") != "on":
                    raise ValueError(f"Row {row}: requested_state supports recording/on or controllino/move, positional, directional")
                if any(values.get(column, "") for column in ("max_age_ms", "health", "device")):
                    raise ValueError(f"Row {row}: recording requires blank max_age_ms, health, device")
                if any(values.get(column, "") for column in MOVE_COLUMNS):
                    raise ValueError(f"Row {row}: recording requires blank motion columns")
                actions.append(RequestedStateAction("recording", "on", timeout_s))
                continue
            if values["action"] != "telemetry_health":
                raise ValueError(f"Row {row}: action must be telemetry_health, reading_state, or requested_state; Start is external")
            if values.get("set_point", ""):
                raise ValueError(f"Row {row}: telemetry_health requires a blank set_point")
            if any(values.get(column, "") for column in MOVE_COLUMNS):
                raise ValueError(f"Row {row}: telemetry_health requires blank motion columns")
            source = values["source"]
            if source not in DEVICE_REQUIREMENTS:
                raise ValueError(f"Row {row}: source must be esp32, dxmr90, controllino, or ed593")
            if values["health"] != "ok":
                raise ValueError(f"Row {row}: health must be ok (available telemetry, no transport error)")
            devices = (tuple(part.strip() for part in values.get("device", "").split(";"))
                       if values.get("device", "") else ())
            if any(not device for device in devices) or len(devices) != len(set(devices)):
                raise ValueError(f"Row {row}: device targets must be nonempty and unique")
            unknown = set(devices) - DEVICE_REQUIREMENTS[source].keys()
            if unknown:
                raise ValueError(f"Row {row}: unsupported {source} devices: {', '.join(sorted(unknown))}")
            actions.append(TelemetryHealthAction(
                source=source,
                max_age_ms=_number(values["max_age_ms"], "max_age_ms", row, positive=False),
                health=values["health"],
                devices=devices,
                timeout_s=timeout_s,
            ))
    except csv.Error as exc:
        raise ValueError(f"Invalid CSV: {exc}") from exc
    if not actions:
        raise ValueError("Spreadsheet must contain at least one action row")
    active = False
    for row, action in enumerate(actions, 1):
        if isinstance(action, DroReadingAction) and action.set_point == "on":
            if active:
                raise ValueError(f"Row {row}: DRO check already enabled; disable it before replacing it")
            active = True
        elif isinstance(action, DisableDroCheckAction):
            if not active:
                raise ValueError(f"Row {row}: no persistent DRO check to disable")
            active = False
    if active:
        raise ValueError("An on DRO check requires an explicit off row before experiment end")
    return tuple(actions)


class ExperimentRunner:
    """A sequential state machine with explicit Start and telemetry events."""

    def __init__(self, request_state: Callable[[RequestedStateAction | MoveStepperAction | StepperModeAction | ActuatorAction], object] | None = None,
                 abort_state: Callable[[MoveStepperAction], object] | None = None) -> None:
        self.program: tuple[ExperimentAction, ...] = ()
        self.request_state = request_state
        self.abort_state = abort_state
        self.name = ""
        self.state = "empty"
        self.revision = 0
        self.program_id = 0
        self.current_index = 0
        self.row_started_s: float | None = None
        self.started_s: float | None = None
        self.finished_s: float | None = None
        self.results: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []
        self.monitors: dict[str, dict[str, object]] = {}
        self._monitor_action: DroReadingAction | None = None
        self._last_sample: dict[str, object] = {}
        self._last_sample_s: float | None = None
        self._pending_request: Future | None = None
        self._pending_stop: Future | None = None

    def _event(self, event: str, now_s: float, **details: object) -> None:
        self.revision += 1
        self.events.append({"event": event, "elapsed_s": now_s, **details})

    def load(self, csv_text: object, name: str, now_s: float, *,
             max_travel_mm: float = core.DEFAULT_STEPPER_MAX_DISTANCE_MM) -> None:
        if self.state == "running":
            raise RuntimeError("The experiment is running; wait for it to finish before loading")
        program = parse_program(csv_text, max_travel_mm=max_travel_mm)
        self.program = program
        self.name = name
        self.program_id += 1
        self.state = "ready"
        self.current_index = 0
        self.row_started_s = self.started_s = self.finished_s = None
        self.results = []
        self.events = []
        self.monitors = {}
        self._monitor_action = None
        self._last_sample = {}
        self._last_sample_s = None
        self._pending_request = None
        self._pending_stop = None
        self._event("loaded", now_s)

    def start(self, now_s: float, program_id: object = None) -> None:
        if not self.program:
            raise RuntimeError("Load a spreadsheet before starting")
        if self.state == "running":
            raise RuntimeError("The experiment is already running")
        if program_id is not None and (type(program_id) is not int or program_id != self.program_id):
            raise RuntimeError("The loaded spreadsheet has changed; review it before starting")
        self.state = "running"
        self.current_index = 0
        self.started_s = now_s
        self.finished_s = None
        self.results = []
        self.events = []
        self.monitors = {}
        self._monitor_action = None
        self._last_sample = {}
        self._last_sample_s = None
        self._pending_request = None
        self._pending_stop = None
        self._event("start", now_s, action="start")
        self._begin_row(now_s)

    def _begin_row(self, now_s: float) -> None:
        self.row_started_s = now_s
        self.results.append({
            "row": self.current_index + 1, "status": "waiting", "checks": {},
        })
        self._event("row_started", now_s, row=self.current_index + 1,
                    action=self.program[self.current_index].as_dict()["action"],
                    source=self.program[self.current_index].source)

    def _accept_request(self, action: ExperimentAction, request: object, now_s: float) -> None:
        result = self.results[-1]
        if isinstance(action, MoveStepperAction):
            if not isinstance(request, Mapping) or not isinstance(request.get("command_id"), str):
                raise RuntimeError("Move dispatch did not supply a command ID")
            result["request"] = dict(request)
        elif isinstance(action, ActuatorAction) and isinstance(request, Mapping):
            result["request"] = dict(request)
            failures = [f"{device}: {details['error']}"
                        for device, details in request.get("targets", {}).items()
                        if details.get("status") == "failed"]
            if failures:
                raise RuntimeError("Actuator dispatch failed: " + "; ".join(failures))
        result.update(requested=True, request_pending=False)
        self._event("command_completed", now_s, row=self.current_index + 1)

    def on_io_event(self, now_s: float) -> None:
        """Consume completed I/O without waiting, including stops after failure."""
        if self._pending_stop is not None and self._pending_stop.done():
            future, self._pending_stop = self._pending_stop, None
            result = self.results[-1]
            result["stop_pending"] = False
            try:
                future.result()
                result["stop_requested"] = True
            except Exception as exc:
                result["stop_error"] = str(exc)
            self._event("stop_completed", now_s, row=self.current_index + 1)
        if self.state != "running" and self._pending_request is not None and self._pending_request.done():
            future, self._pending_request = self._pending_request, None
            result = self.results[-1]
            result["request_pending"] = False
            if future.cancelled():
                result["request_cancelled"] = True
            else:
                try:
                    request = future.result()
                    if isinstance(request, Mapping):
                        result["request"] = dict(request)
                    result["request_finished_after_failure"] = True
                except Exception as exc:
                    result["request_error"] = str(exc)
            self._event("command_completed_after_failure", now_s, row=self.current_index + 1)

    def on_sample(self, sample: Mapping[str, object], now_s: float,
                  actual_states: Mapping[str, object] | None = None) -> None:
        self.on_io_event(now_s)
        if self.state != "running" or now_s < self.row_started_s:
            return
        # Update evidence before timer checks so an arriving fresh sample is used.
        self._last_sample = dict(sample)
        self._last_sample_s = now_s
        # Row deadlines still reject late completion evidence.
        if self.tick(now_s):
            return
        action = self.program[self.current_index]
        result = self.results[-1]
        if isinstance(action, DisableDroCheckAction):
            self._monitor_action = None
            self.monitors["controllino/dro"].update(active=False, disabled_row=self.current_index + 1,
                                               disabled_s=now_s)
            self._event("monitor_disabled", now_s, row=self.current_index + 1, device="dro")
            checks = {"monitor_disabled": {"ok": True, "observed": {"device": "dro", "active": False}}}
        elif isinstance(action, (RequestedStateAction, MoveStepperAction, StepperModeAction, ActuatorAction)):
            if self._pending_request is not None:
                future = self._pending_request
                if not future.done():
                    return  # Monitoring already ran above; never await command I/O.
                self._pending_request = None
                try:
                    self._accept_request(action, future.result(), now_s)
                except Exception as exc:
                    result.update(error=str(exc), request_pending=False)
                    self.fail("request_failed", now_s)
                return  # Actual-state confirmation needs a subsequent sample.
            if not result.get("requested"):
                if isinstance(action, (MoveStepperAction, StepperModeAction, ActuatorAction)):
                    checks = source_checks(sample, action.source, action.max_age_ms, action.health)
                    if result["checks"] != checks:
                        result["checks"] = checks
                        self.revision += 1
                    if not all(check["ok"] for check in checks.values()):
                        return
                if self.request_state is None:
                    self.fail("requested_state_unavailable", now_s)
                    return
                self._event("state_requested", now_s, row=self.current_index + 1,
                            source=action.source, set_point=action.set_point)
                result["request_attempted"] = True
                try:
                    request = self.request_state(action)
                    if isinstance(request, Future):
                        self._pending_request = request
                        result["request_pending"] = True
                        self.revision += 1
                        if not request.done():
                            return
                        self._pending_request = None
                        request = request.result()
                    self._accept_request(action, request, now_s)
                except Exception as exc:
                    result.update(error=str(exc), request_pending=False)
                    self.fail("request_failed", now_s)
                    return
                return  # Confirm on the next state event, never optimistically.
            if isinstance(action, MoveStepperAction):
                started = result.get("motion_started", False)
                checks = action.evaluate(sample, result["request"]["command_id"], started=started)
                error = (actual_states or {}).get("stepper_command_error")
                if error and not started:
                    result["checks"] = checks
                    result["error"] = str(error)
                    self.fail("move_rejected", now_s)
                    return
                if not started and (not checks["freshness"]["ok"] or not checks["health"]["ok"]):
                    # Allow telemetry to recover within the start handshake deadline.
                    if result["checks"] != checks:
                        result["checks"] = checks
                        self.revision += 1
                    return
                move = checks["move"]
                matching = move["command_id"] == move["expected_command_id"]
                if matching and move["state"] in ("aborted", "emergency_stop", "limit_blocked", "fault"):
                    result["checks"] = checks
                    result["error"] = str(sample.get("stepper_fault") or sample.get("stepper_blocked_reason") or move["state"])
                    self.fail("move_aborted", now_s)
                    return
                if not started and matching and (
                        (move["state"] == "moving" and move["moving"] is True) or move["ok"]):
                    result.update(motion_started=True, motion_started_s=now_s)
                    self._event("motion_started", now_s, row=self.current_index + 1,
                                command_id=move["expected_command_id"])
                    checks = action.evaluate(sample, move["expected_command_id"], started=True)
            elif isinstance(action, (StepperModeAction, ActuatorAction)):
                checks = action.evaluate(sample)
                error = (actual_states or {}).get("stepper_command_error")
                if error and action.source == "controllino":
                    result["checks"] = checks
                    result["error"] = str(error)
                    self.fail("actuator_rejected" if isinstance(action, ActuatorAction) else "mode_rejected", now_s)
                    return
                if not checks["freshness"]["ok"] or not checks["health"]["ok"]:
                    result["checks"] = checks
                    self.fail("actuator_telemetry_unhealthy" if isinstance(action, ActuatorAction) else "mode_telemetry_unhealthy", now_s)
                    return
            else:
                checks = action.evaluate(actual_states or {})
        else:
            checks = action.evaluate(sample)
        if isinstance(action, (TemperatureReadingAction, DroReadingAction)) and not all(check["ok"] for check in checks.values()):
            result["checks"] = checks
            self.fail("reading_check_failed", now_s)
            return
        if result["checks"] != checks:
            result["checks"] = checks
            self.revision += 1
        if not all(check["ok"] for check in checks.values()):
            return
        if isinstance(action, DroReadingAction) and action.set_point == "on":
            self._monitor_action = action
            self.monitors["controllino/dro"] = {
                "active": True, "row": self.current_index + 1, "action": action.as_dict(),
                "checks": action.evaluate(sample, persistent=True), "checked_s": now_s,
                "phase": "persistent",
            }
            self._event("monitor_enabled", now_s, row=self.current_index + 1, device="dro")
        result.update(status="passed", finished_s=now_s)
        self._event("row_passed", now_s, row=self.current_index + 1,
                    source=action.source, checks=deepcopy(checks))
        self.current_index += 1
        if self.current_index == len(self.program):
            self.state = "completed"
            self.finished_s = now_s
            self.row_started_s = None
            self._event("completed", now_s)
        else:
            # Each subsequent row consumes a subsequent telemetry event.
            self._begin_row(now_s)

    def tick(self, now_s: float) -> bool:
        """Deadline events also fail a check when no telemetry ever arrives."""
        self.on_io_event(now_s)
        if self.state != "running":
            return False
        if self._monitor_action is not None:
            sample = dict(self._last_sample)
            checks = self._monitor_action.evaluate(sample, persistent=True)
            monitor = self.monitors["controllino/dro"]
            if monitor["checks"] != checks:
                monitor.update(checks=deepcopy(checks), checked_s=now_s)
                self.revision += 1
            if not all(check["ok"] for check in checks.values()):
                self.results[-1].update(monitor_row=monitor["row"],
                                        monitor_checks=deepcopy(checks))
                self.fail("persistent_reading_check_failed", now_s)
                return True
        action = self.program[self.current_index]
        assert self.row_started_s is not None
        if isinstance(action, MoveStepperAction) and self.results[-1].get("motion_started"):
            return False
        if now_s - self.row_started_s < action.timeout_s:
            return False
        self.fail("timeout", now_s)
        return True

    def fail(self, reason: str, now_s: float) -> None:
        if self.state != "running":
            return
        self.results[-1].update(status="failed", reason=reason, finished_s=now_s)
        self.state = "failed"
        self.finished_s = now_s
        action = self.program[self.current_index]
        if self._pending_request is not None:
            self._pending_request.cancel()  # Only cancels work that has not begun.
        if (isinstance(action, MoveStepperAction) and self.results[-1].get("request_attempted")
                and self.abort_state is not None):
            try:
                stop = self.abort_state(action)
                if isinstance(stop, Future):
                    self._pending_stop = stop
                    self.results[-1]["stop_pending"] = True
                else:
                    self.results[-1]["stop_requested"] = True
            except (OSError, RuntimeError, ValueError) as exc:
                self.results[-1]["stop_error"] = str(exc)
        self.on_io_event(now_s)
        self._event("row_failed", now_s, row=self.current_index + 1, reason=reason,
                    checks=deepcopy(self.results[-1]["checks"]))

    def snapshot(self) -> dict[str, object]:
        action = self.program[self.current_index] if self.state == "running" else None
        started_move = isinstance(action, MoveStepperAction) and self.results[-1].get("motion_started")
        deadline = (self.row_started_s + action.timeout_s
                    if action is not None and self.row_started_s is not None and not started_move else None)
        return deepcopy({
            "state": self.state,
            "revision": self.revision,
            "program_id": self.program_id,
            "name": self.name,
            "columns": list(CSV_COLUMNS),
            "program": [action.as_dict() for action in self.program],
            "current_row": self.current_index + 1 if self.state == "running" else None,
            "started_s": self.started_s,
            "finished_s": self.finished_s,
            "deadline_s": deadline,
            "monitors": self.monitors,
            "results": self.results,
            "events": self.events,
        })
