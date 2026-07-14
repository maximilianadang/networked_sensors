# RUNBOOK - flow-management supervisor

Operational procedure for running and verifying the flow-management dashboard.
This starts with simulation because the supervisor is intended to be useful
before hardware is connected.

## 0. Current state

The local dashboard/API and disk-backed recorder/exporter run without hardware.
ESP32, DXMR90, and Yún stepper sources are simulated by default. Real ESP32
HTTP/SSE input, real DXMR90 Modbus input, and Yún `usb` mode are implemented.
The stepper USB path has Local Velocity, Web Position, optional D8-limit seek,
move, Stop, and calibration controls. T5 is currently uploaded and stopped-live
verified on the bench Yún; the latched software E-STOP revision is now uploaded
and its stopped-state latch/reset path is live-verified. Moving-stop tests remain.
The page's **SOFTWARE E-STOP** inhibits
STEP output through the laptop/USB/Yún-firmware chain. It does not remove motor
power and is not a hardwired, safety-rated emergency stop. Yún `network` is
reserved but not yet implemented. Start the all-simulated dashboard with:

```bash
python3 networked_sensors/dashboard.py --host 127.0.0.1 --port 8000 --record-dir networked_sensors/recordings
```

Then open:

```text
http://127.0.0.1:8000/
```

The lower-level supervisor smoke CLI also remains available. It emits merged
simulated ESP32 + DXMR90 + stepper samples as JSON lines:

```bash
python3 networked_sensors/supervisor.py --samples 12
```

Scenario controls are available for stale/missing-source development:

```bash
python3 networked_sensors/supervisor.py --scenario dxmr90_stale --samples 45 --drop-after-s 1 --stale-after-s 1
python3 networked_sensors/supervisor.py --scenario esp32_stale --samples 45 --drop-after-s 1 --stale-after-s 1
python3 networked_sensors/supervisor.py --scenario dxmr90_missing --samples 3
python3 networked_sensors/supervisor.py --scenario stepper_missing --samples 3
python3 networked_sensors/supervisor.py --scenario stepper_stale --samples 45 --drop-after-s 1 --stale-after-s 1
python3 networked_sensors/supervisor.py --scenario all_stale --samples 45 --drop-after-s 1 --stale-after-s 1
```

The dashboard accepts the same simulation scenario family:

```bash
python3 networked_sensors/dashboard.py --scenario dxmr90_missing --host 127.0.0.1 --port 8000
python3 networked_sensors/dashboard.py --scenario dxmr90_stale --drop-after-s 1 --stale-after-s 1
```

SICK/DXMR90-only live mode, with no ESP32 quorum:

```bash
python3 networked_sensors/dashboard.py \
  --esp32-source off \
  --dxmr90-source real \
  --dxmr90-host 192.168.0.1 \
  --dxmr90-data-path direct \
  --dxmr90-rate-hz 10 \
  --host 127.0.0.1 --port 8000 \
  --record-dir networked_sensors/recordings
```

`direct` reads SICK process-data windows `1002-1017` and `2002-2017` and is the
default real-hardware path. It bypasses the approximately 1 Hz ScriptBasic
republished block so actual SICK measurements reach the merged stream at 10 Hz.
Use `--dxmr90-data-path republished` only for comparison or diagnostics.

If the SICK/DXMR90 host is unreachable, the page still runs and shows
`dxmr90_connected=false` with SICK values as `null`.

ESP32-only live mode uses the headless API hosted by the primary
`Flow_management_unit_sch1.ino`:

```bash
python3 networked_sensors/dashboard.py \
  --esp32-source real \
  --esp32-url http://testbench.local \
  --dxmr90-source off \
  --stepper-source off \
  --host 127.0.0.1 --port 8000 \
  --record-dir networked_sensors/recordings
```

If mDNS is unavailable, replace `http://testbench.local` with the IP printed by
the ESP32 serial monitor. The laptop must have a route to that address. The
adapter reads `/events` in the background, reconnects after errors, and shows
the current transport error in the Sources panel. It strictly requires
version-2 readings containing `sample_ms`, three pressure values, three flow
values, three clamped pressure-sensor voltages, three clamped flow-sensor
voltages, and four solenoid states. The adapter rejects older or incomplete
readings. A separate `sol` event provides immediate state updates.

