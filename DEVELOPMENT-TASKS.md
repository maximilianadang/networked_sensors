# DEVELOPMENT-TASKS - flow-management supervisor

## Current task

Build the laptop-side Python supervisor and local web dashboard for the
flow-management test bench. The supervisor must run in simulation mode before
any sensor is connected, then gain real adapters for the ESP32 and DXMR90.

Progress is chronicled in `DEVELOPMENT-PROCEDURE.md`.

## Translation from dpc-flight

`dpc-flight` used `DEVELOPMENT-*` to rebuild a simulator/control stack one
layer at a time, with each layer verified before the next. Here the layers are
data acquisition and operator workflow:

1. schema and source interfaces before UI;
2. simulated sources before hardware;
3. recording/export before long bench runs;
4. real adapters after the simulator proves the workflow;
5. firmware cleanup only where the real adapter exposes a contract gap.

## Execution plan

- [x] **Step 0 - process layer.** Establish all-caps docs for protocol,
      development, integration, documentation, runbook, and bench checklist.
- [x] **Step 1 - supervisor skeleton.**
  *READ / INFER / DE-RISK first; then implement and verify.*
  - [x] Create a small Python app entrypoint under `networked_sensors/`.
  - [x] Define the merged sample schema and source adapter interface.
  - [x] Add `protocol_map.py` with a generated/checkable visual protocol graph for
    the actual skeleton topology.
  - [x] Keep dependencies minimal; use stdlib where practical.
  - [x] Verification: imports, simulated sample generation, and protocol check.
- [x] **Step 2 - simulation scenario hardening.**
  - [x] Add CLI-selectable simulation scenarios, including stale/disconnected source
    cases for dashboard and recorder development.
  - [x] Keep `SimulatedEsp32Source` at 10 Hz and `SimulatedDxmr90Source` at 1 Hz,
    but make flow bursts and source failure modes configurable.
  - [x] Verification: no-hardware smoke that demonstrates healthy and stale source
    states without changing the merged sample schema.
- [x] **Step 3 - local dashboard and API.**
  - [x] Serve `localhost` dashboard from the supervisor.
  - [x] Expose live merged data via SSE, plus JSON state/latest/history
    endpoints.
  - [x] Include cards, plots, metadata form, run state, and simulated solenoid
    controls.
  - [x] Verification: localhost HTML/API/SSE smoke in simulation mode.
- [x] **Step 4 - recorder/exporter.**
  - [x] Write source-scoped fresh update logs and merged CSV to disk.
  - [x] Preserve existing metadata fields and computed summary fields.
  - [x] Add source mode, source health, and staleness fields to merged export.
  - [x] Verification: start/stop/export in simulation mode.
- [x] **Step 5 - real DXMR90 adapter.**
  - [x] Reuse `read_dxmr90_modbus.py` functions instead of shelling out.
  - [x] Read the two live SICK IO-Link process windows directly at 10 Hz;
    retain the ScriptBasic republished block as a diagnostic fallback.
  - [x] Verification: hardware heartbeat/register smoke and sustained 10 Hz
    direct-source smoke on the DXMR90 network.
- [x] **Step 5A - unified simulated Yún stepper source.**
  - Add the stepper as a third supervisor source rather than a separate app.
  - Expose strict positive-distance-magnitude move, stop, and status endpoints;
    derive direction from D5 at command time.
  - Put motion state and `stepper_raw.csv` on the same run timeline as ESP32
    and DXMR90 measurements.
  - Verification: unit tests plus localhost move/status/recording smoke; this
    layer never contacts the real Yún.
- [ ] **Step 5B - read-only physical Yún diagnostics over USB.**
  - [x] Add compact firmware status for D4/D5, D6/D8, latches, blocking reason,
    effective speed, and sequence without changing manual motion logic.
  - [x] Add a no-dependency `UsbStepperSource`, `--stepper-source usb`, port and
    baud configuration, and shared webpage diagnostics.
  - [x] Keep Move/Stop disabled and make missing/unplugged USB source-scoped.
  - [x] Verify parsing and transport with unit tests and a pseudo-terminal.
  - [x] Compile for the Yún with a temporary official toolchain: 42% flash and
    16% RAM.
  - [x] Upload with verification after explicit safe-state confirmation and
    receive live status heartbeats on the localhost webpage.
  - [ ] Complete deliberate D4/D5/D6/D8 transition and USB-unplug browser smoke.
