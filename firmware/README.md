# Controllino IO (firmware 1.5.0)

All three motor builds use the LinACE encoder and expanded IO by default.
The existing USB/Ethernet protocol and motor commands remain compatible.
Controllino mode has exactly three logical solenoids: 1 = R3, 2 = R4 and
3 = R5. They replace the old ESP32-controlled valves. The host maps them
to existing firmware commands `L5`, `L6` and `L4`, respectively; protocol
wire IDs are independent of dashboard numbers. Older firmware reporting only
`sol4` enables only logical solenoid 3 (R5). Legacy ESP32-only mode keeps four
solenoids.
All three relays start off and turn off on software E-stop.

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

## CubeMars manual control

The `controllino-ak80-6` build uses node 104 at 500 kbps, with a 400 output-RPM
limit and an 80 RPM/s default ramp. An explicit, confirmed dashboard Start gives
the backend ownership of that manual run. Its background worker renews the
existing 1500 ms controller lease every 250 ms; browser visibility and browser
connection state do not renew or cancel it. Switching tabs, minimizing or closing
the browser therefore leaves the motor running while the backend and controller
remain healthy. Use Stop to release torque; release allows the motor to coast.

Stop, E-stop and backend shutdown retire that ownership. Inactive or mismatched
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
  (case-sensitive). The host converts pressure values and summary means to PSI.
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

LinACE PWM goes to header pin 21 and Status to pin 42 through the enabled,
non-inverting 5 V buffer in the wiring diagram. Its regulated 5 V supply is
separate from the controller header supply. Set a new top zero after installation;
the old DRO zero is retained under its old encoder identity and cannot apply
to LinACE. Machine travel and direction still require installation checks.

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