The generated protocol map should also be checked after changes to runnable
topology:

```bash
python3 networked_sensors/protocol_map.py --check
```

Other runnable paths:

```bash
# Headless ESP32 service descriptor, after flashing and connecting to WiFi
curl http://testbench.local/

# DXMR90 one-shot read
python3 networked_sensors/read_dxmr90_modbus.py --host 192.168.0.1

# DXMR90 JSON polling
python3 networked_sensors/read_dxmr90_modbus.py --host 192.168.0.1 --interval 1 --format json
```

## 1. CLI simulation smoke

This verifies schema, source cadence, and merge/staleness behavior without
starting the web server:

```bash
python3 networked_sensors/supervisor.py --samples 12
```

Expected behavior:

- each output line is a JSON object;
- ESP32-like pressure/flow values are fresh at 10 Hz;
- DXMR90-like values are held between 1 Hz updates;
- `dxmr90_age_ms` increases between polls and resets on heartbeat update;
- source modes show `sim`;
- connected flags are present for all three sources.

The simulated DXMR90 remains intentionally 1 Hz. A real source using
`--dxmr90-data-path direct --dxmr90-rate-hz 10` emits fresh SICK source readings
at 10 Hz; it is not governed by the simulation cadence.

Optional realtime pacing:

```bash
python3 networked_sensors/supervisor.py --samples 12 --realtime
```

Available scenarios:

| Scenario | Purpose |
| --- | --- |
| `healthy` | both simulated sources update normally |
| `esp32_stale` | ESP32 stops after `--drop-after-s` |
| `dxmr90_stale` | DXMR90 stops after `--drop-after-s` |
| `dxmr90_missing` | DXMR90 never emits; expected fields are `null` |
| `stepper_stale` | stepper stops after `--drop-after-s` |
| `stepper_missing` | stepper never emits; expected fields are `null` |
| `all_stale` | all three sources stop after `--drop-after-s` |

## 2. Local dashboard/API smoke

Start the dashboard:

```bash
python3 networked_sensors/dashboard.py --host 127.0.0.1 --port 8000 --record-dir networked_sensors/recordings
```

Useful endpoints:

| Endpoint | Purpose |
| --- | --- |
| `/` | local browser dashboard with separate ESP32/SICK pressure plots in bar and individual/total SICK mass-flow traces |
| `/api/state` | latest sample, run state/config, metadata, history size |
| `/api/latest` | latest sample and run state |
| `/api/history?limit=240` | recent in-memory merged samples |
| `/api/events` | SSE live stream with `state` and `sample` events |
| `/api/run/start`, `/api/run/stop` | disk-backed recording lifecycle |
| `/api/metadata` | in-memory metadata save |
| `/api/solenoid/toggle?n=0..3` | simulated or real ESP32 solenoid control; index 3 is GPIO 10 |
| `/api/stepper/status` | mode, D4/D5 authority, D6/D8 limits, command, speed, and transport health; open-loop position fields are null |
| `/api/stepper/control-mode` | `{"web_position": true|false}`; D4 must be OFF and motion stopped |
| `/api/stepper/home` | optional move to the D8 limit at fixed 1.5 mm/s; Web Position, D4 armed, D5 Reverse |
| `/api/stepper/move` | positive relative travel magnitude and speed; D5 selects direction; fixed acceleration; simulation and T5 USB |
| `/api/stepper/stop` | immediate Web Position motion abort; simulation and T5 USB |
| `/api/stepper/estop` | latch the software E-STOP in either control mode; waits for fresh Yún confirmation |
| `/api/stepper/estop/reset` | reset the latch while stopped with physical D4 OFF; waits for fresh Yún confirmation |
| `/api/stepper/speed` | USB Local Velocity speed setpoint; `{"speed_mm_s": 3.0}`, D4 must be OFF; success waits for a fresh Yún configured-speed echo |
| `/api/stepper/direction-mapping` | USB electrical DIR mapping; `{"inverted": true}`, D4 must be OFF; the page toggle submits immediately and success waits for a fresh Yún mapping echo |
| `/api/recordings` | completed recordings and active recording status |
| `/api/export/latest` | latest completed export CSV |
| `/api/export?run_id=...&file=...` | selected artifact download |

