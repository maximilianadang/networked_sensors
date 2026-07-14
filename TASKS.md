# TASKS - Yún Rev2 web-controlled stepper

Focused execution queue for adapting `limit_switch_palas.ino` to the Arduino
Yún Rev2 and commanding relative travel magnitude and speed from the existing
laptop dashboard, with physical D5 selecting direction.

This plan follows the directory's development rules: settle contracts before
code, simulate before requiring hardware, change one layer at a time, verify
each layer before advancing, and update protocol/runbook/checklist documents
when runnable behavior changes.

## Settled decisions

- Board: Arduino Yún Rev2 (ABX00020).
- Motion feedback: open-loop step counting is acceptable.
- Safety: physical limit switches remain authoritative.
- Operator input: positive relative travel distance in mm and positive speed in
  mm/s; physical D5 selects Forward or Reverse.
- Architecture: the laptop dashboard remains the primary full supervisor
  webpage and supports four explicit stepper source modes:
  - `sim`: no-hardware development;
  - `usb`: the laptop hosts `localhost`, talks directly to the Yún ATmega32U4
    over USB Serial, and can retain its normal internet connection;
  - `network`: the laptop reaches the Yún Linux/Bridge API over the trusted
    bench LAN;
  - `off`: preserve the stepper schema while disabling the source.
- The ATmega32U4 owns time-sensitive stepping and local safety inputs in every
  mode. USB and network transports use one command/status contract and must
  never own motion concurrently.
- Existing pin assignments retained where possible:
  - D3 STEP;
  - D2 DIR;
  - D4 local run/enable switch;
  - D5 local direction/manual-mode switch;
  - D6 positive limit switch;
  - D8 negative limit switch (D7 remains reserved for the Yún handshake).
- The page selects one explicit control mode, changed only with D4 OFF and
  motion stopped:
  - `Local Velocity`: D4 runs/stops continuous motion and D5 selects direction;
  - `Web Position`: D4 arms/aborts, while D5 selects the direction of the next
    positive-magnitude move. Changing D5 during motion aborts; it never reverses
    an active move. The optional D8-limit seek requires D5 Reverse.
- Firmware boots stopped in Local Velocity mode and requires D4 to be observed
  OFF once before it can arm; Web Position requires an explicit selection after
  every reset.
- **Current safety decision:** D4 and the directional D6/D8 switches authorize
  and stop motion. Open-loop position, target, and homed state are not safety
  inputs and are not shown on the operator page.
- The dashboard has a latched **software E-STOP** path that inhibits STEP
  output in either control mode until a separate reset with D4 OFF. Because it
  depends on the laptop, USB, firmware, and powered logic, it is an operational
  stop aid—not a hardwired, safety-rated emergency stop or energy isolation.
- The optional **Move to D8 Limit** action approaches D8 at a fixed 1.5 mm/s.
  It is not required before a relative move. The measured 137.18 mm stroke only
  caps one requested travel magnitude and the D8 search distance; it is not an
  absolute software-position envelope.
- Acceleration is fixed in firmware at a provisional 5 mm/s² rather than being
  an operator-adjustable webpage/API field.
- Firmware pulse instrumentation measures AccelStepper's emitted-step counter
  over a 250 ms window. It distinguishes configured/scheduled rate from actual
  D3 pulse attempts, but it is not driver-acceptance or piston-position feedback.
- The standalone sketch now reads both limits, reports raw switch-level changes
  over Serial, and blocks only motion farther into the corresponding end.
- The Yún Linux side is configured as a WPA2 client of `GL-MT3000-b3a` using
  DHCP. The observed bring-up address was `192.168.8.137`; it is not a fixed
  address and must be reserved in the router before software relies on it.

## Ordered tasks

- [ ] **ACTIVE - integrate Yún stepper control into the existing flow-management supervisor.**
  - Treat the stepper as a third supervisor source alongside ESP32 and DXMR90;
    do not create a separate operator webpage or recording system.
  - First add a simulation-backed motion/status contract and supervisor API,
    then add controls and live state to the existing dashboard.
  - Record stepper status in the existing run artifacts so motion, limits, and
    flow measurements share one run timeline.
  - Preserve one status/command shape across `sim`, `usb`, and `network`.
  - Execution order is T2-T4 (contract/simulation/UI), T4A (USB diagnostics),
    T4B (manual speed tuning without remote motion), T4C (D4-off electrical
    direction mapping), T5 (bounded USB motion), T5A (latched software
    E-STOP), T6 (network/Bridge), T7
    (transport parity), then T8-T9 (hardware and acceptance). T1
    hardware-envelope values remain a gate before remotely started motion, but
    do not block diagnostics or D4-off calibration settings.

