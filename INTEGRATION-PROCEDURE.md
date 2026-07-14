# INTEGRATION-PROCEDURE - sensor/source integration

Meta-history of integrating the source arms into the supervisor. This file
records the evidence and decisions behind each integration step.

## Rules translated from dpc-flight

- **Arms are sources, not methods.** Each physical or simulated source lands as
  an adapter with the same schema contract.
- **Performance is not an admission criterion.** If a sensor is part of the
  bench, it integrates; source quality is reported through health/staleness and
  calibration fields.
- **Projection is allowed.** The archived ESP32 embedded dashboard was an
  intermediate architecture; preserve relevant hardware facts while projecting
  the current workflow onto the laptop supervisor.
- **Adapters must be simulatable.** Every real source should have a simulator
  implementing the same output shape.

## Step I0 - establish integration task

**Direction given:** after identifying the laptop supervisor as the cleanest
architecture, create the all-caps document set and translate the `dpc-flight`
integration practice to this system.

**Read evidence:**

- The ESP32 sketch already exposes useful endpoints but also owns UI, plotting,
  recording state, and CSV buffering.
- The DXMR90 reader already contains a clean metric map, Modbus client, decode
  logic, and JSON/CSV/table emitters.
- The handoff notes explicitly recommend offloading the web UI to the laptop for
  longer runs and easier integration with other data sources.

**Inferred integration shape:** the laptop supervisor is the integration point.
The ESP32 and DXMR90 become source arms; simulation arms land first so the
operator workflow can be verified without hardware.

**Executed:** wrote `INTEGRATION-TASKS.md` and this chronicle. No source adapter
code has landed yet.

## Step I1/I2 - common schema and simulated sources

**Direction given:** execute Step 1 of development, which includes the
integration base case: common schema, simulated ESP32 source, simulated DXMR90
source, and merge/staleness fields.

**De-risked:** the simulated DXMR90 source imports the existing metric map rather
than inventing names; the simulated ESP32 source carries the richer legacy CSV
shape while the protocol still records the real SSE limitation.

**Executed:** landed `SourceReading`, `SourceAdapter`, `SimulatedEsp32Source`,
`SimulatedDxmr90Source`, `RealEsp32Source`/`RealDxmr90Source` placeholders, and
`SourceMerger`.

**Verified:** `python3 networked_sensors/supervisor.py --samples 12` runs with
no hardware and emits merged JSONL with source modes, connected flags, and age
fields. `I1` and `I2` are dry; real DXMR90/ESP32 adapters remain pending.

## Step I2a - simulated source failure modes

**Direction given:** harden the simulation layer so dashboard/recorder work can
exercise stale and missing source states without hardware.

**Executed:** added scenario-controlled source dropout and missing-source
behavior for `healthy`, `esp32_stale`, `dxmr90_stale`, `dxmr90_missing`, and
`all_stale`.

**Verified:** stale scenarios age out through existing `*_age_ms` fields and
flip `*_connected` false; `dxmr90_missing` preserves expected DXMR90 keys with
null values from the first sample. The source arms remain dry and schema-stable.

## Step I2b - simulated dashboard control path

**Direction given:** land the local dashboard/API before real hardware adapters.

**Executed:** `dashboard.py` now consumes the same simulated source/merge layer
as the JSONL supervisor and exposes simulated solenoid toggles through
`/api/solenoid/toggle?n=0..2`.

**Verified:** localhost smoke in `dxmr90_missing` mode confirmed the dashboard
can render missing Modbus state while still exercising the ESP32 simulated
control path. Real ESP32 command parity remains Step I4/Step 6.

## Step I2c - simulated recorder/export parity

**Direction given:** add disk-backed recording/export before real hardware
adapters.

**Executed:** `recorder.py` now writes one run directory per recording with a
merged CSV, ESP32/DXMR90 source CSVs, metadata JSON, summary JSON, and export
CSV. `dashboard.py` owns the start/stop/export endpoints.

**Verified:** simulated dashboard start/stop/export produced source-scoped fresh
update logs and a merged table containing source mode, source connected flags,
and source age fields. Real-source parity remains pending until the DXMR90 and
ESP32 adapters land.