Start and stop a run from the dashboard controls. Each completed run writes:

| File | Contents |
| --- | --- |
| `merged_samples.csv` | merged rows with `run_elapsed_s`, ESP32 values, DXMR90 values, source mode, source connected flags, and source age |
| `esp32_raw.csv` | fresh ESP32 source updates |
| `dxmr90_raw.csv` | fresh DXMR90 source updates |
| `stepper_raw.csv` | fresh stepper command, local-enable, and D6/D8 status updates; USB absolute position fields remain null |
| `metadata.json` | operator metadata, run config, and artifact paths |
| `summary.json` | row counts, run config, metadata, and legacy summary metrics |
| `export.csv` | legacy-friendly metadata header plus merged CSV |

Default artifact root:

```text
networked_sensors/recordings/<run_id>/
```

### Stepper command contract

The Stepper panel is part of the same dashboard. `distance_mm` is always a
positive travel magnitude. Physical D5 selects Forward/toward-D6 or
Reverse/toward-D8 when the request is accepted:

```json
{
  "distance_mm": 5.0,
  "speed_mm_s": 1.5,
  "command_id": "optional-client-id"
}
```

The current envelope is a positive relative distance no larger than the
measured 137.18 mm stroke and speed from 0.1 through 10 mm/s. Acceleration is
not an operator field; it is fixed at a provisional 5 mm/s². A command is
rejected while busy, disarmed, missing a valid D5 selection, or pointed into an
active/latched directional limit. Home/reference and accumulated open-loop
position do not authorize motion and no absolute software target is enforced.

The supervisor resolves the positive magnitude and D5 snapshot into a signed
internal delta. The USB adapter and ATmega firmware re-check D5; a selector
change during dispatch or motion rejects/aborts instead of reversing. STEP
pulses terminate the requested relative quantity, but only D6/D8 are used as
travel-end safety inputs.

`--stepper-source network` is implemented for the Yún Linux UART bridge service.
It must not be selected until the matching T6 firmware and
`yun_stepper_bridge.py` are installed and verified on the Yún. Use
`--stepper-source off` when no stepper status should be emitted.

### USB-backed Local Velocity and Web Position

After the firmware has been compiled, uploaded under the motor-safe procedure,
and the Yún appears as a USB serial device, run:

```bash
python3 networked_sensors/dashboard.py \
  --stepper-source usb \
  --stepper-port /dev/ttyACM0 \
  --stepper-baud 9600 \
  --host 127.0.0.1 --port 8000 \
  --record-dir networked_sensors/recordings
```

Open `http://127.0.0.1:8000/`. This mode does not require the laptop to join the
bench LAN; it can retain its normal internet Wi-Fi. It reports the physical D4
enable, D5 manual direction, D6/D8 raw/active/latched state, configured and
effective speed, and an explicit motion decision such as `positive_limit`.

In **Local Velocity** mode, speed may be changed without starting motion:

1. Put D4 in OFF; the page must show `Disabled / HIGH` and zero effective speed.
2. Enter 0.1 through 10.0 mm/s and select **Apply Local Velocity Speed**.
3. Wait for **Configured speed** to show the requested value.
4. Select the safe direction with D5, then use D4 to start/stop continuous motion.

The firmware receives `V1 S10..1000`, where the integer is steps/s and the
provisional conversion is 100 steps/mm. It rejects out-of-range commands and
any speed change while D4 is ON. The laptop applies the same checks. The 10
mm/s limit is provisional software protection, not a proven
mechanical rating: begin at 1.5 mm/s and increase through short 3.0 and 5.0 mm/s
travel-away checks before attempting anything faster.

With T4C uploaded, **Electrical direction mapping** corrects the relationship
between logical D5 Forward/Reverse and the driver's DIR level. Put D4 OFF,
toggle Normal/Inverted and wait for confirmation. The firmware receives `V1 D0`
or `V1 D1`; the setting does not move the motor. D5 remains the
direction selector, and D6/D8 continue to protect logical positive/negative
travel. Starting at an active D6, test Reverse briefly at 1.5 mm/s under Normal;
if the mechanism loads into the stop rather than moving away, stop with D4,
apply Inverted while D4 is OFF, and repeat. Do not switch mapping during motion.
Like speed, direction mapping resets to Normal after an ATmega restart.

