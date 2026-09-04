"""Safe, notebook-friendly Arduino CLI firmware upload helpers.

Arduino CLI expects the main sketch file to live in a directory with the same
name, so uploads are compiled from a hash-checked temporary copy instead of
modifying the repository. Local quoted includes such as the ignored ESP32
``wifi_credentials.h`` are staged with the sketch without printing their
contents.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parent
WORKSPACE_TOOLS_ROOT = REPOSITORY_ROOT.parent / "tools"
PROJECT_ARDUINO_CONFIG = REPOSITORY_ROOT / ".arduino-build" / "arduino-cli.yaml"
SHARED_ARDUINO_CONFIG = (
    WORKSPACE_TOOLS_ROOT / "arduino-config" / "arduino-cli.yaml"
)
LOCAL_INCLUDE_PATTERN = re.compile(
    r'^\s*#\s*include\s*"([^"]+)"',
    re.MULTILINE,
)


class FirmwareUploadError(RuntimeError):
    """Raised when a firmware upload precondition or command fails."""


@dataclass(frozen=True)
class FirmwareTarget:
    key: str
    label: str
    fqbn: str
    default_payload: str
    monitor_baud: int
    safety_note: str


@dataclass(frozen=True)
class ArduinoToolchain:
    executable: Path
    config_file: Path | None

    @property
    def command_prefix(self) -> list[str]:
        prefix = [str(self.executable)]
        if self.config_file is not None:
            prefix.extend(["--config-file", str(self.config_file)])
        return prefix


TARGETS: Mapping[str, FirmwareTarget] = {
    "controllino": FirmwareTarget(
        key="controllino",
        label="CONTROLLINO MAXI Automation",
        fqbn="CONTROLLINO_Boards:avr:controllino_maxi_automation",
        default_payload="controllino_ethernet_diagnostic.ino",
        monitor_baud=9600,
        safety_note=(
            "Uploading resets the controller. Inspect the selected payload, "
            "confirm its startup output states, and make all connected equipment "
            "safe before continuing."
        ),
    ),
    "esp32": FirmwareTarget(
        key="esp32",
        label="Adafruit Feather ESP32-S3 No PSRAM",
        fqbn="esp32:esp32:adafruit_feather_esp32s3_nopsram",
        default_payload="Flow_management_unit_sch1.ino",
        monitor_baud=115200,
        safety_note=(
            "Uploading resets the controller. Put all connected solenoid loads "
            "in a safe state before continuing."
        ),
    ),
    "yun": FirmwareTarget(
        key="yun",
        label="Arduino Yún Rev2 ATmega32U4",
        fqbn="arduino:avr:yun",
        default_payload="limit_switch_palas.ino",
        monitor_baud=9600,
        safety_note=(
            "Set D4 OFF, disconnect the brushless ESC battery, and keep the "
            "DM542T motor supply off before compiling or uploading."
        ),
    ),
}


@dataclass(frozen=True)
class BoardPort:
    address: str
    protocol: str
    board_names: tuple[str, ...]
    fqbns: tuple[str, ...]

    @property
    def description(self) -> str:
        boards = ", ".join(self.board_names) if self.board_names else "unknown board"
        fqbns = ", ".join(self.fqbns) if self.fqbns else "no FQBN reported"
        return f"{self.address} ({self.protocol}; {boards}; {fqbns})"


@dataclass(frozen=True)
class UploadResult:
    target: FirmwareTarget
    payload: Path
    payload_sha256: str
    staged_files: tuple[str, ...]
    port: str | None
    compiled: bool
    uploaded: bool


def get_target(target: str) -> FirmwareTarget:
    """Return a configured firmware target by its short key."""

    try:
        return TARGETS[target.strip().lower()]
    except (AttributeError, KeyError) as exc:
        choices = ", ".join(sorted(TARGETS))
        raise FirmwareUploadError(
            f"Unknown target {target!r}; choose one of: {choices}"
        ) from exc


def resolve_payload(
    payload: str | Path | None,
    *,
    target: FirmwareTarget,
    repository_root: Path = REPOSITORY_ROOT,
) -> Path:
    """Resolve and validate the selected main Arduino sketch."""

    candidate = Path(payload or target.default_payload).expanduser()
    if not candidate.is_absolute():
        candidate = repository_root / candidate
    try:
        candidate = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FirmwareUploadError(f"Payload does not exist: {candidate}") from exc
    if not candidate.is_file():
        raise FirmwareUploadError(f"Payload is not a file: {candidate}")
    if candidate.suffix.lower() != ".ino":
        raise FirmwareUploadError(
            f"Payload must be a main .ino Arduino sketch: {candidate}"
        )
    return candidate


def sha256_file(path: Path) -> str:
    """Return a lowercase SHA-256 digest for a file."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_local_dependencies(source: Path) -> tuple[Path, ...]:
    """Collect a sketch and existing quoted includes beneath its directory."""

    source = source.resolve(strict=True)
    source_root = source.parent
    pending = [source]
    collected: set[Path] = set()

    while pending:
        current = pending.pop()
        if current in collected:
            continue
        try:
            current.relative_to(source_root)
        except ValueError as exc:
            raise FirmwareUploadError(
                f"Local sketch dependency escapes the payload directory: {current}"
            ) from exc
        collected.add(current)

        try:
            text = current.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise FirmwareUploadError(
                f"Local sketch dependency is not UTF-8 text: {current}"
            ) from exc

        for include_name in LOCAL_INCLUDE_PATTERN.findall(text):
            included = (current.parent / include_name).resolve()
            try:
                included.relative_to(source_root)
            except ValueError as exc:
                raise FirmwareUploadError(
                    "Local quoted include escapes the payload directory: "
                    f"{include_name!r} in {current}"
                ) from exc
            if not included.is_file():
                continue
            pending.append(included)
            if included.suffix.lower() in {".h", ".hh", ".hpp", ".hxx"}:
                for suffix in (".c", ".cc", ".cpp", ".cxx", ".S"):
                    companion = included.with_suffix(suffix)
                    if companion.is_file():
                        pending.append(companion.resolve())

    return tuple(
        sorted(
            collected,
            key=lambda path: str(path.relative_to(source_root)),
        )
    )


