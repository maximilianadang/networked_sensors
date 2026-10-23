# Controllino IO (firmware 1.6.0)

All three motor builds use the LinACE encoder and expanded IO by default.
USB and Ethernet share the same V1 motor commands and compact JSON telemetry.
Native Ethernet now uses a persistent TCP stream; install firmware 1.6.0 before
using the updated Ethernet dashboard. USB remains compatible with older firmware.
STEP uses Timer3 hardware PWM on X1 DO1 / Arduino D3 (OC3C), approximately
50% duty, with the existing 250 ms driver-enable delay. Interrupts count falling
edges and stop a positional move at the final period boundary; they do not
generate individual pulse edges. Speed changes latch at PWM cycle boundaries.
Stop, E-STOP and directional limits disconnect PWM and return STEP HIGH.
Servo/ESC timing uses Timer1 at the same 50 Hz; LinACE retains Timer5.
Pulse counts and rate are controller output bookkeeping, not driver acceptance
or measured physical travel. The LN238S E09050 screw lead is 5.08 mm/revolution.
SRX SW5–SW8 ON/ON/ON/OFF selects 1000 input pulses/revolution (5× microstepping),
giving 0.00508 mm/input pulse. The host applies this calibration only to
Controllino sources; legacy Yún and simulation retain their existing conversion.
Firmware boots at 295 pulses/s (about 1.5 mm/s). Pulse-rate bounds 20–1969
preserve the existing nominal 0.1–10 mm/s host bounds. No physical per-move
distance ceiling is imposed: finite nonzero moves must fit the signed 32-bit
pulse-count representation. Physical limits remain enforced in the controller.
The system-config `max_travel_mm` sets the graphic span, not a command limit.
Idle-current SW4 is left ON (50%); stop still disables
the driver. Validate physical travel after changing the DIP settings.
Controllino mode has exactly three logical solenoids: 1 = R3, 2 = R4 and
3 = R5. They replace the old ESP32-controlled valves. The host maps them
to existing firmware commands `L5`, `L6` and `L4`, respectively; protocol
wire IDs are independent of dashboard numbers. Older firmware reporting only
`sol4` enables only logical solenoid 3 (R5). Legacy ESP32-only mode keeps four
solenoids.
All three relays start off and turn off on software E-stop.

