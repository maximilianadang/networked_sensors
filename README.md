# Networked sensors and experiment control

Run the bench dashboard to monitor sensors, control the piston, CAN motor and
valves, record measurements, and execute experiments designed as CSV spreadsheets.

## Start the dashboard

Use Python 3.10 or newer. Ethernet operation supports Linux, macOS and Windows
using Python's standard library. Firmware uploads have separate toolchain
requirements. From this directory, run on Linux or macOS:

```sh
python3 dashboard.py --host 127.0.0.1 --port 8000
```

On Windows, use the Python launcher:

```powershell
py -3 dashboard.py --host 127.0.0.1 --port 8000
```

If `py` is unavailable, use `python` instead of `py -3`.

Open <http://127.0.0.1:8000/>. Keep the terminal open and press **Ctrl+C** there
to shut down the dashboard. Closing the browser leaves the backend running;
an active CAN motor run continues while its backend and controller remain healthy.

`--host` controls browser access; it does not change sensor communication:

| Launch setting | Browser access |
| --- | --- |
| `--host 127.0.0.1` | Only the computer running the dashboard |
| `--host 0.0.0.0` | Any of that computer's network interfaces, including Ethernet and Wi-Fi |

For shared access over Ethernet or the **Mars** Wi-Fi network, run one dashboard
instance with:

```sh
python3 dashboard.py --host 0.0.0.0 --port 8000
```

On Windows, replace `python3` with `py -3`. Other devices open
`http://<dashboard-computer-IP>:8000/`, using the computer's reachable IP address,
not `0.0.0.0`. The dashboard computer can use Ethernet while browser clients use
Mars Wi-Fi, provided the access point bridges or routes to that LAN. Wi-Fi isolation
and the computer's firewall must allow access to TCP port 8000. Clients need only
a browser and share the same dashboard state and controls. There is no login;
use a trusted private network because anyone with access can control equipment.

If `--host` is omitted, the default is `0.0.0.0` unless `DASHBOARD_HOST` overrides it.

### Configure the sensor-network adapter

The default launch connects to the following real devices. The computer running
the dashboard needs network access to both address ranges shown below. Configure
its Ethernet adapter with an unused IPv4 address in each subnet (`10.77.0.0/24`
and `192.168.0.0/24`).
For example, use `10.77.0.3/24` and `192.168.0.20/24` if those addresses are free.
Each dashboard computer needs different addresses; this OS network configuration
is outside Git. Browser-only clients need access to the dashboard computer, not
direct access to both sensor subnets.

| Source | Default address | Role |
| --- | --- | --- |
| Controllino MAXI | `10.77.0.10:80` (TCP) | Piston/stepper, LinACE DRO, travel limits, AK80-6 CAN motor, three solenoids and seven pressure inputs |
| DXMR90 | `192.168.0.1:502` (Modbus TCP) | Three SICK flow sensors |
| ED-593 | `10.77.0.11:9500` (DCON ASCII) | Thermocouple inputs TC0–TC7 |

Controllino Ethernet requires firmware **1.6.0 or newer**. Override addresses with
`--stepper-url tcp://HOST:PORT`, `--dxmr90-host HOST` or `--ed593-host HOST`.
Disable an unused source with `--dxmr90-source off` or `--ed593-source off`.

The steps below use a dedicated Ethernet adapter connected to the sensor switch's
**LAN** side. Keep your Wi-Fi/Internet adapter's settings unchanged. Use both
addresses on the **same Ethernet adapter**:

| Computer address (example; must be unused) | Subnet mask | Reaches |
| --- | --- | --- |
| `192.168.0.20` | `255.255.255.0` (`/24`) | DXMR90 |
| `10.77.0.3` | `255.255.255.0` (`/24`) | Controllino and ED-593 |

If the DXMR90 already works with an existing static `192.168.0.x` address, keep
that address instead of `192.168.0.20`. These are computer addresses; keep the
devices' addresses unchanged. The dedicated sensor adapter needs no default
gateway or DNS server. Administrator access may be needed to change these settings.

