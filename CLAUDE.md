# CLAUDE.md - networked_sensors

Root orientation map for coding agents and humans working on the flow-management
test bench. Thin by design: this file orients; the task/procedure documents hold
the reasoning; source files remain the interface of record.

## Read first

- This directory currently contains a headless ESP32-S3 test-bench sketch, an
  archived copy of the former ESP32-hosted dashboard firmware, a
  no-dependency DXMR90 Modbus TCP reader, a Python supervisor skeleton, a local
  dashboard/API, a Yún Linux UART/HTTP bridge, a disk-backed recorder/exporter,
  and handoff notes from the
  former embedded dashboard.
- The current architecture is a laptop-side Python supervisor with a local web
  dashboard. The skeleton runs healthy/stale/missing simulation scenarios for
  ESP32, DXMR90, and stepper sources before hardware is connected;
  `dashboard.py` serves their shared localhost UI/API with disk-backed
  recording/export, simulation stepper controls, a physical-Yún USB path, and
  a software-complete network Yún path pending physical installation.
  Its real ESP32 adapter accepts healthy version-2 `/events` samples and the
  primary firmware's version-3 stream with explicit per-ADC availability,
  nullable unavailable sensor families, and four solenoid states. It forwards
  the solenoid-toggle endpoint and rejects older, partial, or inconsistent
  samples.
  The live board now has explicit Local Velocity/Web Position modes, optional
  D8-limit seek, positive travel magnitude with D5-selected direction, and Stop; compile,
  verified upload, and stopped live USB status pass, with staged physical motion
  checks still required. Signed deltas exist only below the page/API boundary.
  D4 and D6/D8—not homed/absolute open-loop position—are the motion safety inputs.
  Runtime Normal/Inverted direction mapping is retired after it was shown to
  decouple physical travel from the selected endpoint interlock. The current
  revision fixes Normal (Forward -> D6, Reverse -> D8), refuses Web Position
  commands from legacy `ds:-1` status, and controls common-anode DM542T ENA-
  from D9. It disables motor holding current whenever stopped/blocked/E-stopped
  and waits 200 ms after re-enable before STEP. Local Velocity STEP timing now
  uses Timer1 rather than cooperative `runSpeed()`, removing the observed
  webpage-traffic speed ceiling. Thirty-seven desktop tests, a
  22,620-byte/78%-flash, 1,453-byte/56%-RAM Yún compile, verified upload, and
  an operator-confirmed working Local Velocity run pass; the complete
  two-endpoint D9/retreat matrix on this exact image remains.
  A prominent dashboard software E-STOP now latches in the ATmega across both
  modes and requires D4 OFF to reset. It depends on the host/USB/firmware path
  and must never be represented as a hardwired safety-rated E-stop. The T5A
  image is uploaded and stopped-state latch/reset verification passes; moving
  stop and latency checks remain.
  A Yún Linux network path is now software-landed: the ATmega uses bounded,
  non-blocking Serial1 queues; `yun_stepper_bridge.py` relays the existing V1
  contract through trusted-LAN HTTP; and `NetworkStepperSource` polls/commands
  it with explicit USB/network firmware ownership. Six focused network tests
  and a 72%-flash/54%-RAM Yún compile pass. The image is now uploaded and a
  stopped USB heartbeat verifies owner `none`; Linux-service installation and
  motor-off LAN checks remain.
- Do not expand the ESP32 into the central data aggregator. Keep it as hardware
  I/O: analog pressure/flow reads, solenoid control, and a small telemetry/control
  API. The laptop supervisor owns merged logging, plotting, metadata, and CSV
  export.

## Doc map

- `PROTOCOL.md` - current and target run protocol: sources, streams, commands,
  artifacts, verification tiers, and the visual graph. It is generated/checked
  by `protocol_map.py`.
- `DEVELOPMENT-TASKS.md` / `DEVELOPMENT-PROCEDURE.md` - build plan and chronicle
  for the supervisor/dashboard work.
- `INTEGRATION-TASKS.md` / `INTEGRATION-PROCEDURE.md` - how each sensor/source arm
  is dried into the supervisor.