def stage_sketch(source: Path, sketch_dir: Path) -> tuple[str, ...]:
    """Copy a sketch and local dependencies, verifying every copied hash."""

    source = source.resolve(strict=True)
    source_root = source.parent
    staged_names: list[str] = []
    for dependency in collect_local_dependencies(source):
        relative = dependency.relative_to(source_root)
        destination = sketch_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dependency, destination)
        if sha256_file(destination) != sha256_file(dependency):
            raise FirmwareUploadError(
                f"Temporary sketch copy failed hash verification: {relative}"
            )
        staged_names.append(str(relative))
    return tuple(staged_names)


def _version_key(path: Path) -> tuple[int, ...]:
    version = path.parent.name.removeprefix("arduino-cli-")
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return ()


def resolve_arduino_cli(executable: str | Path | None = None) -> Path:
    """Find an explicit, workspace-local, or PATH Arduino CLI executable."""

    if executable is not None and str(executable).strip().lower() != "auto":
        requested = str(executable).strip()
        if not requested:
            raise FirmwareUploadError("Arduino CLI executable cannot be empty")
        resolved = shutil.which(requested)
        if resolved is None:
            candidate = Path(requested).expanduser()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                resolved = str(candidate.resolve())
        if resolved is None:
            raise FirmwareUploadError(
                f"Arduino CLI executable does not exist or is not executable: "
                f"{requested}"
            )
        return Path(resolved).resolve()

    local_candidates = sorted(
        WORKSPACE_TOOLS_ROOT.glob("arduino-cli-*/arduino-cli"),
        key=_version_key,
        reverse=True,
    )
    for candidate in local_candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()

    path_cli = shutil.which("arduino-cli")
    if path_cli is None:
        raise FirmwareUploadError(
            "Arduino CLI was not found on PATH or under the workspace tools "
            f"directory {WORKSPACE_TOOLS_ROOT}. Install it or set ARDUINO_CLI "
            "to its executable path."
        )
    return Path(path_cli).resolve()


