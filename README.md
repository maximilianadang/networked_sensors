# DXMR90-4k network measurements

The exported notes describe a Banner DXMR90-4k reading SICK flow sensors over
IO-Link, converting the raw two-register IEEE-754 values with ScriptBasic, and
republishing the useful measurements into DXM local registers.

## USB firmware uploads

Use [`firmware_upload.ipynb`](firmware_upload.ipynb) to compile and upload a
controller sketch over USB. Its configuration cell selects the target,
payload `.ino`, and port:

| Target | Arduino FQBN | Default payload |
| --- | --- | --- |
| `controllino` | `CONTROLLINO_Boards:avr:controllino_maxi_automation` | `controllino_ethernet_diagnostic.ino` |
| `esp32` | `esp32:esp32:adafruit_feather_esp32s3_nopsram` | `Flow_management_unit_sch1.ino` |
| `yun` | `arduino:avr:yun` | `limit_switch_palas.ino` |

Start Jupyter from this directory, open the notebook, run its preflight cells,
and set `SAFETY_CONFIRMED = True` only after completing the printed hardware
check. The uploader stages a hash-checked temporary sketch directory, including
existing local quoted includes such as the ignored `wifi_credentials.h`,
compiles, selects only an unambiguous exact-FQBN USB match when `PORT = "auto"`,
uploads, and requests verification. An explicit port can be used when board
discovery does not report an FQBN.

The notebook automatically uses the downloaded workspace Arduino CLI under
`../tools/arduino-cli-*` and the isolated `.arduino-build/arduino-cli.yaml`
configuration. The local macOS ARM64 installation uses Arduino CLI 1.5.1,
ESP32 core 3.3.10, Arduino AVR core 1.8.8, Controllino AVR core 3.1.3,
Ethernet 2.0.2, Adafruit ADS1X15 2.6.2, Adafruit BusIO 1.17.4,
ESP Async WebServer 3.12.1, and Async TCP 3.5.0. If the
workspace toolchain is absent, it falls back to `arduino-cli` on `PATH`; the
configuration cell also accepts explicit CLI and config paths. The Yún sketch
has no external library dependency.

Keep the Yún DM542T motor supply off, disconnect the brushless ESC battery, and
set D4 OFF before upload. Stop any dashboard or serial monitor currently holding
the selected USB port.

From this directory, inspect the workspace toolchain or connected USB boards:

```sh
../tools/arduino-cli-1.5.1/arduino-cli --config-file .arduino-build/arduino-cli.yaml core list
../tools/arduino-cli-1.5.1/arduino-cli --config-file .arduino-build/arduino-cli.yaml board list
```

Board packages, libraries, downloads, and the build cache live under the ignored
`.arduino-build/` directory. The notebook discovers this installation automatically. All five current top-level
sketches have passed local target compilation; details and installed package
versions are saved in `.arduino-build/verification.md` and
`.arduino-build/toolchain-manifest.json`.
For ESP32 deployment, create the ignored `wifi_credentials.h` using
`wifi_credentials.example.h`; build-verification placeholder credentials are
confined to `.arduino-build/verify/` and are not deployment credentials.

On the Yún, this USB workflow updates only the ATmega32U4 `.ino` firmware.
`yun_stepper_bridge.py` runs on the separate Linux processor and must still be
deployed over SSH with `provision_yun.sh`.

## Controllino version and auxiliary output

The current sketch reports firmware **1.1.0** and its auxiliary capability.
The default build supports a position servo on X1 Digital 2 (Arduino D4);
`CONTROLLINO_AUX_SERVO=0` selects the existing brushless ESC instead. The
connected firmware selects the dashboard panel automatically. Servo pulses
start disabled; apply a pulse position within the firmware's reported limits.
See [firmware versions and calibration](CONTROLLINO-FIRMWARE.md).

## Firmware wiring configuration

Edit the board header to change wiring; the sketches consume named signals:

