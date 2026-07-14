# INTEGRATION-TASKS - sensor/source integration

## Current task

Integrate every data/control source into the laptop supervisor without turning
the ESP32 into a larger monolith. In `dpc-flight`, integration meant drying
method arms into a common protocol. Here, integration means drying source arms
into one timestamped supervisor schema and one operator workflow.

Progress is chronicled in `INTEGRATION-PROCEDURE.md`.

## Terminology

- **Dry source:** simulated, tested, documented, emits the common sample schema,
  reports health/staleness, and participates in recording/export.
- **Wet source:** useful but ad hoc: manually run, embedded in a device UI,
  not yet merged with the rest of the bench data, or not yet covered by a
  simulator.
- **Integration:** the drying process that turns a wet source into a stable
  supervisor adapter.

## Source arms

| Arm | Current state | Target landing | Verification |
| --- | --- | --- | --- |
| ESP32 analog/control | `RealEsp32Source` consumes strict v2 SSE readings/four-state solenoid events and forwards toggle POSTs for indices 0–3; loopback contract passes | physical stream/toggle/recording smoke | 6 loopback layout/parser/adapter/CLI/runtime tests and target compile pass; physical SSE/HTTP smoke and merged CSV pending |
| DXMR90 Modbus | `RealDxmr90Source` reads both live SICK process windows at 10 Hz | direct process data by default; republished ScriptBasic block retained as fallback | heartbeat, schema, and sustained 10 Hz hardware smoke |
| ESP32 simulator | Exists as `SimulatedEsp32Source` | scenario controls and dashboard solenoid toggles landed | no-hardware 10 Hz stream plus simulated control smoke |
| DXMR90 simulator | Exists as `SimulatedDxmr90Source` | scenario controls landed | no-hardware 1 Hz stream |
| Yún stepper simulator | `SimulatedStepperSource` and shared dashboard controls exist, including the latched software E-STOP | positive operator travel is resolved to the simulator's signed internal delta | move/stop/E-STOP, validation, limit, stale/missing, and shared-recorder smoke |
| Yún stepper USB | Dual Local Velocity/Web Position and software E-STOP firmware are uploaded and live | localhost mode, D8-limit seek, positive travel magnitude with D5 direction, Stop, latched software E-STOP, and diagnostics | 26 tests, compile/upload/readback, fresh stopped latch/reset smoke pass; moving stops and other staged motion remain |
| Yún stepper network | Standalone firmware has no Bridge motion transport | non-blocking firmware + Bridge API + network adapter | staged LAN, ownership, motor, limit, and open-loop distance tests |
| Merge/clock | Exists as `SourceMerger` | stale/disconnect scenarios landed | laptop timestamp, latest-value hold, age fields |
| Recorder/export | Disk-backed supervisor recorder exists in `recorder.py` | real-source parity after adapters land | simulated start/stop/export |

## Integration sequence

- [x] **I1 - common schema.** Define merged sample fields, units, null handling,
      source modes, and age fields.
- [x] **I2 - simulated sources.** Make both source arms runnable with no
      hardware. This is the base case for dashboard and recorder development.
- [x] **I2a - simulated source failure modes.** Add healthy/stale/missing
      scenario controls while preserving the merged sample schema.
- [x] **I2b - disk-backed simulated recording/export.** Use the simulated
      dashboard workflow to write merged/source CSVs, metadata, summary, and
      browser export artifacts.
- [x] **I3 - DXMR90 real adapter.** Reuse the existing Modbus client, decode
      both direct SICK process windows at 10 Hz, and retain `read_metrics` for
      the republished diagnostic path.
- [x] **I3a - simulated Yún stepper adapter.** Add a third source, strict
      move/stop/status API, dashboard controls, stale/missing scenarios, and
      shared recording without contacting the physical Yún.
- [ ] **I3b - USB Yún diagnostic adapter.** Compact read-only firmware status,
      source-scoped disconnect behavior, laptop parser, and dashboard consumer
      are implemented; compile, verified upload, and initial live heartbeat pass;
      deliberate switch/unplug smoke remains.
- [ ] **I3b2 - USB Yún manual-speed adapter.** A separate D4-off-only speed
      setpoint, configured/effective speed status, endpoint, and page action are
      implemented without remote start/stop/direction authority; desktop tests,
      Yún compile/upload, and the live zero-motion 3.0 mm/s echo pass, while
      staged physical-speed checks remain.
- [ ] **I3b3 - USB Yún direction-mapping adapter.** Normal/Inverted electrical
      mapping is implemented as a D4-off calibration parameter while D5 and the
      logical limits retain authority; desktop tests, Yún compile/upload, and
      live capability pass, while physical sign verification remains.
- [ ] **I3b4 - USB Yún dual-mode position adapter.** Local Velocity preserves
      D4/D5 continuous control; Web Position gives D4 arm/abort and uses D5 as
      the direction selector for a positive travel magnitude. Mode, fixed-speed
      optional D8-limit seek, relative move, immediate Stop, and fixed
      acceleration are
      implemented across firmware/adapter/API/page; 22 tests, Yún compile,
      verified upload, and stopped live USB status pass. Homed/absolute position
      and software-margin gates are deliberately absent; staged physical motion
      checks remain.
- [ ] **I3b5 - USB Yún software E-STOP adapter.** A mode-independent firmware
      latch, compact capability/status, exact USB commands, dashboard APIs, and
      prominent top-level stop/reset controls are implemented. Reset requires
      D4 OFF and the page inhibits motion controls while latched. Twenty-six
      stepper tests, 32 combined tests, and a 65%-flash/28%-RAM Yún compile
      pass. Verified upload and a stopped live latch/reset smoke pass; deliberate
      stops in both modes, D4-on reset rejection, disconnect persistence, and
      moving latency remain. E-STOP dispatch now bypasses blocking source polls;
      stopped response with DXMR90 unreachable improved from 1.26 s to 0.041 s.
      This path is not a hardwired safety-rated E-stop.
- [ ] **I3c - network Yún stepper adapter.** Land only after the non-blocking
      firmware and Bridge transport pass their safety gates; preserve the I3a
      command/status shape.
- [ ] **I4 - ESP32 real adapter.** Existing SSE/HTTP is implemented with
      `--esp32-url`, background reconnect, health/error state, solenoid control,
      strict version-2 complete-sample validation, and loopback contract
      coverage. Physical stream/toggle/recording smoke remains before this
      source arm is dry.
- [ ] **I5 - headless firmware split.** The former ESP32-hosted page is archived
      under `legacy/`; the primary firmware emits complete version-2 samples,
      controls four active-low relays including GPIO 10, and keeps only
      sensor/solenoid APIs. Desktop tests and target compile pass;
      physical flash and live contract verification remain.
- [ ] **I6 - full source parity.** Run the same dashboard/recording/export
      workflow in sim, ESP32-only, DXMR90-only, and full hardware modes.

## Known integration risks

- The primary ESP32 event contract is atomic and versioned: each reading must
  include pressure, flow, clamped sensor voltage, and solenoid arrays. The real
  adapter rejects the archived firmware's unversioned partial readings so a
  mixed deployment fails visibly instead of silently losing fields.
- The DXMR90 default IP may collide with routers at `192.168.0.1`.
- Source rates can differ; merged logs expose age/staleness and record fresh
  direct DXMR90 rows at the configured hardware poll rate.
- The archived ESP32 UI buffers CSV in RAM. It is reference/fallback firmware,
  not a compatibility target or dependency of the laptop supervisor.
- The installed Yún limits are normally open, so a broken wire looks clear.
  The real adapter cannot compensate for this electrical limitation.