def resolve_arduino_config(config_file: str | Path | None = None) -> Path | None:
    """Find an explicit or workspace-local Arduino CLI configuration."""

    if config_file is not None and str(config_file).strip().lower() != "auto":
        requested = Path(config_file).expanduser()
        if not requested.is_absolute():
            requested = REPOSITORY_ROOT / requested
        try:
            resolved = requested.resolve(strict=True)
        except FileNotFoundError as exc:
            raise FirmwareUploadError(
                f"Arduino CLI config does not exist: {requested}"
            ) from exc
        if not resolved.is_file():
            raise FirmwareUploadError(f"Arduino CLI config is not a file: {resolved}")
        return resolved

    for candidate in (PROJECT_ARDUINO_CONFIG, SHARED_ARDUINO_CONFIG):
        if candidate.is_file():
            return candidate.resolve()
    return None


def resolve_toolchain(
    *,
    executable: str | Path | None = None,
    config_file: str | Path | None = None,
) -> ArduinoToolchain:
    """Resolve the Arduino CLI and the matching isolated workspace config."""

    return ArduinoToolchain(
        executable=resolve_arduino_cli(executable),
        config_file=resolve_arduino_config(config_file),
    )


def require_arduino_cli(executable: str | Path | None = None) -> str:
    """Compatibility wrapper returning the resolved CLI path as text."""

    return str(resolve_arduino_cli(executable))


