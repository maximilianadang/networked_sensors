# Controllino IO (firmware 1.6.4)

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
AI0–AI2. Firmware 1.6.1 appends P8 on AI08 (Arduino A8), a fifth
low-pressure input. Firmware 1.6.2 appends P9 on AI09 (Arduino A9), a sixth
low-pressure input, without renumbering existing channels. In Controllino mode these replace the dashboard's ESP32 and SICK
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

The piston Stop command (`V1 X`) halts only the stepper. Starting with firmware
1.6.4, it leaves the CAN motor's target and lease unchanged; the backend also
continues renewing that lease. To stop the CAN motor, request zero RPM separately.
Software E-stop (`V1 E1`) still stops the stepper, releases CAN torque immediately
and turns off the solenoids.

For a dashboard-owned manual run, the CAN panel's Stop updates the existing
target to zero using the same 80 RPM/s ramp. The firmware retains the run/lease
while ramping, then releases torque when the ramped setpoint reaches zero; this does not assert
that measured shaft speed has reached zero. The backend continues lease renewals
through deceleration. `V1 U0,<ramp>,<token>` reuses the existing speed update;
`cstop=1` advertises support. Legacy firmware, unowned runs and a Stop during an
unconfirmed Start retain immediate release. E-stop, faults, feedback loss,
lease loss and dashboard shutdown also release immediately. `V1 C0` remains
the explicit immediate-release command; spreadsheet/API zero-RPM release
behavior is unchanged.

The full sequence in `examples/record_motor_solenoids.csv` uses a CAN speed row
with blank `duration_s` to start a leased run using the same ramp and renewal
path as the dashboard. Its separate response is stored in
`examples/record_motor_solenoids_fail_safe.csv`; both are loaded by default.
An explicit `rpm=0` row is required before program end;
it ramps down and waits for the controller to report inactive. Its `timeout_s`
and freshness/health conditions cover stop acceptance; after acceptance the
row waits for ramp completion without a completion deadline. Reported motor
stop faults still fail the row. A CSV fail-safe section retains renewal while
its ordered response runs, allowing an explicit zero-RPM row to ramp down.
Without a fail-safe section, or after it finishes, failed experiments retire
their motor lease; firmware lease expiry remains the fallback stop mechanism.

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
Each entry is keyed by the stable P1–P9 ID and accepts `label`,
`volts_per_count` and `calibration`.

- `calibration: null` leaves pressure blank; raw voltage and ADC counts remain
  available in recordings for diagnosis.
- A calibration requires `voltage_min`, `voltage_max`, `pressure_min`,
  `pressure_max`, `unit` and `reference` from the sensor's specifications.
- Calibration units may be `Pa`, `kPa`, `MPa`, `hPa`, `mbar`, `bar` or `psi`
  (case-sensitive). The host converts pressure values and summary means to
  **mbar** for low-pressure P1–P4, P8 and P9 and **PSI** for high-pressure P5–P7.
  Configuration and recording metadata retain the original calibration units,
  endpoints and gauge/absolute reference. Unit conversion does not change that
  reference. Unsupported units are rejected.