With T5 firmware, use the webpage **Control mode** toggle while D4 is OFF:

After every reset, D4 must be observed OFF once before it can arm either mode.
If D4 was left ON through reset, the firmware reports `boot_disarmed` and emits
no STEP pulses until D4 is cycled OFF.

- **Local Velocity** is the boot/default fallback. D4 runs/stops continuously,
  D5 selects Forward/Reverse, and **Apply Local Velocity Speed** changes the
  stopped setpoint.
- **Web Position** makes D4 an arm/immediate-abort and D5 the direction selector
  for the next positive travel magnitude. D5 Forward commands toward D6; D5
  Reverse commands toward D8. Changing D5 during motion aborts and never
  reverses the active command.

The optional D8-limit action is not a prerequisite for Move:

1. With D4 OFF, select Web Position and wait for the Yún confirmation.
2. Put D5 in Reverse.
3. Put D4 ON to arm motion.
4. Select **Move to D8 Limit** and confirm. Its fixed speed is 1.5 mm/s.
5. D8 activation stops motion. Put D5 Forward before moving away from D8.

For any relative move, enter a positive travel distance and positive speed,
select Forward or Reverse with D5, and arm D4. No prior Home is required and no
open-loop absolute target is checked. **Stop Motion**, D4 OFF, a D5 change, or
the destination limit aborts motion in the ATmega loop. Acceleration is fixed
at 5 mm/s² and is intentionally absent from the webpage.

The coordinate uses provisional 100 steps/mm. Before accepting dimensional
accuracy, compare a known STEP-pulse count with a DRO-measured displacement and
update the constant if needed.

The red **SOFTWARE E-STOP** near the top is deliberately a one-click action: it
sends `V1 E1`, aborts bounded motion or continuous Local Velocity, and remains
latched in the ATmega if the browser disconnects. While latched, the page
disables motion controls. To reset, first put physical D4 OFF, then use
**Reset E-STOP** and confirm; the Yún accepts `V1 E0` only while stopped with D4
HIGH. Reset does not start motion. If USB, the laptop, the Yún logic supply, or
the firmware is unavailable, this software path cannot be relied upon—use a
hardwired safety-rated stop/energy-isolation circuit for emergency safety.

If the port is absent, inaccessible, unplugged, or stops producing heartbeats,
the stepper becomes disconnected/stale while ESP32, DXMR90, the webpage, and
recording continue. Find the port with `arduino-cli board list` when that CLI is
available, or inspect `/dev/ttyACM*` without assuming the number is permanent.

### Yún LAN bridge service

The network path uses a small Python service on the Yún Linux processor rather
than calling the archived `Bridge.transfer()` API from the time-sensitive
ATmega loop. The ATmega polls Serial1 without blocking, drains status/ack JSON
only into available UART capacity, and retains all D4/D5/D6/D8 and software
E-STOP decisions. Linux validates and relays the exact same `V1` command lines;
it never generates STEP/DIR itself.

LEDEYun launches `::askconsole` on `/dev/ttyATH0`; that login shell and the
custom service cannot read the UART together. The init wrapper saves and
comments that inittab entry on start, terminates only the process attached to
the UART, and restores the exact saved console configuration on stop. First
upload and verify the matching `limit_switch_palas.ino` over USB. With motor
power still off, copy the service and start it manually over SSH, substituting
the Yún's current DHCP address. Modern OpenSSH clients require the scoped
legacy RSA and SCP flags shown here:

```bash
scp -O -o HostKeyAlgorithms=+ssh-rsa networked_sensors/yun_stepper_bridge.py root@YUN_IP:/root/
scp -O -o HostKeyAlgorithms=+ssh-rsa networked_sensors/yun-stepper-bridge.init root@YUN_IP:/etc/init.d/yun-stepper-bridge
ssh -o HostKeyAlgorithms=+ssh-rsa root@YUN_IP 'chmod 700 /root/yun_stepper_bridge.py /etc/init.d/yun-stepper-bridge && /etc/init.d/yun-stepper-bridge start'
curl --fail --max-time 2 http://YUN_IP:8080/v1/health
curl --fail --max-time 2 http://YUN_IP:8080/v1/status
```

