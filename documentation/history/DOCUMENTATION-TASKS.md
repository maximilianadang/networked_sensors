# DOCUMENTATION-TASKS - networked_sensors

## Current task

Keep the flow-management supervisor documentation current as the system moves
from embedded ESP32 dashboard to laptop-side supervisor. Documentation must
orient both humans and coding agents, and it must distinguish existing
interfaces from planned ones.

Progress is chronicled in `DOCUMENTATION-PROCEDURE.md`.

## Translation from dpc-flight

`dpc-flight` uses documentation as an operating surface: root map, run protocol,
runbook, hardware checklist, task/procedure chronicles. Here the same kinds
translate as:

| dpc-flight kind | networked_sensors translation |
| --- | --- |
| `CLAUDE.md` | agent-facing orientation map for this directory |
| `PROTOCOL.md` | source/endpoint/artifact/verification map |
| `DEVELOPMENT-*` | supervisor/dashboard build plan and chronicle |
| `INTEGRATION-*` | source-arm integration plan and chronicle |
| `DOCUMENTATION-*` | doc discipline and interface inventory |
| `RUNBOOK.md` | how to run sim and hardware modes |
| `FLIGHT_CHECKLIST.md` | `TESTBENCH_CHECKLIST.md`, hardware bring-up gaps |

## Documentation rules

- Document what exists today, and label planned supervisor interfaces as planned.
- When code lands, update the protocol map and runbook in the same step.
- If a task changes how something is run, configured, selected, guarded,
  produced, or consumed, check `PROTOCOL.md` and probably regenerate it with
  `protocol_map.py`.
- If a task only changes what happened in a run, record the result in the
  relevant procedure doc rather than expanding the protocol map.
- Capture verify-pass findings. If a smoke run reveals a setup gotcha, it goes
  in `RUNBOOK.md` or `TESTBENCH_CHECKLIST.md`.
- Keep the root orientation thin. Put detailed commands in the runbook and
  source-specific facts near the relevant source docs.
- Do not duplicate the DXMR90 register map in every file; `README.md` remains
  the register table.

## Current interface inventory

- Primary headless ESP32 API:
  - `/` serves a small JSON service descriptor, not a webpage.
  - `/events` sends version-3 `reading` events with explicit per-ADC health and
    nullable unavailable sensor families, plus immediate `sol` state events.
  - `/solenoid/toggle?n=<0..3>` toggles four active-low relays; index 3 is
    Solenoid 4 on GPIO 10.
  - UI, metadata, recording, and CSV export belong to laptop `dashboard.py`.
- Archived ESP32-hosted dashboard firmware:
  - `legacy/Flow_management_unit_sch1/Flow_management_unit_sch1.ino` retains
    the former `/`, `/events`, and `/test/*` surfaces for reference only.
- Existing laptop-side ESP32 adapter:
  - `RealEsp32Source` consumes `/events` in a reconnecting background thread.
  - Healthy version-2 `reading` events remain accepted during deployment.
    Version 3 supplies explicit pressure/flow ADC health, nullable unavailable
    sensor families, clamped sensor volts when available, and all four solenoid
    states atomically. Invalid partial readings fail visibly; separate `sol`
    events update relay state immediately.
  - `--esp32-source real --esp32-url URL --esp32-timeout S` selects it, and the
    shared solenoid buttons forward to `/solenoid/toggle?n=<0..3>`.
- Existing DXMR90 reader:
  - CLI supports `--host`, `--port`, `--unit-id`, `--addressing`,
    `--word-order`, `--group`, `--metric`, `--interval`, `--format`, and `--raw`.
  - Python functions/classes are reusable for a real supervisor adapter.
- Existing stepper surfaces:
  - `limit_switch_palas.ino` drives a STEP/DIR controller from D2-D6 and D8,
    uses D6/D8 as positive/negative directional limits, reports raw limit levels
    over USB Serial, and now has a bounded non-blocking Serial1 command/status
    path for the Yún Linux service.
  - The laptop supervisor has a simulation stepper source, shared dashboard
    controls, move/stop/status API, merged status fields, and `stepper_raw.csv`.
    It also has a live USB dual-mode adapter. The firmware and laptop path
    implement backward-compatible Local Velocity plus Web Position mode,
    optional D8-limit seek, bounded relative distance, and Stop; desktop tests,
    compile, verified upload, and
    stopped live status pass, while staged physical motion remains pending. The
    new top-level software E-STOP is uploaded; stopped-state live latch/reset
    verification passes, while deliberate moving-stop checks remain. It is
    explicitly not a replacement for a hardwired safety-rated E-stop. The
    network adapter/service and firmware ownership compile/loopback pass, while
    physical installation and LAN/motion timing checks remain.
  - `documentation/README.md` assesses an original Arduino Yún replacement and
    indexes the downloaded Yún, Bridge, and AccelStepper sources.
- Existing supervisor:
  - local dashboard at `localhost`;
  - simulated sources plus real ESP32, real DXMR90, and USB/network Yún adapters;
  - disk-backed recording and merged CSV export.

## Execution

- [x] **D0 - establish doc layer.** Create the all-caps docs and translate the
      `dpc-flight` practices.
- [x] **D1 - update after supervisor skeleton.** Add actual commands,
      entrypoints, schema, and protocol-map check once code lands.
- [x] **D2 - update after simulation smoke/scenarios.** Record no-hardware run
      commands, expected output artifacts, and stale/missing scenario behavior.
- [x] **D3 - update after dashboard/API.** Record local server command, live
      stream contract, and UI verification once that code lands.
- [x] **D4 - update after recorder/exporter.** Record disk artifact contract,
      export endpoints, and simulation verification.