- [ ] **Step 5C - bounded manual speed tuning over USB.**
  - [x] Add a D4-off-only `V1 S25..2520` firmware command without remote
    start/stop/direction authority.
  - [x] Report configured speed separately from effective speed.
  - [x] Add `/api/stepper/speed` and an Apply Manual Speed action while keeping
    USB Move/Stop disabled.
  - [x] Verify validation and wire output through pseudo-terminal tests; compile
    the Yún target at 48% flash and 18% RAM.
  - [x] Upload under the motor-safe procedure and verify a live D4-off 3.0 mm/s
    configured-speed echo with zero effective motion.
  - [x] Make the browser action remain clickable for explicit validation errors,
    preserve its result message, and require a fresh Yún configured-speed echo
    before reporting success; verify the live 10.0 mm/s interface path.
  - [x] Replace the provisional 100 pulses/mm conversion with the documented
    0.00396875 mm/pulse at the photographed 200-pulse/rev driver setting;
    compile/upload and verify stopped default `csps: 378`.
  - [ ] Complete staged 1.5/3.0/5.0 mm/s motion-away checks.
- [x] **Step 5D - D4-off electrical direction mapping.**
  - [x] Add `V1 D0|1`, mapping status/capability, the guarded USB adapter/API,
    and the page control without replacing D5 authority.
  - [x] Verify normal/inverted logical direction, exact serial bytes, strict
    input, and dashboard-runtime forwarding in 18 desktop tests.
  - [x] Compile the Yún target at 49% flash and 18% RAM.
  - [x] Upload under the motor-safe procedure, fix the live selector-render race,
    and verify an Inverted echo with D4 OFF and zero effective speed.
  - [x] Replace the two-choice dropdown plus Apply action with one immediate
    Normal/Inverted toggle that waits for the Yún echo and rolls back on error.
  - [x] Confirm one physical endpoint: with Normal mapping and D5 Reverse, D8
    closes LOW at the negative mechanical end while D6 remains clear.
  - [x] Confirm D6 at the opposite Forward/positive mechanical end and record
    the external-DRO limit-to-limit span as 137.18 mm.
- [ ] **Step 5E - dual-mode relative USB motion and optional D8-limit seek.**
  - [x] Preserve the original D4/D5 continuous behavior as explicit Local
    Velocity mode and boot into that stopped, backward-compatible mode.
  - [x] Add Web Position mode: D4 arms/aborts, D5 selects the direction for a
    positive travel magnitude, and changing either during motion cannot silently
    reverse it.
  - [x] Add optional fixed-speed D8-limit seek, fixed 5 mm/s² acceleration,
    relative distance/positive speed, and immediate Stop. Do not require Home or
    use an absolute software envelope; D6/D8 are the travel safety inputs.
  - [x] Remove operator-adjustable acceleration from the shared webpage/API;
    add immediate mode selection and optional Move to D8 Limit.
  - [x] Verify 22 desktop tests and compile the limit-switch-only Yún target at
    63% flash and 27% global RAM.
  - [x] Upload the limit-switch-only authorization revision with verification;
    confirm stopped Local Velocity, D4 OFF, D6/D8 clear, suppressed position
    fields, zero effective speed, and no motion command issued.
  - [ ] Perform deliberate D8-limit seek, short D5-selected Forward/Reverse
    moves, D4-abort, destination-limit stop, and mid-move D5-abort checks.
- [ ] **Step 5F - latched software E-STOP.**
  - [x] Add a firmware latch and `V1 E1`/`V1 E0` commands that stop both Local
    Velocity and Web Position; require D4 OFF for reset.
  - [x] Add simulator/USB capability and latch state, dashboard API methods,
    fresh-device acknowledgement, and a prominent top-of-page stop/reset UI.
  - [x] Disable motion controls while latched and distinguish this software path
    from a hardwired safety-rated E-stop that removes hazardous energy.
  - [x] Verify 26 stepper tests, 32 combined desktop tests, and compile the Yún
    target at 65% flash and 28% global RAM.
  - [x] Upload with flash readback verification and confirm stopped-state latch
    plus D4-off reset through the live dashboard/API.
  - [x] Dispatch E-STOP outside the shared source-poll lock so an unreachable
    DXMR90 cannot delay the Yún write; stopped live response improved from
    1.26 s to 0.041 s with DXMR90 still disconnected.
  - [ ] Deliberately prove stopping from both control modes, D4-on reset
    rejection, disconnect persistence, and moving latency before claiming the gate.
