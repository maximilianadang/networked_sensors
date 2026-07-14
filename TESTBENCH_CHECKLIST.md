# TESTBENCH_CHECKLIST - flow-management bench bring-up

Hardware and network checks that simulation cannot validate. This is the
test-bench translation of `dpc-flight/FLIGHT_CHECKLIST.md`.

## 1. Power and grounds

- ESP32 Feather receives stable 5 V power.
- ADS1115 boards and sensors receive their required supply.
- ESP32, 5 V, and 24 V grounds are tied together.
- Manual solenoid override switches behave as expected with the board off.

## 2. ESP32 network

- ESP32 joins the intended 2.4 GHz network.
- `http://testbench.local` resolves, or the serial monitor IP is reachable.
- `curl http://testbench.local/` returns the headless version-2 JSON service
  descriptor; no ESP32-hosted webpage is expected from the primary firmware.
- `/events` streams readings for at least one minute without disconnecting.
- Laptop dashboard started with
  `--esp32-source real --esp32-url http://testbench.local` reports ESP32 Live,
  advances pressure/flow at 10 Hz, and shows no transport error.
- If `testbench.local` does not resolve, the ESP32 serial-monitor IP works in
  `--esp32-url`; the laptop has a route to that address.
- A deliberately safe laptop-dashboard solenoid toggle changes the expected
  relay and its state returns in complete readings and the immediate `sol` event.
- Real ESP32 readings show version 1, advancing `sample_ms`, pressure/flow,
  clamped pressure/flow sensor voltages, and all four solenoid states. An
  ESP32 version error means older firmware was flashed and must be replaced by
  the primary headless sketch.

## 3. ADS1115 and analog sensors

- ADS1115 at `0x48` is pressure; ADS1115 at `0x49` is flow.
- Pressure channels map 0.5-4.5 V to 0-10 bar gauge.
- Festo flow channels map 1-5 V to 0-258.58 g/min unless the sensor/gas changes.
- Floating pressure inputs may read non-zero noise; connect real sensors before
  judging offsets.
- Festo SFAH meters have warmed up for roughly 10 minutes before accuracy tests.

## 4. Solenoids and relays

- Relay module is active-low as configured, or `RELAY_ACTIVE_LOW` has been
  flipped.
- Existing channels map GPIO 5/6/9 to relay IN1/IN2/IN3.
- Before enabling Solenoid 4, wire GPIO 10 only to relay IN4—not directly to the
  24 V coil—and verify the ESP32/relay grounds are common.
- Solenoid 4 has the same correctly oriented flyback diode and, if desired, the
  same parallel manual-override template as the existing coils.
- All four outputs are OFF after ESP32 reset before any command is issued.
- The four on-screen buttons toggle their expected valves; Solenoid 4 state
  returns as `esp32_sol4`.
- Flyback diodes are installed across solenoids.
- A safe dry toggle test has been performed before pressurized operation.

## 5. DXMR90 network

- Laptop Ethernet is on the DXMR90 subnet, for example `192.168.0.10/24`.
- DXMR90 default `192.168.0.1` does not conflict with a router.
- Device has had at least 20 seconds after power-up for scripts to start.
- `ping 192.168.0.1` succeeds.
- `read_dxmr90_modbus.py --host 192.168.0.1` shows heartbeat and plausible
  values.
- The live dashboard uses `--dxmr90-data-path direct --dxmr90-rate-hz 10` and
  its recent history advances at 0.1-second intervals.
- If values look nonsensical, try alternate `--word-order` or `--addressing` as
  documented in `README.md`.

## 6. Laptop supervisor readiness

Before using the laptop supervisor as the primary logger:

- simulation mode has passed start/stop/export;
- real ESP32 source shows live values and solenoid state;
- `python3 -m unittest -v networked_sensors.test_real_esp32` passes before the
  physical stream/toggle smoke;
- real DXMR90 source shows direct SICK measurements at 10 Hz plus heartbeat;
- SICK pressure cards and the dedicated SICK pressure plot use bar;
- the mass-flow plot shows SICK 1, SICK 2, and their total independently;
- dashboard clearly shows source mode and stale/disconnected status;
- merged CSV contains source age fields;
- simulation-only stepper Move/Stop/status works on the same page and a test
  recording contains `stepper_raw.csv`;
- archived ESP32 dashboard firmware remains available for reference, but is
  not compatible with the strict laptop real-source adapter.

## 7. Run shutdown

- Stop recording before powering down sensors.
- Confirm CSV/export exists on laptop disk.
- Turn off solenoids before depressurizing or disconnecting lines.
- Preserve raw logs when a run has any source disconnect or unexpected value.

## 8. Yún stepper magnetic limits

- Confirm each device is a passive two-wire dry-contact switch before attaching
  it between a digital input and GND; identify any powered sensor first.
- With motor-driver power off, open the Serial monitor at 9600 baud and move the
  piston magnet (or a test magnet) through each sensor's active area.
