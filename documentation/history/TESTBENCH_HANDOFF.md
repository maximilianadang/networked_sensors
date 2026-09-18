**Test Bench Controller**

Handoff Notes

An ESP32-based data acquisition and control system for pneumatic testing. The
current headless firmware reads 3 pressure sensors and 3 flow meters and
supports 4 solenoid relays; the fourth physical solenoid is pending installation.
The live dashboard, recording, and CSV export now run on the laptop.

# **Hardware**

| Component | Detail |
| :---- | :---- |
| MCU | Adafruit ESP32-S3 Feather (8 MB flash, USB-C) |
| ADCs | 2× ADS1115 on I²C — 0x48 (pressure), 0x49 (flow) |
| Level shifter | BSS138 4-channel, between ESP32 (3.3 V) and ADS1115 bus (5 V) |
| Relays | 8-channel module; IN1–IN3 existing, IN4 reserved for new GPIO 10 channel; active-LOW |
| Pressure sensors | 3× ratiometric, 0.5–4.5 V \= 0–10 bar (gauge) |
| Flow meters | 3× Festo SFAH-200, analog out set to 1–5 V, display g/min |
| Solenoids | 3× existing 24 V; fourth pending; each requires a flyback diode and may use the same parallel manual override template |

## **Pin map (ESP32-S3)**

* I²C: SDA \= GPIO 3, SCL \= GPIO 4

* Solenoid relays: GPIO 5, 6, 9, 10 → relay IN1/IN2/IN3/IN4

* ADC channels: pressure P1–P3 on ADS 0x48 A0–A2; flow F1–F3 on ADS 0x49 A0–A2

## **Power**

Runs on external 5 V (battery/buck converter into the Feather’s USB pin) — no laptop needed. All grounds (ESP32, 5 V, 24 V) **must** be tied together. Manual switches operate the solenoids even when the board is off.

# **Software**

Single-file Arduino sketch. **Libraries required** (install via Library Manager):

* Adafruit ADS1X15

* ESPAsyncWebServer (by **ESP32Async**)

* AsyncTCP (by **ESP32Async**)

Board in Arduino IDE: **Adafruit Feather ESP32-S3 No PSRAM**.

If upload fails to connect: hold **BOOT**, tap **RESET**, release BOOT, then upload.

## **Key constants (top of sketch)**

* WIFI\_SSID / WIFI\_PASS — currently AsteraMesh

* MDNS\_NAME — testbench → dashboard at http://testbench.local

* Sensor scaling: P\_\* (pressure), F\_\* (flow). F\_MAX \= 258.58 is g/min full-scale for the SFAH-200 on air; change if the sensor model or gas differs.

* RELAY\_ACTIVE\_LOW \= true — flip if relays behave inverted.

## **Runtime behavior**

* Samples all channels at **10 Hz**.

* Serves a dashboard: live numeric cards \+ two plots (3 pressures, 3 flows), plus derived **P combined** \= min(p1,p2,p3) and **F combined** \= f1+f2+f3.

* Solenoids toggle via on-screen buttons or keyboard **1 / 2 / 3**.

* Onboard LED: blinking \= connecting to WiFi, solid \= ready.

# **Using the Dashboard**

1. Power the board; wait for the LED to go solid.

2. Open http://testbench.local (macOS works natively; Windows needs Bonjour installed, or use the IP printed on the serial monitor).

3. Fill in the metadata fields (persist automatically in the browser).

4. **Start Test** → run → **Stop & Save**. A CSV downloads to your machine.

# **CSV Output**

Two sections separated by \# \===.

**Metadata header** — \# key:,value lines (comma-separated so each key/value lands in its own spreadsheet column). Auto-filled fields:

* pressure\_1/2/3\_bar and pressure\_combined\_bar — mean over the first 1.0 s

* air\_flow\_1/2/3\_g\_per\_min and air\_flow\_combined\_g\_per\_min — mean over each channel’s active window (flow rises above 10, falls below 10\)

* date — ISO 8601, from the browser clock

**Data table** — 10 Hz rows: time\_s, p1-3\_bar, f1-3\_gmin, raw \*\_volt columns, sol1-3, p\_combined\_bar, f\_combined\_gmin.

# **Architecture Notes / Gotchas**

* **CSV streaming:** data is buffered in RAM during a test and streamed to the browser via a chunked response (/test/csv); computed averages come from a small separate JSON endpoint (/test/metrics). This split was necessary — an earlier single-JSON approach truncated on long (\~1 min+) runs because of JSON-escaping overhead.

* **RAM limit:** the in-RAM buffer caps practical test length (very roughly tens of minutes at 10 Hz). For longer runs, the next step would be writing to flash/SD.

* **Floating ADC inputs** read noise, not zero. Unconnected pressure channels show a small non-zero value; unconnected flow channels read 0 because scaling clamps sub-1 V to zero. Both snap to real values once a sensor is attached.

* **Festo warmup:** SFAH meters need \~10 min after power-on for rated accuracy.

* **Pressure is gauge,** not absolute. Cheap regulator gauges can disagree by \~0.2 bar; the sensors are generally the more trustworthy reading.

# **Status: Web UI Offloaded to the Laptop**

The recommendation below has now been implemented. The primary
`Flow_management_unit_sch1.ino` is headless and emits version-3 sensor-health,
nullable sensor, and four-solenoid telemetry for laptop `dashboard.py`. Missing
ADS1115 hardware no longer prevents Wi-Fi or the solenoid control surface; the
affected measurement family is explicitly unavailable instead. The former self-hosted
sketch is preserved under `legacy/Flow_management_unit_sch1/` as historical reference;
it is not a compatibility target for the strict laptop adapter. The following
text describes the inherited design and the reason for the transition.

