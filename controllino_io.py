"""Controllino channel identities and host-side pressure calibration.

ADC samples remain available even when their engineering calibration is unknown.
The standard-input scale is nominal for the board's 24 V input-divider setting;
the dedicated 0–10 V input scale must be established separately.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy


PRESSURE_STALE_MS = 500
STANDARD_VOLTS_PER_COUNT = 5.0 * 6.14 / 1024
# Calibration keeps its source units; each channel defines its published unit.
# SI prefixes are case-sensitive (MPa is megapascal, not millipascal).
PSI_PER_BAR = 14.503773773020923
PRESSURE_TO_PSI = {"Pa": 1e-5 * PSI_PER_BAR, "kPa": .01 * PSI_PER_BAR,
                   "MPa": 10.0 * PSI_PER_BAR, "hPa": .001 * PSI_PER_BAR,
                   "mbar": .001 * PSI_PER_BAR, "bar": PSI_PER_BAR, "psi": 1.0}
PRESSURE_INPUTS = tuple(
    {
        "id": index, "terminal": terminal, "arduino_pin": pin,
        "group": "low" if index <= 4 else "high",
        "group_label": "Low pressure" if index <= 4 else "High pressure",
        "group_index": index if index <= 4 else index - 4,
        "unit": "mbar" if index <= 4 else "psi",
        "input_range_v": (0, 10) if index in (3, 4) else None,
        "supply_v": 5 if index in (5, 6, 7) else None,
        "supply_known": index in (5, 6, 7),
        "volts_per_count": None if index in (3, 4) else STANDARD_VOLTS_PER_COUNT,
    }
    for index, (terminal, pin) in enumerate(
        (("AI10", "A10"), ("AI11", "A11"), ("AI12", "A14"),
         ("AI13", "A15"), ("AI0", "A0"), ("AI1", "A1"), ("AI2", "A2")), 1
    )
)
CONTROLLINO_RELAYS = (
    {"index": 0, "relay": "R3", "pin": 25, "wire_id": 5, "label": "R3 solenoid"},
    {"index": 1, "relay": "R4", "pin": 26, "wire_id": 6, "label": "R4 solenoid"},
    {"index": 2, "relay": "R5", "pin": 27, "wire_id": 4, "label": "R5 solenoid"},
)
SOLENOID_COUNT = len(CONTROLLINO_RELAYS)
DEFAULT_PRESSURE_CONFIG = {
    str(channel["id"]): {
        "label": f"{channel['group_label']} {channel['group_index']}",
        "volts_per_count": channel["volts_per_count"],
        "calibration": None,
    }
    for channel in PRESSURE_INPUTS
}


def _finite(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _text(value: object, key: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{key} must be a nonempty string of at most {limit} characters")
    return value.strip()


def _pressure_endpoints(calibration: Mapping, unit: str) -> tuple[float, float]:
    factor = PRESSURE_TO_PSI[calibration["unit"]] / PRESSURE_TO_PSI[unit]
    return calibration["pressure_min"] * factor, calibration["pressure_max"] * factor


def validate_pressure_config(values: object = None) -> dict[str, dict]:
    """Merge partial version-1 configuration with defaults; reject silent typos."""
    if values is None:
        return deepcopy(DEFAULT_PRESSURE_CONFIG)
    if not isinstance(values, dict) or set(values) - set(DEFAULT_PRESSURE_CONFIG):
        raise ValueError("pressure_inputs must be an object keyed by channel IDs '1' through '7'")
    result = deepcopy(DEFAULT_PRESSURE_CONFIG)
    calibration_keys = {"voltage_min", "voltage_max", "pressure_min", "pressure_max", "unit", "reference"}
    for descriptor in PRESSURE_INPUTS:
        channel_id = str(descriptor["id"])
        update = values.get(channel_id, {})
        key = f"pressure_inputs.{channel_id}"
        if not isinstance(update, dict) or set(update) - {"label", "volts_per_count", "calibration"}:
            raise ValueError(f"{key} supports label, volts_per_count and calibration only")
        channel = result[channel_id]
        channel.update(deepcopy(update))
        channel["label"] = _text(channel["label"], f"{key}.label", 80)
        scale = channel["volts_per_count"]
        if scale is not None and (not _finite(scale) or scale <= 0 or not _finite(scale * 1023)):
            raise ValueError(f"{key}.volts_per_count must be positive and finite, or null")
        calibration = channel["calibration"]
        if calibration is None:
            continue
        if not isinstance(calibration, dict) or set(calibration) != calibration_keys:
            raise ValueError(f"{key}.calibration requires voltage_min/max, pressure_min/max, unit and reference")
        for name in ("voltage_min", "voltage_max", "pressure_min", "pressure_max"):
            if not _finite(calibration[name]):
                raise ValueError(f"{key}.calibration.{name} must be finite")
        if calibration["voltage_min"] >= calibration["voltage_max"] or calibration["pressure_min"] >= calibration["pressure_max"]:
            raise ValueError(f"{key}.calibration endpoints must be ascending")
        if not all(_finite(calibration[f"{name}_max"] - calibration[f"{name}_min"])
                   for name in ("voltage", "pressure")):
            raise ValueError(f"{key}.calibration spans must be finite")
        calibration["unit"] = _text(calibration["unit"], f"{key}.calibration.unit", 16)
        if calibration["unit"] not in PRESSURE_TO_PSI:
            raise ValueError(f"{key}.calibration.unit must be one of {', '.join(PRESSURE_TO_PSI)}")
        low, high = _pressure_endpoints(calibration, descriptor["unit"])
        if not all(_finite(value) for value in (low, high, high - low)) or low >= high:
            raise ValueError(f"{key}.calibration must have finite ascending endpoints and span in {descriptor['unit']}")
        calibration["reference"] = _text(calibration["reference"], f"{key}.calibration.reference", 40)
    return result


def pressure_fields() -> tuple[str, ...]:
    return tuple(f"stepper_pressure{channel['id']}_{suffix}"
                 for channel in PRESSURE_INPUTS for suffix in ("adc", "voltage_v", "value", "valid"))


def pressure_descriptors(config: Mapping[str, Mapping]) -> list[dict]:
    """Use one channel description for display, charts and recording metadata."""
    result = []
    for channel in PRESSURE_INPUTS:
        channel_id = channel["id"]
        settings = config[str(channel_id)]
        prefix = f"stepper_pressure{channel_id}_"
        calibration = settings["calibration"]
        result.append({
            **channel, "label": settings["label"], "volts_per_count": settings["volts_per_count"],
            **{f"{name}_key": prefix + suffix for name, suffix in
               (("adc", "adc"), ("voltage", "voltage_v"), ("value", "value"), ("valid", "valid"))},
            "calibrated": calibration is not None,
            "reference": calibration["reference"] if calibration else None,
            "data_key": prefix + "value",
        })
    return result


def transform_pressure_sample(sample: dict, config: Mapping[str, Mapping], *, stale_after_ms: float = PRESSURE_STALE_MS) -> None:
    """Derive calibrated channel-unit values, retaining raw evidence when invalid.