Do not enable startup yet. Confirm the status JSON shows the real D4/D5/D6/D8
levels, zero motion, and the expected E-STOP state. Then start the laptop page:

```bash
python3 networked_sensors/dashboard.py \
  --esp32-source off --dxmr90-source off \
  --stepper-source network \
  --stepper-url http://YUN_IP:8080 \
  --stepper-timeout 0.75 \
  --host 0.0.0.0 --port 8000
```

Use `http://127.0.0.1:8000/` on the hosting laptop or
`http://HOST_LAPTOP_LAN_IP:8000/` from another LAN client. Never browse to
`0.0.0.0`; that is a bind address only.

USB and network accept the same status/command semantics. The first accepted
mutating command claims the firmware owner. A competing transport is rejected;
Stop and software E-STOP remain accepted from either transport. Ownership
releases after motion is stopped, D4 is physically OFF, and the owner is idle
for two seconds. The page waits for both the Linux acknowledgement and a fresh
ATmega status before reporting a physical command as confirmed.

After the motor-off LAN status and command-rejection checks pass, enable the
service at boot:

```bash
ssh -o HostKeyAlgorithms=+ssh-rsa root@YUN_IP '/etc/init.d/yun-stepper-bridge enable'
```

The service listens without application authentication on port 8080. Keep it
on the isolated trusted bench LAN, do not forward that port through a router,
and reserve the Yún address in DHCP.

If `/v1/command` reports an acknowledgement timeout or says the command
channel is unsynchronized, stop motion with the physical controls, inspect the
Yún log at `/tmp/yun-stepper-bridge.log`, and restart the service before issuing
another software command. The service deliberately refuses later commands
because an uncorrelated late UART acknowledgement must not be mistaken for a
new command's acknowledgement.

Roll back to USB-only operation with:

```bash
ssh -o HostKeyAlgorithms=+ssh-rsa root@YUN_IP '/etc/init.d/yun-stepper-bridge stop; /etc/init.d/yun-stepper-bridge disable'
```

`stop` restores the saved `::askconsole` entry. Reboot the Linux side if an
immediate USB login prompt is required and procd has not respawned it yet.

## 3. Current mixed real-hardware run

The current laptop-local combination is ESP32 over HTTP/SSE, DXMR90 over
Modbus TCP, and the Yún stepper over USB:

```bash
python3 networked_sensors/dashboard.py \
  --esp32-source real --esp32-url http://testbench.local \
  --dxmr90-source real --dxmr90-host 192.168.0.1 \
  --dxmr90-timeout 0.1 --dxmr90-data-path direct --dxmr90-rate-hz 10 \
  --stepper-source usb --stepper-port /dev/ttyACM0 --stepper-baud 9600 \
  --host 127.0.0.1 --port 8000
```

For the LAN-installed Yún bridge, replace the USB flags with:

```bash
  --stepper-source network --stepper-url http://YUN_IP:8080 \
  --stepper-timeout 0.75
```

The network adapter software and loopback contract pass, but this variant is
not a hardware claim until the T6 firmware/service are installed and the
motor-off status/ownership/E-STOP checklist passes on the physical Yún.

The 0.1-second DXMR90 timeout keeps an absent LAN device from making ordinary
dashboard requests sluggish. Software E-STOP dispatch independently bypasses
the shared source-poll lock: a stopped test with DXMR90 unreachable improved
from 1.26 seconds to 0.041 seconds while still waiting for a fresh Yún latch
acknowledgement. This is not a moving-load stop-time qualification.

Network assumptions:

- laptop can reach ESP32 over WiFi or local network;
- laptop Ethernet can reach DXMR90 Modbus TCP on port `502`;
- no router conflict with DXMR90 default `192.168.0.1`;
- firewall allows local browser access to the dashboard port.

## 4. Verification tiers