- [x] **T0 - research and immediate pin correction.**
  - Correct the limit input from unused D7 to the physically wired D6.
  - Download the Yún Rev2 pinout, schematic, Bridge documentation/examples,
    and AccelStepper reference into `documentation/`.
  - Record the 5 V maximum VIN, 20 mA I/O limit, D0/D1 Linux UART, D7
    handshake, and D2/D3 I2C-sharing constraints.
  - Verification: inspect the corrected pin block, validate downloaded file
    types, and check `protocol_map.py --check`.

- [ ] **T1 - freeze the hardware and safety contract.**
  - [x] Identify the driver as a STEPPERONLINE DM542T with a 24 V motor supply,
    5 V control-logic selector, and ENA- left open/default-enabled.
  - Record its final STEP pulse-width and DIR setup-time firmware settings.
  - Record the motor current, microstep configuration, screw lead, usable
    travel, and confirmed `STEPS_PER_MM`.
  - [x] Correct the motor phase grouping at A+/A-/B+/B-; the prior grouping
    produced buzzing/rough attempted motion, and the corrected grouping produced
    smooth manual motion without changing microsteps or STEP pulse width.
  - [x] Assign the positive limit to D6 and the negative limit to D8.
  - [x] Add raw D6/D8 Serial change reporting for switch characterization.
  - [x] Empirically determine whether each piston magnet opens or closes its
    two-wire switch and set each active-level constant accordingly: both are
    open/HIGH away and close/LOW when the magnet is detected.
  - [x] Confirm whether normally-closed fail-safe wiring is available: the
    installed two-wire switches are normally open, so broken-wire detection is
    unavailable without different sensors or interface hardware.
  - [x] Compile for `arduino:avr:yun`, upload over USB, and observe repeatable
    raw D6/D8 transitions with the DM542T motor supply off.
  - [x] Configure the Yún Linux side for `GL-MT3000-b3a`, verify WPA2
    association, DHCP, strong signal, and gateway reachability over USB console,
    reboot Linux, and restore the stepper firmware.
  - [x] Decide local control authority: the explicit Local Velocity/Web Position
    modes give D4 and D5 the settled roles described above.
  - Establish maximum speed, acceleration, and maximum distance per command;
    do not derive a safety envelope from the open-loop counter.
  - **Gate:** do not implement network motion until the driver, both travel
    directions, and local-stop behavior are unambiguous.

- [x] **T2 - define the motion command and status protocol.**
  - Define explicit operations rather than toggles:
    - `move`: request ID, positive relative `distance_mm`, and positive
      `speed_mm_s`; the supervisor snapshots D5 and resolves a signed internal
      delta that the adapter and firmware re-check;
    - `stop`: stop/abort the active command;
    - `emergency_stop`: latch a mode-independent software stop;
    - `reset_emergency_stop`: re-enable commands only while stopped with D4 OFF;
    - `status`: report command, motion, switch, limit, and connection state;
    - `home`: approach D8 at fixed speed and establish the 0 mm coordinate.
  - Define validation and error responses for non-finite values, zero/negative
    speed, overspeed, excessive distance, busy state, disabled local switch,
    active limit, stale Yún connection, and malformed commands.
  - Define counter semantics explicitly: STEP counts implement relative travel
    only; they are not exposed as piston position or used for safety logic.
  - Define limit semantics: a limit blocks motion farther into that limit but
    permits motion away from it.
  - Define restart behavior: boot stopped, discard unfinished commands, require
    a new explicit command.
  - Add the accepted endpoints/configuration/artifacts to `protocol_map.py` and
    regenerate `PROTOCOL.md` when implementation begins.
  - **Gate:** command fields, units, ranges, ownership, and error behavior are
    reviewable before UI or firmware code depends on them.

- [x] **T3 - implement the simulation-first motion/API layer.**
  - Add a simulated stepper adapter/state machine to the laptop supervisor.
  - Preserve the command/status shape intended for the real Yún adapter.
  - Simulate position, target, speed, running/stopped state, local enable,
    positive limit, negative limit, missing source, and stale source.
  - Add supervisor API handlers for simulated `move`, `stop`, and `status`.
  - Add automated tests for unit conversion, sign/direction, range validation,
    limit blocking, movement away from a limit, stop, stale state, and stable
    status shape.
  - **Gate:** all normal and rejected commands can be exercised without the
    motor, driver, or Yún.