- [ ] **D5 - update after hardware smoke.** Record ESP32/DXMR90 network and
      source health findings.
- [x] **D6 - document Yún stepper feasibility.** Preserve authoritative Yún,
      Bridge, and AccelStepper references locally; record pin, power, timing,
      position, and web-integration constraints without claiming implementation.
- [x] **D7 - establish the Yún stepper execution queue.** Add `TASKS.md` with
      ordered contract, simulation, UI, firmware, Bridge, adapter, bench, and
      handoff gates.
- [x] **D8 - add the second limit and characterization surface.** Assign D8 as
      the negative limit, retain D6 as the positive limit, and document the raw
      Serial test needed to settle whether each magnetic switch opens or closes.
- [x] **D9 - document unified simulated stepper control.** Add the third source,
      provisional command envelope, endpoints, shared dashboard, test command,
      stale/missing scenarios, and recorder artifact to the generated protocol
      and runbook.
- [x] **D10 - document dual real-hardware transports and T4A USB diagnostics.**
      Distinguish laptop-local USB testing from LAN/Bridge production, require
      shared semantics and exclusive ownership, and document the implemented
      read-only USB status path with compile, upload, heartbeat, and remaining
      transition-smoke status tracked separately.
- [x] **D11 - document T4B manual USB speed tuning.** Record the D4-off guard,
      0.1..10.0 mm/s provisional range, separate configured/effective speeds,
      `/api/stepper/speed`, retained manual authority, and motor-safe upload/live
      verification gate.
- [x] **D12 - document T4C direction mapping.** Record Normal/Inverted as an
      electrical calibration guarded by D4 OFF, not a remote motion override;
      document `V1 D0|1`, the endpoint, D5/limit authority, and physical sign
      verification gate.
- [x] **D13 - document T5 dual-mode USB position control.** Record Local
      Velocity versus Web Position authority, optional D8-limit seek, fixed acceleration,
      positive travel magnitude with D5-selected direction, directional D6/D8
      safety without an absolute software envelope, the signed internal wire command, new commands/endpoints,
      provisional steps/mm, the decision not to expose/use absolute open-loop
      position for safety, desktop verification, verified upload/stopped live
      status, and remaining deliberate-motion hardware gates.
- [x] **D14 - document the real ESP32 software adapter.** Record the strict
      versioned SSE/toggle contract, `--esp32-url` and timeout configuration, complete
      sample fields, immediate solenoid events, loopback verification, launch
      commands, and the remaining physical stream/toggle/recording gate.
- [x] **D15 - document the headless firmware split.** Mark the primary sketch
      as a sensor/solenoid service, archive the old webpage firmware, and state
      that the laptop dashboard has no legacy-telemetry compatibility duty.
- [x] **D16 - document Solenoid 4.** Record GPIO 10, active-low/OFF-at-boot
      behavior, versioned four-state telemetry, the fourth laptop control, and
      the remaining physical relay/solenoid wiring and safe-toggle checks.
- [x] **D20 - document the software E-STOP boundary.** Record the latched
      `V1 E1`/D4-off `V1 E0` contract, top-level dashboard controls, capability
      and status fields, endpoints, tests/compile result, pending upload/bench
      checks, and the fact that it does not remove power or provide a
      safety-rated emergency-stop function.
- [x] **D21 - document the Yún LAN bridge software landing.** Record the
      non-blocking Serial1/AR9331 service architecture, trusted-LAN endpoints,
      `--stepper-url`/timeout selection, USB/network ownership, exact install
      and rollback commands, desktop/compile evidence, and the remaining
      physical upload/service/timing/restart/disconnect gates.
- [x] **D22 - document Yún cold-start operation.** Add one-time provisioning
      and daily dashboard commands, record physical boot-service/key evidence,
      and keep target-LAN association separate from completed AsteraMesh checks.
- [x] **D23 - document the linear pulse calibration.** Tie the motor datasheet's
      0.00396875 mm/full-step value to the photographed DM542T all-ON SW5-SW8
      200-pulse/rev setting, update pulse-domain contracts, and retain a DRO
      verification gate.
- [x] **D24 - document emitted STEP instrumentation.** Distinguish configured,
      scheduled, and measured emitted-pulse speed; document the optional `aps`
      wire field, 250 ms window, dashboard display, compatibility behavior, and
      the boundary that this is not driver or piston feedback.
- [x] **D25 - document fixed direction and driver-output control.** Retire the
      unsafe runtime mapping contract, record immutable Normal physical
      direction, D9-to-ENA- common-anode wiring, LOW-disabled/HIGH-enabled
      polarity, the 200 ms wake-up, compact `en`, legacy inversion refusal,
      authoritative raw-endpoint latch clearing, Timer1 Local Velocity timing,
      compile/upload/tests, the working physical run, and the remaining exact-
      image two-endpoint hardware matrix.
- [x] **D26 - document independent ESP32 startup.** Correct the inherited-code
      comparison, define v3 ADC health/null semantics and healthy-v2 support,
      distinguish Off/Stale/Live/ADC-unavailable UI states, and record compile,
      verified flash, test evidence, and the remaining field-LAN smoke.
- [x] **D27 - document source-worker isolation.** Record that real DXMR90
      connection/read timeouts run outside the shared merge loop, preserve
      source-scoped stale/error behavior, and cannot reduce live ESP32,
      stepper, dashboard, or recorder cadence.
- [x] **D28 - document responsive solenoid control.** Distinguish the 1 Hz ADC
      presence check from 10 Hz browser state, record 10 Hz SSE/fallback parity,
      merge-lock-free serialized relay POSTs, pending-button feedback, cached
      field mDNS resolution, and the remaining physical latency retest.