## Step I3 - direct SICK/DXMR90 source dried at 10 Hz

**Direction given:** expose actual SICK measurements to the browser at 10 Hz.

**Read/infer:** the existing real adapter read ScriptBasic output registers that
updated at roughly 1 Hz. The DXM also exposes the underlying IO-Link process
windows at `1002-1017` and `2002-2017`; those are the correct integration arm
for live acquisition.

**Executed:** the real adapter now reads both process windows directly, decodes
and converts them to the established merged schema, and emits fresh source rows
at a configurable 10 Hz default. `republished` remains a selectable diagnostic
path.

**Verified:** the live DXMR90 completed 20/20 direct source polls in 2.000
seconds. The merged schema remained stable and gained bar pressure fields for
unit-consistent SICK charting. I3 is dry; full source parity still awaits I4.

## Step I3a - simulated Yún stepper dried into the shared supervisor

**Direction given:** make web-controlled stepper work alongside the locally
hosted test-bench/flow-management supervisor, using the existing page and run
recorder rather than a separate application.

**De-risked:** the first adapter is simulation-only. It models signed relative
distance, speed, acceleration, local enable, D6/D8 active and latched limits,
stop, command identity, and commanded open-loop position. The provisional
100 mm, 10 mm/s, and 50 mm/s² maxima are contract-development bounds, not
hardware authorization.

**Executed:** added `SimulatedStepperSource` as the third `SourceMerger` arm;
added stepper stale/missing scenarios; added GET status and POST move/stop
endpoints; added a motion panel to the existing dashboard; and made the generic
recorder write `stepper_raw.csv` beside ESP32 and DXMR90 artifacts.

**Verified:** eight unit tests passed. A localhost smoke accepted command
`smoke-2`, advanced to exactly 1.5 mm commanded open-loop position, reported
`completed`, and finalized one shared run with 98 merged rows and 97 stepper
source rows. No real Yún request or motor command occurred.

**Remaining boundary:** browser smoke still needs explicit simulated D6/D8 and
stale/missing exercises. Firmware, Bridge transport, and the real laptop
adapter remain T5-T7/I3c and are gated by the unresolved hardware envelope.

## Step I3b - USB diagnostic arm implemented read-only

**Direction given:** make physical switch/motion-decision information visible
on the existing laptop-hosted page without requiring the laptop to join the
Yún's LAN.

**Executed:** the firmware now has a compact read-only status producer and the
laptop has `UsbStepperSource` as a third-source transport. Human startup lines
are ignored, compact version-1 JSON is validated and expanded into the stable
`stepper_*` schema, and port failure or unplugging remains source-scoped.

**Ownership boundary:** this arm cannot issue Move or Stop yet and reports
`stepper_command_capable=false`, owner `manual_switches`. USB/network competing
ownership is a T5-T7 requirement, not implied by this diagnostic implementation.

**Verified:** parser and pseudo-terminal tests passed, and the Yún target
compiled at 42% flash and 16% RAM using an official temporary toolchain. The
physical producer was then uploaded twice with verification after safe-state
confirmation: first to establish the live path, then to correct limit-reason
precedence found by that live path. The localhost source is connected and
reports D4/D5, D6/D8, `positive_limit`, and an advancing sequence. Deliberate
switch transitions and unplug behavior remain before this arm is fully dry.

## Step I3b2 - manual-speed USB command integrated

**Direction given:** make physical motion fast enough to diagnose direction and
travel without waiting for the bounded-distance engine.

**Executed:** the same USB arm now has a `set_speed` write path and a distinct
capability flag. New firmware advertises `csps`; old T4A status remains readable
but cannot enable the speed button. The shared page sends `speed_mm_s` through
`/api/stepper/speed`, while USB Move/Stop remains rejected.

**Ownership boundary:** this is parameter authority, not motion authority. D4
must be OFF for a change and remains the only manual start/stop; D5 remains the
direction selector. The 0.1..10.0 mm/s bound is provisional until staged bench
tests establish the physical envelope.