#### Windows

1. Press **Win+R**, enter `ncpa.cpl`, and press Enter to open Network Connections.
2. Right-click the Ethernet adapter connected to the sensor switch and select
   **Properties**. Select **Internet Protocol Version 4 (TCP/IPv4)**, then **Properties**.
3. Select **Use the following IP address**. Set the address to `192.168.0.20`
   (or keep your existing static address) and the subnet mask to `255.255.255.0`.
   Leave the default gateway and DNS server fields blank on this dedicated adapter.
4. Click **Advanced…**. Under **IP addresses**, click **Add…** and enter
   `10.77.0.3` with subnet mask `255.255.255.0`. Both addresses should now appear
   in the list.
5. Confirm with **OK** in each dialog. Run `ipconfig` in PowerShell or Command
   Prompt and confirm both addresses appear under that Ethernet adapter.

These settings persist across restarts. If the adapter previously used DHCP,
this procedure replaces it with the two static addresses above.

#### Linux (NetworkManager)

Find the active Ethernet connection's profile name:

```sh
nmcli -f NAME,DEVICE connection show --active
```

Replace `Wired connection 1` below with that profile name and adjust the example
addresses if necessary. Save both addresses to the profile, then reconnect it:

```sh
sudo nmcli connection modify "Wired connection 1" \
  ipv4.method manual ipv4.addresses "192.168.0.20/24,10.77.0.3/24" \
  ipv4.gateway "" ipv4.dns "" ipv4.never-default yes
sudo nmcli connection up "Wired connection 1"
```

Run `ip -br -4 address` to confirm both addresses appear on the Ethernet adapter.
These settings persist across restarts. On Linux systems without NetworkManager,
configure the same two static addresses through the installed network manager.

#### macOS

1. Open **System Settings → Network**, select the Ethernet adapter connected to
   the sensor switch, then open **Details → TCP/IP**. On older macOS versions,
   use **System Preferences → Network → Advanced → TCP/IP**.
2. Set **Configure IPv4** to **Manually**. Enter `192.168.0.20` (or keep your
   existing static address), subnet mask `255.255.255.0`, and leave **Router**
   blank. Apply the settings.
3. In Terminal, run `networksetup -listallhardwareports`. Find the **Device**
   name for that Ethernet adapter, such as `en5`; do not select the Wi-Fi device.
4. Add the second address, replacing `en5` with the device name you found:

```sh
sudo ifconfig en5 alias 10.77.0.3 netmask 255.255.255.0
ifconfig en5
```

Both IPv4 addresses should appear in the output. The first address is saved in
Network settings; the `ifconfig` alias is temporary. Reapply the alias after a
restart or if reconnecting the adapter removes it, before launching the dashboard.

#### Verify connectivity

On Windows, run:

```powershell
ping -n 2 192.168.0.1
ping -n 2 10.77.0.10
ping -n 2 10.77.0.11
```

On Linux or macOS, use `-c 2` instead of `-n 2`. Replies confirm IP reachability;
then launch the dashboard and check **Source details** for application-level
connection errors. If only the DXMR90 replies, check the adapter's `10.77.0.x/24`
address. If only the Controllino and ED-593 reply, check its `192.168.0.x/24` address.
Sharing a switch alone does not provide routes between the two IP subnets.

### Optional USB connection

For a direct USB connection to the current Controllino AK80-6 build on Linux, use:

```sh
python3 dashboard.py --host 127.0.0.1 \
  --stepper-source controllino-usb --stepper-port auto --stepper-baud 115200
```

Linux USB discovery follows the controller across port renumbering. If several
compatible boards are attached, select one with `--stepper-port /dev/serial/by-id/...`.
On macOS, use an explicit USB port such as `--stepper-port /dev/cu.usbmodemXXXX`
with the same source and baud options. Windows uses Ethernet; the built-in USB
serial transport requires Unix support.

Use `python3 dashboard.py --help` for all launch options. Explicit CLI options
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

The [CSV reference](examples/README.md) lists actions, parameters and limits for the installed bench.

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
