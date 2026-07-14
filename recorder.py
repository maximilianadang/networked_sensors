#!/usr/bin/env python3
"""Disk-backed recording/export helpers for the flow-management dashboard."""

from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Mapping, Sequence, TextIO

try:
    from .supervisor_core import SourceReading
except ImportError:  # pragma: no cover - direct script execution fallback
    from supervisor_core import SourceReading


ROOT = Path(__file__).resolve().parent
DEFAULT_RECORD_DIR = ROOT / "recordings"
FLOW_ACTIVE_THRESHOLD_G_MIN = 10.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def slugify(value: object, default: str = "run") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("_")
    return text[:48] or default


def json_default(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )


def csv_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def comment_line(key: str, value: object) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="")
    writer.writerow([f"# {key}:", csv_cell(value)])
    return buffer.getvalue()


@dataclass(frozen=True)
class RecordingPaths:
    run_id: str
    directory: Path
    merged_csv: Path
    export_csv: Path
    metadata_json: Path
    summary_json: Path
    source_csvs: dict[str, Path]

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "directory": str(self.directory),
            "merged_csv": str(self.merged_csv),
            "export_csv": str(self.export_csv),
            "metadata_json": str(self.metadata_json),
            "summary_json": str(self.summary_json),
            "source_csvs": {key: str(path) for key, path in self.source_csvs.items()},
        }


