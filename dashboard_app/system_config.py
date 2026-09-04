"""Persistent machine-level settings shared across dashboard connections."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any


SYSTEM_CONFIG_VERSION = 1
DEFAULT_POWDER_MASS_PER_STEPPER_TRAVEL_G_PER_MM = 2.4
DEFAULT_SYSTEM_CONFIG: dict[str, object] = {
    "version": SYSTEM_CONFIG_VERSION,
    "geometry": {
        # Equivalent to (g/s) / (mm/s). Powder flow divided by this value
        # gives the required linear stepper speed.
        "powder_mass_per_stepper_travel_g_per_mm": (
            DEFAULT_POWDER_MASS_PER_STEPPER_TRAVEL_G_PER_MM
        ),
    },
    "stepper": {
        "dro_zero_raw_mm": None,
    },
}


class SystemConfig:
    """Load and atomically update persistent dashboard system settings."""

    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._values = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path is None or not self.path.exists():
            return deepcopy(DEFAULT_SYSTEM_CONFIG)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load system config {self.path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"system config {self.path} must contain a JSON object")
        if payload.get("version") != SYSTEM_CONFIG_VERSION:
            raise ValueError(
                f"system config {self.path} must use version {SYSTEM_CONFIG_VERSION}"
            )
        stepper = payload.get("stepper")
        if not isinstance(stepper, dict):
            raise ValueError(
                f"system config {self.path} must contain a stepper object"
            )
        dro_zero_raw_mm = self._validate_dro_zero(
            stepper.get("dro_zero_raw_mm")
        )
        geometry = payload.get("geometry")
        if geometry is None:
            # Version-1 files created before geometry was added remain valid.
            geometry = deepcopy(DEFAULT_SYSTEM_CONFIG["geometry"])
        if not isinstance(geometry, dict):
            raise ValueError(
                f"system config {self.path} must contain a geometry object"
            )
        powder_mass_per_travel = self._validate_positive_finite(
            geometry.get(
                "powder_mass_per_stepper_travel_g_per_mm",
                DEFAULT_POWDER_MASS_PER_STEPPER_TRAVEL_G_PER_MM,
            ),
            "geometry.powder_mass_per_stepper_travel_g_per_mm",
        )

        normalized = deepcopy(payload)
        normalized_stepper = deepcopy(stepper)
        normalized_stepper["dro_zero_raw_mm"] = dro_zero_raw_mm
        normalized_geometry = deepcopy(geometry)
        normalized_geometry[
            "powder_mass_per_stepper_travel_g_per_mm"
        ] = powder_mass_per_travel
        normalized["stepper"] = normalized_stepper
        normalized["geometry"] = normalized_geometry
        return normalized

    @staticmethod
    def _validate_dro_zero(value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("stepper.dro_zero_raw_mm must be finite or null")
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("stepper.dro_zero_raw_mm must be finite or null")
        return parsed

    @staticmethod
    def _validate_positive_finite(value: object, key: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a positive finite number")
        parsed = float(value)
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError(f"{key} must be a positive finite number")
        return parsed

    @property
    def stepper_dro_zero_raw_mm(self) -> float | None:
        with self._lock:
            stepper = self._values["stepper"]
            assert isinstance(stepper, dict)
            return self._validate_dro_zero(stepper.get("dro_zero_raw_mm"))

    @property
    def powder_mass_per_stepper_travel_g_per_mm(self) -> float:
        with self._lock:
            geometry = self._values["geometry"]
            assert isinstance(geometry, dict)
            return self._validate_positive_finite(
                geometry.get("powder_mass_per_stepper_travel_g_per_mm"),
                "geometry.powder_mass_per_stepper_travel_g_per_mm",
            )

    def set_stepper_dro_zero_raw_mm(self, value: float) -> float:
        parsed = self._validate_dro_zero(value)
        assert parsed is not None
        rounded = round(parsed, 2)
        with self._lock:
            updated = deepcopy(self._values)
            stepper = updated["stepper"]
            assert isinstance(stepper, dict)
            stepper["dro_zero_raw_mm"] = rounded
            if self.path is not None:
                self._write_atomic(updated)
            self._values = updated
        return rounded

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._values)

    def _write_atomic(self, payload: dict[str, Any]) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