| Tier | Command/status | Pass condition |
| --- | --- | --- |
| imports | `python3 -m compileall -q networked_sensors` | modules parse cleanly |
| protocol drift | `python3 networked_sensors/protocol_map.py --check` | generated protocol matches `PROTOCOL.md` |
| simulated supervisor | `python3 networked_sensors/supervisor.py --samples 12` | JSONL contains source modes, connected flags, age fields |
| stale scenario | `python3 networked_sensors/supervisor.py --scenario dxmr90_stale --samples 45 --drop-after-s 1 --stale-after-s 1` | `dxmr90_connected` flips false after age threshold |
| missing scenario | `python3 networked_sensors/supervisor.py --scenario dxmr90_missing --samples 3` | DXMR90 fields are present as `null` |
| stepper contract | `python3 -m unittest -v networked_sensors.test_stepper_control` | 33 tests cover D5-selected travel, mode, D8 seek, Stop, latched software E-STOP/reset, limits, USB/network bytes/acks, ownership, rejection/timeout, nonblocking UART reads, fresh runtime acknowledgements, legacy status, and merged schema |
| Yún T5A compile/upload | temporary official CLI/core/library, `arduino:avr:yun` | 65% flash/28% RAM; 18,652 bytes uploaded and read back, fresh D4-off stopped latch/reset confirmed; moving-stop checks pending |
| Yún T6 network compile/upload | same Yún toolchain | 20,794 bytes/72% flash and 1,399 bytes/54% RAM; upload and Linux service install pass; AsteraMesh health/status report owner none, D4 OFF, clear limits/E-STOP, and zero motion; motion qualification pending |
| Yún network loopback | `python3 -m unittest -v networked_sensors.test_stepper_control.NetworkStepperSourceTests` | 7 tests pass exact UART/HTTP relay, ownership status, rejection, timeout, nonblocking UART `EAGAIN`, CLI/factory, and fresh network E-STOP confirmation |
| dashboard/API | `python3 networked_sensors/dashboard.py --host 127.0.0.1 --port 8000` | browser dashboard, JSON endpoints, SSE stream, metadata, run state, and simulated solenoid controls respond |
| simulated stepper API | dashboard plus GET status and POST move/stop/E-STOP/reset endpoints | bounded moves and a latched mode-independent software stop remain on the shared stream |
| recorder/export | `python3 networked_sensors/dashboard.py --record-dir /tmp/flow-dashboard-recordings` | start/stop writes merged/source CSVs including `stepper_raw.csv`, metadata JSON, summary JSON, and export CSV; export endpoints serve artifacts |
| SICK-only direct 10 Hz | `python3 networked_sensors/dashboard.py --esp32-source off --dxmr90-source real --dxmr90-host HOST --dxmr90-data-path direct --dxmr90-rate-hz 10` | dashboard runs without ESP32 and receives fresh direct process-data samples at 10 Hz |
| DXMR90 CLI one-shot | current reader command | heartbeat and values print without Modbus errors |
| ESP32 adapter contract | `python3 -m unittest -v networked_sensors.test_real_esp32` | strict v2 parsing/rejection, complete four-solenoid SSE projection, GPIO 10 layout, solenoid POST, and dashboard real-source selection pass against a loopback firmware-contract server |
| ESP32 headless compile | temporary official Arduino CLI with `esp32:esp32@3.3.10`, Adafruit ADS1X15/BusIO, ESP Async WebServer, and Async TCP | four-solenoid primary sketch compiles for `esp32:esp32:adafruit_feather_esp32s3_nopsram`; observed 1,095,265 bytes/52% flash and 80,940 bytes/24% global RAM |
| ESP32 physical source | `--esp32-source real --esp32-url http://ESP32_HOST` | sustained live pressure/flow, truthful source health, one deliberately safe solenoid command, and recorded ESP32 rows work |
| DXMR90 real source | implemented | direct process windows sustain 10 Hz; heartbeat and selected metrics update |
| full bench | planned | merged CSV includes both sources plus age/health fields |

## 5. Archived ESP32 dashboard firmware