- Record D6 and D8 both far from and near the magnet: INPUT_PULLUP reports an
  open contact as HIGH and a closed contact as LOW.
- The currently installed switches were observed open/HIGH away and closed/LOW
  near the magnet; confirm both active-level constants remain LOW unless the
  sensors or interface wiring change.
- Verify that D6 blocks only positive travel, D8 blocks only negative travel,
  and each direction can move away from its activated limit.
- If a switch is open away from the magnet and closes near it, record that a
  broken wire looks like the normal away state and is therefore not fail-safe.

## 9. Yún stepper driver and network

- DM542T main supply is 24 V and its separate logic selector is set to 5 V.
- A+/A- are one verified motor phase and B+/B- are the other; incorrect phase
  grouping previously caused buzzing/rough attempted motion.
- Current and SW5-SW8 microstep positions are recorded before changing firmware
  calibration.
- `GL-MT3000-b3a` association, DHCP, signal, and gateway reachability pass after
  a Linux reboot; do not treat a one-time DHCP address as fixed.
- Any temporary maintenance sketch is replaced by verified stepper firmware
  before applying DM542T motor power.
- Powered testing confirms physical forward/reverse mapping, both directional
  limit stops, and motion away from each active limit.

## 10. Unified stepper supervisor simulation

- `python3 -m unittest -v networked_sensors.test_stepper_control` passes.
- The existing flow-management page, not a second page, shows stepper
  connection, D4 local enable, D6/D8 limits, command, mode, configured speed,
  and effective speed without implying reliable absolute position.
- Positive and negative simulated moves reach their exact commanded open-loop
  targets; Stop leaves the simulated source stopped.
- Invalid, busy, disabled, and into-limit commands are rejected; motion away
  from an active limit is permitted.
- `stepper_stale` and `stepper_missing` make the source visibly unavailable and
  disable Move.
- A recorded simulated move appears in both `merged_samples.csv` and
  `stepper_raw.csv` on the same timestamps as flow-source records.
- The UI clearly reports `sim` source mode during no-hardware checks.

## 11. USB-backed Yún diagnostics and calibration (T4A-T4C)

- Before upload, compile the repository sketch for `arduino:avr:yun`; do not
  infer compile success from Python tests.
- Upload only under the established motor-safe procedure. T4B may change the
  speed setpoint but must not alter D4 start/stop, D5 direction, or D6/D8 limit
  authority.
- Start the laptop dashboard with `--stepper-source usb --stepper-port PORT` and
  keep the browser on `http://127.0.0.1:8000/`; the laptop may retain its normal
  internet Wi-Fi.
- Confirm D4 raw/enabled and D5 raw/Forward/Reverse change correctly on screen.
- Confirm D6/D8 raw, active, and latched fields agree with the magnet positions.
- Reproduce Forward blocked at D6 and confirm the page says
  `BLOCKED: positive_limit` with zero effective speed; select Reverse and
  confirm motion-away is allowed and the positive latch clears.
- Unplug USB and confirm only the stepper source becomes stale/disconnected;
  the webpage, ESP32/DXMR90 sources, and any active recording remain alive.
- With D4 OFF, apply 1.5 mm/s and confirm Configured speed reads 1.5 mm/s while
  Effective speed remains 0; repeat at 3.0 mm/s.
- With D4 ON, confirm Apply Manual Speed is disabled and a direct API request is
  rejected. A rejected request must not change the configured speed.
- With D4 OFF, apply Normal and Inverted direction mappings and confirm each is
  echoed without effective motion. With D4 ON, confirm mapping changes are
  disabled/rejected.
- Starting at active D6, use a brief 1.5 mm/s Reverse test to identify the one
  mapping that physically moves away from D6. Stop immediately if the coupling
  loads, slips, stalls, or moves farther into the hard stop; record the accepted
  mapping before any range test.
- Run short motion-away tests at 1.5, 3.0, then 5.0 mm/s. Confirm direction,
  smoothness, D4 stopping, and the destination limit at every stage; stop
  escalation at the first missed step, stall, roughness, or unexpected motion.
- When deliberately testing legacy T4C firmware, confirm USB Move, Home, and
  Stop remain disabled rather than implying position capability.

## 12. USB bounded position control (T5)

- Compile and upload the exact repository firmware with verification. Confirm
  the page reports mode, D8-limit-seek, Move, and Stop capabilities without
  absolute-position fields before attempting motion.
- After reset, confirm the firmware is in Local Velocity and cannot move if D4
  was left ON: D4 must be observed OFF once before either mode can arm motion.
- With D4 OFF, toggle Local Velocity -> Web Position and wait for a fresh Yún
  confirmation. Confirm mode changes are rejected while D4 is ON or moving.
- Without first using the D8-limit action, command a short relative move and
  confirm no homing/reference prerequisite exists.
- For the optional D8 seek, set D5 Reverse, set D4 ON, and use **Move to D8
  Limit**. Confirm motion is at the fixed 1.5 mm/s, D4 OFF aborts immediately, a
  D5 change aborts rather than reverses, and D8 stops motion.