- Standard-input voltage scaling is nominal for a 24 V-powered controller:
  `5 * 6.14 / 1024` V/count ([manufacturer](https://www.controllino.com/inputs/)).
  Confirm the reference voltage/divider before calibrating pressure.

Restart the dashboard after changing calibration. Invalid or stale samples
are blank in live charts; raw evidence remains available. `merged_samples.csv`
contains converted values and validity; `stepper_raw.csv` contains raw telemetry.
Recording metadata freezes the configuration used for the run. New pressure
summaries include all nine channels and never substitute old ESP32 values.

The saved machine configuration currently uses:

| Channel | Terminal | Sensor output | Pressure range | V/count |
|---|---|---|---|---|
| P1 | AI10 | 0–10 V | 0–25 mbar | 0.03 |
| P2 | AI11 | 0–10 V | 0–250 mbar | 0.03 |
| P3 | AI12 | 0–10 V | 0–25 mbar | 0.0098 |
| P4 | AI13 | 0–10 V | 0–25 mbar | 0.0098 |
| P5 | AI0 | 0.507526457–4.573126457 V | 0–200 PSI absolute | 0.03333333333333333 |
| P6 | AI1 | 0.511193123–4.576793123 V | 0–200 PSI absolute | 0.03333333333333333 |
| P7 | AI2 | 0.513886665–4.579486665 V | 0–200 PSI absolute | 0.03345833333333333 |
| P8 | AI08 | 0–10 V | 0–25 mbar | 0.03 |
| P9 | AI09 | 0–10 V | 0–250 mbar | 0.03 |

P6/P7 retain their October bench-measured voltage scales: 24 ADC counts
corresponded to 0.800 V and 0.803 V respectively. P5 now uses the same
0.800 V / 24-count factor as P6, based on the operator’s 0.8 V measurement.

P5–P7 use the operator-confirmed 200 PSI span and a ratiometric model at the
measured **5.082 V supply**: the voltage span is `0.8 * 5.082 = 4.0656 V`, giving
**49.193231011413 PSI/V**. On 2026-11-13 a fresh 20-second, 200-sample capture
at **1.005 bar absolute (14.576292641886 PSI)** anchored each channel:

| Channel | Mean ADC-derived voltage at reference |
|---|---|
| P5 | 0.803833333 V |
| P6 | 0.807500000 V |
| P7 | 0.810193542 V |

For each channel, `voltage_min = mean_voltage - reference_psi / slope` and
`voltage_max = voltage_min + 4.0656`. Pressure endpoints remain 0 and 200 PSI
absolute. The result is `pressure_psi = slope * (voltage_v - voltage_min)`;
readings outside the fitted voltage endpoints are invalid, so this mapping does
not admit negative absolute pressures. Each captured mean converts to
14.576292641886 PSI. This replaces the earlier pressure-endpoint offset fit.

ADC-to-voltage factors and acquisition are unchanged. This calibration uses the
assumed linear ratiometric model at the measured supply; an independent second
pressure point can validate its slope. Live samples retain their fluctuations.
Restart the dashboard after changing these settings; no firmware upload is
needed for this calibration update.

P8 is configured in advance for the planned AI08 sensor. Its pressure range
matches P3; its voltage conversion follows the standard input used by AI10.
P9 on AI09 copies P2/AI11’s sensor calibration and voltage conversion.
Sensor pressure ranges do not establish the controller's ADC voltage scale.

### Optional high-pressure oversampling

Firmware 1.6.3 adds a build-time boolean in `controllino_firmware.h`:

```cpp
constexpr bool PRESSURE_OVERSAMPLING = true;
```

`true` enables oversampling for P5–P7 (AI0–AI2) only; `false` restores their
single-conversion behavior. Recompile and flash after changing it. This switch
is in the firmware configuration header, not `system_config.json`; voltage and
pressure calibration remain in that JSON file.

After discarding the first conversion following an ADC channel change, enabled
channels take 16 conversions and decimate the sum to two extra bits, expressed
as quarter-count increments. Other channels retain one conversion. Work is
bounded to one channel per 10 ms acquisition slice (up to 17 conversions with
interrupts enabled). Nine-channel scans still target 90 ms and telemetry still
targets 10 Hz; actual timing and effective improvement need hardware validation.
Suitable ADC noise is necessary for the extra resolution to represent useful
information. This does not correct calibration or sensor accuracy.

The original `adc` array retains the last individual 10-bit conversion. Enabled
firmware also reports `adcos` (channel bitmask, P5–P7 = 112) and `adcq` (nine
quarter-count integers, 0–4092). The updated host uses `adcq / 4` on selected
channels for voltage/pressure conversion and retains both forms in recordings:
`stepper_pressureN_adc` and `stepper_pressureN_adc_oversampled`. The live
`stepper_pressure_oversampling_mask` records which channels are enabled; hover
text shows the oversampled value alongside the original count. Restart the
dashboard after updating its code. Disabled builds and older firmware continue
to use the original integer counts without a calibration-factor change.

## Nitrogen Pitot velocity

P4 (AI13) measures total-minus-static pressure. Its dashboard reading includes
an adjacent estimated velocity in m/s. TC-0 supplies gas temperature in Celsius;
the calculation converts this to Kelvin and uses nitrogen's specific gas
constant, 296.8 J/(kg K). The pressure path is:

- Total absolute pressure, mbar: `6 + P1 (AI10) - P8 (AI08) + P3 (AI12)`.
- Static absolute pressure, Pa: `100 * (total_mbar - P4)`.
- Density, kg/m³: `static_pa / (296.8 * (TC0_c + 273.15))`.
- Estimated velocity, m/s: `sqrt(2 * 100 * P4 / density)`.

The 6 mbar value is an assumed absolute reference. This is the low-speed,
approximately incompressible Pitot relation. Appreciable compressibility requires
a different velocity model. Displayed velocity has two decimal places; this does
not establish measurement accuracy.

All four pressures must be calibrated and valid under the existing pressure
freshness rules, and TC-0 must be ready on a fresh, error-free ED-593 source.
Missing inputs, negative Pitot differential, nonpositive static pressure or
nonphysical temperature leave velocity blank. Zero Pitot differential gives
zero velocity. No missing pressure is silently substituted with zero.

AI08 is acquired as the eighth ADC channel, with the same standard-input voltage
scale convention as AI10/AI11. Its sensor pressure calibration belongs in
`system_config.json` under `pressure_inputs.8`: 0–10 V maps to 0–25 mbar,
matching P3. AI08 retains the standard-input 0.03 V/count conversion; P3 uses
its separate dedicated-input conversion. An analog zero cannot prove that a sensor is connected.
The host also accepts older seven- and eight-channel firmware, leaving
unreported channels unavailable. P9 is recorded and displayed independently;
it does not change the Pitot pressure formula.
Merged recordings and CSV exports include `pitot_total_pressure_pa`,
`pitot_static_pressure_pa`, `pitot_density_kg_m3` and `pitot_velocity_m_s`, including
blank columns before inputs become available. Raw pressure ADCs and TC-0 remain
available separately.

## Mass flow

The third identical SICK flow sensor uses DXMR90 IO-Link port 3 in direct mode.
The first mass-flow trace remains the sum of ports 1 and 2; it excludes port 3
and is independent of valve state. Port 3 is the second, independent trace.
All three ports use the same process-input reader and decoder, without a
separate per-port connection/status gate. A read failure on any port reports a
DXMR90 transport error; the next successful poll restores all three readings.
Republished mode has no assigned port 3 register and leaves this channel
unavailable.

In Controllino mode, that third sensor supplies Open-Line Air Flow, without
gating on a valve or controller connection.
The shared `open_line_air_flow_g_min` field is blank unless DXMR90 is fresh,
transport has no error and the measured value is finite. A measured zero stays
zero. Its `open_line_air_flow_source` is `dxmr90_port3`; legacy mode instead uses
`esp32_open_channels` and retains the existing valve-gated ESP32 flow, provided
the ESP32 source is healthy.

The air-to-powder mass ratio uses the sum of all three SICK sensors: ports 1 + 2
plus port 3, in g/min, divided by the entered powder flow in g/s multiplied by
60. This converts both flows to the same units. The ratio stays blank unless
DXMR90 is fresh and error-free, both flow terms are available, and the entered
powder rate is positive. It uses requested powder flow rather than measured
powder delivery.

Recordings include both canonical fields from startup. The summary/export metric
`open_line_air_flow_g_per_min_active` uses the same measurement and the existing
first-active-interval rule (starts above 10 g/min, ends below 10 g/min). In
Controllino mode the old ESP32 per-channel and combined flow summaries stay
blank; legacy mode retains them. Raw sensor fields remain diagnostic evidence.

`merged_samples.csv` and `dxmr90_raw.csv` include the separate
`dxmr90_port3_mass_flow_g_min`, `dxmr90_port3_flow_valid`,
`dxmr90_port3_status` and `dxmr90_port3_error` fields from recording startup.
The last three fields are retained for recording compatibility: `flow_valid`
indicates a finite decoded flow; `status` and `error` are unset in direct mode.
Transport errors are reported through the shared DXMR90 transport-error field.
This keeps the channel when the sensor connects after recording begins.
Cached raw values remain diagnostic evidence; chart freshness also requires
a live DXMR90 source without a transport error.

The process-input windows follow [Banner data reference 229732](https://info.bannerengineering.com/cs/groups/public/documents/literature/229732.pdf),
registers 41002–41017, 42002–42017 and 43002–43017 (the reader omits the leading
4). All three sensors use the shared 32-byte SICK FTMg process-data decoder;
no master settings are written.

## Encoder and validation

LinACE PWM connects directly to X1 SCL (Arduino pin 21), and Status connects
directly to X2 Digital 12 (Arduino pin 42), with a common ground. Both pins are
configured as inputs without pull-ups. The encoder requires a 4.5–5.5 V supply
at the readhead; its signal outputs are 3.3 V TTL compatible. Set a new top zero after installation;
the old DRO zero is retained under its old encoder identity and cannot apply
to LinACE. Machine travel and direction still require installation checks.

The configured encoder is `CHE12PWA450E05BA00`: its accuracy code E specifies
±100 µm (±0.1 mm), while the 450 mm PWM version resolves 10 µm (0.01 mm).
See the local [LinACE datasheet](../devices/LinACE/CHD01_07.pdf), PWM interface
specifications and part-number table. The operator's piston position is rounded
to 0.1 mm; raw diagnostics, acquisition and recordings retain 0.01 mm readings.
Resolution is distinct from absolute accuracy and does not establish acquisition
accuracy in this installation.

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

### Capture latency investigation

Inspection of the compiled CubeMars firmware identified improvements to evaluate
on hardware; these optimizations have not been implemented:

- `StepperPwm::start()` calculates its timer period with interrupts disabled.
  `onStep()` repeats that calculation inside the ISR when speed changes. The
  compiled speed-change path calls two 32-bit software division routines; their
  instruction counts alone exceed 70 µs at 16 MHz. Precompute timer settings
  before entering these sections. This division is not repeated at steady speed.
- `LinaceReader::onClock()` updates diagnostics before reading Timer5. In the
  inspected build, `millis()` also executes before reading the PWM pin level.
  Read the timer and input level first and reduce interrupt callback overhead.
- Timer3 overflow interrupts occur every step period, although their handler
  only acts when finishing a move. Investigate arming them only for completion,
  preserving pulse counts and the final output edge.
- USB status streams alongside Ethernet, generating UART transmit interrupts.
  Consider optional streaming without breaking USB control or discovery.

These are identified delay paths, not measurements of their contribution to
position jitter. Compare stationary and moving raw readings before and after
changes, including operation with network/serial traffic and CAN activity.
No exposed MAXI Automation terminal or X1/X2 pin provides a free dedicated ICP
input: PD4/ICP1, PL0/ICP4 and PL1/ICP5 are not exposed; PE7/ICP3 is wired to
internal overload detection, and Timer3 drives STEP.