**Verified:** source validation, exact serial bytes, and fresh configured-speed
acknowledgement pass on a pseudo-terminal; all 18 stepper tests pass, and the
Yún compile/upload passes at 48% flash/18% RAM. The live adapter advertised
speed capability and echoed 3.0 mm/s with D4 OFF and zero effective motion. A
later interface repair preserved operator feedback, waited for the Yún echo,
and confirmed the full localhost-to-Yún path at 10.0 mm/s with D4 still OFF.
Physical staged-speed tests remain pending.

## Step I3b3 - electrical direction mapping integrated

**Direction given:** expose direction reversal on the same localhost page for
physical sign calibration.

**Executed:** T4C extends the USB arm with a D4-off Normal/Inverted mapping and
capability. The adapter converts electrical signed step rate back into logical
travel using the reported mapping, so D6/D8 semantics do not reverse merely
because the DIR output polarity does.

**Ownership boundary:** D5 remains the logical direction command and D4 remains
the motion enable. The page can calibrate electrical polarity only while
stopped; it cannot create or reverse active motion.

**Verified so far:** all 18 tests and the 49%-flash/18%-RAM Yún compile/upload
pass. The live adapter reports direction capability and Normal mapping with D4
OFF/zero effective motion. A brief physical Reverse-away-from-D6 comparison
remains.

**UI isolation finding:** direct API mapping succeeded while the browser still
showed Normal, identifying a client render race rather than transport/firmware
failure. Holding dirty selector state until the firmware echo fixed it; live
status now reports Inverted while stopped. The later UI refinement replaces the
two-option selector plus Apply button with one immediate Normal/Inverted toggle;
the API now waits for a fresh Yún echo, and the toggle rolls back visibly if the
request is rejected.

**First physical endpoint confirmed:** after installing the actuator's required
lead-screw anti-rotation/clutch restraint, the mechanism reached an endpoint
with Normal electrical mapping, D5 Reverse/LOW, D8 closed/LOW and latched, and
D6 clear/HIGH. This confirms D8 as the Reverse/negative mechanical endpoint.
D6 remains the assigned Forward/positive protection input, but its placement at
the opposite mechanical endpoint still requires a deliberate confirmation.
The calibration was added as a detailed firmware source note; the unchanged
14,294-byte/484-byte build was uploaded with verification while D4 was OFF, and
the prior stopped 3.0 mm/s setpoint was restored and echoed afterward.

**Opposite endpoint and stroke confirmed:** at the other mechanical end, live
status showed Normal mapping, D5 Forward/HIGH, D6 closed/LOW and latched, and D8
clear/HIGH. An independent DRO measured 137.18 mm between the two magnetic-limit
trip positions. This completes the physical D6/D8 and Normal-mapping pairing.
The span is recorded for the future bounded-distance engine, but it is not an
absolute position or a `STEPS_PER_MM` calibration without homing and a known
pulse-count-versus-DRO displacement measurement.

## Step I3b4 - dual-mode USB position control integrated

**Direction given:** keep the useful local D5 behavior while adding optional
D8-limit seek and relative-distance control to the same supervisor page.

**Executed:** the USB source now exposes one explicit control-mode field and
capabilities for mode, optional D8-limit seek, move, and Stop. Local Velocity retains D4 run/stop
and D5 direction. Web Position changes D4 to arm/immediate-abort; the operator
enters a positive relative travel magnitude and D5 selects Forward or Reverse.
The supervisor resolves a signed internal delta, the adapter and firmware
independently re-check D5, and the firmware aborts if D4 or D5 changes during
motion. The same page switches mode, can seek D8, and issues bounded relative
moves without exposing or consulting open-loop absolute position. Operator
acceleration was removed; the firmware owns a fixed provisional 5 mm/s² value.

**Boot guard:** D4 must be observed OFF after every ATmega reset before either
mode can move. Leaving D4 ON through reset reports `boot_disarmed`; it cannot
cause the Local Velocity fallback to begin stepping immediately.

**Safety boundary:** D4 arms/aborts and D6/D8 are the directional travel stops.
Home/reference state and an accumulated open-loop coordinate do not authorize
motion. The measured 137.18 mm stroke caps one relative command and the D8
search distance only. At that stage the 100 steps/mm quantity conversion remained provisional
until a counted STEP-pulse displacement is compared with the DRO.