class FlowRunRecorder:
    """Write one flow-management run to a stable disk artifact set."""

    def __init__(
        self,
        *,
        record_dir: Path,
        metadata: Mapping[str, str],
        run_config: Mapping[str, object],
        source_fields: Mapping[str, Sequence[str]],
        first_sample: Mapping[str, object] | None = None,
    ) -> None:
        self.record_dir = Path(record_dir)
        self.record_dir.mkdir(parents=True, exist_ok=True)
        self.started_iso = utc_now_iso()
        self.stopped_iso: str | None = None
        self.metadata = dict(metadata)
        self.run_config = dict(run_config)
        self.source_fields = {name: tuple(fields) for name, fields in source_fields.items()}
        self.run_id = self._allocate_run_id(self.metadata)
        self.directory = self.record_dir / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        source_csvs = {
            name: self.directory / f"{name}_raw.csv" for name in sorted(self.source_fields)
        }
        self.paths = RecordingPaths(
            run_id=self.run_id,
            directory=self.directory,
            merged_csv=self.directory / "merged_samples.csv",
            export_csv=self.directory / "export.csv",
            metadata_json=self.directory / "metadata.json",
            summary_json=self.directory / "summary.json",
            source_csvs=source_csvs,
        )
        self._merged_file: TextIO | None = None
        self._merged_writer: csv.DictWriter[str] | None = None
        self._merged_fieldnames: list[str] | None = None
        self._source_files: dict[str, TextIO] = {}
        self._source_writers: dict[str, csv.DictWriter[str]] = {}
        self._row_count = 0
        self._source_row_counts = {name: 0 for name in self.source_fields}
        self._run_elapsed0: float | None = None
        self._last_run_elapsed_s: float | None = None
        self._pressure_sums = [0.0, 0.0, 0.0]
        self._pressure_counts = [0, 0, 0]
        self._pressure_combined_sum = 0.0
        self._pressure_combined_count = 0
        self._flow_trackers = [ActiveFlowTracker() for _ in range(3)]
        self._flow_combined_tracker = ActiveFlowTracker()
        self._open_source_writers()
        self.write_metadata()
        if first_sample is not None:
            self.record_sample(first_sample, ())

    def _allocate_run_id(self, metadata: Mapping[str, str]) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sample = slugify(metadata.get("sample_number"), "sample")
        sub = slugify(metadata.get("sub_number"), "run")
        base = f"{timestamp}_{sample}_{sub}"
        candidate = base
        suffix = 2
        while (self.record_dir / candidate).exists():
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _open_source_writers(self) -> None:
        for source, fields in self.source_fields.items():
            path = self.paths.source_csvs[source]
            handle = path.open("w", newline="", encoding="utf-8")
            fieldnames = [
                "timestamp_iso",
                "supervisor_elapsed_s",
                "source_elapsed_s",
                "source",
                "mode",
                *fields,
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            self._source_files[source] = handle
            self._source_writers[source] = writer

    def _ensure_merged_writer(self, sample: Mapping[str, object]) -> None:
        if self._merged_writer is not None:
            return
        leading = ["timestamp_iso", "elapsed_s", "run_elapsed_s"]
        rest = [key for key in sample if key not in {"timestamp_iso", "elapsed_s"}]
        self._merged_fieldnames = [*leading, *rest]
        self._merged_file = self.paths.merged_csv.open("w", newline="", encoding="utf-8")
        self._merged_writer = csv.DictWriter(
            self._merged_file,
            fieldnames=self._merged_fieldnames,
            extrasaction="ignore",
        )
        self._merged_writer.writeheader()

    def record_sample(
        self,
        sample: Mapping[str, object],
        fresh_readings: Sequence[SourceReading],
    ) -> None:
        sample_elapsed = float(sample.get("elapsed_s") or 0.0)
        if self._run_elapsed0 is None:
            self._run_elapsed0 = sample_elapsed
        run_elapsed_s = max(0.0, sample_elapsed - self._run_elapsed0)
        self._last_run_elapsed_s = round(run_elapsed_s, 3)

        self._ensure_merged_writer(sample)
        assert self._merged_writer is not None
        row = dict(sample)
        row["run_elapsed_s"] = self._last_run_elapsed_s
        self._merged_writer.writerow(row)
        self._row_count += 1
        self._update_summary(row)

        timestamp_iso = csv_cell(sample.get("timestamp_iso"))
        for reading in fresh_readings:
            writer = self._source_writers.get(reading.source)
            if writer is None:
                continue
            source_row: dict[str, object] = {
                "timestamp_iso": timestamp_iso,
                "supervisor_elapsed_s": sample_elapsed,
                "source_elapsed_s": reading.elapsed_s,
                "source": reading.source,
                "mode": reading.mode,
            }
            source_row.update(reading.values)
            writer.writerow(source_row)
            self._source_row_counts[reading.source] = (
                self._source_row_counts.get(reading.source, 0) + 1
            )

    def _update_summary(self, row: Mapping[str, object]) -> None:
        run_elapsed = float(row.get("run_elapsed_s") or 0.0)
        pressures = [
            as_float(row.get("esp32_p1_bar")),
            as_float(row.get("esp32_p2_bar")),
            as_float(row.get("esp32_p3_bar")),
        ]
        combined_pressure = as_float(row.get("esp32_p_combined_bar"))
        flows = [
            as_float(row.get("esp32_f1_gmin")),
            as_float(row.get("esp32_f2_gmin")),
            as_float(row.get("esp32_f3_gmin")),
        ]
        combined_flow = as_float(row.get("esp32_f_combined_gmin"))

        if run_elapsed <= 1.0:
            for index, pressure in enumerate(pressures):
                if pressure is None:
                    continue
                self._pressure_sums[index] += pressure
                self._pressure_counts[index] += 1
            if combined_pressure is not None:
                self._pressure_combined_sum += combined_pressure
                self._pressure_combined_count += 1

        for index, flow in enumerate(flows):
            self._flow_trackers[index].observe(flow)
        self._flow_combined_tracker.observe(combined_flow)

    def update_metadata(self, metadata: Mapping[str, str]) -> None:
        self.metadata = dict(metadata)
        self.write_metadata()

    def write_metadata(self) -> None:
        write_json(
            self.paths.metadata_json,
            {
                "schema_version": "1.0",
                "run_id": self.run_id,
                "started_iso": self.started_iso,
                "stopped_iso": self.stopped_iso,
                "metadata": self.metadata,
                "run_config": self.run_config,
                "paths": self.paths.as_dict(),
            },
        )

    def finish(self, metadata: Mapping[str, str] | None = None) -> dict[str, object]:
        if metadata is not None:
            self.metadata = dict(metadata)
        self.stopped_iso = utc_now_iso()
        self._flush_and_close()
        summary = self.summary()
        write_json(self.paths.summary_json, summary)
        self.write_metadata()
        self._write_export_csv(summary)
        return summary

    def summary(self) -> dict[str, object]:
        duration_s = None
        if self._row_count > 0:
            duration_s = self._last_run_elapsed_s
        metrics = {
            "pressure_bar_first1s": [
                mean_or_none(total, count)
                for total, count in zip(self._pressure_sums, self._pressure_counts)
            ],
            "pressure_combined_bar_first1s": mean_or_none(
                self._pressure_combined_sum,
                self._pressure_combined_count,
            ),
            "air_flow_g_per_min_active": [
                tracker.mean() for tracker in self._flow_trackers
            ],
            "air_flow_combined_g_per_min_active": self._flow_combined_tracker.mean(),
        }
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "started_iso": self.started_iso,
            "stopped_iso": self.stopped_iso,
            "duration_s": duration_s,
            "merged_rows": self._row_count,
            "source_rows": dict(self._source_row_counts),
            "metadata": dict(self.metadata),
            "run_config": dict(self.run_config),
            "metrics": metrics,
            "paths": self.paths.as_dict(),
        }

    def _flush_and_close(self) -> None:
        if self._merged_file is not None and not self._merged_file.closed:
            self._merged_file.flush()
            self._merged_file.close()
        for handle in self._source_files.values():
            if not handle.closed:
                handle.flush()
                handle.close()

    def _write_export_csv(self, summary: Mapping[str, object]) -> None:
        metrics = summary.get("metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        pressure = metrics.get("pressure_bar_first1s", [None, None, None])
        flow = metrics.get("air_flow_g_per_min_active", [None, None, None])
        if not isinstance(pressure, list):
            pressure = [None, None, None]
        if not isinstance(flow, list):
            flow = [None, None, None]

        header_lines = [
            comment_line("schema_version", "1.0"),
            comment_line("run_id", self.run_id),
            comment_line("date", self.started_iso),
            comment_line("sample_number", self.metadata.get("sample_number", "")),
            comment_line("sub_number", self.metadata.get("sub_number", "")),
            comment_line("dispenser", self.metadata.get("dispenser", "")),
            comment_line("material", self.metadata.get("material", "")),
            "#",
            comment_line("pressure_1_bar", format_float(item_at(pressure, 0), 3)),
            comment_line("pressure_2_bar", format_float(item_at(pressure, 1), 3)),
            comment_line("pressure_3_bar", format_float(item_at(pressure, 2), 3)),
            comment_line(
                "pressure_combined_bar",
                format_float(metrics.get("pressure_combined_bar_first1s"), 3),
            ),
            comment_line("air_flow_1_g_per_min", format_float(item_at(flow, 0), 2)),
            comment_line("air_flow_2_g_per_min", format_float(item_at(flow, 1), 2)),
            comment_line("air_flow_3_g_per_min", format_float(item_at(flow, 2), 2)),
            comment_line(
                "air_flow_combined_g_per_min",
                format_float(metrics.get("air_flow_combined_g_per_min_active"), 2),
            ),
            comment_line(
                "powder_flow_rate_g_per_min",
                self.metadata.get("powder_flow_rate_g_per_min", ""),
            ),
            comment_line("description", self.metadata.get("description", "")),
            comment_line("notes", self.metadata.get("notes", "")),
            "# ===",
        ]
        with self.paths.export_csv.open("w", encoding="utf-8", newline="") as export:
            export.write("\n".join(header_lines) + "\n")
            if self.paths.merged_csv.exists():
                with self.paths.merged_csv.open("r", encoding="utf-8") as merged:
                    shutil.copyfileobj(merged, export)

    def artifact_payload(self) -> dict[str, object]:
        payload = self.summary()
        payload["active"] = self.stopped_iso is None
        return payload

    def status_payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "started_iso": self.started_iso,
            "stopped_iso": self.stopped_iso,
            "merged_rows": self._row_count,
            "source_rows": dict(self._source_row_counts),
            "paths": self.paths.as_dict(),
            "active": self.stopped_iso is None,
        }


