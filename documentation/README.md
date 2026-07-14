# Arduino Yún stepper-control documentation

Offline source bundle and engineering notes for evaluating an Arduino Yún Rev2
(ABX00020) as a replacement controller for `limit_switch_palas.ino`.

Acquired on 2026-07-10. The physical board was identified by its owner as a Yún
Rev2. Original-Yún documents are retained only because Arduino's archived
Bridge material and getting-started explanations provide useful background for
the same ATmega32U4/AR9331 architecture; Rev2 pinout and schematic documents are
the hardware authority for this adaptation.

## Source inventory

| Local file | Upstream source | Purpose |
| --- | --- | --- |
| `arduino-yun-rev2-hardware-page.html` | <https://docs.arduino.cc/hardware/yun-rev2/> | Official ABX00020 hardware page snapshot; the product is end-of-life |
| `arduino-yun-rev2-pinout.pdf` | <https://docs.arduino.cc/resources/pinouts/ABX00020-full-pinout.pdf> | Official Rev2 I/O, voltage/current, and processor-interconnect pinout |
| `arduino-yun-rev2-schematic.pdf` | <https://docs.arduino.cc/resources/schematics/ABX00020-schematics.pdf> | Official Rev2 electrical schematic |
| `arduino-yun-bridge-library.md` | <https://docs.arduino.cc/retired/archived-libraries/YunBridgeLibrary/> ([source Markdown](https://github.com/arduino/docs-content/blob/main/content/retired/05.archived-libraries/YunBridgeLibrary/YunBridgeLibrary.md)) | Bridge, Mailbox, and BridgeServer API reference; archived and unmaintained |
| `bridge-web-control-example.ino` | <https://github.com/arduino-libraries/Bridge/tree/master/examples/Bridge> | Official REST-style BridgeServer example |
| `bridge-mailbox-rest-example.ino` | <https://github.com/arduino-libraries/Bridge/tree/master/examples/MailboxReadMessage> | Official Linux-to-ATmega mailbox example |
| `accelstepper-class-reference.html` | <https://www.airspayce.com/mikem/arduino/AccelStepper/classAccelStepper.html> | Primary AccelStepper API and timing reference |
| `arduino-yun-product.md` | <https://docs.arduino.cc/retired/boards/arduino-yun/> ([source Markdown](https://github.com/arduino/docs-content/blob/main/content/retired/01.boards/arduino-yun/content.md)) | Legacy original-Yún architecture background; not the Rev2 hardware authority |
| `arduino-yun-getting-started.md` | <https://docs.arduino.cc/retired/getting-started-guides/ArduinoYun/> ([source Markdown](https://github.com/arduino/docs-content/blob/main/content/retired/06.getting-started-guides/ArduinoYun/ArduinoYun.md)) | Legacy setup and Bridge background |
| `arduino-yun-original-schematic.pdf` | <https://www.arduino.cc/en/uploads/Main/YUN-V04(20150114).pdf> | Legacy original-Yún schematic for comparison only |

Do not use the original A000008 schematic to settle a Rev2 wiring or power
question; use the ABX00020 pinout and schematic.

## Feasibility conclusion

The replacement is feasible. The current connections can retain their Arduino
header numbers on the Yún:

| Function | Existing pin | Yún use |
| --- | --- | --- |
| Step pulse | D3 | ATmega32U4 digital output; also SCL if I2C is used |
| Direction | D2 | ATmega32U4 digital output; also SDA if I2C is used |
| Run/enable switch | D4 | ATmega32U4 input with internal pull-up |
| Direction switch | D5 | ATmega32U4 input with internal pull-up |
| Positive limit switch | D6 | ATmega32U4 input with internal pull-up |
| Negative limit switch | D8 | ATmega32U4 input with internal pull-up |
| Driver disable (`ENA-`) | D9 | ATmega32U4 output; LOW disables DM542T output |

Do not add an I2C device on D2/D3 without moving STEP and DIR. Avoid D0/D1:
the Yún uses the ATmega32U4 hardware serial connection to communicate with the
AR9331 Linux processor. D7 also has a Yún handshake connection and is not needed
by this design.

The Yún Rev2 is a 5 V I/O board, which is compatible with the assumptions in
the existing switch wiring and with typical 5 V-capable STEP/DIR inputs. Its
official pinout sets a maximum of 20 mA per I/O pin. STEP and DIR are logic
signals rather than motor-power outputs, but the actual driver input
specification must still be verified. The Yún must not power the motor or motor
driver load; retain the external driver supply and join its logic ground to Yún
GND.

### DM542T enable and fixed physical direction

The installed common-anode driver wiring leaves `PUL+`, `DIR+`, and `ENA+` at
Yún 5 V. `PUL-` and `DIR-` remain on their established controller outputs;
`ENA-` now connects to D9. The DM542T V4.0 manual specifies that 4.5-24 V across
ENA disables the drive and 0-0.5 V enables it. Therefore D9 LOW disables the
motor output/holding current and D9 HIGH enables it. The same manual requires a
200 ms enable interval before motion. Firmware enforces that interval without a
blocking delay and holds D9 LOW whenever motion is stopped, limit-blocked, or
software-E-stopped. This disables motor current, not the external 24 V supply.

