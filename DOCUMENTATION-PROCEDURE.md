# DOCUMENTATION-PROCEDURE - networked_sensors

Meta-history of documenting the flow-management test bench. This file records
how documentation decisions are made and how interface claims are kept honest.

## Rules translated from dpc-flight

- **Interface evidence first.** Read source before documenting behavior.
- **No future-tense confusion.** Planned supervisor contracts are useful, but
  they must be labeled planned until code exists.
- **Harvest run findings.** Setup details discovered during simulation or
  hardware smoke runs are documentation inputs.
- **Separate protocol from results.** Runnable topology belongs in
  `PROTOCOL.md`; what happened in a particular run belongs in procedure docs.
- **Layer docs.** `CLAUDE.md` orients, `PROTOCOL.md` maps, `RUNBOOK.md` runs,
  checklists gate hardware, and task/procedure pairs record intent/history.

## Step D0 - establish documentation track

**Direction given:** create all-caps markdown documents under `networked_sensors`
based on the `dpc-flight` examples, and explain how each kind translates.

**Discovery procedure:**

1. Read `dpc-flight` all-caps docs to identify document kinds and practices.
2. Read current `networked_sensors` code/docs to identify real interfaces.
3. Separate existing embedded-dashboard behavior from planned supervisor behavior.
4. Write documentation that makes the next implementation step safer rather than
   claiming it is already complete.

**Executed:** wrote the documentation track and root orientation docs. The
supervisor remains planned; the existing ESP32 and DXMR90 surfaces are the only
current runnable interfaces documented as existing.

## Turn - protocol regeneration boundary

**Direction given:** `protocol_map.py` should arrive as soon as topology can
become confusing, and this project is already there by virtue of source
integration. `PROTOCOL.md` changes when run/config/select/guard/produce/consume
behavior changes; procedure docs receive run results.

**Applied:** documentation rules now make `protocol_map.py` part of the Step 1
documentation acceptance surface and record the protocol/results boundary.

## Step D1/D2 - skeleton and simulation smoke documented

**Direction given:** update docs in the same step as the supervisor skeleton.

**Interface evidence:** `supervisor.py` now provides the no-hardware smoke
entrypoint; `supervisor_core.py` owns the source/merge schema; `protocol_map.py`
generates and checks the visual protocol map.

**Executed:** regenerated `PROTOCOL.md`, updated `RUNBOOK.md` with the real
smoke/check commands, marked development/integration/documentation task items
complete, and recorded the verification commands in the procedure docs.

**Verify-pass finding harvested:** the 12-sample smoke demonstrates the intended
rate mismatch behavior: ESP32 samples are fresh at each 10 Hz merged row, while
DXMR90 values are held between 1 Hz updates and expose increasing/resetting age
fields.

## Step D2 - scenario behavior documented

**Direction given:** document the simulation scenario hardening in the same step
as the code and protocol changes.

**Executed:** regenerated `PROTOCOL.md` from `protocol_map.py`, updated
`RUNBOOK.md` with healthy/stale/missing scenario commands, marked Step 2
complete in `DEVELOPMENT-TASKS.md`, and recorded scenario landing in the
integration docs.

**Verify-pass finding harvested:** `dxmr90_missing` keeps expected metric keys
present with JSON `null` values, which should simplify later dashboard card and
CSV recorder code.

## Step D3 - dashboard/API documented

**Direction given:** keep documentation moving with the implemented localhost
dashboard/API.

**Interface evidence:** `dashboard.py` now serves the simulation-backed operator
surface at `http://127.0.0.1:8000/` by default and exposes JSON/SSE endpoints
under `/api/*`.

**Applied:** regenerated `PROTOCOL.md` from `protocol_map.py`, updated
`RUNBOOK.md` with the dashboard command and API surface, marked Step 3 complete
in `DEVELOPMENT-TASKS.md`, and marked this documentation task complete.

**Boundary recorded:** dashboard run state, metadata, recent history, and
simulated solenoid controls are in memory only. Persisted recorder/export
artifacts remain Step 4 work.

**Verify-pass finding harvested:** localhost HTML/API/SSE smoke passed in
`dxmr90_missing` simulation mode, including metadata save, run start/stop, and a
simulated solenoid toggle.

## Step D4 - recorder/exporter documented

**Direction given:** document the disk-backed recorder/exporter in the same task
as the code and protocol changes.