def run_arduino_cli(
    arguments: Sequence[str],
    *,
    executable: str | Path | None = None,
    config_file: str | Path | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run Arduino CLI without a shell so paths cannot become shell commands."""

    toolchain = resolve_toolchain(
        executable=executable,
        config_file=config_file,
    )
    command = [*toolchain.command_prefix, *arguments]
    print(f"$ {shlex.join(command)}", flush=True)
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=capture_output,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f"\n{detail}" if detail else ""
        raise FirmwareUploadError(
            f"Arduino CLI command failed with exit code {exc.returncode}: "
            f"{shlex.join(command)}{suffix}"
        ) from exc


def _as_tuple(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(value) for value in values if value)


def parse_board_list_json(document: Any) -> list[BoardPort]:
    """Normalize Arduino CLI's current and older board-list JSON layouts."""

    if isinstance(document, dict):
        entries = document.get("detected_ports", document.get("ports", []))
    elif isinstance(document, list):
        entries = document
    else:
        raise FirmwareUploadError("Arduino CLI returned an unexpected board-list shape")

    ports: list[BoardPort] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        port_data = entry.get("port", entry)
        if not isinstance(port_data, dict):
            continue
        address = port_data.get("address") or entry.get("address")
        if not address:
            continue
        protocol = port_data.get("protocol") or entry.get("protocol") or "unknown"
        matches = (
            entry.get("matching_boards")
            or entry.get("boards")
            or port_data.get("matching_boards")
            or []
        )
        if not isinstance(matches, list):
            matches = []
        names = _as_tuple(
            match.get("name")
            for match in matches
            if isinstance(match, dict)
        )
        fqbns = _as_tuple(
            match.get("fqbn")
            for match in matches
            if isinstance(match, dict)
        )
        ports.append(
            BoardPort(
                address=str(address),
                protocol=str(protocol),
                board_names=names,
                fqbns=fqbns,
            )
        )
    return ports


def discover_ports(
    *,
    executable: str | Path | None = None,
    config_file: str | Path | None = None,
) -> list[BoardPort]:
    """Ask Arduino CLI for currently connected boards."""

    result = run_arduino_cli(
        ["board", "list", "--format", "json"],
        executable=executable,
        config_file=config_file,
        capture_output=True,
    )
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FirmwareUploadError(
            "Arduino CLI returned invalid JSON from `board list`"
        ) from exc
    return parse_board_list_json(document)


def format_ports(ports: Sequence[BoardPort]) -> str:
    """Format discovered ports for a notebook cell."""

    if not ports:
        return "No serial boards detected."
    return "\n".join(f"- {port.description}" for port in ports)


def select_port(
    target: FirmwareTarget,
    requested_port: str | None,
    ports: Sequence[BoardPort],
) -> str:
    """Resolve an explicit port or one unambiguous exact FQBN match."""

    requested = (requested_port or "auto").strip()
    if requested.lower() != "auto":
        if not requested:
            raise FirmwareUploadError("An explicit upload port cannot be empty")
        return requested

    matches = [
        port
        for port in ports
        if target.fqbn in port.fqbns and port.protocol.lower() == "serial"
    ]
    if len(matches) == 1:
        return matches[0].address
    if len(matches) > 1:
        choices = ", ".join(port.address for port in matches)
        raise FirmwareUploadError(
            f"Multiple {target.label} ports match ({choices}); set PORT explicitly."
        )

    detected = format_ports(ports)
    raise FirmwareUploadError(
        "No connected serial/USB board reports the exact target FQBN "
        f"{target.fqbn!r}. Connect the target and retry, or inspect the list "
        "and set PORT explicitly if board detection is unavailable. Network "
        f"ports are never selected automatically by this USB workflow.\n{detected}"
    )


def command_preview(
    target: FirmwareTarget,
    payload: Path,
    port: str = "<selected USB port>",
    *,
    toolchain: ArduinoToolchain | None = None,
) -> str:
    """Show the commands that the temporary staging workflow will run."""

    sketch_dir = f"<temporary>/{payload.stem}"
    build_dir = "<temporary>/build"
    prefix = toolchain.command_prefix if toolchain else ["arduino-cli"]
    preview_port = (
        "<auto-selected USB port>" if port.strip().lower() == "auto" else port
    )
    commands = [
        [
            *prefix,
            "compile",
            "--fqbn",
            target.fqbn,
            "--output-dir",
            build_dir,
            sketch_dir,
        ],
        [
            *prefix,
            "upload",
            "--fqbn",
            target.fqbn,
            "--port",
            preview_port,
            "--input-dir",
            build_dir,
            "--verify",
            sketch_dir,
        ],
    ]
    return "\n".join(f"$ {shlex.join(command)}" for command in commands)


def compile_and_upload(
    target_name: str,
    payload: str | Path | None = None,
    *,
    port: str | None = "auto",
    safety_confirmed: bool = False,
    compile_only: bool = False,
    executable: str | Path | None = None,
    config_file: str | Path | None = None,
) -> UploadResult:
    """Stage, compile, and optionally upload one selected firmware sketch."""

    target = get_target(target_name)
    source = resolve_payload(payload, target=target)
    payload_hash = sha256_file(source)

    if not compile_only and not safety_confirmed:
        raise FirmwareUploadError(
            "Upload blocked: set SAFETY_CONFIRMED = True only after completing "
            f"the target safety check. {target.safety_note}"
        )

    toolchain = resolve_toolchain(
        executable=executable,
        config_file=config_file,
    )
    selected_port: str | None = None
    if not compile_only:
        selected_port = select_port(
            target,
            port,
            discover_ports(
                executable=toolchain.executable,
                config_file=toolchain.config_file,
            ),
        )

    with tempfile.TemporaryDirectory(
        prefix=f"networked_sensors_{target.key}_"
    ) as temporary:
        temporary_root = Path(temporary)
        sketch_dir = temporary_root / source.stem
        build_dir = temporary_root / "build"
        sketch_dir.mkdir()
        build_dir.mkdir()
        staged_files = stage_sketch(source, sketch_dir)

        run_arduino_cli(
            [
                "compile",
                "--fqbn",
                target.fqbn,
                "--output-dir",
                str(build_dir),
                str(sketch_dir),
            ],
            executable=toolchain.executable,
            config_file=toolchain.config_file,
        )

        if not compile_only:
            run_arduino_cli(
                [
                    "upload",
                    "--fqbn",
                    target.fqbn,
                    "--port",
                    selected_port or "",
                    "--input-dir",
                    str(build_dir),
                    "--verify",
                    str(sketch_dir),
                ],
                executable=toolchain.executable,
                config_file=toolchain.config_file,
            )

    return UploadResult(
        target=target,
        payload=source,
        payload_sha256=payload_hash,
        staged_files=staged_files,
        port=selected_port,
        compiled=True,
        uploaded=not compile_only,
    )


def monitor_command(
    target_name: str,
    port: str,
    *,
    toolchain: ArduinoToolchain | None = None,
) -> list[str]:
    """Return the non-executed serial-monitor command for a target."""

    target = get_target(target_name)
    return [
        *(toolchain.command_prefix if toolchain else ["arduino-cli"]),
        "monitor",
        "--port",
        port,
        "--config",
        f"baudrate={target.monitor_baud}",
    ]
