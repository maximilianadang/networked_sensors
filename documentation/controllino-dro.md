# Controllino DRO integration — 2026-09-14

## Application isolation test — 2026-09-15

Ran `check_dro_firmware_isolation.py`: uploaded `controllino_dro_input_test.ino`
(no servo timer/control, STEP generation, Ethernet, or DRO decoder), sampled
raw PE4/PH3 plus INT4 edge count over USB, then restored the motion firmware.
54 samples over roughly 11 seconds all showed clock LOW and zero edges;
data took both HIGH and LOW values. Registers showed PE4 input and INT4 enabled
for falling edges. See `dro-input-isolation-results.json` for captured samples.
This reproduces the fault without the servo application, but is not a rollback
to a historical known-good DRO binary: the Git checkpoint predates DRO support.
Shared board mapping/input hardware and the external signal path remain unresolved.

## Register diagnostic — 2026-09-15

User-approved diagnostic upload SHA-256:
`0f032ab99032e1f8939d75ea01681b2711dcf64adc25d2030c071f491f7eed97`.
`GET /diagnostic` snapshots GPIO/interrupt/timer registers without pin writes.
Four reads: PINE=225/227 (PE4 LOW), DDRE=40 (PE4 input), PORTE=32
(PE4 pull-up off), EIMSK=16 (INT4 enabled), EICRB=2 (falling edge),
TCCR3A=2 (no hardware compare outputs enabled), edges=0.
This independently confirms the processor sees clock LOW with correct input
and interrupt configuration. The reported 4.7 V at the wire/connector is not
reconciled yet; connector/header mapping, intervening hardware, and input damage
remain possibilities, not established causes. No motion commanded by this check.

`controllino_motion_control.ino`, section **4b**, contains the read-only
AbsoluteDRO Plus reader. The original Yún sketch is unchanged. This adds
measurements, not closed-loop motor control or a replacement for limit switches.

## Current wiring allocation

| Signal | Level shifter | X1 label | Arduino pin |
| --- | --- | --- | --- |
| DRO D− clock (blue) | LV1 → HV1 | Digital Out 0 | D2 / PE4 |
| DRO D+ data (purple) | LV2 → HV2 | Digital Out 4 | D6 / PH3 |
| DRO + (orange) | LV | 3V3 | Regulated supply |
| High-side reference | HV | 5V | Supply |
| DRO − (black) | GND | GND | Common return |

D2/D6 are configured as INPUT without internal pull-ups. These are X1 logic
pins, not the 24 V output screw terminals. Leave their corresponding industrial
outputs unloaded. STEP/DIR/ENABLE remain X1 DO1/DO3/DO5 (Arduino D3/D5/D7).
The reader-head REQ-to-ground modification remains required for continuous
frames. The older migration illustration's D50/D51 assignment must not be
used: those are the Ethernet SPI bus, not the current DRO allocation.

## Data path and guarantees

- D2 falling-edge interrupt samples D6 directly; 16 leading ones synchronize
  each 52-bit frame. Main-loop decoding validates sign, six BCD digits, metric
  units and two decimal places, matching the existing Yún implementation.
- One completed frame is handed off atomically. Pending-frame overruns and
  rejected frames are counted. Capture time, not processing time, determines
  freshness; readings become stale after 250 ms.
- HTTP/USB retain `dc/df/dr/dd/da/dq/dx`: capability, freshness, raw position
  and boot-relative displacement in 0.01 mm, age, valid count, packed diagnostics.
- Additional HTTP/USB diagnostics: `dce` falling-edge count (wraps), `dcl`
  instantaneous clock level, `ddl` instantaneous data level. These are available
  at `/status`; the existing dashboard decoder ignores unknown fields.
- The existing dashboard supplies DRO velocity and the saved-zero offset from
  `system_config.json`. Enabling this reader does not change that saved zero.
- STEP Timer1, ESC Timer3, motion commands and Ethernet/serial buffering remain
  intact. The ISR neither prints nor makes motion decisions. Serial telemetry
  is coalesced at the UART's available bandwidth rather than blocking motion.

## Validation

Native tests execute capture and decoding with positive/negative frames, noise,
malformed frames, dropped-frame saturation and delayed processing. Tests also
verify the dashboard preserves DRO fields with absent limit switches and that
the expanded worst-case HTTP response fits its fixed 512-byte buffer.

Verified upload source SHA-256:
`a88844305278dcd5e83cc3ddf56efbf75c8cb2a6725ca55c270fa6ac2801e7ba`.
Compile: 23,748 bytes flash, 2,155 bytes static RAM, 6,037 bytes remaining.
All 115 regression tests pass.

The running dashboard recognizes DRO capability. Ten consecutive hardware
reads showed `dce=0`, `dcl=0`, `ddl=1`, `dq=0`, `dx=0`, `df=0`: no falling
clock edges observed on D2, clock sampled LOW, data sampled HIGH, and no
completed valid or rejected frames. Physical measurements remain unverified;
check clock wiring and shifter supplies/common ground next. No motor motion
was initiated, and the saved dashboard zero was not changed.

### Confirmed wiring correction

The user moved blue/clock to X1 DO0 and left purple/data on X1 DO4.
Firmware now samples D6/PH3 rather than D4/PG5; D2 remains the clock interrupt.
Verified upload SHA-256:
`39df65c17de8edc42857d5713fc334b727e35a316c244c8a84f0d954cc030f6a`.
Compile: 23,750 bytes flash, 2,155 bytes static RAM. All 115 tests pass.
Post-upload samples show both inputs HIGH, zero falling clock edges and zero
completed frames. Pin allocation is updated, but live DRO measurements remain
unverified. Check the DRO supply, common ground and continuous REQ grounding.