**Interface evidence:** `dashboard.py` now accepts `--record-dir`, start/stop
creates durable run artifacts through `recorder.py`, and export/listing
endpoints exist under `/api/recordings` and `/api/export*`.

**Applied:** regenerated `PROTOCOL.md` from `protocol_map.py`, updated
`RUNBOOK.md` with run artifact files and export endpoints, marked Step 4
complete in `DEVELOPMENT-TASKS.md`, and marked this documentation task complete.

**Boundary recorded:** source CSVs are fresh source-update logs from the current
simulated adapters. Lower-level transport diagnostics remain future real-adapter
work.

**Verify-pass finding harvested:** simulated start/stop/export writes merged
CSV, ESP32/DXMR90 source CSVs, metadata JSON, summary JSON, and export CSV. The
merged CSV includes source health/staleness fields.

## Step D6 - Arduino Yún stepper feasibility documentation

**Direction given:** obtain online documentation for adapting
`limit_switch_palas.ino` to an Arduino Yún, preserve it under a local
documentation folder, and determine whether the existing web interface can
control stepper speed and travel distance.

**Interface evidence:** the board was identified as a Yún Rev2 (ABX00020), a
dual-processor board with a 5 V, 16 MHz ATmega32U4 for D2-D6 I/O and an AR9331
Linux processor for WiFi/Ethernet. The Rev2 pinout limits I/O pins to 20 mA,
marks VIN as 5 V maximum, shows D0/D1 on the Linux UART, and shows D7 on the
AR9331 handshake connection. The archived Bridge library passes network
commands between the processors. AccelStepper supports relative/absolute
targets, maximum speed, acceleration, current position, and non-blocking
`run()` calls.

**Applied:** downloaded the Yún Rev2 hardware page, official ABX00020 pinout and
schematic, Bridge reference and examples, legacy original-Yún background, and
AccelStepper class reference into `documentation/`. Added
`documentation/README.md` with provenance, pin/power analysis, a recommended
laptop-dashboard -> Yún Linux -> ATmega32U4 architecture, and hardware gates.

**Boundary recorded:** the replacement is feasible but not implemented. The
existing D2-D6 wiring can remain, but bounded motion requires replacing the
indefinite `runSpeed()` path with target-position logic. Web work must not
starve the frequent AccelStepper calls. Absolute positioning remains open-loop
until a homing procedure and travel envelope are established. The Rev2 VIN is
5 V maximum, and its archived Linux/Bridge stack should remain on a trusted
network.

**Protocol decision:** no `PROTOCOL.md` regeneration was required because this
step added research artifacts and a feasibility decision, not a new runnable
verb, configuration axis, endpoint, or produced run artifact.

## Step D7 - Yún stepper task order established

**Direction given:** create a dedicated `TASKS.md` for the Yún Rev2 webpage
distance/speed work and make the implementation order explicit.

**Applied:** added a gated T0-T9 queue. Hardware and safety facts land first;
the command/status contract follows; dashboard/API behavior is proven in
simulation before firmware and Bridge work; real adapter and staged bench work
follow only after each earlier layer passes. The final task is end-to-end
acceptance and documentation handoff.

**Boundary recorded:** this task adds a plan, not a runnable endpoint or device
mode. `PROTOCOL.md` remains unchanged until T3 adds the simulated motion/API
surface. T3, T6, and T7 carry explicit protocol regeneration requirements.

## Step D8 - second directional limit and magnetic-switch characterization

**Direction given:** add a second limit input on D8 and make it possible to
determine empirically whether each two-wire magnetic switch opens or closes as
the piston magnet reaches it.

**Applied:** `limit_switch_palas.ino` now treats D6 as the positive-end input and
D8 as the negative-end input, leaving the Yún Rev2 handshake pin D7 unused. The
sketch reports raw D6/D8 HIGH/open and LOW/closed transitions over the 9600-baud
Serial connection. Separate active-level constants retain the provisional
normally-closed, active-HIGH assumption until the switches are tested. Each
limit blocks only travel farther into its end, motion away clears that end's
latch, and a short magnetic activation remains latched rather than allowing
motion to resume after the piston passes the sensing zone.

**Wiring boundary:** each signal Wago is a two-wire splice: D6 plus one lead of
the positive switch, or D8 plus one lead of the negative switch. Each remaining
switch lead returns to Arduino GND, either through separate GND pins or a common
three-or-more-port ground splice. D6 and D8 are never joined and neither switch
is connected to 5 V. This assumes passive dry-contact switches; powered sensors
must be identified before connection.