DI0 (Arduino A12) is the bottom/Reverse travel limit; DI1 (A13) is the
top/Forward travel limit. Both use plain digital inputs: HIGH while the normally
closed contact supplies the terminal, LOW when actuated or disconnected. Use
the 24 V digital screw terminals, not the 5 V headers. The official MAXI
Automation [pinout](https://www.controllino.com/wp-content/uploads/2023/05/CONTROLLINO_MAXI_AUTOMATION_Pinout_Table.pdf)
maps these separately from pressure AI12/AI13 (Arduino A14/A15).

The shared input qualifier requires 5 ms of continuous LOW, rejects shorter
glitches and releases immediately on HIGH. A qualified limit stops STEP pulses
and disables the driver only for travel toward that endpoint, in both positional
and directional modes. Only a currently LOW input blocks its direction; HIGH
clears that block immediately without a remembered trip or controller reset.
Retreat is allowed whenever the opposite input is HIGH. A stopped run requires
a new command and never resumes merely because a switch releases. Both LOW inputs block
both directions. Input acquisition and interlock service continue during
USB/Ethernet traffic. The firmware limit response affects the stepper; a
spreadsheet move fails on `limit_blocked` and retains its existing abort behavior.

Existing `d6`/`d8` and `lx` telemetry retain their signed-pulse semantics;
legacy `lp`/`ln` fields are always zero because Controllino limits do not latch.
`lppin`/`lnpin` report the actual Arduino inputs. The operator convention is
Forward/up and Reverse/down, independent of electrical HIGH/LOW labels. The
host maps Forward to positive pulses and Reverse to negative pulses; DI0
remains the negative/bottom limit and DI1 the positive/top limit. The firmware's
electrical direction levels are retained; physical motion still needs a bench
check against this calibration after connecting the switches.
The dashboard displays DI0/DI1 and their current raw and qualified states.
These remain controller interlocks, outside the spreadsheet. Firmware 1.5.9
advertises `hc=1` and accepts `V1 H<sps>` in positional mode: retract down
until the qualified DI0 bottom limit is active, then stop with `home_complete`.
The dashboard supplies the speed field. An already-active bottom limit completes
without motion. Stop and E-STOP interrupt homing. Homing preserves the DRO zero;
it does not establish a new encoder reference.

Dashboard ESP32 selection defaults to `auto`: Controllino Ethernet/USB launches
disable it, legacy Yún launches enable it, and simulation uses a simulated source.
Older Controllino firmware that still needs ESP32 can opt in with
`--esp32-source real` or `ESP32_SOURCE=real`; explicit CLI settings override the
environment. Motor variants do not select ESP32—the expanded IO is shared by all
three current builds. A disabled ESP32 has no top status pill.

Low-pressure inputs P1–P4 are AI10–AI13; high-pressure inputs P5–P7 are
AI0–AI2. In Controllino mode these replace the dashboard's ESP32 and SICK
pressure measurements respectively. Flow measurements keep their existing
sources. Open-flow calculations use the three logical valve states; the SICK
total remains gated by physical R5 (now logical solenoid 3). Channel labels,
charts, calibration and recording metadata share
the definitions in `controllino_io.py`.

## Ethernet stream

Run the dashboard with `--stepper-source controllino`. Its address defaults to
`tcp://10.77.0.10:80`; override it with `--stepper-url` or `CONTROLLINO_URL`.
An existing `http://host[:port]` setting is accepted as an address alias and
uses the same TCP stream. The native controller no longer serves HTTP
`/status` or `/command` endpoints. The legacy Yún HTTP bridge is unchanged;
select it with `--stepper-source network` (default `http://arduino.local:8080`).

One background worker owns the persistent connection and receives telemetry
while command callers await acknowledgement. Newline-delimited requests use
`@<id> V1 <command>`, with a positive 31-bit request ID. For example:

```text
@17 V1 M1
{"v":1,"t":"a","ok":1,"id":17,"e":"none"}
```

The existing actual-state confirmation still follows acknowledgement. Telemetry
advertises `net:"tcp"`; old firmware is not treated as a working stream source.
An absent acknowledgement is an unknown outcome, whereas `ok:0` is an explicit
rejection. The host reconnects after disconnection, discards pending commands
and never replays them. Commands are not automatically retried. Request queues
are bounded; a full queue rejects additional dispatch rather than accumulating
late actuator requests. `--stepper-timeout` defaults to five seconds for network
I/O and loss of stream telemetry; spreadsheet feedback deadlines remain separate.

Firmware services USB and Ethernet cooperatively, without waiting for serial
buffer space, TCP send completion or graceful connection teardown. Two replies
and one telemetry frame per transport bound memory use; a slow output coalesces
telemetry updates instead of queuing a history. Overflow discards a telemetry
frame rather than sending truncated JSON; replies never interleave with a
partially sent frame. The Ethernet library's WIZnet send/close adaptation
is isolated in `ethernet_stream.h`; shared framing is in `stream_io.h`.
Motion, limit checks, encoder acquisition and CAN lease/fault handling retain
their existing behavior. Upload through direct USB if the USB extender cannot
complete outbound transfers. The operator launches the dashboard after updating
the firmware and restarting the backend.

## CubeMars manual control

There is no configured 400 RPM operating cap. The firmware advertises the
manufacturer's velocity command range: ±100,000 ERPM, converted to ±793 whole
output RPM for the AK80-6 (126 ERPM per output RPM). The host and interface use
that protocol range directly. It is not a verified mechanical speed capability.

For a dashboard-owned manual run, Stop updates the existing target to zero
using the same 80 RPM/s ramp. The firmware retains the run/lease while ramping,
then releases torque when the ramped setpoint reaches zero; this does not assert
that measured shaft speed has reached zero. The backend continues lease renewals
through deceleration. `V1 U0,<ramp>,<token>` reuses the existing speed update;
`cstop=1` advertises support. Legacy firmware, unowned runs and a Stop during an
unconfirmed Start retain immediate release. E-stop, faults, feedback loss,
lease loss and dashboard shutdown also release immediately. `V1 C0` remains
the explicit immediate-release command; spreadsheet/API zero-RPM release
behavior is unchanged.

The full sequence in `examples/record_motor_solenoids.csv` uses a CAN speed row
with blank `duration_s` to start a leased run using the same ramp and renewal
path as the dashboard. An explicit `rpm=0` row is required before program end;
it ramps down and waits for the controller to report inactive. Its `timeout_s`
and freshness/health conditions cover stop acceptance; after acceptance the
row waits for ramp completion without a completion deadline. Reported motor
stop faults still fail the row. Failed experiments stop renewing their motor
lease, leaving firmware lease expiry as the stop mechanism.

`action=wait,duration_s=5` holds the current outputs for five seconds. All other
cells in a wait row are blank, including `timeout_s`: this is a successful dwell,
not a command timeout. Telemetry acquisition and enabled reading monitors keep
running. The example waits after solenoid 3 opens, closes 3, 2 and 1 in order,
then ramps the motor to zero. Recording remains on to capture shutdown. CAN
rows with an explicit `duration_s` retain their timed-run behavior.

For requested-state rows, `timeout_s` starts when the transport has sent the
command, not when the row begins. Queueing, command preparation and the initial
freshness/health gate have no CSV feedback deadline. USB marks a complete serial
write; TCP marks the complete command frame; HTTP marks the complete request
before awaiting its response. Multiple
compatible targets begin their shared feedback window after the last command
has been sent. Transport exceptions still fail immediately; this does not add
command retries. Read-only check deadlines and `wait` timing still start at row
entry. Spreadsheet CAN commands bypass the manual API's fixed 1.5-second
confirmation wait and use the runner's CSV deadline. Lease renewal waits for
initial command acceptance; controller watchdogs and transport I/O limits remain
independent of the spreadsheet deadline. `command_sent_s` and `deadline_s` expose
the timing in execution results.


For a USB dashboard connection, use `--stepper-source controllino-usb`.
The port defaults to `auto`: on Linux the host discovers compatible USB IDs,
prefers `/dev/serial/by-id`, and retains the selected controller's identity
across disconnects and `ttyACM` renumbering. Multiple compatible controllers
require an explicit `--stepper-port`; those USB IDs also identify Arduino Mega
boards, so discovery alone does not verify the installed firmware. The existing
firmware decoder still validates received telemetry. An explicit numbered port
is tracked by identity when possible; a missing numbered ACM port can recover
to the sole compatible device. An explicit stable path never falls back to a
different controller. CubeMars USB builds require `--stepper-baud 115200`.

CubeMars recurring speed requests run at 10 Hz (100 ms). After a run is
accepted, a rejected recurring CAN send does not stop it: the next due request
sends the latest setpoint, without a command backlog or a new Start. Initial
command acceptance still requires a successful send. E-stop, motor faults,
motor restart, CAN error flags, lost feedback, lease expiry and timed completion
retain their existing stop behavior. Send rejection alone has no retry-count
or elapsed-time cutoff; live feedback does not prove outbound commands arrived.
Optional `cdiag.txfail` counts lifetime send rejections and `cdiag.txcode` retains
the last rejected send's library result (2 = all TX buffers busy, 4 = failed TX).
These fields are preserved in host telemetry/history/recordings; they do not
change transport-health or motor-fault semantics.

The `controllino-ak80-6` build uses node 104 at 500 kbps, the manufacturer's
velocity command range and an 80 RPM/s default ramp. An explicit, confirmed dashboard Start gives
the backend ownership of that manual run. Its background worker renews the
existing 1500 ms controller lease every 250 ms; browser visibility and browser
connection state do not renew or cancel it. Switching tabs, minimizing or closing
the browser therefore leaves the motor running while the backend and controller
remain healthy. Stop ramps the speed setpoint to zero before torque release.

Completed ramp-down, E-stop and backend shutdown retire that ownership. Inactive or mismatched
motor sessions, stale controller/motor feedback, faults and failed renewals also
retire it permanently. Backend/USB failure stops renewal and the firmware lease
expires; CAN feedback and fault protections remain unchanged. A recovered link
or restarted backend never resumes or adopts a run automatically. A refreshed
page can stop an existing run before starting another. Timed CSV commands keep
their original bounded duration and do not receive automatic renewal.

## Pressure configuration

The dashboard reads `pressure_inputs` from `system_config.json`. Missing
entries use the shared defaults; no channel has an assumed pressure range.
Each entry is keyed by the stable P1–P7 ID and accepts `label`,
`volts_per_count` and `calibration`.

- `calibration: null` leaves pressure blank; raw voltage and ADC counts remain
  available in recordings for diagnosis.
- A calibration requires `voltage_min`, `voltage_max`, `pressure_min`,
  `pressure_max`, `unit` and `reference` from the sensor's specifications.
- Calibration units may be `Pa`, `kPa`, `MPa`, `hPa`, `mbar`, `bar` or `psi`
  (case-sensitive). The host converts pressure values and summary means to
  **mbar** for low-pressure P1–P4 and **PSI** for high-pressure P5–P7.
  Configuration and recording metadata retain the original calibration units,
  endpoints and gauge/absolute reference. Unit conversion does not change that
  reference. Unsupported units are rejected.
- Standard-input voltage scaling is nominal for a 24 V-powered controller:
  `5 * 6.14 / 1024` V/count ([manufacturer](https://www.controllino.com/inputs/)).
  Confirm the reference voltage/divider before calibrating pressure.
- AI12/AI13 cannot report pressure until their `volts_per_count` is verified
  and sensor calibration supplied; a 0–10 V input rating alone does not establish
  ADC scaling. Their raw counts remain recorded.

Restart the dashboard after changing calibration. Invalid or stale samples
are blank in live charts; raw evidence remains available. `merged_samples.csv`
contains converted values and validity; `stepper_raw.csv` contains raw telemetry.
Recording metadata freezes the configuration used for the run. New pressure
summaries include all seven channels and never substitute old ESP32 values.

The installation configuration uses the user-confirmed 0–5 V, 0–100 PSI
range for low-pressure P1–P4 (reference unspecified). High-pressure P5–P7
use McMaster 2957N36's supplier-listed nominal 0–5 V output and 0–75 PSI
absolute range. Those sensor ranges do not establish the controller's ADC
voltage scale; AI12/AI13 remain unscaled and their pressure stays unavailable
until that separate scale is verified.

## Mass flow

The third identical SICK flow sensor uses DXMR90 IO-Link port 3 in direct mode.
The first mass-flow trace remains the sum of ports 1 and 2; it excludes port 3
and is independent of valve state. Port 3 is the second, independent trace.
Its connection and process-data-valid flags must both be set, and missing,
invalid or stale readings stay blank. A port 3 read error does not discard
successful readings from ports 1 and 2. Republished mode has no assigned
port 3 register and leaves this channel unavailable.

In Controllino mode, that third sensor also supplies Open-Line Air Flow and the
air-to-powder mass ratio, without gating on a valve or controller connection.
The shared `open_line_air_flow_g_min` field is blank unless DXMR90 is fresh,
transport has no error and port 3 process data is valid. A measured zero stays
zero. Its `open_line_air_flow_source` is `dxmr90_port3`; legacy mode instead uses
`esp32_open_channels` and retains the existing valve-gated ESP32 flow, provided
the ESP32 source is healthy. The ratio divides this air flow in g/min by the
specified powder flow in g/s multiplied by 60; missing air flow stays blank.

Recordings include both canonical fields from startup. The summary/export metric
`open_line_air_flow_g_per_min_active` uses the same measurement and the existing
first-active-interval rule (starts above 10 g/min, ends below 10 g/min). In
Controllino mode the old ESP32 per-channel and combined flow summaries stay
blank; legacy mode retains them. Raw sensor fields remain diagnostic evidence.

`merged_samples.csv` and `dxmr90_raw.csv` include the separate
`dxmr90_port3_mass_flow_g_min`, `dxmr90_port3_flow_valid`,
`dxmr90_port3_status` and `dxmr90_port3_error` fields from recording startup.
This keeps the channel when the sensor connects after recording begins.
Cached raw values remain diagnostic evidence; chart freshness also requires
a live DXMR90 source without a transport error.

The port 3 status/process block follows [Banner data reference 229732](https://info.bannerengineering.com/cs/groups/public/documents/literature/229732.pdf),
registers 43001–43017 (the reader omits the leading 4). All three sensors use
the shared 32-byte SICK FTMg process-data decoder; no master settings are written.

## Encoder and validation

LinACE PWM connects directly to X1 SCL (Arduino pin 21), and Status connects
directly to X2 Digital 12 (Arduino pin 42), with a common ground. Both pins are
configured as inputs without pull-ups. The encoder requires a 4.5–5.5 V supply
at the readhead; its signal outputs are 3.3 V TTL compatible. Set a new top zero after installation;
the old DRO zero is retained under its old encoder identity and cannot apply
to LinACE. Machine travel and direction still require installation checks.

Read-only `dpwm`, `dstat` and `dedge` diagnostics expose the instantaneous
PWM/Status input levels and total observed PWM edges. The dashboard preserves
them as `stepper_dro_pwm_raw`, `stepper_dro_status_raw` and
`stepper_dro_edge_count`. Frame validity uses Status at its rising edge; a later
Status LOW removes freshness without discarding that valid reading. An unchanged zero edge
counter means no PWM interrupts were serviced; it does not prove that no short
pulses reached the input. Position is unavailable
until the first valid frame, so the firmware's initial zero is not treated as
a measured position. The diagnostics do not change acquisition or validation.

Timer5 timestamps PWM edges without blocking motion or CAN service. The nominal
10 µm encoder resolution is not a verified acquisition accuracy: software
interrupt latency and very short pulses need a powered bench test. Missing,
invalid or faulted signals lose freshness. `CONTROLLINO_DRO_KIND=0` selects
the old AbsoluteDRO reader when compiling an older installation; the Yún
firmware keeps that reader unchanged.

Run `python3 -m unittest discover -s tests -v` from the project root. Compile
the `controllino`, `controllino-esc` and `controllino-ak80-6` targets with the
existing `tools.firmware_upload` helper before deployment. Flashing resets
the controller; keep motor and connected load supplies off during upload.

Firmware 1.5.10 adds LinACE `dt`, the acquisition timestamp of the reported
position in controller milliseconds (unsigned 32-bit, wraps at rollover).
The dashboard derives velocity from acquisition intervals when present, retaining
the arrival-time fallback for older firmware. Wiring and PWM capture are unchanged.