- `DOCUMENTATION-TASKS.md` / `DOCUMENTATION-PROCEDURE.md` - documentation cadence
  and interface map discipline.
- `TASKS.md` - ordered implementation queue for the Yún Rev2 web-controlled
  stepper adaptation.
- `RUNBOOK.md` - how to run the simulated supervisor now and real hardware later.
- `TESTBENCH_CHECKLIST.md` - bench bring-up checks the simulator cannot validate.
- `TESTBENCH_HANDOFF.md` - hardware facts inherited from the former
  embedded-dashboard implementation plus the headless transition.
- `README.md` - DXMR90 Modbus network setup and register map.

## Current code surface

```
networked_sensors/
  Flow_management_unit_sch1.ino   # primary headless ESP32-S3 sensor/solenoid API, strict telemetry v1
  legacy/Flow_management_unit_sch1/Flow_management_unit_sch1.ino # archived ESP32-hosted dashboard firmware
  limit_switch_palas.ino          # dual-mode engine, limits, USB motion/status, latched software E-STOP
  yun_stepper_bridge.py           # Yún Linux /dev/ttyATH0 to trusted-LAN HTTP relay
  yun-stepper-bridge.init         # staged OpenWrt service wrapper (install only after smoke)
  documentation/                  # offline Arduino Yún/Bridge/AccelStepper references and adaptation notes
  TASKS.md                       # Yún Rev2 stepper web-control execution order and acceptance gates
  read_dxmr90_modbus.py           # stdlib Modbus TCP reader for Banner DXMR90-4k republished registers
  supervisor_core.py              # source schema, simulations, real ESP32/DXMR90 and USB Yún adapters, merge logic
  supervisor.py                   # no-hardware JSONL smoke CLI with healthy/stale/missing scenarios
  dashboard.py                    # laptop dashboard with real/sim/off ESP32/DXMR90 and sim/USB/network/off stepper sources
  recorder.py                     # Step-4 run directories, merged/source CSVs, metadata, summary, export CSV
  test_stepper_control.py         # simulated motion plus USB parser/pseudo-terminal contract tests
  protocol_map.py                 # generated PROTOCOL.md graph/table checker
  README.md                       # DXMR90 network setup and register table
  TESTBENCH_HANDOFF.md            # existing test-bench hardware/software handoff
```

## Intended architecture

```
ESP32 analog/solenoid I/O  -> RealEsp32Source      \
Simulated ESP32 readings   -> SimulatedEsp32Source  \
                                                     -> Python supervisor -> local dashboard + logs
DXMR90 Modbus TCP          -> RealDxmr90Source      /
Simulated DXMR90 readings  -> SimulatedDxmr90Source/
Simulated Yún stepper      -> SimulatedStepperSource/
Yún native USB status      -> UsbStepperSource      /
Yún Wi-Fi/UART bridge API  -> NetworkStepperSource /  (older service installed; current bridge redeploy pending)
```

The supervisor should expose one merged sample model with laptop timestamps,
source health, and staleness fields. The web dashboard should consume the
supervisor API, not the ESP32 directly.

## Translation from dpc-flight practice

- `PROTOCOL.md` in `dpc-flight` maps verbs/configs/artifacts. Here it maps
  sources/endpoints/recording artifacts and is generated once the supervisor
  skeleton exists. Check it whenever a task changes how something is run,
  configured, selected, guarded, produced, or consumed.
- `DEVELOPMENT-*` in `dpc-flight` tracked a staged rebuild. Here it tracks the
  supervisor build: schema first, simulation next, dashboard/logging, then real
  hardware adapters.
- `INTEGRATION-*` in `dpc-flight` dried method arms. Here it dries sensor arms:
  ESP32 analog/control, DXMR90 Modbus, merged timing, and export contracts.
- `RUNBOOK` and `FLIGHT_CHECKLIST` in `dpc-flight` separate runnable procedure
  from hardware-only checks. Here that becomes `RUNBOOK.md` plus
  `TESTBENCH_CHECKLIST.md`.