The current laptop runtime also isolates each real network source. ESP32 SSE,
DXMR90 Modbus, and Yún status acquisition run in source-owned workers; an absent
or slow device changes only its own error/stale state and cannot throttle fresh
measurements from the others. A blocked-Modbus regression test covers the
specific DXMR90-to-ESP32 coupling found during the field-readiness audit.

ESP32 relay control is likewise isolated from the dashboard merge lock. The
normal browser path and its GET fallback both update at 10 Hz; the firmware's
1 Hz ADC-presence check does not govern buttons or telemetry. The adapter caches
the resolved `testbench.local` address for serialized relay commands, while the
page gives immediate pending feedback and prevents duplicate toggles.

The archived dashboard (HTML, CSS, JavaScript, and the Chart.js reference) was embedded in the sketch and served by the ESP32 itself. This was convenient — one device, nothing to install — but it had real drawbacks worth flagging for whoever works with the legacy sketch:

* It eats flash and a little RAM on the ESP32, and every UI tweak means recompiling and reflashing the firmware.

* The live plotting, the growing in-RAM CSV buffer, and the chunked-download workaround all exist because a small microcontroller is doing a job better suited to a real computer. The RAM limit on test length is a direct consequence.

* It depends on Chart.js loading from a CDN, so the archived dashboard needs internet unless that library is embedded or hosted locally.

A cleaner architecture for the next iteration: **keep the ESP32 as a pure data/control device** and move all UI to the laptop. The ESP32 would expose a minimal API — stream raw samples (e.g. over a WebSocket, serial, or a small JSON/SSE endpoint) and accept simple solenoid commands — while a program on the laptop handles plotting, the metadata form, recording, and CSV export.

Practical options for the laptop side:

* A Python app (e.g. a small Flask/FastAPI server \+ browser front-end, or a native GUI with matplotlib/PyQtGraph) that connects to the ESP32 and does all the heavy lifting. Recording length is then bounded by the laptop’s disk, not the ESP32’s RAM.

* A local static web page served from the laptop that talks to the ESP32’s API — keeps the existing dashboard look, but the ESP32 no longer stores or streams the page itself.

**Benefits:** no test-length RAM limit, faster iteration on the UI (no reflashing), no CDN/internet dependency, and easierintegration with other data sources or logging pipelines. **Trade-off:** the system is no longer fully self-contained — it needs the laptop app running to view or record data, whereas today any browser on the network works with nothing installed.

**Decision implemented:** use the laptop dashboard for current testing and
production-style LAN viewing. Flash the archived sketch only for deliberate
legacy investigation, not as the data source for the current laptop UI.

## Yún stepper transport handoff

The current physically uploaded Yún image is the fixed-direction Timer1
revision. It includes a non-blocking Serial1 path, exclusive USB/network
mutation ownership, D9/ENA control, and timer-backed Local Velocity pulses.
It compiles at 22,620 bytes/78% flash and 1,453 bytes/56% RAM. Upload and
operator-confirmed Local Velocity motion pass.

Software loopback and compile checks pass; the earlier T6 Linux service is
installed. Physical AsteraMesh health, advancing stopped status, and an
expected Local-Velocity Stop rejection pass through the full HTTP/UART path.
The service is enabled at boot, a Linux restart returned synchronized status
without an SSH login, and `GL-MT3000-b3a` is stored for the next power cycle.
Use `provision_yun.sh` for one-time deployment/network changes and
`run_lan_dashboard.sh` for ordinary operation.
The uploaded firmware and laptop adapter now use 251.96850394 pulses/mm. That
comes from the motor datasheet's 0.00396875 mm/full-step value and the observed
DM542T SW5-SW8 all-ON 200-pulse/rev setting; SW4 is only the standstill-current
selector. Stopped post-upload status reports `csps:378` for the 1.5 mm/s
default. A repeated DRO distance check and high-rate smoothness test remain.
Compact status field `aps` measures the emitted pulse-counter change over
250 ms. The laptop presents this separately from configured and scheduled
speed. It is electrical open-loop evidence rather than DM542T acceptance or
piston feedback.
Do not represent the stepper as motion-qualified until the remaining
ownership/E-STOP checklist and
the moving jitter/latency/restart/disconnect checks pass. The custom service
temporarily replaces LEDEYun's stock `askconsole` as `/dev/ttyATH0` owner and
restores that console when stopped. It has no application authentication, so
it belongs only on the isolated bench LAN.

The current safety revision retires the former runtime Normal/Inverted
direction mapping. Physical endpoint testing established Normal as
Forward/positive to D6 and Reverse/negative to D8, but the old toggle could
reverse the motor while the interlock still selected the logical endpoint. It removes
`V1 D` across firmware/bridge/adapter/page, centralizes physical-direction
interlocks, and flags legacy `ds:-1` status as unsafe for Web Position commands.
It also connects DM542T `ENA-` to Yún D9 while `ENA+` remains at 5 V. Firmware
holds D9 LOW whenever stopped, blocked, or software-E-stopped, raises it only
for permitted motion, waits 200 ms before STEP, and publishes compact `en`.
Thirty-seven desktop tests, the current target compile, verified USB upload,
stopped `ds:1`/`en:0`/`aps:0`, stale opposite-limit correction, and an
operator-confirmed Local Velocity run pass. The installed AR9331 bridge is an
older deployment and must be replaced from this checkout before LAN testing.
The exact-image two-endpoint D9/retreat matrix and Web Position timing remain;
do not claim full physical motion qualification before those pass.