| Board | Wiring source | Used by |
| --- | --- | --- |
| Controllino MAXI Automation | [`wiring_controllino.h`](wiring_controllino.h) | motion controller and stepper bring-up |
| Feather ESP32-S3 | [`wiring_esp32.h`](wiring_esp32.h) | headless flow-management firmware |
| Yún | [`wiring_yun.h`](wiring_yun.h) | `limit_switch_palas.ino` |

For example, moving the Controllino direction wire changes `PIN_DIR` in one
place, shared by both sketches. On ESP32, reorder `SOLENOID_PINS` or the ADC
channel arrays to preserve logical channel order after rewiring. Pin values are
Arduino/GPIO numbers; adjacent terminal comments describe the installed wiring.
Update those comments when moving a wire. ESP32 ADC addresses and relay polarity,
and Controllino output polarities, also live in their board headers.

Compile-time checks reject duplicate assignments, listed reserved-pin conflicts,
and unsupported fixed-function remaps. The bring-up sketch's STEP output is
fixed to D3/OC3C; the Controllino motion sketch uses an ISR and has no such pin
restriction. Yún DRO capture remains fixed to D10/PB6 and D11/PB7. Its direction
calibration stays in the motion code. Legacy status names such as `d4` and `d6`
retain their protocol meanings after rewiring; they are not a live pin map.
The protocol-map checker separately pins the documented installed Yún wiring;
update that installation contract when changing the physical installation.

These checks do not establish that an arbitrary pin exists, is exposed, or has
suitable electrical characteristics. Match the selected board and its pinout.
Changing channel counts or adding hardware is a feature change, not a wiring
change; the ESP32 v3 contract still has four solenoids and three channels per ADC.
The Ethernet-only diagnostic and archived/example sketches do not use these maps.

The uploader automatically stages these headers and their shared checks alongside
the selected sketch. From this directory, run:

```sh
PYTHONPATH=.. python3 -m unittest test_wiring test_controllino_motion_firmware test_firmware_upload
```

`test_wiring` uses a host C++11 compiler with Arduino pin definitions stubbed to
check valid/invalid configurations and verifies upload staging. A target Arduino
compile and physical wiring verification are still required before deployment.

## Controllino DRO

The motion firmware reads the existing iGaging AbsoluteDRO Plus on these **X1
logic-level signals**, through the existing level shifter:

| DRO signal | Controllino label | Gray chip-pin number | Port | Firmware constant |
| --- | --- | --- | --- | --- |
| Clock | SCL | 43 | PD0 | `PIN_DRO_CLOCK = 21` |
| Data | Digital 4 | 15 | PH3 | `PIN_DRO_DATA = 6` |

The gray numbers identify the ATmega package pins, not connector positions or
Arduino code numbers. Both assignments live in `wiring_controllino.h`. Clock must
support an external interrupt; changing it to an unsupported pin fails compilation.
The data input register/mask are derived from the selected pin rather than hardcoded.
The DRO's existing REQ-to-GND modification and level-shifter connections remain
required. The older proposed migration drawing uses a different pin allocation.

The code path is deliberately small:

- `absolute_dro_protocol.h`: assemble 52 clocked bits; validate sign, six decimal
  digits, two decimal places, and millimetre units. Shared with the Yún firmware.
- `absolute_dro_avr.h`: configure inputs, capture falling edges, hand the newest
  complete frame to the loop, and report position/freshness/frame diagnostics.
- `controllino_motion_control.ino`: connect the reader to the wiring map and the
  existing USB/Ethernet status output. STEP and ESC timer ownership is unchanged.

No valid frame means capable but not fresh, with age `-1`. A sample becomes stale
past 250 ms, measured from capture time rather than when the loop processes it.
Rejected frames do not refresh the last good reading. If the loop falls behind,
the newest complete frame replaces the pending one and increments the dropped
counter. These diagnostics saturate at 255 rather than wrapping.

