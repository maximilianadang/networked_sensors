"""Realtime dashboard state and operator command boundary."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from ..recorder import FlowRunRecorder, list_recordings, resolve_artifact
    from ..supervisor_core import (
        DEFAULT_ESP32_BASE_URL,
        DEFAULT_ESP32_TIMEOUT_S,
        DEFAULT_STEPPER_HOME_SPEED_MM_S,
        DEFAULT_STEPPER_MAX_DISTANCE_MM,
        DEFAULT_STEPPER_MAX_SPEED_MM_S,
        DEFAULT_STEPPER_MIN_SPEED_MM_S,
        DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
        DEFAULT_STEPPER_NETWORK_URL,
        DEFAULT_STEPPER_STEPS_PER_MM,
        ESP32_SOLENOID_COUNT,
        MAX_BRUSHLESS_PULSE_US,
        MIN_BRUSHLESS_PULSE_US,
        SourceMerger,
        make_sources,
    )
except ImportError:  # pragma: no cover - direct dashboard.py execution
    from recorder import FlowRunRecorder, list_recordings, resolve_artifact
    from supervisor_core import (
        DEFAULT_ESP32_BASE_URL,
        DEFAULT_ESP32_TIMEOUT_S,
        DEFAULT_STEPPER_HOME_SPEED_MM_S,
        DEFAULT_STEPPER_MAX_DISTANCE_MM,
        DEFAULT_STEPPER_MAX_SPEED_MM_S,
        DEFAULT_STEPPER_MIN_SPEED_MM_S,
        DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
        DEFAULT_STEPPER_NETWORK_URL,
        DEFAULT_STEPPER_STEPS_PER_MM,
        ESP32_SOLENOID_COUNT,
        MAX_BRUSHLESS_PULSE_US,
        MIN_BRUSHLESS_PULSE_US,
        SourceMerger,
        make_sources,
    )

from .config import DEFAULT_METADATA
from .system_config import SystemConfig


DRO_VELOCITY_WINDOW_S = 0.65
DRO_VELOCITY_MIN_SPAN_S = 0.15
DRO_VELOCITY_STOP_DEADBAND_MM_S = 0.04


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
        esp32_base_url: str = DEFAULT_ESP32_BASE_URL,
        esp32_timeout: float = DEFAULT_ESP32_TIMEOUT_S,
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
        stepper_network_url: str = DEFAULT_STEPPER_NETWORK_URL,
        stepper_network_timeout: float = DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
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
        self.sources = make_sources(
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
        self.merger = SourceMerger(self.sources, stale_after_s=stale_after_s)
        self.history: deque[dict[str, object]] = deque(maxlen=history_limit)
        self.metadata = dict(DEFAULT_METADATA)
        self.recording = False
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
            "solenoid_count": ESP32_SOLENOID_COUNT,
            "stepper": {
                "max_distance_mm": DEFAULT_STEPPER_MAX_DISTANCE_MM,
                "min_speed_mm_s": DEFAULT_STEPPER_MIN_SPEED_MM_S,
                "max_speed_mm_s": DEFAULT_STEPPER_MAX_SPEED_MM_S,
                "default_speed_mm_s": DEFAULT_STEPPER_HOME_SPEED_MM_S,
                "home_speed_mm_s": DEFAULT_STEPPER_HOME_SPEED_MM_S,
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

    def set_recording(self, recording: bool) -> dict[str, object]:
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
                    first_sample=self.latest,
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

    def toggle_solenoid(self, index: int) -> dict[str, object]:
        with self._condition:
            esp32 = next(
                (source for source in self.sources if source.name == "esp32"),
                None,
            )
            if esp32 is None or not hasattr(esp32, "toggle_solenoid"):
                raise RuntimeError("ESP32 source does not support solenoid controls")
            if (
                esp32.mode == "real"
                and (
                    self.latest is None
                    or self.latest.get("esp32_connected") is not True
                )
            ):
                raise RuntimeError("ESP32 control stream is not live")

        # Network I/O must not hold the condition used by the 10 Hz merge/SSE
        # loop. The ESP32's immediate sol event can now reach the browser while
        # this request is still completing.
        with self._solenoid_command_lock:
            state = esp32.toggle_solenoid(index)  # type: ignore[attr-defined]

        with self._condition:
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            states = (
                list(esp32.solenoid_states())  # type: ignore[attr-defined]
                if hasattr(esp32, "solenoid_states")
                else None
            )
            return {
                "index": index,
                "state": state,
                "solenoids": states,
                "sample": self.latest,
            }

    def _stepper_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None or not hasattr(stepper, "move"):
            raise RuntimeError("stepper source does not support motion controls")
        return stepper

    def _stepper_speed_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None or not hasattr(stepper, "set_speed"):
            raise RuntimeError("stepper source does not support manual speed tuning")
        return stepper

    def _stepper_mode_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None or not hasattr(stepper, "set_control_mode"):
            raise RuntimeError("stepper source does not support control modes")
        return stepper

    def _stepper_home_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None or not hasattr(stepper, "home"):
            raise RuntimeError("stepper source does not support Home")
        return stepper

    def _stepper_estop_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if (
            stepper is None
            or not hasattr(stepper, "emergency_stop")
            or not hasattr(stepper, "reset_emergency_stop")
        ):
            raise RuntimeError("stepper source does not support software E-STOP")
        return stepper

    def _stepper_brushless_locked(self) -> object:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None or not hasattr(stepper, "set_brushless_motor"):
            raise RuntimeError(
                "stepper source does not support brushless motor control"
            )
        return stepper

    def _stepper_brushless_pulse_locked(self) -> object:
        stepper = self._stepper_brushless_locked()
        if not hasattr(stepper, "set_brushless_pulse_us"):
            raise RuntimeError(
                "stepper source does not support brushless pulse-width control"
            )
        return stepper

    def _stepper_payload_locked(self) -> dict[str, object]:
        stepper = next(
            (source for source in self.sources if source.name == "stepper"),
            None,
        )
        if stepper is None:
            raise RuntimeError("stepper source is unavailable")
        payload: dict[str, object] = {
            "stepper_mode": stepper.mode,
            "stepper_connected": False,
            "stepper_age_ms": None,
        }
        if self.latest is not None:
            for key, value in self.latest.items():
                if key.startswith("stepper_"):
                    payload[key] = value
        if hasattr(stepper, "status"):
            payload.update(stepper.status())  # type: ignore[attr-defined]
        return payload

    def stepper_status(self) -> dict[str, object]:
        with self._condition:
            return {
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

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

    def move_stepper(self, values: dict[str, object]) -> dict[str, object]:
        with self._condition:
            stepper = self._stepper_locked()
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
                raise ValueError(
                    "distance_mm must be a positive finite travel magnitude"
                ) from exc
            if not math.isfinite(travel_mm) or travel_mm <= 0:
                raise ValueError("distance_mm must be a positive finite travel magnitude")

            # Snapshot the physical D5 selection and turn the operator's
            # positive magnitude into the signed internal/wire command. The
            # USB adapter and firmware both re-check D5, so a selector change
            # during this handoff rejects or aborts instead of reversing.
            current = self._stepper_payload_locked()
            selected_direction = current.get("stepper_authorized_direction")
            if selected_direction == "both":
                # Simulation has no physical D5 input; use Forward by default.
                selected_direction = "forward"
            if selected_direction not in ("forward", "reverse"):
                raise RuntimeError("D5 direction is unavailable")
            signed_distance_mm = (
                -travel_mm if selected_direction == "reverse" else travel_mm
            )
            command_id = values.get("command_id")
            before_sequence = current.get("stepper_status_sequence")
            stepper.move(  # type: ignore[attr-defined]
                signed_distance_mm,
                values["speed_mm_s"],
                command_id,
            )
            expected_id = getattr(stepper, "pending_command_id", None)
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    command_error = getattr(
                        stepper,
                        "pending_command_error",
                        None,
                    )
                    if command_error:
                        raise RuntimeError(
                            f"Yún rejected the move: {command_error}"
                        )
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_command_id") == expected_id
                        and payload.get("stepper_state")
                        in ("moving", "completed", "limit_blocked")
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm the move within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            return {
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
                "travel_mm": travel_mm,
                "resolved_direction": selected_direction,
                "signed_distance_mm": signed_distance_mm,
            }

    def stop_stepper(self) -> dict[str, object]:
        with self._condition:
            stepper = self._stepper_locked()
            before_sequence = self._stepper_payload_locked().get(
                "stepper_status_sequence"
            )
            stepper.stop()  # type: ignore[attr-defined]
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_moving") is False
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm Stop within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            return {
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def emergency_stop_stepper(self) -> dict[str, object]:
        """Dispatch E-STOP before any blocking source poll, then confirm it.

        This command deliberately bypasses the runtime condition for its first
        write. A slow or unreachable DXMR90 poll may hold that shared lock, but
        it must not delay delivery of the short E-STOP command to the Yún.
        Status acknowledgement still uses the normal merged-data condition.
        """

        stepper = self._stepper_estop_locked()
        status = (
            stepper.status()  # type: ignore[attr-defined]
            if hasattr(stepper, "status")
            else {}
        )
        before_sequence = status.get("stepper_status_sequence")
        stepper.emergency_stop()  # type: ignore[attr-defined]

        with self._condition:
            elapsed_s = time.monotonic() - self.monotonic0
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_estop_latched") is True
                        and payload.get("stepper_moving") is False
                        and (
                            payload.get("stepper_brushless_motor_capable") is not True
                            or payload.get("stepper_brushless_motor_on") is False
                        )
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm software E-STOP within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            else:
                self._poll_locked(elapsed_s)
            return {
                "confirmed": True,
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def toggle_stepper_brushless_motor(self) -> dict[str, object]:
        """Toggle the D12 ESC between OFF and its configured ON pulse."""

        with self._condition:
            stepper = self._stepper_brushless_locked()
            current = self._stepper_payload_locked()
            if current.get("stepper_brushless_motor_capable") is not True:
                raise RuntimeError(
                    "Yún firmware does not support brushless motor control"
                )
            requested_on = current.get("stepper_brushless_motor_on") is not True
            if requested_on and current.get("stepper_estop_latched") is True:
                raise RuntimeError(
                    "reset the software E-STOP before starting the brushless motor"
                )
            before_sequence = current.get("stepper_status_sequence")
            stepper.set_brushless_motor(requested_on)  # type: ignore[attr-defined]
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_brushless_motor_on")
                        == requested_on
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm brushless motor state "
                            "within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            confirmed = self._stepper_payload_locked()
            return {
                "confirmed": True,
                "on": requested_on,
                "pulse_us": confirmed.get(
                    "stepper_brushless_motor_pulse_us"
                ),
                "stepper": confirmed,
                "sample": self.latest,
            }

    def set_stepper_brushless_pulse(
        self,
        values: dict[str, object],
    ) -> dict[str, object]:
        """Set and confirm the configured 1000..2000 us brushless ON pulse."""

        with self._condition:
            if "pulse_us" not in values:
                raise ValueError("pulse_us is required")
            raw_pulse = values["pulse_us"]
            if isinstance(raw_pulse, bool):
                raise ValueError(
                    "pulse_us must be an integer from 1000 through 2000"
                )
            try:
                requested_pulse = int(raw_pulse)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "pulse_us must be an integer from 1000 through 2000"
                ) from exc
            if (
                requested_pulse != raw_pulse
                or not MIN_BRUSHLESS_PULSE_US
                <= requested_pulse
                <= MAX_BRUSHLESS_PULSE_US
            ):
                raise ValueError(
                    "pulse_us must be an integer from 1000 through 2000"
                )
            stepper = self._stepper_brushless_pulse_locked()
            current = self._stepper_payload_locked()
            if (
                current.get("stepper_brushless_motor_variable_capable")
                is not True
            ):
                raise RuntimeError(
                    "Yún firmware does not support brushless pulse-width control"
                )
            before_sequence = current.get("stepper_status_sequence")
            stepper.set_brushless_pulse_us(  # type: ignore[attr-defined]
                requested_pulse
            )
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_brushless_motor_setpoint_us")
                        == requested_pulse
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm brushless pulse width "
                            "within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            confirmed = self._stepper_payload_locked()
            return {
                "confirmed": True,
                "pulse_us": confirmed.get(
                    "stepper_brushless_motor_pulse_us"
                ),
                "setpoint_us": confirmed.get(
                    "stepper_brushless_motor_setpoint_us"
                ),
                "stepper": confirmed,
                "sample": self.latest,
            }

    def reset_stepper_emergency_stop(self) -> dict[str, object]:
        """Reset the software latch and require fresh device confirmation."""

        with self._condition:
            stepper = self._stepper_estop_locked()
            before_sequence = self._stepper_payload_locked().get(
                "stepper_status_sequence"
            )
            stepper.reset_emergency_stop()  # type: ignore[attr-defined]
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_estop_latched") is False
                        and payload.get("stepper_moving") is False
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm E-STOP reset within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            return {
                "confirmed": True,
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def set_stepper_control_mode(
        self,
        values: dict[str, object],
    ) -> dict[str, object]:
        with self._condition:
            stepper = self._stepper_mode_locked()
            if "web_position" not in values:
                raise ValueError("web_position is required")
            requested = values["web_position"]
            if not isinstance(requested, bool):
                raise ValueError("web_position must be true or false")
            expected_mode = "web_position" if requested else "local_velocity"
            before_sequence = self._stepper_payload_locked().get(
                "stepper_status_sequence"
            )
            stepper.set_control_mode(requested)  # type: ignore[attr-defined]
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_control_mode") == expected_mode
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm the control mode within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            return {
                "confirmed": True,
                "requested_control_mode": expected_mode,
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def home_stepper(self) -> dict[str, object]:
        with self._condition:
            stepper = self._stepper_home_locked()
            before_sequence = self._stepper_payload_locked().get(
                "stepper_status_sequence"
            )
            stepper.home()  # type: ignore[attr-defined]
            elapsed_s = time.monotonic() - self.monotonic0
            self._poll_locked(elapsed_s)
            if getattr(stepper, "mode", None) in ("usb", "network"):
                deadline = time.monotonic() + 1.5
                while True:
                    payload = self._stepper_payload_locked()
                    if (
                        payload.get("stepper_status_sequence") != before_sequence
                        and payload.get("stepper_state") in ("homing", "ready")
                    ):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError(
                            "Yún did not confirm Home within 1.5 seconds"
                        )
                    self._condition.wait(timeout=min(remaining, 0.1))
            return {
                "confirmed": True,
                "stepper": self._stepper_payload_locked(),
                "sample": self.latest,
            }

    def set_stepper_speed(self, values: dict[str, object]) -> dict[str, object]:
        with self._condition:
            stepper = self._stepper_speed_locked()
            if "speed_mm_s" not in values:
                raise ValueError("speed_mm_s is required")
            requested_speed = values["speed_mm_s"]
            if isinstance(requested_speed, bool):
                raise ValueError("speed_mm_s must be a finite number")
            try:
                requested_speed_value = float(requested_speed)
                expected_speed = (
                    round(requested_speed_value * DEFAULT_STEPPER_STEPS_PER_MM)
                    / DEFAULT_STEPPER_STEPS_PER_MM
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("speed_mm_s must be a finite number") from exc
            before_sequence = self._stepper_payload_locked().get(
                "stepper_status_sequence"
            )
            stepper.set_speed(values["speed_mm_s"])  # type: ignore[attr-defined]

            # A successful serial write only proves that bytes entered the USB
            # driver. Do not tell the browser the change succeeded until a new
            # firmware status frame echoes the requested configured speed.
            deadline = time.monotonic() + 1.5
            while True:
                payload = self._stepper_payload_locked()
                echoed_speed = payload.get("stepper_command_speed_mm_s")
                echoed_sequence = payload.get("stepper_status_sequence")
                if (
                    isinstance(echoed_speed, (int, float))
                    and not isinstance(echoed_speed, bool)
                    and abs(float(echoed_speed) - expected_speed) < 0.0001
                    and echoed_sequence != before_sequence
                ):
                    return {
                        "confirmed": True,
                        "requested_speed_mm_s": expected_speed,
                        "stepper": payload,
                        "sample": self.latest,
                    }
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "Yún did not confirm the requested speed within 1.5 seconds"
                    )
                # Condition.wait releases the runtime lock, allowing the 10 Hz
                # source thread to ingest the firmware acknowledgement.
                self._condition.wait(timeout=min(remaining, 0.1))

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