Config must be normalized by validate_pressure_config. Uncalibrated channels
are valid when their raw ADC sample is fresh; calibrated channels additionally
need a voltage scale and a voltage inside the inclusive calibration range.
"""
    age, source_age = sample.get("stepper_pressure_sample_age_ms"), sample.get("stepper_age_ms")
    fresh = (sample.get("stepper_pressure_capable") is True and sample.get("stepper_connected") is True
             and not sample.get("stepper_transport_error") and _finite(age) and age >= 0
             and _finite(source_age) and source_age >= 0 and age + source_age <= stale_after_ms)
    for channel in PRESSURE_INPUTS:
        prefix = f"stepper_pressure{channel['id']}_"
        settings = config[str(channel["id"])]
        adc = sample.get(prefix + "adc")
        raw_valid = type(adc) is int and 0 <= adc <= 1023
        scale = settings["volts_per_count"]
        voltage = adc * scale if raw_valid and scale is not None else None
        sample[prefix + "voltage_v"] = voltage
        sample[prefix + "value"] = None
        valid = fresh and raw_valid
        calibration = settings["calibration"]
        if calibration is not None:
            valid = (valid and voltage is not None
                     and calibration["voltage_min"] <= voltage <= calibration["voltage_max"])
            if valid:
                fraction = ((voltage - calibration["voltage_min"])
                            / (calibration["voltage_max"] - calibration["voltage_min"]))
                low, high = _pressure_endpoints(calibration, channel["unit"])
                sample[prefix + "value"] = low + fraction * (high - low)
        sample[prefix + "valid"] = bool(valid)