- [ ] **T4 - add dashboard motion controls against simulation and USB.**
  - [x] Add distance and speed inputs with visible units and allowed ranges;
    remove operator-adjustable acceleration.
  - Add Move and Stop buttons; do not add a software direction toggle because
    physical D5 selects direction.
  - Add a prominent, one-action software E-STOP near the top of the page and a
    visually separate deliberate reset; never label it as safety-rated.
  - [x] Add immediate Local Velocity/Web Position mode selection and optional
    Move to D8 Limit.
  - Display connection, local-enable, moving/stopped, active command ID, and
    both limit states; do not display open-loop position/target/remaining.
  - Disable Move when input is invalid, the Yún/adapter is stale, local enable
    is off, or a requested direction is blocked by a limit.
  - Require deliberate confirmation for unusually long or fast moves.
  - Verification: browser/API smoke covering D5-selected Forward/Reverse moves,
    stop, both limits, invalid input, and lost-source status in simulation.
  - **Gate:** the operator workflow is stable before real hardware commands are
    enabled.
  - **Progress:** the existing flow-management page now contains positive
    relative-distance magnitude, speed, mode, optional D8-limit move, Move, and
    Stop controls plus live control mode, D4, D5 direction, D6/D8, and command
    state. Open-loop homed/position/target/remaining are absent from the page.
    Acceleration is fixed in firmware and absent from the form.
    The remaining gate is browser smoke for both simulated limits, invalid
    input, and a stale/missing stepper source.

- [ ] **T4A - add supported USB diagnostic mode without changing motion behavior.**
  - Extend the current manual switch-controlled firmware with a compact,
    machine-readable status heartbeat while preserving its existing D4/D5
    movement behavior.
  - Report D4 and D5 raw/interpreted states, D6/D8 raw/active/latched states,
    requested direction, whether STEP output is blocked, an explicit blocked
    reason, scheduled speed, measured emitted STEP rate, and a monotonically
    increasing sequence number.
  - Add `--stepper-source usb` and `--stepper-port` to the laptop supervisor;
    the webpage remains at `localhost` and the laptop does not need the bench
    LAN or Yún Wi-Fi for this mode.
  - Keep Move and Stop disabled until the bounded command engine in T5 exists;
    USB diagnostics in this task are deliberately read-only.
  - Ensure a disconnected USB cable becomes stale/disconnected rather than
    crashing the flow supervisor or hiding ESP32/DXMR90 data.
  - Verification: parser/unit tests, Yún compile without upload, then a
    motor-safe USB live-status smoke after explicit bench readiness.
  - **Gate:** the webpage truthfully explains why current manual motion is or is
    not occurring before USB is allowed to command it.
  - **Progress:** compact firmware JSON, `UsbStepperSource`, dashboard
    diagnostics, missing-port behavior, and pseudo-terminal/parser tests are
    implemented. A temporary official toolchain compiled the sketch for the Yún
    at 42% flash and 16% RAM. Host-level discovery confirms the Yún at
    `/dev/ttyACM0` (the sandbox initially hid host devices). The firmware was
    uploaded with verification and the localhost page receives one-second USB
    heartbeats. The first live state correctly showed D4 disabled, D5 Forward,
    D6 active/latched, D8 clear, and `positive_limit`; deliberate D4/D5/D6/D8
    transition and unplug testing remain open.

