# Controllino firmware versions

One sketch is maintained: `controllino_motion_control.ino`. Its version is
`FIRMWARE_VERSION` in `controllino_firmware.h`; the running controller reports it
as `fw` in every status frame. Bump this version whenever firmware behavior,
protocol, or installation configuration changes. Record changes here. Git tracks
source history; do not maintain copied `.ino` files for each version.

## 1.2.0 — MS62 angle range and autonomous release (2026-09-16)

- MS62 servo limits are now 500–2500 µs (nominal 270° travel). Pin assignments,
  stepper timing, DRO capture and relay behavior are unchanged.
- `V1 A1550,1500` commands 1550 µs, then disables pulses after approximately
  1500 ms. Timer3 counts 20 ms frames independently of browser/network activity.
  Optional release delay accepts 20–5000 ms. A subsequent servo command replaces
  the countdown; E-STOP or `V1 A0` cancels it immediately. Ordinary `V1 A...`
  without a delay holds the target until another command.
- Status adds `srel:1` (return-and-release supported) and `sr` (frames remaining).
  Servo pulses remain disabled at boot. Disabling pulses does not cut supply
  power or guarantee the shaft is mechanically released.
- Dashboard settings: `system_config.json` → `servo.off_pulse_us` and
  `servo.displacement_deg`. Initial reference is the user-confirmed 1550 µs
  position. Clockwise target = off pulse − degrees × 2000/270; out-of-range
  requests are rejected. “Zero” explicitly commands 2500 µs
  with timed release, then saves that commanded endpoint as Off after controller
  acknowledgement. This provides 0–270° clockwise travel down to 500 µs.
  The button is unavailable while On or returning to Off; it does not measure
  shaft arrival. The installed servo's counterclockwise endpoint was physically
  confirmed by the user; the system configuration and new-config default now
  use 2500 µs as Off. Zero does not overwrite the requested clockwise angle.
  Direction corrected after the installed servo was observed moving counterclockwise
  with increasing pulse width. Saved zero is unchanged; no firmware change is needed.
- On holds the requested target; Off returns to the saved reference and releases
  after 1.5 s (a settling allowance, not measured arrival). Editing the field
  alone does not move the servo; clicking On saves and applies it. Loading the
  dashboard never commands motion. Both dashboards share `components/servo.js`.
- Verification: 99 regression tests, isolated Firefox panel checks, and the AVR
  target build pass (21,770 bytes flash / 1,078 bytes RAM).

## 1.1.0 — position-servo capability (2026-09-15)

First explicit version. Both variants include the stepper, DRO, R5 solenoid,
ownership, and E-STOP behavior from the preceding deployed, unversioned firmware.

- `CONTROLLINO_AUX_SERVO=1` (default): position servo on **X1 Digital 2 / DO2,
  Arduino D4**. Use the logic-level header signal, not an industrial-voltage
  output terminal. `SERVO_MIN_US`, `SERVO_MAX_US`, and `SERVO_DEFAULT_US` define
  pulse calibration (initially 1000, 2000, and 1500 µs). No angle range or
  measured-position feedback is claimed without a servo model/calibration.
- `CONTROLLINO_AUX_SERVO=0`: retains the brushless ESC on Arduino D12.
  Set the definition in the header, or override it with the compiler flag
  `-DCONTROLLINO_AUX_SERVO=0`.
- Both use the existing Timer3 20 ms frame; Timer1 remains the stepper owner.
  No Servo library or second pulse engine is introduced.
- Servo commands: `V1 A1000` through `V1 A2000` (configured limits apply) set a
  pulse position and enable pulses. `V1 A0` disables pulses. Startup, E-STOP,
  and E-STOP reset leave servo pulses disabled until an explicit position
  command. Disabling pulses is not a power disconnect or position guarantee.
- Wrong-capability commands are rejected: `V1 A...` on ESC builds; `V1 B...`
  and `V1 P...` on servo builds. Existing ownership checks apply.
- Status: `aux` is `servo` or `esc`; `apin` is the Arduino signal pin. Servo
  builds report `sv` (pulses enabled), `sp` (configured position pulse), `smin`,
  and `smax`. ESC builds retain `bo`/`bp`. `v:1` is the wire-protocol version,
  separate from the firmware version.
- Both dashboards select their auxiliary panel from these capabilities and
  display the reported firmware version. Older firmware remains supported as
  legacy/unversioned ESC firmware. Servo commands require fresh confirmation.

Target builds passed: servo 21,538 bytes flash / 1,057 bytes RAM; ESC 21,482 /
1,055. Native tests execute both command/timer variants. The servo build was
USB-uploaded with verification; live telemetry confirmed `fw:1.1.0`, `aux:servo`,
`apin:4`, and `sv:0`. Dashboard metadata was preserved across restart. Physical
servo travel has not been calibrated or exercised.

## Prior unversioned deployment

R5 solenoid support, constant-speed stepper pulse engine, and shared AbsoluteDRO
reader. The previously deployed `.ino` SHA-256 was
`2420800428f6054e7531b03496dd776b39e6ac4a0c1e3ac7c5e511bba6b8f9ce`.