- At D8, set D5 Forward and enter a short positive travel magnitude at 1.5 mm/s.
  Confirm the emitted pulse count and compare actual DRO displacement.
- At a safe mid-stroke position, enter the same positive travel magnitude once
  with D5 Forward and once with D5 Reverse. Confirm the resulting directions
  follow D5 and that no negative operator distance can be submitted.
- Confirm one relative request above 137.18 mm is rejected as a quantity bound;
  confirm there is no absolute open-loop target or software-margin rejection.
- During a short move, test **Stop Motion** and D4 OFF separately. Both must stop
  in the ATmega loop without waiting for the webpage or Linux side.
- Repeat away from each active limit: D6 blocks D5 Forward, D8 blocks D5
  Reverse, and the opposite D5 direction remains permitted.
- Confirm the operator page does not present Homed, Position, Target, or
  Remaining. Confirm USB/API position fields are null and any legacy homed flag
  is not consulted before Move.
- Confirm DM542T SW5-SW8 remain all ON (200 pulses/revolution) and SW4 is the
  separate standstill-current switch. With that setting, verify the datasheet
  conversion of 0.00396875 mm/pulse, or 251.96850394 pulses/mm, using a known
  pulse count and DRO-measured displacement in both directions.
- Acceleration must not appear as an operator-adjustable field; it remains the
  source-reviewed fixed 5 mm/s² provisional firmware value.

## 13. Dashboard software E-STOP (T5A)

- Treat this as a software operational stop only. Confirm a separate hardwired,
  safety-rated stop or power-isolation method exists wherever the risk analysis
  requires emergency stopping; the dashboard path does not remove motor power.
- Compile and upload the exact repository firmware with verification before the
  page is expected to advertise E-STOP capability.
- With D4 OFF and no motion, press **SOFTWARE E-STOP**. Confirm the page reports
  `LATCHED`, firmware state is `emergency_stop`, and reset is available only
  under the documented D4-off condition.
- Leave the latch set, turn D4 ON, and confirm neither Local Velocity nor a Web
  Position command emits STEP pulses. Confirm reset remains disabled/rejected.
- Turn D4 OFF, reset the latch, and confirm motion remains stopped until a new
  deliberate physical/web action.
- In Local Velocity, begin a short safe motion-away interval and press the top
  button. Confirm pulses stop without changing D4 and remain stopped after a
  browser refresh or temporary USB disconnect/reconnect.
- In Web Position, begin a short bounded safe move and repeat the stop. Confirm
  the command aborts, status becomes latched, and Move/Home/Stop/setup controls
  remain disabled until reset.
- Record measured stop latency at the approved maximum speed. A network, USB,
  process, or logic-power failure is a failed software-stop path, not proof of
  a safe physical stop.

## 14. Yún Linux/LAN bridge (T6-T7)

- Keep DM542T motor power off. Compile and upload the exact repository T6
  firmware; confirm the build is approximately 20,794 bytes flash and 1,399
  bytes global RAM, then verify USB stopped status before touching Linux.
- Reserve the Yún DHCP address and confirm the dashboard laptop can reach that
  address on the isolated bench LAN. Do not expose TCP 8080 outside that LAN.
- Run `provision_yun.sh CURRENT_YUN_HOST [TARGET_WIFI_SSID]`, or use the
  documented manual recovery commands. Confirm the wrapper saved/commented
  `::askconsole` and no `/bin/ash --login` process retains `/dev/ttyATH0`.
- `GET /v1/health` reports the expected UART and no error. `GET /v1/status`
  returns advancing compact status with the real D4/D5/D6/D8 values and zero
  motion.
- Start `YUN_URL=http://YUN_IP:8080 run_lan_dashboard.sh`. Confirm source mode
  `network`, connection age, configured speed, limits, control mode, owner, and
  E-STOP state match USB observations.
- With D4 OFF, change speed over LAN and require a fresh ATmega sequence/echo.
  Connect a USB dashboard concurrently and confirm its competing mutating
  command is rejected while network owns control. Confirm Stop and software
  E-STOP remain accepted from the non-owner transport.
- Stop motion, put D4 OFF, wait at least two seconds, and confirm ownership
  releases. Then prove USB can claim and the network side is rejected until
  the same release conditions occur.
- Latch E-STOP over LAN with motor power off, refresh/restart the laptop page,
  and confirm the ATmega latch persists. Reset only with D4 OFF.
- Restart only the Yún Linux processor/service. Confirm no STEP pulse or new
  command occurs, status reconnects visibly, and any ambiguous command times
  out rather than being blindly retried.
- With a safely staged short motion-away test, measure STEP jitter and network
  Stop/E-STOP latency under 10 Hz status traffic. Compare to USB and stop the
  qualification if the mechanism becomes rough, misses steps, or exceeds the
  approved response bound.
- The init service is boot-enabled and a Linux-only stopped reboot has passed.
  Before routine motion use, reboot both processors on the target LAN, repeat
  stopped-state verification, and complete all remaining moving checks.