- [ ] **ACTIVE - T4B - allow bounded manual speed tuning over USB.**
  - Preserve manual authority: D4 alone starts/stops motion, D5 selects its
    direction, and D6/D8 remain authoritative directional limits.
  - Accept only a positive 0.1..10.0 mm/s speed magnitude and only while D4 is
    OFF; changing the setpoint must never itself emit STEP pulses.
  - Use a short versioned command (`V1 S25..2520` pulses/s) so USB parsing remains
    bounded inside the high-frequency firmware loop.
  - Report configured speed separately from effective signed speed so the page
    can distinguish a stopped 3 mm/s setpoint from actual zero motion.
  - Instrument emitted STEP pulses separately from the requested AccelStepper
    rate so the 5 mm/s qualification measures firmware output instead of
    inferring it from the setpoint or DRO alone.
  - Add a separate `/api/stepper/speed` action and Apply Manual Speed button;
    keep USB Move and Stop disabled until T5.
  - Verification: unit and pseudo-terminal command tests, Yún compile, upload
    only under the motor-safe procedure, confirm the configured-speed echo,
    then test 1.5 -> 3.0 -> 5.0 mm/s in short travel-away intervals while
    observing smoothness, direction, and both limits.
  - **Gate:** no remote start/direction/distance behavior is introduced, and a
    speed request made with D4 ON is rejected at both laptop and firmware.
  - **Progress:** firmware, USB adapter, localhost endpoint/UI, and 16 desktop
    tests are implemented. The 48%-flash/18%-RAM build was uploaded with
    verification under the motor-safe procedure. The live page advertises speed
    capability and echoed a D4-off 3.0 mm/s setpoint while effective speed stayed
    zero. The later 250 ms emitted-STEP instrument compiles at 75% flash/55%
    RAM, is uploaded, and reports stopped `aps:0`; physical 1.5/3.0/5.0 mm/s
    measured-pulse/DRO travel checks remain pending.

- [ ] **ACTIVE - T4C - allow guarded electrical direction mapping.**
  - Add Normal/Inverted mapping to the existing page for correcting the
    logical-to-electrical DIR sign without rewiring motor phases.
  - Accept only `V1 D0` (normal) or `V1 D1` (inverted), only while D4 is OFF;
    changing the mapping must never itself emit STEP pulses.
  - Keep D5 as the logical Forward/Reverse selector and keep D6/D8 tied to
    logical positive/negative travel rather than electrical DIR polarity.
  - Report the mapping and a distinct capability flag. Old firmware remains
    readable but cannot enable the direction-mapping button.
  - Add `/api/stepper/direction-mapping` with strict boolean input and retain
    USB Move/Stop rejection.
  - Verification: normal/inverted decode tests, exact pseudo-terminal bytes,
    D4-on rejection, Yún compile, motor-safe upload, then brief low-speed tests
    proving which mapping makes Reverse travel away from active D6.
  - **Progress:** firmware, adapter, API/UI, and 18 desktop tests are complete;
    the 49%-flash/18%-RAM build was uploaded with verification. A live browser
    race that reset the selected mapping before Apply was fixed; the restarted
    page now reports `inverted` with D4 OFF and zero effective speed. Physical
    Normal/Inverted identification remains pending.

- [ ] **ACTIVE - T5 - refactor Yún firmware into a non-blocking distance engine.**
  - Preserve D2-D6 and D8 assignments and local safety inputs.
  - Replace indefinite-only `runSpeed()` behavior with a motion state machine
    using `setMaxSpeed()`, `setAcceleration()`, `move()`/`moveTo()`, and frequent
    `run()` calls.
  - Convert millimetres to steps in one checked function; prevent overflow and
    clamp values to the T1 limits.
  - Evaluate local enable and both limit inputs every loop iteration.
  - Implement immediate command abort behavior separately from a normal
    decelerating stop.
  - Keep blocking waits, `delay()`, network reads, and long serial prints out of
    the stepping path.
  - Accept the T2 `move` and `stop` commands over the supported USB transport,
    not a disposable harness, and report the same command/status fields used by
    simulation.
  - [x] Add explicit Local Velocity and Web Position modes. In Local Velocity,
    D4 runs/stops and D5 selects direction. In Web Position, D4 arms/aborts and
    D5 selects the next positive-magnitude move direction; a mid-move change
    aborts rather than reverses.
  - Establish exclusive command ownership. Boot has no remote owner; accepting
    USB ownership prevents network ownership until released, timed out safely,
    or reset while stopped.
  - Verification: compile for Arduino Yún Rev2, dry STEP/DIR observation, local
    disable, positive/negative limit checks, D5-selected moves, commanded step count,
    stop, and repeated back-and-forth moves.
  - **Gate:** bounded motion and physical safety work without networking.
  - **Progress:** firmware, USB adapter, shared API/page, fixed-acceleration
    command contract, optional D8-limit seek, immediate Stop, D4 abort, D5
    direction, directional D6/D8 stops, and 10 Hz moving status are implemented.
    Homing and absolute software-envelope gates were removed: STEP counts only
    terminate a requested relative distance. Twenty-two desktop tests pass, and
    the revised Yún build fits at 63% flash/27% global RAM. Verified upload and
    stopped live-status smoke pass with D4 OFF, D6/D8 clear, position fields
    suppressed, and zero motion. Deliberately commanded D8-limit seek,
    short-distance Forward/Reverse, D4-abort, limit-stop, and mid-move D5-abort
    hardware checks remain before this gate is complete.

