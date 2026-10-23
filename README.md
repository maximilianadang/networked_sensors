# Networked sensors and experiment control

Run the bench dashboard to monitor sensors, control the piston, CAN motor and
valves, record measurements, and execute experiments designed as CSV spreadsheets.
`main` contains the current system; `dev-legacy` preserves the previous `main`.

## Start the dashboard

Use Python 3.10 or newer. The dashboard uses Python's standard library; firmware
uploads have separate toolchain requirements. From this directory, run:

```sh
python3 dashboard-lean.py --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. Keep the terminal open and press **Ctrl+C** there
to shut down the dashboard. Closing the browser leaves the backend running;
an active CAN motor run continues while its backend and controller remain healthy.

The default launch connects to the following real devices. The laptop needs
network access to both address ranges shown below.

| Source | Default address | Role |
| --- | --- | --- |
| Controllino MAXI | `10.77.0.10:80` (TCP) | Piston/stepper, LinACE DRO, travel limits, AK80-6 CAN motor, three solenoids and seven pressure inputs |
| DXMR90 | `192.168.0.1:502` (Modbus TCP) | Three SICK flow sensors |
| ED-593 | `10.77.0.11:9500` (DCON ASCII) | Thermocouple inputs TC0–TC7 |

Controllino Ethernet requires firmware **1.6.0 or newer**. ESP32 is automatically
disabled for Controllino launches. Override addresses with `--stepper-url
tcp://HOST:PORT`, `--dxmr90-host HOST` or `--ed593-host HOST`. Disable an unused
source with `--dxmr90-source off` or `--ed593-source off`.

For a direct USB connection to the current Controllino AK80-6 build, use:

```sh
python3 dashboard-lean.py --host 127.0.0.1 \
  --stepper-source controllino-usb --stepper-port auto --stepper-baud 115200
```

USB discovery follows the controller across port renumbering. If several
compatible boards are attached, select one with `--stepper-port /dev/serial/by-id/...`.
Use `python3 dashboard-lean.py --help` for all launch options. Explicit CLI options
override environment settings.

## Use the controls

Check the source status dots before operating equipment. **Source details**
shows communication age, errors and device diagnostics; unavailable live readings
appear as `--`.

- **Piston:** select Directional or Positional mode, speed and direction. Forward
  means up; Reverse means down. Positional moves use the requested distance and
  complete by firmware pulse count. The DRO independently measures actual travel.
  **Home** in Positional mode moves down to the bottom limit at the selected speed.
  **Set top zero** saves the current DRO position as the display reference.
- **AK80-6 CAN motor:** enter target RPM, then **Start** or **Apply speed**.
  Its **Stop** ramps a dashboard-owned run to zero before releasing torque.
  Measured RPM, current and temperature update from telemetry.
- **Solenoids 1–3:** toggle the normally closed valves. Open energizes the relay;
  closed de-energizes it. They retain their requested state until changed.
- **Interlocks:** the top and bottom limit switches block piston travel toward
  their respective endpoints while active. These protections run in firmware.
  The dashboard **E-STOP** requests the controller's software emergency stop.

## Record and export

Enter the run information in **Test Metadata**, then **Save Metadata**. The
toolbar's **Start** and **Stop** control recording; use the motor and experiment
controls for those operations. Recording starts off, but observing a solenoid
turn on also starts it automatically, including an already-open valve at startup.

After recording stops, **Export** downloads the completed run. Files remain in
`recordings/<run-id>/`: `merged_samples.csv`, per-source `*_raw.csv`,
`metadata.json`, `summary.json`, and `export.csv`. Use `--record-dir PATH` to
choose another destination.

## Run a spreadsheet experiment

Expand **Experiment spreadsheet**. The dashboard initially loads
[`examples/record_motor_solenoids.csv`](examples/record_motor_solenoids.csv).
Review the displayed rows, then click **Start experiment**. To run your own
procedure, export a spreadsheet as CSV, select the file and click **Load
spreadsheet**. Loading validates its columns, applicable fields and numeric
bounds before any rows execute. **Edit CSV** allows direct editing followed by
another **Load spreadsheet**.

The default example records the run, starts the CAN motor at **250 RPM**, opens
solenoids **1, 2, 3** with **1-second waits** between starts, selects Positional
mode, and moves the piston **up at 2 mm/s for a nominal 5 seconds** (10 mm).
After pulse-count completion it waits **5 seconds**, closes valves **3, 2, 1**,
ramps the CAN motor to zero, and stops recording. Ensure the requested travel is
available before starting it.

Rows execute in spreadsheet order while sensor acquisition continues.

| `action` | Purpose |
| --- | --- |
| `telemetry_health` | Check source freshness, transport health and selected device readiness |
| `reading_state` | Compare a DRO or thermocouple value with explicit bounds |
| `requested_state` | Change recording, an output, controller mode or piston motion |
| `wait` | Hold the current states for `duration_s`; other cells remain blank |

`source` identifies the communicator, and `device` identifies its target(s).
Separate compatible targets with semicolons, for example
`solenoid1;solenoid2;solenoid3`. Leave fields that do not apply to a row blank.
`max_age_ms` sets the allowed telemetry age; `health=ok` requires no reported
transport error. On requested-state rows, `timeout_s` starts after dispatch and
bounds command feedback. A confirmed stepper move then waits for firmware pulse
completion without a motion-completion deadline. Read-only check timeouts start
at row entry. See the other [CSV examples](examples/) for checks and monitors.

Outputs retain their state between rows, so include explicit closing/stopping
rows. A failed row halts the sequence; later cleanup rows do not execute.
Firmware interlocks and CAN lease protections still apply. Completed experiments
can be started again with **Start experiment**.

## Thermocouples and saved settings

The thermocouple panel displays enabled ED-593 inputs discovered on the first
successful read after dashboard startup. IDs **TC-0** through **TC-7** follow
physical input numbers. Enter a component name beside a reading and click
**Save names**; names are saved and included in recording metadata.

To add a probe, enable its input and select its matching thermocouple type on
the ED-593, then restart the dashboard. Temperatures and faults keep updating
between discoveries. The dashboard reads device settings without changing them.

`system_config.json` stores the DRO reference, graphic travel span, pressure
calibration, thermocouple names and other machine settings. The graphic travel
span does not limit commanded moves. Restart after editing the file manually;
use `--system-config PATH` to select another configuration.

## Troubleshooting and references

- **Address already in use:** a dashboard is already using that port. Use it,
  stop it with Ctrl+C in its terminal, or launch with `--port 8001` and open
  <http://127.0.0.1:8001/>.
- **A source is disconnected:** check its power, network route/address or USB
  connection, then inspect **Source details**. Other sources continue updating.
- **A mode selection does not change:** the selection follows confirmed controller
  telemetry. Check the Controllino connection and installed firmware.
- **A thermocouple is missing or faulted:** inspect its enabled state and diagnostic
  fault in **Source details**; verify the probe wiring and device configuration.

Further detail:

- [Firmware, wiring, calibration and controller behavior](firmware/README.md)
- [ED-593 setup and channel discovery](devices/ed593/setup.txt)
- [Firmware upload notebook](tools/firmware_upload.ipynb) — uploading resets the
  controller; make connected equipment safe before uploading.

Developers can run `python3 -m unittest discover -s tests -v`. Native firmware
checks need a C++ compiler; some checks also need GJS or the MCP2515 library and
report a skip when those dependencies are unavailable.