The lean dashboard already consumes this telemetry: raw position, saved display
zero, signed velocity, and frame diagnostics. **Set zero here** requires a fresh
reading with motion stopped; **Move to zero** remains disabled. DRO readings do
not authorize or control motion. The piston display retains its existing
raw-minus-zero sign convention and configured display span.

Launch the lean dashboard from this directory:

```sh
python3 dashboard-lean.py --stepper-source controllino --stepper-url http://10.77.0.10 --esp32-source off --dxmr90-source off
```

For USB, use the same machine protocol over serial:

```sh
python3 dashboard-lean.py --stepper-source controllino-usb --stepper-port /dev/cu.usbmodem1101 --stepper-baud 9600 --esp32-source off --dxmr90-source off
```

The serial device name may change after reconnecting. Select `real` for the other sources when they are needed. The existing
`run_controllino_dashboard.sh` still launches the original dashboard; both use the
same DRO backend. Upload `controllino_motion_control.ino` explicitly in the
notebook: the Controllino upload default remains the Ethernet-only diagnostic,
which does not read the DRO.

Validation: `PYTHONPATH=.. python3 -m unittest test_dro_firmware` executes the real
C++ frame reader with injected edges and passes its status into the Controllino
adapter and lean runtime. It covers signed values, malformed frames, stale and
missing data, delayed processing, buffer overruns, timestamp rollover, saved zero,
and derived velocity. Target builds pass for Controllino motion/bring-up and Yún.
The motion firmware has been uploaded and verified on the MAXI Automation.
USB dashboard telemetry and physical DRO capture are verified with motion stopped:
clock on X1 SCL and data on Digital 4 produced fresh 94.16 mm readings, increasing
valid-frame counts, and zero rejected frames. Digital 0 previously remained low
in direct-register testing; the cause is unresolved. Coexistence with active
motion remains unverified. X1 SCL is reserved for the DRO, not I2C.

### Solenoid 4 on Controllino R5

2026-09-15: motion firmware compiled (21,318 bytes flash / 1,025 bytes RAM),
USB-uploaded with verification, and the restarted dashboard confirmed R5 capability
and OFF state. Physical valve actuation was not exercised during deployment.