The primary firmware is headless and `dashboard.py` is the current UI. The
former self-hosted implementation is preserved at
`legacy/Flow_management_unit_sch1/Flow_management_unit_sch1.ino` for reference
or an explicit fallback flash. The laptop adapter does not support its partial,
unversioned reading events. Known limitations of that archived firmware:

- CSV rows are buffered in ESP32 RAM during a test.
- UI changes require firmware edits and reflashing.
- Chart.js is loaded from a CDN unless embedded locally.
- It does not display DXMR90 Modbus values.

Do not flash the archived sketch when testing the real laptop ESP32 source; the
strict adapter will reject it and report the version error.

## 6. Standalone Yún stepper limit diagnostic

Keep the DM542T motor supply off for compilation, upload, and raw limit-input
characterization. Power the Yún only over USB. The verified board target is
`arduino:avr:yun`; install its core and the sketch dependency once:

```bash
arduino-cli core install arduino:avr
arduino-cli lib install AccelStepper
```

Arduino requires the main `.ino` name to match its sketch directory. Stage an
unchanged temporary copy of the repository source, then compile:

```bash
mkdir -p /tmp/limit_switch_palas /tmp/limit_switch_build
cp networked_sensors/limit_switch_palas.ino /tmp/limit_switch_palas/limit_switch_palas.ino
arduino-cli compile --fqbn arduino:avr:yun --output-dir /tmp/limit_switch_build /tmp/limit_switch_palas
```

Find the current port with `arduino-cli board list`, then upload the compiled
artifact. `/dev/ttyACM0` was the observed port but may change after reconnecting:

```bash
arduino-cli upload --fqbn arduino:avr:yun --port /dev/ttyACM0 --input-dir /tmp/limit_switch_build --verify /tmp/limit_switch_palas
arduino-cli monitor --port /dev/ttyACM0 --config baudrate=9600
```

If Linux reports permission denied and the port is owned by `root:dialout`, add
the operator account to that group and log out/in before retrying:

```bash
sudo usermod -aG dialout "$USER"
```

Observed switch contract:

- D6 is physically confirmed at the Forward/positive mechanical endpoint and
  D8 at the Reverse/negative endpoint under Normal electrical mapping;
- an external DRO measured 137.18 mm between the two magnetic-limit trip
  positions; this is not an absolute position or a `STEPS_PER_MM` calibration;
- both installed magnetic switches read HIGH/open away from the piston magnet;
- both read LOW/closed when the magnet is detected;
- both active-level constants are therefore LOW;
- a broken/disconnected wire reads HIGH/clear, so these normally-open switches
  do not provide broken-wire fail-safe detection.

Observed motor-driver bring-up:

- the DM542T motor supply is 24 V, while its separate control-logic selector
  must be set to 5 V for the Yún STEP/DIR signals;
- initial buzzing/rough attempted motion was caused by incorrect motor phase
  grouping at A+/A-/B+/B-, not by microstepping or STEP pulse width;
- correcting the phase grouping produced smooth manual motion with the existing
  `STEPS_PER_MM`, microstep switches, and firmware pulse settings unchanged.

### Yún Linux Wi-Fi maintenance over USB

Keep the DM542T motor supply off because this procedure temporarily replaces
the safety/motion firmware. Install the retired official Bridge library, compile
and USB-upload its `YunSerialTerminal` example, and open Serial at 115200 baud.
Send `~~` to stop the Bridge service and obtain the OpenWrt root console. Never
put a Wi-Fi key in a repository command, runbook, or chat transcript; enter it
locally with terminal echo disabled.

The configured non-secret network contract is:

- mode: station/client;
- SSID: `GL-MT3000-b3a`;
- encryption: WPA2 PSK/CCMP;
- network interface: `lan`, DHCP;
- observed bring-up address: `192.168.8.137/24` (dynamic, not guaranteed);
- observed signal during setup: -33 dBm;
- observed router: `192.168.8.1`, reachable with zero packet loss.

After committing the wireless configuration, run `wifi reload`, verify
association with `iwinfo wlan0 info`, verify an IPv4 address with
`ip -4 -o addr show wlan0`, and ping the router. Reboot the Linux side to return
normal Bridge services, then immediately restore and verify
`limit_switch_palas.ino` over USB before motor power is allowed again.