class ActiveFlowTracker:
    def __init__(self) -> None:
        self.in_active = False
        self.done = False
        self.total = 0.0
        self.count = 0

    def observe(self, value: float | None) -> None:
        if self.done or value is None:
            return
        if not self.in_active and value > FLOW_ACTIVE_THRESHOLD_G_MIN:
            self.in_active = True
        if not self.in_active:
            return
        if value >= FLOW_ACTIVE_THRESHOLD_G_MIN:
            self.total += value
            self.count += 1
            return
        self.in_active = False
        self.done = True

    def mean(self) -> float | None:
        return mean_or_none(self.total, self.count)


def as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def mean_or_none(total: float, count: int) -> float | None:
    if count <= 0:
        return None
    return round(total / count, 6)


def format_float(value: object, digits: int) -> str:
    numeric = as_float(value)
    if numeric is None:
        return ""
    return f"{numeric:.{digits}f}"


def item_at(values: Sequence[object], index: int) -> object | None:
    return values[index] if index < len(values) else None


def read_recording_summary(run_dir: Path) -> dict[str, object] | None:
    summary_path = run_dir / "summary.json"
    metadata_path = run_dir / "metadata.json"
    source_path = summary_path if summary_path.exists() else metadata_path
    if not source_path.exists():
        return None
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    payload.setdefault("run_id", run_dir.name)
    payload.setdefault("paths", {})
    return payload


def list_recordings(record_dir: Path) -> list[dict[str, object]]:
    directory = Path(record_dir)
    if not directory.exists():
        return []
    recordings: list[dict[str, object]] = []
    for run_dir in sorted(directory.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        summary = read_recording_summary(run_dir)
        if summary is not None:
            recordings.append(summary)
    return recordings


def resolve_artifact(record_dir: Path, run_id: str, filename: str) -> Path:
    allowed = {
        "merged_samples.csv",
        "export.csv",
        "metadata.json",
        "summary.json",
        "esp32_raw.csv",
        "dxmr90_raw.csv",
        "stepper_raw.csv",
    }
    if filename not in allowed:
        raise ValueError(f"unsupported artifact {filename!r}")
    directory = Path(record_dir).resolve()
    path = (directory / run_id / filename).resolve()
    if directory not in path.parents:
        raise ValueError("artifact path escapes record directory")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    return path
