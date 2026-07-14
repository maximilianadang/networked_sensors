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

### Critical power constraint

Power the Yún Rev2 with regulated 5 V. Its official pinout marks VIN as "5V MAX."
Do not apply the 7-12 V commonly used on an Uno VIN.

## Speed and distance control

The current calibration is 100 steps/mm. Therefore:

```text
steps_per_second = requested_mm_per_second * 100
relative_steps   = requested_distance_mm * 100
```

The present fixed setting of 1.5 mm/s is 150 steps/s. That is modest for a
16 MHz ATmega32U4. The code's 1000 steps/s ceiling corresponds to 10 mm/s, but
the safe mechanical limit must be established on the actual motor, driver,
load, microstep setting, and screw.

`runSpeed()` provides indefinite constant-velocity motion; it does not stop at
a requested distance. For bounded moves, the adapted sketch should:

1. validate and clamp the requested speed and distance;
2. convert millimetres to steps;
3. call `setMaxSpeed()`, `setAcceleration()`, and `move(relative_steps)` (or
   `moveTo()` for a known absolute position);
4. call `stepper.run()` as frequently as possible from every loop iteration;
5. stop immediately if the run switch opens or motion is directed into an
   asserted limit switch.

Do not use blocking `runToPosition()` in the network event loop. The official
Bridge example's `delay(50)` is also unsuitable for this motor loop: at the
current 150 steps/s, a step can be due every 6.67 ms. Network command handling
must be non-blocking and subordinate to frequent `stepper.run()` calls.

Distance is open-loop: AccelStepper counts commanded pulses, not actual travel.
If the motor stalls or loses steps, its believed position becomes wrong. A
repeatable absolute-position feature therefore needs a homing procedure. The
current single positive-end limit can protect that end and establish a datum,
but robust bidirectional travel also needs a known software travel envelope and
preferably a negative-end limit switch.

## Recommended web architecture

Keep real-time motion on the ATmega32U4 and web handling off the stepping path:

```text
Existing laptop dashboard
        -> validated HTTP command
Yún AR9331 Linux / Bridge endpoint
        -> compact queued command
Yún ATmega32U4 + AccelStepper
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
an isolated/trusted LAN, avoid exposing it to the Internet, and test Bridge
latency for missed-step jitter. At substantially higher speeds or when precise
motion is safety-critical, use timer-driven pulse generation or a dedicated
motion controller instead of relying on a cooperative Bridge/AccelStepper
loop.

## Minimum safety acceptance checks

- Confirm the exact Yún revision and supply it with regulated 5 V.
- Confirm STEP/DIR voltage compatibility and common logic ground.
- Boot with motion disabled and require an explicit, bounded move command.
- Exercise D6's normally-closed fail-safe limit before coupling the load.
- Reject non-finite, zero/negative-speed, overspeed, and out-of-envelope moves.
- Make local STOP independent of Linux/network availability.
- Home at low speed before accepting absolute-position commands.
- Verify commanded versus measured travel over repeated cycles; update
  `STEPS_PER_MM` if microstepping or mechanics differ from the current setup.