**Hardware finding:** the motor-power-off USB/Serial test produced repeatable
transitions on both inputs. Both switches read HIGH/open away from the piston
magnet and LOW/closed when the magnet is detected. The firmware active levels
are therefore LOW for both D6 and D8. These installed switches are normally
open, so a disconnected or broken wire also reads HIGH/clear; fail-safe
broken-wire detection would require different sensors or interface hardware.

**Remaining verification boundary:** the Yún-target compile and USB upload
passed, and raw switch transitions passed. Physical direction assignment and
motor stop/escape behavior remain staged motor-powered checks and are not yet
claimed.

## Step D9 - DM542T motion fault isolated and Yún Wi-Fi configured

**Direction given:** diagnose rough/non-moving stepper behavior without assuming
the previously working STEP/DIR firmware was at fault, then connect only the
Yún to the `GL-MT3000-b3a` local Wi-Fi while the laptop retained its existing
internet connection.

**Hardware finding:** the DM542T initially received 24 V motor power but its
separate control-input selector was set to 24 V. Selecting 5 V allowed the
Yún's control signals to be recognized. Remaining rough attempted motion was
then traced to incorrect motor phase grouping at A+/A-/B+/B-. Correcting those
connections produced smooth motion with no microstep or pulse-width firmware
change. Actual DIP/current settings, physical direction mapping, and powered
limit stop/escape tests remain open T1 work.

**Network procedure:** with DM542T power off, installed retired official Bridge
1.7.0 into a temporary Arduino toolchain, compiled and uploaded the official
`YunSerialTerminal`, stopped the Bridge service with `~~`, and inspected
OpenWrt without displaying its saved keys. Replaced the old `GL` station SSID
with `GL-MT3000-b3a`, retained station mode, WPA2 PSK, `lan`, and DHCP, committed
the configuration, and reloaded Wi-Fi. The secret was entered locally and is
not recorded in repository files.

**Verification:** `wlan0` authenticated and associated, received dynamic address
`192.168.8.137/24`, reported WPA2 PSK/CCMP at -33 dBm, and reached router
`192.168.8.1` twice with zero packet loss. OpenWrt was rebooted to return normal
services, and the exact corrected `limit_switch_palas.ino` build was restored
and verified over USB. The observed address is not a fixed contract; reserve it
in the router or discover the Yún by hostname before the real adapter lands.

**Protocol decision:** the actual USB maintenance verb and configured Yún WLAN
arm now exist, so `protocol_map.py` and generated `PROTOCOL.md` were expanded.
No motion endpoint exists yet; webpage status/speed/direction/distance remain
T2-T7 work.

## Step D10 - direct 10 Hz SICK acquisition documented

**Direction given:** document the distinction between the 10 Hz browser stream,
the approximately 1 Hz ScriptBasic republished block, and actual SICK process
measurements.

**Applied:** documented `direct` as the real-source default, the two raw process
windows, the configurable `--dxmr90-rate-hz`, the `republished` diagnostic
fallback, SICK pressure charting in bar, and the fact that fresh DXMR90 source
CSV rows now follow the configured hardware poll rate.

**Verify-pass finding harvested:** direct Modbus acquisition sustained 10.0 Hz
for 20 consecutive samples on live hardware. STP pressure and idle flow were
zero while sensor temperatures remained plausible, confirming that zeros were
measurements rather than missing data.

## Step D11 - source-specific dashboard plots documented

**Direction given:** document the operator-facing split between ESP32 pressure,
SICK pressure, and SICK mass-flow measurements.

**Applied:** the protocol and runbook now state that SICK pressure is presented
in bar, the center plot carries SICK 1/2 pressure, and the right plot carries
SICK 1, SICK 2, and total mass flow. The first pressure plot remains scoped to
ESP32 P1/P2/P3.

**Boundary recorded:** this is a browser-consumer change. Live API fields,
recording schema, direct 10 Hz acquisition, and export artifacts are unchanged.

## Step D12 - unified simulated stepper protocol documented

**Direction given:** make the Yún stepper part of the existing supervisor scope
and start implementation.

**Applied:** documented the stepper as a third selectable source, its
stale/missing scenarios, provisional signed-distance command envelope,
move/stop/status endpoints, dashboard panel, stable merged fields,
`stepper_raw.csv`, unit-test command, and simulation-only safety boundary.