Normal physical direction is fixed after endpoint verification: D5
Forward/positive approaches D6, while D5 Reverse/negative approaches D8.
Runtime electrical inversion was removed because it could reverse physical
travel without reversing the endpoint selected by the software interlock.

### Critical power constraint

Power the Yún Rev2 with regulated 5 V. Its official pinout marks VIN as "5V MAX."
Do not apply the 7-12 V commonly used on an Uno VIN.

## Speed and distance control

The model `LN176S-E06008-210-S-200` datasheet supplied during physical
calibration specifies a 0.79375 mm screw lead, 1.8 degree motor steps, and
0.00396875 mm linear travel per full step. The installed DM542T photograph
shows SW5-SW8 all ON, selecting 200 driver pulses/revolution. SW4 is the lone
opposite switch and controls standstill current, not microstepping. Therefore
one PUL input pulse equals one datasheet full step in this configuration:

```text
mm_per_driver_pulse = 0.00396875
pulses_per_mm        = 1 / 0.00396875 = 251.96850394
pulses_per_second    = round(requested_mm_per_second * 251.96850394)
relative_pulses      = round(requested_distance_mm * 251.96850394)
```

The 1.5 mm/s default is 378 pulses/s; 10 mm/s is 2520 pulses/s. The latter
requires staged timing/smoothness verification on the 16 MHz ATmega32U4. The
safe mechanical limit must still be established on the actual motor, driver,
load, and screw. Any SW5-SW8 change invalidates this conversion and requires a
new pulses/mm calculation.

Timer1 now provides indefinite constant-velocity Local Velocity motion; it does
not stop at a requested distance. For bounded Web Position moves, the adapted
sketch uses AccelStepper and must:

1. validate and clamp the requested speed and distance;
2. convert millimetres to steps;
3. call `setMaxSpeed()`, `setAcceleration()`, and `move(relative_steps)` (or
   `moveTo()` for a known absolute position);
4. call `stepper.run()` as frequently as possible from every loop iteration;
5. stop immediately if the run switch opens or motion is directed into an
   asserted limit switch.

Do not use blocking `runToPosition()` in the network event loop. The official
Bridge example's `delay(50)` is also unsuitable for this motor loop: at the
default 378 pulses/s, a pulse can be due every 2.65 ms. Network command handling
must remain non-blocking. Timer1 makes Local Velocity pulse timing independent
of transport/status traffic, while Web Position still depends on frequent
`stepper.run()` calls and requires separate timing tests.

Distance is open-loop: AccelStepper counts commanded pulses, not actual travel.
If the motor stalls or loses steps, its believed position becomes wrong. A
repeatable absolute-position feature therefore needs a homing procedure. The
installed D6 and D8 switches independently stop travel into their physical
endpoints and permit travel away; they do not make the pulse counter closed-loop.

## Recommended web architecture

Keep real-time motion on the ATmega32U4 and web handling off the stepping path:

```text
Existing laptop dashboard
        -> validated HTTP command
Yún AR9331 Linux / Bridge endpoint
        -> compact queued command
Yún ATmega32U4 + Timer1 / AccelStepper
        -> STEP/DIR driver and switches
```

The existing laptop dashboard should remain the operator UI. Add explicit
`move`, `stop`, `home`, and `status` operations rather than a toggle command. A
move command should include a request identifier, signed distance in mm, speed
in mm/s, and optionally acceleration. The ATmega side should acknowledge the
accepted target and publish current position, target, running state, and limit
state.

The classic Bridge library can implement the transport with `BridgeServer` or
Mailbox, but it is archived and no longer maintained. Keep a Yún deployment on
an isolated/trusted LAN and avoid exposing it to the Internet. Local Velocity
now uses timer-driven pulse generation; Web Position remains cooperative. At
substantially higher bounded-move speeds or when precise motion is
safety-critical, extend the timer-backed engine or use a dedicated motion
controller instead of relying on a cooperative AccelStepper loop.

## Minimum safety acceptance checks

- Confirm the exact Yún revision and supply it with regulated 5 V.
- Confirm STEP/DIR voltage compatibility and common logic ground.
- Boot with motion disabled and require an explicit, bounded move command.
- Exercise both normally-open magnetic limits before coupling the load; record
  that a broken wire looks clear and is therefore not electrically fail-safe.
- Confirm D9/ENA- is LOW and motor holding torque is absent whenever stopped.
- Reject non-finite, zero/negative-speed, overspeed, and out-of-envelope moves.
- Make local STOP independent of Linux/network availability.
- Home at low speed before accepting absolute-position commands.
- Verify commanded versus measured travel over repeated cycles; update
  `STEPS_PER_MM` if microstepping or mechanics differ from the current setup.
