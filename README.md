# DXMR90-4k network measurements

The exported notes describe a Banner DXMR90-4k reading SICK flow sensors over
IO-Link, converting the raw two-register IEEE-754 values with ScriptBasic, and
republishing the useful measurements into DXM local registers.

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

The same laptop dashboard can consume the primary headless ESP32's strict
version-2 SSE service (including four solenoids, with the fourth on GPIO 10)
and Yún USB or network status/control alongside the real DXMR90:

```bash
python3 networked_sensors/dashboard.py \
  --esp32-source real --esp32-url http://testbench.local \
  --dxmr90-source real --dxmr90-host 192.168.0.1 \
  --dxmr90-data-path direct --dxmr90-rate-hz 10 \
  --stepper-source usb --stepper-port /dev/ttyACM0 --stepper-baud 9600 \
  --host 127.0.0.1 --port 8000 \
  --record-dir networked_sensors/recordings
```

Use the ESP32's printed IP in `--esp32-url` if `testbench.local` does not
resolve, and replace `/dev/ttyACM0` with its stable `/dev/serial/by-id/...`
path when available. The archived self-hosted ESP32 sketch emits an older,
partial stream and is intentionally rejected by the current laptop adapter.

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