With `--stepper-source controllino` or `controllino-usb`, dashboard Solenoid 4
routes to terminal **R5** (Arduino pin 27) in `wiring_controllino.h`. Solenoids
1–3 retain their ESP32 controls. The official mapping is documented in
[Controllino.h](https://github.com/CONTROLLINO-PLC/CONTROLLINO_Library/blob/master/Controllino.h).
The relay energizes on HIGH, starts de-energized, and is de-energized by software
E-STOP; opening it while E-STOP is latched is rejected.

The motion sketch accepts idempotent `V1 L4,0` / `V1 L4,1` commands over either
transport and reports `sol4:0|1`. The dashboard waits for fresh status matching
the requested state. Older firmware without `sol4` disables this control rather
than falling back to ESP32 GPIO 10. Upload the updated motion sketch and restart
the dashboard before using it. No ESP32 reflash is needed for this routing.

Merged samples expose `solenoid1_on` through `solenoid4_on`, each with `_source`
and `_connected` fields. Source-specific `esp32_sol*` values remain raw ESP32
telemetry. The fourth button and SICK open-line flow calculation use the logical
Solenoid 4 state, including when ESP32 is offline. Reported state represents the
controller's output command; it is not independent valve-position feedback.

## Network setup

1. Put the laptop Ethernet interface on the same subnet as the DXM, for example
   `192.168.0.10` with netmask `255.255.255.0`.
2. The DXM default IP in the notes is `192.168.0.1`. If a LAN router is also
   using `192.168.0.1`, change either the router or the DXM so there is no IP
   conflict.
3. Wait at least 20 seconds after DXM power-up; the notes say the scripts start
   after 20 seconds.
4. Confirm basic connectivity:

   ```bash
   ping 192.168.0.1
   ```

## Read the useful values

The network-facing path is Modbus TCP on port `502`. Read DXM holding/local
registers. The heartbeat at `12001` should increment when the script is running.
The useful values start at `13001`.

For the live dashboard, prefer the direct IO-Link process windows at
`1002-1017` and `2002-2017`. The ScriptBasic `13001+` block was observed to
update only about once per second, while the direct adapter sustains 10 Hz:

```bash
python3 networked_sensors/dashboard.py \
  --esp32-source off \
  --dxmr90-source real \
  --dxmr90-host 192.168.0.1 \
  --dxmr90-data-path direct \
  --dxmr90-rate-hz 10
```

Use `--dxmr90-data-path republished` when specifically diagnosing the
ScriptBasic output block.

The same laptop dashboard can consume the primary headless ESP32's version-3
SSE service (including four solenoids, with the fourth on GPIO 10) and Yún USB
or network status/control alongside the real DXMR90. Version 3 keeps the
controller stream live when either ADS1115 is absent and marks that sensor
family unavailable; healthy version-2 firmware remains accepted:

```bash
python3 networked_sensors/dashboard.py \
  --esp32-source real --esp32-url http://testbench.local \
  --dxmr90-source real --dxmr90-host 192.168.0.1 \
  --dxmr90-data-path direct --dxmr90-rate-hz 10 \
  --stepper-source usb --stepper-port /dev/ttyACM0 --stepper-baud 9600 \
  --host 127.0.0.1 --port 8000 \
  --record-dir networked_sensors/recordings
```

The dashboard loads machine-level settings from
`networked_sensors/system_config.json` by default. **Set zero here** updates the
saved DRO reference atomically, so dashboard restarts and USB/LAN reconnections
reuse it. Pass `--system-config PATH` only to select a different machine
configuration.

Use the ESP32's printed IP in `--esp32-url` if `testbench.local` does not
resolve, and replace `/dev/ttyACM0` with its stable `/dev/serial/by-id/...`
path when available. The archived self-hosted ESP32 sketch emits an older,
partial stream and is intentionally rejected by the current laptop adapter.
The page receives state at 10 Hz over SSE and retains a 10 Hz polling fallback.
Relay commands do not pause that stream; the adapter resolves a `.local` ESP32
once and reuses its address so each button press avoids another mDNS lookup.

After installing the matching T6 firmware and `yun_stepper_bridge.py` service
on the Yún, use its reserved DHCP address instead of a USB device:

```bash
python3 networked_sensors/dashboard.py \
  --esp32-source real --esp32-url http://testbench.local \
  --dxmr90-source real --dxmr90-host 192.168.0.1 \
  --dxmr90-data-path direct --dxmr90-rate-hz 10 \
  --stepper-source network --stepper-url http://YUN_IP:8080 \
  --stepper-timeout 0.75 \
  --host 0.0.0.0 --port 8000
```

The Yún service has no application authentication and belongs only on the
isolated trusted bench LAN. See `RUNBOOK.md` for installation, motor-off
verification, ownership, rollback, and browser-address instructions.

### Cold-start Yún and one-command LAN dashboard

Provision a Yún once while it is reachable on its current network. The optional
second argument stores the Wi-Fi network it should join after its next power
cycle:

```bash
networked_sensors/provision_yun.sh CURRENT_YUN_IP GL-MT3000-b3a
```

The first run creates `~/.ssh/yun_stepper`, may ask once for the Yún root
password to install its public key, installs the bridge, and enables it at
boot. When a target SSID is supplied, it also asks once for that Wi-Fi password.
The provisioner does not store entered passwords in this repository. The ESP32
uses its compile-time lab-WLAN credential from `Flow_management_unit_sch1.ino`.
Reserve device addresses in the LAN router when possible.

Ordinary cold starts require no SSH: power the Yún, allow roughly one minute
for its Linux/Wi-Fi side to boot, and run:

```bash
networked_sensors/run_lan_dashboard.sh
```

For the Controllino MAXI direct-Ethernet motion transport, run:

```bash
networked_sensors/run_controllino_dashboard.sh
```

It defaults to `http://10.77.0.10`; override that with
`CONTROLLINO_URL=http://CONTROLLER_IP` when needed. This runtime transport is
independent of the Ethernet firmware-upload workflow.

The Yún wrapper defaults to the network Yún at `http://arduino.local:8080`, the
real DXMR90 at `192.168.0.1`, and the real ESP32 at
`http://testbench.local`. Each source remains independent; an unavailable
device reports disconnected without preventing the other sources from running.
ESP32 SSE, DXMR90 Modbus, and Yún network reads use source-owned workers, so a
connection timeout cannot stall the dashboard's 10 Hz merge/recording loop.
Override only what differs, for example:

```bash
YUN_URL=http://192.168.8.137:8080 \
networked_sensors/run_lan_dashboard.sh
```

Open `http://127.0.0.1:8000/` on the hosting laptop. SSH is now a maintenance
path for deployments and diagnostics, not part of the normal startup sequence.

This repo includes a no-dependency Python reader:

```bash
python3 networked_sensors/read_dxmr90_modbus.py --host 192.168.0.1
```

Poll once per second:

```bash
python3 networked_sensors/read_dxmr90_modbus.py --host 192.168.0.1 --interval 1
```

Read every documented output register:

```bash
python3 networked_sensors/read_dxmr90_modbus.py --host 192.168.0.1 --group all
```

JSON output for logging or another program:

```bash
python3 networked_sensors/read_dxmr90_modbus.py --host 192.168.0.1 --format json
```

If values look like tiny or enormous nonsense, try the alternate Modbus
conventions:

```bash
python3 networked_sensors/read_dxmr90_modbus.py --host 192.168.0.1 --word-order low-high
python3 networked_sensors/read_dxmr90_modbus.py --host 192.168.0.1 --addressing zero-based
```

## Register map from the export

The float measurements use two consecutive 16-bit registers, high word first by
default.

| Register | Measurement | Unit |
| --- | --- | --- |
| `12001` | heartbeat | count |
| `13001-13002` | port 1 mass flow rate | g/min |
| `13003-13004` | port 2 mass flow rate | g/min |
| `13005-13006` | total mass flow rate | g/min |
| `13007-13008` | port 1 pressure | psi |
| `13009-13010` | port 2 pressure | psi |
| `13011-13012` | pressure delta P1-P2 | psi |
| `13013-13014` | port 1 pressure | bar |
| `13015-13016` | port 2 pressure | bar |
| `13017-13018` | pressure delta P1-P2 | bar |
| `13019-13020` | port 1 temperature | C |
| `13021-13022` | port 2 temperature | C |
| `13023-13024` | temperature delta P1-P2 | C |
| `13025-13026` | port 1 volumetric flow rate | L/min |
| `13027-13028` | port 2 volumetric flow rate | L/min |
| `13029-13030` | total volumetric flow rate | L/min |
| `13031-13032` | port 1 flow velocity | m/s |
| `13033-13034` | port 2 flow velocity | m/s |
| `13035-13036` | port 1 mass counter | g |
| `13037-13038` | port 2 mass counter | g |
| `13039-13040` | total mass counter | g |
| `13041-13042` | port 1 volume counter | L |
| `13043-13044` | port 2 volume counter | L |
| `13045-13046` | total volume counter | L |
| `13047-13048` | port 1 energy counter | Wh |
| `13049-13050` | port 2 energy counter | Wh |
| `13051-13052` | total energy counter | Wh |
| `13053-13054` | temperature delta P2-P1 | C |
| `13055-13056` | absolute temperature delta | C |
| `13057-13058` | temperature delta P1-P2 x 10 | C x10 |

Controllino motion uses the selected pulse rate immediately, with no acceleration
or deceleration ramp. Speed changes take effect at a pulse boundary. The 250 ms
driver-enable wake delay remains before the first pulse. Finite moves stop at
their target pulse count.