- [ ] **ACTIVE - T5A - add a latched dashboard software E-STOP.**
  - Add `V1 E1` ahead of normal mode/arming checks so it immediately aborts
    Web Position and suppresses Local Velocity STEP pulses.
  - Keep the latch in ATmega state if the browser or USB connection disappears;
    only `V1 E0` may clear it, and firmware must reject reset until D4 is OFF.
  - Publish explicit capability/latch/state fields and disable all page motion
    controls while latched.
  - Put the red **SOFTWARE E-STOP** at the top of the existing dashboard. Do not
    add a confirmation to stopping; require confirmation for reset.
  - State the boundary everywhere: this path does not remove motor power and is
    not a substitute for a hardwired safety-rated E-stop.
  - Verification: simulated latch/reset, compact-status decoding, exact USB
    bytes, D4-on reset rejection, dashboard runtime/API, full desktop tests,
    Yún compile, then deliberate Local Velocity and Web Position bench stops.
  - **Progress:** firmware, simulator, USB adapter, API, page, and desktop tests
    are implemented. Twenty-six stepper tests and all 32 desktop tests pass;
    the Yún build fits at 65% flash and 28% global RAM. The image was uploaded
    with readback verification, and a D4-off live smoke confirmed latch state 9,
    zero motion, and reset back to stopped. Moving Local Velocity/Web Position
    stop, D4-on reset rejection, disconnect-latch, and moving-stop latency checks
    remain. Priority dispatch reduced the stopped/unreachable-DXMR90 response
    from 1.26 s to 0.041 s without weakening fresh-status acknowledgement.

- [ ] **ACTIVE - T6 - add the Yún Linux motion bridge transport.**
  - Use a small Linux-side service over the Yún internal UART and expose only
    the T2 motion/status contract; do not call blocking Bridge transfers inside
    the stepping loop.
  - Bind the endpoint only on the isolated trusted bench network; it has no
    application authentication and must never be router-forwarded.
  - Queue a compact command for the ATmega instead of performing motion inside
    the HTTP request handler.
  - Remove the official example's 50 ms loop delay; at the calibrated default
    378 pulses/s, a pulse can be due every 2.65 ms.
  - Measure command-processing latency and step-pulse jitter while HTTP/status
    traffic is active.
  - Ensure Linux restart, Bridge failure, malformed requests, duplicate request
    IDs, and client disconnect cannot start or prolong motion unexpectedly.
  - Reuse the USB command/status semantics exactly; Bridge is a second
    transport, not a second motion implementation.
  - Reject network ownership while USB owns motion and reject USB ownership
    while network owns motion.
  - Verification: LAN `move`, `stop`, and `status` smoke plus simultaneous
    step-timing observation.
  - **Gate:** the Yún API is deterministic enough at the approved T1 speed and
    fails stopped when communication is lost.
  - **Progress:** implemented a Python 2/3 AR9331 service on port 8080 that
    owns `/dev/ttyATH0`, validates and relays the existing `V1` grammar, caches
    compact status, and waits for explicit ATmega acceptance/rejection. The
    ATmega polls Serial1 and drains status/ack output only into available UART
    capacity; no Linux/HTTP call runs inside the stepping loop. Mutating USB
    and network commands have explicit firmware ownership with stopped,
    D4-OFF, two-second idle release; Stop and E-STOP remain universal. The
    target compiles at 20,794 bytes/72% flash and 1,399 bytes/54% RAM. It was
    uploaded over `/dev/ttyACM0`; a fresh stopped USB heartbeat confirmed D4/D5
    HIGH, D6/D8 clear, zero motion, E-STOP clear, and owner `none`. The Linux
    service was installed on AsteraMesh at its current DHCP address; health,
    advancing stopped status, reversible `askconsole` ownership, and an
    expected firmware rejection pass over the physical LAN. The bridge is now
    enabled at boot, a Linux reboot returned synchronized status without an SSH
    login, and `provision_yun.sh` plus `run_lan_dashboard.sh` cover one-time
    deployment/Wi-Fi setup and ordinary LAN startup. `GL-MT3000-b3a` is stored
    for the next power cycle. Ownership/E-STOP parity, jitter/latency
    measurement, target-LAN association, and disconnect-during-motion checks
    remain before the gate.