**Verified so far:** 22 unit/pseudo-terminal/runtime tests pass, including
legacy status compatibility and exact `V1 M`, `V1 H`, `V1 G`, and `V1 X`
traffic. The limit-switch-only revision builds at 63% flash and 27% global RAM
and was uploaded with verification. The restarted USB dashboard reported
stopped Local Velocity, D4 OFF, D5 Forward, D6/D8 clear, boot armed, null
position/target/remaining fields, and zero effective speed. No motion command
was sent during this smoke. D8-limit seek, D5-selected Forward/Reverse moves,
D4 abort, destination-limit stop, and mid-move D5-abort remain the integration
gate.

**Operator-direction correction:** the public move form/API now requires a
positive travel magnitude and snapshots D5 for direction. The signed `V1 G`
delta remains below that boundary. A pseudo-terminal check confirms 2.5 mm with
D5 Reverse is serialized as `V1 G-250,200,1`; negative operator input is rejected
without a write. This changes no ATmega bytes and required no firmware upload.

## Step I4 - real ESP32 source integrated in software

**Direction given:** connect the existing ESP32 measurement/control surface to
the laptop supervisor instead of continuing to show simulated ESP32 data.

**Executed:** `RealEsp32Source` now reads the firmware's named SSE events in a
background thread, reconnects after transport failure, maps `p[]`/`f[]` into
the shared ESP32 schema, joins the separate `sol` event, and forwards the
existing solenoid-toggle POST. The source is selected with
`--esp32-source real --esp32-url URL`; `--esp32-timeout` bounds connection,
stream-read, and command waits. It participates in the same merge, dashboard,
recorder, health, age, and error paths as simulation. Because live readings do
not carry raw volts, those fields remain explicitly null.

**Superseded by I5:** this records the intermediate compatibility adapter before
the strict headless v1 decision.

**Verified so far:** a loopback server reproducing `/events` and
`/solenoid/toggle` passes five adapter/parser/CLI/runtime tests. The full combined
desktop suite is 27 tests. Physical network reachability, sustained ESP32 SSE,
a safe real relay toggle, and merged recording/export remain the drying gate;
no firmware cleanup is justified until those checks expose a concrete gap.

## Step I5 - headless ESP32 contract integrated

**Decision:** the current laptop dashboard and the archived ESP32-hosted page
are independent applications. Backward compatibility with the old webpage is
irrelevant, and compatibility with its partial telemetry would hide a firmware
deployment mistake. The current real source therefore requires protocol v1.

**Integrated contract:** every 10 Hz `reading` carries version, sample time,
three pressures, three flows, both clamped sensor-voltage arrays, and three solenoid
states. A separate `sol` event remains an immediate state-change notification;
it is not required to complete a sample. The primary firmware owns only analog
I/O, relay I/O, telemetry, and the toggle API. Laptop `dashboard.py` owns the
webpage, merged logging, metadata, plots, and export.

**Verified so far:** six ESP32 contract/layout tests and 28 combined desktop
tests pass. The headless Feather ESP32-S3 No PSRAM target compiles at 52% flash
and 24% global RAM. Physical flash, network reachability, sustained stream, a
safe relay toggle, and merged recording/export remain the drying gate.

## Step I5A - fourth solenoid integrated

The ESP32 source arm now has four relay states and four commands end to end.
Protocol version 2 requires `sol[]` to contain exactly four booleans; index 3
maps to primary-firmware GPIO 10 and merged field `esp32_sol4`. Simulation,
real SSE decoding, immediate state events, dashboard controls, and source CSV
rows share that contract. The archived three-output firmware is intentionally
unchanged and incompatible with the strict current adapter.

Six loopback/layout tests, 28 combined desktop tests, and the Feather ESP32-S3
No PSRAM compile pass. The source arm remains wet until the fourth relay/coil is
wired with its flyback protection, the v2 image is flashed, all outputs are
observed OFF at boot, and channel 4 is toggled safely through the laptop page.

## Step I3b5 - software E-STOP integrated in source

The simulator and USB Yún arms now expose `stepper_estop_capable` and
`stepper_estop_latched` in the same merged schema. The USB arm maps compact
field `e`, state 9, and exact commands `V1 E1`/`V1 E0`; it permits the stop in
either control mode but rejects reset until raw D4 is HIGH. Dashboard runtime
methods wait for a fresh status sequence confirming latch/reset before reporting
success, and the page disables motion controls while latched.