**Verify-pass finding harvested:** the localhost command/status/recording smoke
completed exactly at 1.5 mm commanded open-loop position and produced a shared
run with stepper rows. No physical Yún transport exists yet, so no documentation
claims real web motion.

## Step D13 - dual transports and USB diagnostics documented

**Direction given:** make the testing and production contexts clear and
discernible in `TASKS.md`, then proceed.

**Applied:** `TASKS.md` now defines `sim`, `usb`, `network`, and `off`; makes USB
a supported localhost mode instead of a disposable harness; reserves network
for the Bridge/LAN path; requires identical semantics and exclusive command
ownership; and separates read-only T4A diagnostics from bounded USB motion.

**Implementation boundary recorded:** compact firmware JSON, laptop USB parsing,
and the diagnostic webpage consumer exist. The Yún compile passed at 42% flash
and 16% RAM. The firmware was uploaded with verification and the corrected live
feed reaches `localhost`; Move/Stop remain disabled in USB mode, deliberate
switch/unplug smoke is pending, and the network adapter deliberately fails
closed. The runbook and checklist state those limits explicitly.

## Step D14 - manual USB speed tuning documented

**Direction given:** expose a faster manual diagnostic speed before full web
distance control.

**Applied:** added T4B to the ordered task plan; documented the D4-off guard,
`V1 S10..1000` firmware command, 0.1..10.0 mm/s provisional range, configured
versus effective speed, `/api/stepper/speed`, and the fact that D4/D5 and both
limits retain motion authority. Updated the generated protocol, runbook,
checklist, development, and integration surfaces in the same change.

**Boundary recorded:** source and desktop verification exist, and the Yún
target compiles. T4B was subsequently uploaded with verification and echoed a
3.0 mm/s setpoint while stopped. No physical speed or travel claim is made yet.

## Step D15 - electrical direction mapping documented

**Direction given:** allow direction reversal from the webpage because the
physical sign may be wrong at the current end stop.

**Applied:** documented T4C as a stopped electrical mapping calibration rather
than remote direction ownership. Added `V1 D0|1`, strict boolean endpoint,
Normal/Inverted UI, retained D5 and logical-limit authority, restart behavior,
and the brief physical sign-identification procedure to tasks, protocol,
runbook, and checklist.

**Boundary recorded:** source, desktop tests, and Yún compile/upload pass. The
live T4C firmware advertises Normal/Inverted calibration while stopped, but no
physical mapping has been accepted yet.

## Step D16 - dual-mode USB position control documented

**Direction given:** make D5 useful in both local and web contexts, add an
optional D8-limit action, and remove acceleration as a user-adjustable field.

**Applied:** documented Local Velocity (D4 continuous run/stop, D5 direction)
and Web Position (D4 arm/immediate-abort, positive travel magnitude with D5
direction selection), the
D4-off mode-change guard, stopped Local Velocity boot default, optional D8
fixed-speed seek, fixed 5 mm/s² acceleration, relative move/Stop endpoints, and
compact signed USB commands/status. Open-loop absolute position and software
target envelopes are explicitly excluded from safety. Updated the generated
protocol, runbook, task tracks, and checklist in the same change.

**Boundary recorded:** source, 22 desktop tests, the revised 63%-flash/27%-RAM
Yún compile, verified upload, and stopped USB status echo pass. That live echo
showed Local Velocity, D4 OFF, D5 Forward, D6/D8 clear, boot armed, null
position/target/remaining, and zero effective speed. Deliberate motion checks remain.
Neither the measured 137.18 mm stroke nor the presumed motor/screw figures
independently validate 100 steps/mm; a pulse-count-versus-DRO check remains
required before distance accuracy is accepted.

**Operator correction:** the page and `/api/stepper/move` now accept positive
relative travel magnitude only. D5 selects Forward or Reverse; the supervisor
resolves the signed delta immediately before the lower-level USB call. The
adapter and firmware retain signed-command/D5 checks for race protection, and a
D5 change in motion still aborts. The page does not show homed state, absolute
position, target, or remaining and labels D5 as Travel direction in Web Position.

## Step D17 - real ESP32 adapter documented

**Direction given:** ensure the current laptop dashboard accepts real ESP32
input and make the operator command unambiguous.