- [ ] **ACTIVE - T7 - harden both laptop real-Yún adapters and transport parity.**
  - Support explicit `--stepper-source usb|network|sim|off` selection plus USB
    port/baud and network host/URL/timeout configuration.
  - Forward validated dashboard commands over the selected transport only.
  - Poll or stream USB/network status into the same schema used by simulation.
  - Expose connection, last-update age, and stale/disconnected state.
  - Never retry a non-idempotent move blindly; use request IDs and reconcile
    status after a timeout.
  - Add fake-transport tests for both adapters: success, rejection, timeout,
    duplicate request, malformed response, disconnect during motion, and
    competing ownership.
  - Verification: USB adapter against a pseudo-terminal and network adapter
    against a stub server before enabling the motor.
  - **Progress:** `NetworkStepperSource`, `--stepper-url`, and
    `--stepper-timeout` are implemented with background 10 Hz status polling,
    proxy bypass, single-attempt command POSTs, explicit HTTP/UART error
    reporting, and the same adapter guards as USB. Six focused network tests
    cover exact UART relay, status/ownership decoding, accepted command,
    firmware rejection, acknowledgement timeout, CLI/factory selection, and a
    fresh dashboard E-STOP status. Full competing-owner/restart/disconnect
    hardware parity remains.

- [ ] **T8 - perform staged hardware bring-up and calibration.**
  - Power the Yún Rev2 with regulated 5 V; retain the motor driver's external
    supply and common logic ground.
  - Test with the load uncoupled or otherwise made safe.
  - Verify that boot, dashboard connection, Linux restart, and laptop disconnect
    never create a step pulse or unexpected movement.
  - Verify local enable and each installed normally-open limit, retaining the
    known fact that a broken wire looks clear rather than fail-safe.
  - Verify direction signs from webpage -> API -> ATmega -> physical travel.
  - Verify the documented 251.96850394 pulses/mm over repeated DRO-measured
    positive/negative moves and refine it if required.
  - Establish demonstrated speed and acceleration limits below stall or missed
    step behavior.
  - Test Stop at minimum and maximum approved speeds.
  - **Gate:** all hardware-only checklist items pass before routine operation.

- [ ] **T9 - complete end-to-end acceptance and handoff.**
  - Run simulation, Yún API without motor power, uncoupled motor, and full
    mechanism verification tiers.
  - Demonstrate webpage commands for representative short/long and
    positive/negative moves at multiple approved speeds.
  - Demonstrate rejection at both limits and successful motion away from each
    limit.
  - Demonstrate local disable, web Stop, stale connection, Linux restart, and
    laptop restart behavior.
  - Update `RUNBOOK.md`, `PROTOCOL.md`, `TESTBENCH_CHECKLIST.md`,
    `DEVELOPMENT-PROCEDURE.md`, `INTEGRATION-PROCEDURE.md`, and this file with
    the actual commands, endpoints, measurements, and findings.
  - Preserve the original switch-only firmware or a documented manual mode as a
    fallback until the web-controlled path is accepted.

- [ ] **T10 - optional standalone Yún webpage.**
  - Only after T9, evaluate a small Yún-hosted status/jog page for cases where no
    laptop supervisor is available.
  - Keep it distinct from the full flow-management supervisor: it need not own
    ESP32/DXMR90 plots, merged recording, metadata, or export.
  - Reuse the T2 API and ownership rules; do not create another motion engine.

## Acceptance definition

This work is complete when the same laptop webpage operates in both supported
real-hardware contexts: `usb` at `localhost` without joining the bench LAN, and
`network` through the Yún Wi-Fi/Bridge API. In either mode an operator can enter
a positive travel distance and speed, select direction with D5, issue one
explicit command, observe truthful
diagnostic and motion status, stop locally or remotely, and have physical limits
prevent travel farther into either end. Repeated moves must meet the measured
open-loop travel tolerance established in T8; competing transports and loss of
laptop, USB, network, Bridge, or Linux must not create a new command or
uncontrolled continued motion.

## Protocol/documentation cadence

- Planning and research alone are procedure material; they do not change
  `PROTOCOL.md`.
- T3 changes the dashboard API and simulation topology, so it must update
  `protocol_map.py`, regenerate `PROTOCOL.md`, and update `RUNBOOK.md`.
- T6/T7 add real device configuration, endpoints, and command consumers, so
  they must repeat the protocol check/regeneration.
- Hardware observations from T8 belong in procedure/checklist documents unless
  they change commands, configuration, guards, artifacts, or consumers.
