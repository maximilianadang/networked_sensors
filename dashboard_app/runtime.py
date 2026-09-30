"""Experiment state, cross-device sampling, recording, and request coordination.

DeviceControls (supervisor_core.py) owns device rules and confirmations.
This module owns locks/waits and persisted application settings; http.py and
static/* own the webpage. No wire encoding or controller polarity belongs here.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

try:
    from ..recorder import FlowRunRecorder, list_recordings, resolve_artifact
    from .. import supervisor_core as core
except ImportError:  # pragma: no cover - direct dashboard-lean.py execution
    from recorder import FlowRunRecorder, list_recordings, resolve_artifact
    import supervisor_core as core

from .system_config import SystemConfig


DEFAULT_STALE_AFTER_S = 5.0
DEFAULT_HISTORY_LIMIT = 600
DEFAULT_METADATA = {
    "sample_number": "",
    "sub_number": "",
    "dispenser": "",
    "material": "",
    "powder_flow_rate_g_per_s": "",
    "test_duration_s": "",
    "description": "",
    "notes": "",
}

DRO_VELOCITY_WINDOW_S = 0.65
DRO_VELOCITY_MIN_SPAN_S = 0.15
DRO_VELOCITY_STOP_DEADBAND_MM_S = 0.04


class SourceMerger:
    """Latest-value merge with per-source health and age fields."""

    OPEN_FLOW_PAIRS = (
        ("esp32_sol1", "esp32_f1_gmin"),
        ("esp32_sol2", "esp32_f2_gmin"),
        ("esp32_sol3", "esp32_f3_gmin"),
    )

    def __init__(
        self,
        sources: list[core.SourceAdapter],
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
    ) -> None:
        self.sources = sources
        self.stale_after_s = stale_after_s
        self._latest: dict[str, core.SourceReading] = {}
        self._fresh_readings: list[core.SourceReading] = []

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
        sample.update(core.solenoid_status(sample))

        sample["esp32_open_flow_gmin"] = self._sum_open_flows(
            sample,
            self.OPEN_FLOW_PAIRS,
        )
        sample["dxmr90_open_total_mass_flow_g_min"] = self._sum_open_flows(
            sample,
            (("solenoid4_on", "dxmr90_total_mass_flow_g_min"),),
        )
        return sample

    @staticmethod
    def _sum_open_flows(
        sample: Mapping[str, object],
        pairs: tuple[tuple[str, str], ...],
    ) -> float | None:
        """Sum measured flow only for channels whose valve state is open."""

        total = 0.0
        for solenoid_field, flow_field in pairs:
            open_state = sample.get(solenoid_field)
            if not isinstance(open_state, bool):
                return None
            if not open_state:
                continue
            raw_value = sample.get(flow_field)
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                return None
            value = float(raw_value)
            if not math.isfinite(value):
                return None
            total += value
        return round(total, 6)

    def fresh_readings(self) -> tuple[core.SourceReading, ...]:
        return tuple(self._fresh_readings)



def iter_merged_samples(
    samples: int,
    rate_hz: float = 10.0,
    sources: list[core.SourceAdapter] | None = None,
    scenario: str = "healthy",
    drop_after_s: float = core.DEFAULT_DROP_AFTER_S,
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
        core.make_simulated_sources(scenario=scenario, drop_after_s=drop_after_s)
        if sources is None
        else sources
    )
    merger = SourceMerger(source_list, stale_after_s=stale_after_s)
    timestamp0 = start_time or datetime.now(timezone.utc)
    for index in range(samples):
        elapsed_s = index * period_s
        yield merger.poll(elapsed_s, timestamp0 + timedelta(seconds=elapsed_s))



class DashboardRuntime:
    """Realtime selected-source supervisor stream shared by HTTP handlers."""

    def __init__(
        self,
        *,
        scenario: str,
        rate_hz: float,
        drop_after_s: float,
        stale_after_s: float,
        history_limit: int,
        record_dir: Path,
        esp32_source: str,
        esp32_base_url: str = core.DEFAULT_ESP32_BASE_URL,
        esp32_timeout: float = core.DEFAULT_ESP32_TIMEOUT_S,
        dxmr90_source: str,
        stepper_source: str,
        stepper_port: str,
        stepper_baud: int,
        dxmr90_host: str,
        dxmr90_port: int,
        dxmr90_unit_id: int,
        dxmr90_timeout: float,
        dxmr90_addressing: str,
        dxmr90_word_order: str,
        dxmr90_data_path: str,
        dxmr90_rate_hz: float,
        stepper_network_url: str = core.DEFAULT_STEPPER_NETWORK_URL,
        stepper_network_timeout: float = core.DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
        system_config_path: Path | None = None,
    ) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        if history_limit < 1:
            raise ValueError("history_limit must be positive")

        self.scenario = scenario
        self.rate_hz = rate_hz
        self.period_s = 1.0 / rate_hz
        self.drop_after_s = drop_after_s
        self.stale_after_s = stale_after_s
        self.history_limit = history_limit
        self.record_dir = Path(record_dir)
        self.esp32_source = esp32_source
        self.esp32_base_url = esp32_base_url
        self.esp32_timeout = esp32_timeout
        self.dxmr90_source = dxmr90_source
        self.stepper_source = stepper_source
        self.stepper_port = stepper_port
        self.stepper_baud = stepper_baud
        self.stepper_network_url = stepper_network_url
        self.stepper_network_timeout = stepper_network_timeout
        self.dxmr90_host = dxmr90_host
        self.dxmr90_port = dxmr90_port
        self.dxmr90_unit_id = dxmr90_unit_id
        self.dxmr90_timeout = dxmr90_timeout
        self.dxmr90_addressing = dxmr90_addressing
        self.dxmr90_word_order = dxmr90_word_order
        self.dxmr90_data_path = dxmr90_data_path
        self.dxmr90_rate_hz = dxmr90_rate_hz
        self.sources = core.make_sources(
            esp32_source=esp32_source,
            esp32_base_url=esp32_base_url,
            esp32_timeout=esp32_timeout,
            dxmr90_source=dxmr90_source,
            stepper_source=stepper_source,
            stepper_port=stepper_port,
            stepper_baud=stepper_baud,
            stepper_network_url=stepper_network_url,
            stepper_network_timeout=stepper_network_timeout,
            scenario=scenario,
            drop_after_s=drop_after_s,
            esp32_auto_sequence=False,
            dxmr90_host=dxmr90_host,
            dxmr90_port=dxmr90_port,
            dxmr90_unit_id=dxmr90_unit_id,
            dxmr90_timeout=dxmr90_timeout,
            dxmr90_addressing=dxmr90_addressing,
            dxmr90_word_order=dxmr90_word_order,
            dxmr90_data_path=dxmr90_data_path,
            dxmr90_rate_hz=dxmr90_rate_hz,
        )
        self.devices = core.DeviceControls(self.sources)
        self.merger = SourceMerger(self.sources, stale_after_s=stale_after_s)
        self.history: deque[dict[str, object]] = deque(maxlen=history_limit)
        self.metadata = dict(DEFAULT_METADATA)
        self.recording = False
        # Remember last known states through disconnects; Stop must stay stopped
        # until a new activation, not restart on every poll of an open valve.
        self._recording_solenoid_states = [False] * core.ESP32_SOLENOID_COUNT
        self.run_started_iso: str | None = None
        self.run_stopped_iso: str | None = None
        self.recorder: FlowRunRecorder | None = None
        self.latest_recording: dict[str, object] | None = None
        self.start_time = datetime.now(timezone.utc)
        self.monotonic0 = time.monotonic()
        self.latest: dict[str, object] | None = None
        # This display-only reference survives dashboard and transport restarts.
        # It is never sent to the motion controller.
        self.system_config = SystemConfig(system_config_path)
        self.sequence = 0
        self._dro_velocity_samples: deque[tuple[float, float, int]] = deque()
        self._dro_velocity_last_frame_count: int | None = None
        self._stop_event = threading.Event()
        self._condition = threading.Condition(threading.RLock())
        self._thread: threading.Thread | None = None
        self._solenoid_command_lock = threading.Lock()
        self._servo_command_lock = threading.Lock()
        with self._condition:
            self._poll_locked(0.0)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="dashboard-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self.recording:
            self.set_recording(False)
        for source in self.sources:
            if hasattr(source, "close"):
                source.close()  # type: ignore[attr-defined]

    def _run(self) -> None:
        next_emit = time.monotonic()
        while not self._stop_event.is_set():
            elapsed_s = time.monotonic() - self.monotonic0
            with self._condition:
                self._poll_locked(elapsed_s)
            next_emit += self.period_s
            delay = max(0.0, next_emit - time.monotonic())
            self._stop_event.wait(delay)

    def _poll_locked(self, elapsed_s: float) -> None:
        timestamp = self.start_time + timedelta(seconds=elapsed_s)
        self.latest = self.merger.poll(elapsed_s, timestamp)
        self._apply_stepper_dro_zero_locked(self.latest)
        self._apply_stepper_dro_velocity_locked(self.latest, elapsed_s)
        fresh_readings = self.merger.fresh_readings()
        self.history.append(self.latest)
        activated = False
        for index in range(core.ESP32_SOLENOID_COUNT):
            prefix = f"solenoid{index + 1}"
            state = self.latest.get(f"{prefix}_on")
            if self.latest.get(f"{prefix}_connected") is True and type(state) is bool:
                activated |= state and not self._recording_solenoid_states[index]
                self._recording_solenoid_states[index] = state
        if activated:
            # This poll writes the first row below, including fresh source data.
            self.set_recording(True, include_latest=False)
        if self.recording and self.recorder is not None:
            self.recorder.record_sample(self.latest, fresh_readings)
        self.sequence += 1
        self._condition.notify_all()

    def _apply_stepper_dro_zero_locked(
        self,
        sample: dict[str, object],
    ) -> None:
        """Add the persistent display reference without changing raw DRO data."""

        zero_raw_mm = self.system_config.stepper_dro_zero_raw_mm
        sample["servo_settings"] = self.system_config.snapshot()["servo"]
        raw_value = sample.get("stepper_dro_position_mm")
        try:
            raw_position_mm = float(raw_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raw_position_mm = math.nan
        has_raw_position = math.isfinite(raw_position_mm)

        sample["stepper_dro_zero_set"] = zero_raw_mm is not None
        sample["stepper_dro_zero_raw_mm"] = zero_raw_mm
        sample["stepper_dro_zeroed_position_mm"] = (
            round(raw_position_mm - zero_raw_mm, 2)
            if zero_raw_mm is not None and has_raw_position
            else None
        )

    def _apply_stepper_dro_velocity_locked(
        self,
        sample: dict[str, object],
        elapsed_s: float,
    ) -> None:
        """Derive display-only velocity from fresh raw DRO position samples."""

        sample["stepper_dro_velocity_mm_s"] = None
        sample["stepper_dro_velocity_window_ms"] = None

        raw_value = sample.get("stepper_dro_position_mm")
        frame_value = sample.get("stepper_dro_valid_frame_count")
        age_value = sample.get("stepper_dro_sample_age_ms")
        try:
            raw_position_mm = float(raw_value)  # type: ignore[arg-type]
            frame_count = int(frame_value)  # type: ignore[arg-type]
            sample_age_ms = float(age_value)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            self._reset_stepper_dro_velocity_locked()
            return

        valid_sample = (
            sample.get("stepper_connected") is True
            and sample.get("stepper_dro_capable") is True
            and sample.get("stepper_dro_fresh") is True
            and math.isfinite(raw_position_mm)
            and not isinstance(frame_value, bool)
            and frame_count >= 0
            and math.isfinite(sample_age_ms)
            and sample_age_ms >= 0.0
        )
        if not valid_sample:
            self._reset_stepper_dro_velocity_locked()
            return

        if (
            self._dro_velocity_last_frame_count is not None
            and frame_count < self._dro_velocity_last_frame_count
        ):
            self._reset_stepper_dro_velocity_locked()

        if frame_count != self._dro_velocity_last_frame_count:
            measurement_time_s = elapsed_s - sample_age_ms / 1000.0
            if (
                self._dro_velocity_samples
                and measurement_time_s <= self._dro_velocity_samples[-1][0]
            ):
                measurement_time_s = elapsed_s
            self._dro_velocity_samples.append(
                (measurement_time_s, raw_position_mm, frame_count)
            )
            self._dro_velocity_last_frame_count = frame_count

        cutoff_s = elapsed_s - DRO_VELOCITY_WINDOW_S
        while (
            len(self._dro_velocity_samples) > 2
            and self._dro_velocity_samples[1][0] < cutoff_s
        ):
            self._dro_velocity_samples.popleft()

        if len(self._dro_velocity_samples) < 2:
            return

        window_s = (
            self._dro_velocity_samples[-1][0]
            - self._dro_velocity_samples[0][0]
        )
        if window_s < DRO_VELOCITY_MIN_SPAN_S:
            return

        mean_time_s = sum(point[0] for point in self._dro_velocity_samples) / len(
            self._dro_velocity_samples
        )
        mean_position_mm = sum(
            point[1] for point in self._dro_velocity_samples
        ) / len(self._dro_velocity_samples)
        denominator = sum(
            (point[0] - mean_time_s) ** 2
            for point in self._dro_velocity_samples
        )
        if denominator <= 0.0:
            return
        velocity_mm_s = sum(
            (point[0] - mean_time_s) * (point[1] - mean_position_mm)
            for point in self._dro_velocity_samples
        ) / denominator
        if abs(velocity_mm_s) < DRO_VELOCITY_STOP_DEADBAND_MM_S:
            velocity_mm_s = 0.0

        sample["stepper_dro_velocity_mm_s"] = round(velocity_mm_s, 3)
        sample["stepper_dro_velocity_window_ms"] = round(window_s * 1000.0)

    def _reset_stepper_dro_velocity_locked(self) -> None:
        self._dro_velocity_samples.clear()
        self._dro_velocity_last_frame_count = None

    def run_config_locked(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "rate_hz": self.rate_hz,
            "drop_after_s": self.drop_after_s,
            "stale_after_s": self.stale_after_s,
            "record_dir": str(self.record_dir),
            "esp32_source": self.esp32_source,
            "esp32_base_url": self.esp32_base_url,
            "esp32_timeout": self.esp32_timeout,
            "dxmr90_source": self.dxmr90_source,
            "stepper_source": self.stepper_source,
            "stepper_port": self.stepper_port,
            "stepper_baud": self.stepper_baud,
            "dxmr90_host": self.dxmr90_host,
            "dxmr90_port": self.dxmr90_port,
            "dxmr90_unit_id": self.dxmr90_unit_id,
            "dxmr90_timeout": self.dxmr90_timeout,
            "dxmr90_addressing": self.dxmr90_addressing,
            "dxmr90_word_order": self.dxmr90_word_order,
            "dxmr90_data_path": self.dxmr90_data_path,
            "dxmr90_rate_hz": self.dxmr90_rate_hz,
            "system_config_path": (
                str(self.system_config.path)
                if self.system_config.path is not None
                else None
            ),
            "stepper_dro_zero_raw_mm": (
                self.system_config.stepper_dro_zero_raw_mm
            ),
            "powder_mass_per_stepper_travel_g_per_mm": (
                self.system_config.powder_mass_per_stepper_travel_g_per_mm
            ),
        }

    def run_state_locked(self) -> dict[str, object]:
        return {
            "recording": self.recording,
            "run_started_iso": self.run_started_iso,
            "run_stopped_iso": self.run_stopped_iso,
            "scenario": self.scenario,
            "rate_hz": self.rate_hz,
            "drop_after_s": self.drop_after_s,
            "stale_after_s": self.stale_after_s,
            "record_dir": str(self.record_dir),
            "esp32_source": self.esp32_source,
            "esp32_base_url": self.esp32_base_url,
            "dxmr90_source": self.dxmr90_source,
            "stepper_source": self.stepper_source,
            "stepper_port": self.stepper_port,
            "stepper_baud": self.stepper_baud,
            "dxmr90_host": self.dxmr90_host,
            "dxmr90_port": self.dxmr90_port,
            "dxmr90_data_path": self.dxmr90_data_path,
            "dxmr90_rate_hz": self.dxmr90_rate_hz,
            "active_recording": (
                self.recorder.status_payload() if self.recorder is not None else None
            ),
            "latest_recording": self.latest_recording,
        }

    def state(self) -> dict[str, object]:
        with self._condition:
            return {
                "sample": self.latest,
                "run": self.run_state_locked(),
                "metadata": dict(self.metadata),
                "history_size": len(self.history),
            }

    def dashboard_config(self) -> dict[str, object]:
        """Return read-only limits needed to build honest browser controls."""

        return {
            "history_limit": self.history_limit,
            "solenoid_count": core.ESP32_SOLENOID_COUNT,
            "stepper": {
                "max_distance_mm": min(
                    self.system_config.stepper_max_travel_mm,
                    core.DEFAULT_STEPPER_MAX_DISTANCE_MM,
                ),
                "max_travel_mm": self.system_config.stepper_max_travel_mm,
                "min_speed_mm_s": core.DEFAULT_STEPPER_MIN_SPEED_MM_S,
                "max_speed_mm_s": core.DEFAULT_STEPPER_MAX_SPEED_MM_S,
                "default_speed_mm_s": core.DEFAULT_STEPPER_HOME_SPEED_MM_S,
                "home_speed_mm_s": core.DEFAULT_STEPPER_HOME_SPEED_MM_S,
            },
            "geometry": {
                "powder_mass_per_stepper_travel_g_per_mm": (
                    self.system_config.powder_mass_per_stepper_travel_g_per_mm
                ),
            },
        }

    def latest_payload(self) -> dict[str, object]:
        with self._condition:
            return {"sample": self.latest, "run": self.run_state_locked()}

    def history_payload(self, limit: int) -> dict[str, object]:
        with self._condition:
            rows = list(self.history)[-max(1, limit) :]
            return {"history": rows, "run": self.run_state_locked()}

    def wait_for_sample(
        self,
        last_sequence: int,
        timeout_s: float = 15.0,
    ) -> tuple[int, dict[str, object] | None]:
        with self._condition:
            if self.sequence <= last_sequence and not self._stop_event.is_set():
                self._condition.wait(timeout=timeout_s)
            if self.sequence <= last_sequence:
                return self.sequence, None
            return self.sequence, self.latest

    def set_recording(self, recording: bool, *, include_latest: bool = True) -> dict[str, object]:
        with self._condition:
            if recording:
                if self.recording:
                    return self.run_state_locked()
                source_fields = {
                    source.name: source.expected_fields for source in self.sources
                }
                self.recorder = FlowRunRecorder(
                    record_dir=self.record_dir,
                    metadata=self.metadata,
                    run_config=self.run_config_locked(),
                    source_fields=source_fields,
                    first_sample=self.latest if include_latest else None,
                )
                self.recording = True
                self.run_started_iso = self.recorder.started_iso
                self.run_stopped_iso = None
                self.latest_recording = None
            else:
                if not self.recording:
                    return self.run_state_locked()
                recorder = self.recorder
                self.recording = False
                if recorder is not None:
                    self.latest_recording = recorder.finish(self.metadata)
                    stopped = self.latest_recording.get("stopped_iso")
                    self.run_stopped_iso = str(stopped) if stopped is not None else None
                else:
                    self.run_stopped_iso = datetime.now(timezone.utc).isoformat(
                        timespec="milliseconds"
                    )
                self.recorder = None
            self._condition.notify_all()
            return self.run_state_locked()

    def update_metadata(self, values: dict[str, object]) -> dict[str, str]:
        with self._condition:
            for key in DEFAULT_METADATA:
                if key in values:
                    value = values[key]
                    self.metadata[key] = "" if value is None else str(value)
            if self.recorder is not None:
                self.recorder.update_metadata(self.metadata)
            self._condition.notify_all()
            return dict(self.metadata)

    # Operator requests: application validation, locking, confirmation, persistence.
    # Hardware-specific choices live in DeviceControls, not in these HTTP-facing methods.
    def toggle_solenoid(self, index: int) -> dict[str, object]:
        # Serialize read/modify/write without holding the merge lock during I/O.
        with self._solenoid_command_lock:
            with self._condition:
                sample = dict(self.latest or {})
                current = self.devices.status(self.latest)
            receipt = self.devices.toggle_solenoid(index, sample, current)
            with self._condition:
                self._finish_command_locked(receipt)
                if receipt.matches is not None:
                    self._poll_locked(time.monotonic() - self.monotonic0)
                return {
                    **receipt.result,
                    "solenoids": [self.latest.get(f"solenoid{i + 1}_on")
                                  for i in range(core.ESP32_SOLENOID_COUNT)],
                    "sample": self.latest,
                }

    def stepper_status(self) -> dict[str, object]:
        with self._condition:
            return {"stepper": self.devices.status(self.latest), "sample": self.latest}

    def set_stepper_dro_zero(self) -> dict[str, object]:
        """Snapshot a fresh stopped DRO reading as a display-only zero."""

        with self._condition:
            latest = self.latest
            if latest is None:
                raise RuntimeError("no dashboard sample is available")
            if latest.get("stepper_moving") is True:
                raise RuntimeError("stop motion before setting the DRO zero")
            if (
                latest.get("stepper_connected") is not True
                or latest.get("stepper_dro_capable") is not True
                or latest.get("stepper_dro_fresh") is not True
            ):
                raise RuntimeError("a fresh connected DRO sample is required")
            try:
                raw_position_mm = float(latest.get("stepper_dro_position_mm"))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("a finite DRO position is required") from exc
            if not math.isfinite(raw_position_mm):
                raise RuntimeError("a finite DRO position is required")

            persisted_zero = self.system_config.set_stepper_dro_zero_raw_mm(
                raw_position_mm
            )
            self._apply_stepper_dro_zero_locked(latest)
            self.sequence += 1
            self._condition.notify_all()
            return {
                "zero": {
                    "set": True,
                    "raw_position_mm": persisted_zero,
                    "scope": "system_config",
                    "motion_commanded": False,
                },
                "sample": latest,
            }


    def _confirm_command_locked(self, receipt: core.CommandReceipt) -> dict[str, object]:
        """Wait for device-defined evidence, releasing the lock between checks.

        Polling and E-STOP must remain able to proceed during this wait. The
        existing 1.5 s confirmation window is unchanged; sending is not success.
        """
        deadline = time.monotonic() + 1.5
        while True:
            payload = self.devices.status(self.latest)
            if receipt.confirmed(payload):
                return payload
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"motion controller did not confirm {receipt.description} within 1.5 seconds"
                )
            self._condition.wait(timeout=min(remaining, 0.1))

    def _finish_command_locked(self, receipt: core.CommandReceipt, *, poll: bool = True) -> dict[str, object]:
        if poll:
            self._poll_locked(time.monotonic() - self.monotonic0)
        payload = self._confirm_command_locked(receipt)
        return {**receipt.result, "stepper": payload, "sample": self.latest}

    def _stepper_command(self, command: Callable, *args: object, poll: bool = True) -> dict[str, object]:
        """Shared orchestration for commands whose writes use the runtime lock."""
        with self._condition:
            receipt = command(self.devices.status(self.latest), *args)
            return self._finish_command_locked(receipt, poll=poll)

    def move_stepper(self, values: dict[str, object]) -> dict[str, object]:
        with self._condition:
            self.devices.require_stepper("move", error="stepper source does not support motion controls")
            if "distance_mm" not in values:
                raise ValueError("distance_mm is required")
            if "speed_mm_s" not in values:
                raise ValueError("speed_mm_s is required")
            raw_distance = values["distance_mm"]
            if isinstance(raw_distance, bool):
                raise ValueError("distance_mm must be a positive finite travel magnitude")
            try:
                travel_mm = float(raw_distance)
            except (TypeError, ValueError) as exc:
                raise ValueError("distance_mm must be a positive finite travel magnitude") from exc
            if not math.isfinite(travel_mm) or travel_mm <= 0:
                raise ValueError("distance_mm must be a positive finite travel magnitude")
            if travel_mm > self.system_config.stepper_max_travel_mm:
                raise ValueError("distance_mm exceeds configured max_travel_mm")
            receipt = self.devices.move(self.devices.status(self.latest), {**values, "distance_mm": travel_mm})
            return self._finish_command_locked(receipt)

    def stop_stepper(self) -> dict[str, object]:
        return self._stepper_command(self.devices.stop)

    def emergency_stop_stepper(self) -> dict[str, object]:
        """Send BEFORE taking the runtime lock, so a slow poll cannot delay E-STOP."""
        receipt = self.devices.emergency_stop(self.devices.status())
        with self._condition:
            # Real adapters confirm from their independent status readers; simulation polls.
            return self._finish_command_locked(receipt, poll=receipt.matches is None)

    def reset_stepper_emergency_stop(self) -> dict[str, object]:
        return self._stepper_command(self.devices.reset_emergency_stop)

    def set_stepper_control_mode(self, values: dict[str, object]) -> dict[str, object]:
        return self._stepper_command(self.devices.control_mode, values)

    def set_stepper_local_run(self, values: dict[str, object]) -> dict[str, object]:
        return self._stepper_command(self.devices.local_run, values, poll=False)

    def home_stepper(self) -> dict[str, object]:
        return self._stepper_command(self.devices.home)

    def set_stepper_speed(self, values: dict[str, object]) -> dict[str, object]:
        return self._stepper_command(self.devices.speed, values, poll=False)

    def toggle_stepper_brushless_motor(self) -> dict[str, object]:
        result = self._stepper_command(self.devices.toggle_brushless)
        result["pulse_us"] = result["stepper"].get("stepper_brushless_motor_pulse_us")
        return result

    def set_stepper_brushless_pulse(self, values: dict[str, object]) -> dict[str, object]:
        result = self._stepper_command(self.devices.brushless_pulse, values)
        result["pulse_us"] = result["stepper"].get("stepper_brushless_motor_pulse_us")
        result["setpoint_us"] = result["stepper"].get("stepper_brushless_motor_setpoint_us")
        return result

    def set_stepper_servo(self, values: dict[str, object]) -> dict[str, object]:
        with self._servo_command_lock:
            with self._condition:
                current = self.devices.status(self.latest)
                settings = self.system_config.snapshot()["servo"]
            # Sending may block; keep other requests and sampling free to proceed.
            settings, receipt = self.devices.servo(current, values, settings)
            with self._condition:
                result = (self._finish_command_locked(receipt) if receipt is not None
                          else {"confirmed": True, "sample": self.latest})
                if values.get("action") is not None:
                    self.system_config.set_servo(settings)
                    self._apply_stepper_dro_zero_locked(self.latest)
                return result

    def recordings_payload(self) -> dict[str, object]:
        with self._condition:
            recordings = list_recordings(self.record_dir)
            latest = self.latest_recording
            if latest is None and recordings:
                latest = recordings[0]
            return {
                "record_dir": str(self.record_dir),
                "active": (
                    self.recorder.status_payload() if self.recorder is not None else None
                ),
                "latest": latest,
                "recordings": recordings,
            }

    def artifact_path(self, run_id: str, filename: str) -> Path:
        return resolve_artifact(self.record_dir, run_id, filename)

    def latest_export_path(self) -> Path:
        with self._condition:
            latest = self.latest_recording
            if latest is None:
                recordings = list_recordings(self.record_dir)
                latest = recordings[0] if recordings else None
            if not latest:
                raise FileNotFoundError("no completed recording is available")
            run_id = latest.get("run_id")
            if not isinstance(run_id, str):
                raise FileNotFoundError("latest recording has no run_id")
        return self.artifact_path(run_id, "export.csv")