**Applied:** updated the orientation, task/procedure tracks, generated protocol,
runbook, checklist, and source interface inventory for the implemented
`RealEsp32Source`. The runnable contract is
`--esp32-source real --esp32-url URL [--esp32-timeout S]`; the adapter consumes
the existing `reading` and `sol` SSE events and forwards the existing solenoid
toggle endpoint. Documentation explicitly leaves raw-voltage fields null,
separates the successful loopback contract tests from pending physical checks,
and retains the ESP32-hosted legacy page as a fallback.

**Superseded by D18:** this records the intermediate documentation state before
the strict headless v1 decision.

**Boundary recorded:** five ESP32 parser/adapter/CLI/runtime tests and 27 combined
desktop tests pass. Physical stream duration, real sensor plausibility, a safe
relay toggle, and merged-recording/export verification remain checklist items.

## Step D18 - headless ESP32 split and strict protocol documented

**Direction given:** make it clear that laptop `dashboard.py` is the current
webpage and does not need compatibility with the old ESP32-hosted webpage.

**Applied:** updated the orientation, tasks, procedures, runbook, bench
checklist, handoff status, and generated protocol source to distinguish the
primary headless firmware from the archived self-hosted sketch. The strict
version-1 reading fields and explicit rejection of legacy partial readings are
now the interface of record. The archived sketch is a historical/reference
artifact, not a fallback protocol consumed by the laptop.

**Boundary recorded:** 28 desktop tests and the headless ESP32-S3 compile pass.
The documentation does not claim a physical flash, live stream, safe relay
toggle, or recorded hardware run; those remain bench checklist items.

## Step D19 - fourth solenoid documented

**Direction given:** add a fourth solenoid following the existing template and
assign its ESP32 control to GPIO 10.

**Applied:** updated the current interface inventory, task tracks, runbook,
hardware checklist, handoff pin map, README launch context, and generated
protocol source for four outputs and strict telemetry version 2. The docs
distinguish completed code/compile verification from the pending physical IN4,
24 V coil, flyback diode, flash, OFF-at-boot, and safe-toggle checks.

**Boundary recorded:** six ESP32 tests, 28 combined desktop tests, protocol
drift checks, and the four-output ESP32 target compile pass. No physical wiring,
flash, or energized-solenoid test is claimed.

## Step D20 - software E-STOP documented

**Direction given:** put an E-STOP for stepper motion near the top of the
dashboard.

**Applied:** documented the distinction between ordinary Web Position Stop and
a latched, mode-independent software E-STOP; added the `V1 E1`/D4-off `V1 E0`
contract, capability/latch fields, endpoints, top-level operator workflow,
desktop/compile evidence, and physical checklist. Every operating surface calls
out that the path neither removes motor power nor replaces a hardwired,
safety-rated emergency-stop circuit.

**Boundary recorded:** implementation, 26 stepper tests, 32 combined tests, and
the 65%-flash/28%-RAM Yún compile pass. Upload, Local Velocity stop, Web Position
stop, reset-guard, disconnect-latch, and stop-latency checks initially remained.
The verified upload and stopped D4-off latch/reset smoke now pass; moving stops,
D4-on rejection, disconnect persistence, and moving latency remain pending. A
combined-source finding is also recorded: priority dispatch prevents an
unreachable DXMR90 poll from delaying the Yún write, reducing stopped response
from 1.26 s to 0.041 s under the observed test configuration.

## Step D21 - Yún LAN bridge documented

**Direction given:** implement the missing Yún LAN control path now that the
dashboard is being run from another laptop on the bench network.

**Applied:** updated the architecture and runnable protocol from a planned
Bridge adapter to a concrete non-blocking ATmega Serial1 path, AR9331
`yun_stepper_bridge.py` service, OpenWrt init wrapper, and
`NetworkStepperSource`. Added the `--stepper-url`/`--stepper-timeout` launch
contract, exact Linux install/rollback commands, trusted-LAN/no-auth boundary,
USB/network ownership rules, fresh acknowledgement semantics, and a dedicated
motor-off/moving checklist.

**Boundary recorded:** six network tests, 38 combined desktop tests, and a
20,794-byte/72%-flash, 1,399-byte/54%-RAM Yún compile/upload pass. A stopped USB
heartbeat reports owner none and zero motion. No Linux service install, LAN
hardware status, jitter/latency, restart, disconnect, or motion result is claimed.