- [ ] **Step 5G - Yún Linux/network bridge.**
  - [x] Add non-blocking ATmega Serial1 command input and bounded status/ack
    output without placing HTTP or Bridge transfers in the stepping loop.
  - [x] Add the Python 2/3 `yun_stepper_bridge.py` AR9331 service, validated V1
    relay, `/v1/status`, `/v1/health`, and `/v1/command`.
  - [x] Add explicit firmware USB/network mutation ownership while retaining
    transport-independent Stop and software E-STOP.
  - [x] Add `NetworkStepperSource`, `--stepper-url`, `--stepper-timeout`,
    background polling, fresh physical acknowledgements, and source-scoped
    failures.
  - [x] Verify seven network-specific UART/HTTP/adapter/runtime tests and compile
    the Yún target at 72% flash and 54% RAM.
  - [x] Upload the T6 image and verify stopped USB status with D4 OFF, limits
    clear, E-STOP clear, and owner none.
  - [x] Install the service manually on the physical Yún, handle LEDEYun
    `askconsole` ownership reversibly, and verify LAN HTTP health plus advancing
    stopped ATmega status.
  - [x] Redeploy the normal-nonblocking-`EAGAIN` correction and verify an
    expected firmware rejection traverses HTTP/UART in both directions.
  - [x] Add one-time key/deployment/Wi-Fi provisioning and a one-command LAN
    dashboard wrapper; enable the bridge at boot and verify a Linux reboot
    returns synchronized stopped status without an SSH login.
  - [ ] Verify ownership and E-STOP parity, then measure moving jitter and stop
    latency before claiming the motion-qualification gate.
- [ ] **Step 6 - real ESP32 adapter.**
  - [x] Consume strict version-2 ESP32 SSE samples and the solenoid command
    endpoint in a background adapter so network reads do not block the merge
    cadence.
  - [x] Add `--esp32-url`/`--esp32-timeout`, source health/error reporting,
    real-source dashboard controls, and loopback HTTP/SSE contract tests.
  - [x] Require complete per-sample pressure, flow, clamped sensor voltage, and
    four solenoid states; reject older or incomplete readings.
  - [x] Verification: 6 firmware-layout/adapter/parser/CLI/dashboard-runtime
    tests against a local server reproducing the v2 contract; 28 combined tests.
  - [ ] Verification: physical live stream smoke and safe solenoid toggle test.
- [ ] **Step 7 - headless ESP32 firmware.**
  - [x] Archive the former self-hosted-dashboard sketch under `legacy/`.
  - [x] Make the primary sketch an I/O-only service with complete version-2
    samples and the solenoid endpoint; laptop `dashboard.py` is the sole UI,
    logger, metadata owner, and CSV exporter for the current workflow.
  - [x] Compile for `esp32:esp32:adafruit_feather_esp32s3_nopsram`: 52% flash,
    24% global RAM.
  - [x] Extend the active-low output template to Solenoid 4 on GPIO 10; keep all
    four outputs OFF at boot and expose the fourth laptop control/state field.
  - [ ] Flash the physical ESP32 and verify sustained v2 telemetry plus one
    deliberately safe solenoid toggle through the laptop dashboard.
- [ ] **Step 8 - full bench handoff.**
  - Run simulator, ESP32-only, DXMR90-only, and full hardware verification tiers.
  - Update `RUNBOOK.md`, `PROTOCOL.md`, and `TESTBENCH_CHECKLIST.md` with the
    actual commands and any hardware findings.

## Protocol update rule

Any task that changes how something is run, configured, selected, guarded,
produced, or consumed must check `PROTOCOL.md` and probably regenerate it with
`protocol_map.py`. Run results, hardware observations, and narrative findings
belong in the relevant `*-PROCEDURE.md` file unless they alter the runnable
protocol.

## Acceptance definition

The supervisor is useful when a user can run it with no sensors connected,
start a simulated test, see live pressure/flow/DXMR90 values, stop the test, and
receive a merged CSV whose shape will also work for real hardware.

The supervisor is bench-ready when both real sources can be enabled, stale or
missing source data is obvious in the UI/export, and the ESP32 no longer owns
the primary recording buffer.
