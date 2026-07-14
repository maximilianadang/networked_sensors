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
`Flow_management_unit_sch1.ino` is headless and emits complete version-2 sensor
and four-solenoid telemetry for laptop `dashboard.py`. The former self-hosted
sketch is preserved under `legacy/Flow_management_unit_sch1/` as historical reference;
it is not a compatibility target for the strict laptop adapter. The following
text describes the inherited design and the reason for the transition.

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