Twenty-six stepper tests, 32 combined tests, and the 65%-flash/28%-RAM Yún
compile pass. This source arm remains wet for the new capability until the
firmware is uploaded and physical stops are demonstrated from both modes. The
65% image was subsequently uploaded with readback verification. A fresh USB
heartbeat advertised the capability while stopped, and live API commands
confirmed latch/state 9/zero motion and D4-off reset. Moving-stop, D4-on reset,
disconnect, and moving-latency checks still keep this arm wet. During the
combined DXMR90/Yún run, E-STOP dispatch was moved ahead of the shared poll lock
so a failing Modbus source cannot delay the Yún write. Stopped live response
with DXMR90 unreachable improved from 1.26 s to 0.041 s. The software transport
is explicitly outside the safety-rated energy-isolation boundary.

## Step I3c - Yún network source software landing

The network source now shares the USB adapter's validation and command methods
while replacing the file descriptor with a background, no-proxy HTTP status
poller and single-attempt command POST. The Yún Linux service serializes
commands onto `/dev/ttyATH0`, and the ATmega returns explicit acceptance or
rejection before the shared dashboard waits for a fresh physical status. The
compact owner field distinguishes none, USB, and network; firmware—not either
laptop adapter—arbitrates competing mutation.

Seven network-specific tests cover the full emulated chain from HTTP through a
pseudo-terminal and back, plus rejection, timeout, CLI/factory selection,
owner decoding, nonblocking UART `EAGAIN`, and fresh dashboard E-STOP status. The firmware compiles at
72% flash/54% RAM, is uploaded, and emits a stopped owner-none USB heartbeat.
This arm remains wet until competing-owner, E-STOP, enabled-service
restart/disconnect, limit, motion, and timing parity checks pass.

After an acknowledgement timeout, the relay refuses further commands until it
is restarted; this is an intentional fail-closed command-correlation boundary.

The service is now manually installed on the physical Yún. The first probe
exposed both normal nonblocking UART `EAGAIN` and LEDEYun `askconsole`
competing for `/dev/ttyATH0`; both are handled explicitly and the console
configuration is restored on service stop. Over AsteraMesh, health is fresh,
error-free, and synchronized; stopped status advances with real D4/D5/D6/D8
levels, clear limits/E-STOP, and owner none. An expected Local-Velocity Stop
rejection completed the LAN HTTP/UART/firmware acknowledgement round trip
without changing motion or ownership.

The cold-start integration now has two repository entrypoints.
`provision_yun.sh` installs a dedicated Dropbear key, deploys/enables the
service, checks health/status, and optionally commits a target station network;
`run_lan_dashboard.sh` starts the ordinary laptop network-source configuration.
After enabling the service, a physical Linux reboot returned Wi-Fi and
synchronized stopped status without an SSH login, and passwordless maintenance
access passed. `GL-MT3000-b3a` is committed for the next power cycle. Target-LAN
association and all motion parity remain pending.

## Step I3d - physical pulse calibration aligned end to end

The supplied `LN176S-E06008-210-S-200` datasheet establishes a 0.79375 mm lead,
1.8-degree full steps, and 0.00396875 mm linear travel per full step. A new
driver close-up shows SW5-SW8 all ON, which the DM542T table maps to 200 input
pulses/revolution; the lone opposite switch is SW4, the independent standstill
current selector. Thus each PUL pulse is one datasheet full step and the shared
conversion is 251.96850394 pulses/mm.

Firmware and both real laptop adapters now share that factor. Default 1.5 mm/s
is 378 pulses/s, the 10 mm/s ceiling is 2520 pulses/s, the 137.18 mm quantity
bound is 34565 pulses, and fixed 5 mm/s² is approximately 1260 pulses/s². The
target compiled at 20,782 bytes/72% flash and 1,399 bytes/54% RAM, uploaded with
verification, and restarted stopped with D4 OFF, D6 active, zero motion, and
`csps:378`. DRO travel and high-rate smoothness remain wet physical checks.
