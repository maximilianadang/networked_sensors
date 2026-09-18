# Yún / Controllino regression audit — 2026-09-15

## Outcome: the missing Controllino clock remains unresolved

The working Yún is the live baseline. No motion commands or firmware uploads
were performed during this audit. Do not treat passing software tests as proof
that the Controllino receives the electrical clock signal.

Live Yún dashboard at 19:49 UTC: raw 95.28 mm, saved zero 137.18 mm,
display -41.90 mm, fresh=true, 7,347 valid frames, one rejected frame,
zero dropped frames, no stepper transport error. Motor stopped/disabled.

Earlier Controllino application-isolation test: 54 samples, clock LOW throughout,
zero falling edges, data both HIGH and LOW. This diagnostic omitted the servo,
STEP generation, Ethernet and decoder. See [captured results](dro-input-isolation-results.json)
and [register measurements](controllino-dro.md). This does not establish a
hardware fault: board mapping, the physical input path and the discrepancy
between connector voltage and MCU input state still need reconciliation.

## Comparison

| Layer | Original Yún | Controllino / lean | Audit finding |
| --- | --- | --- | --- |
| Firmware | `limit_switch_palas.ino` | `controllino_motion_control.ino` | Original retained |
| DRO clock | D10 / PB6 / PCINT6 | X1 DO0 / D2 / PE4 / INT4 | Different MCU interrupt; both sample falling edges |
| DRO data | D11 / PB7 | X1 DO4 / D6 / PH3 | Direct port reads; inputs without pull-ups |
| Capture | 16-one header, 52 bits | Same | Actual routines agree on tested streams |
| Decode | Sign, six BCD digits, metric, 0.01 mm | Same | Signed positions, invalid decimals and mailbox overflow agree |
| Freshness | Timestamp when main loop processes frame | Timestamp when ISR captures frame | Intentional improvement: delayed processing cannot refresh old data |
| STEP | Timer1 | Timer1 | Pin/polarity differ for installed interface; not paced by browser |
| Auxiliary output | D12 ESC, Timer3 | X1 DO2 / D4 positional servo, Timer3 | No Servo library; hardware compare output modes disabled |
| Other X1 outputs | Different Yún map | DO1 STEP, DO3 DIR, DO5 enable | No collision with DO0/DO4 DRO or DO2 servo |
| Limits / direction | Physical switches, qualification and latches | Software direction; absent limits reported -1 | Homing rejected; missing hardware is not equivalent functionality |
| Move completion | Pulse-count motion | Pulse-count motion | DRO is telemetry, not closed-loop completion or an interlock |
| Transport | USB status or Linux bridge | Direct Ethernet /status and /command | Controllino adapter uses shared USB status decoder after normalization |
| Python dashboard | `dashboard.py` | `dashboard-lean.py` | Shared `dashboard_app/runtime.py`, HTTP API and `supervisor_core.py` |
| Browser | Original assets, brushless controls | `*-lean` assets, positional servo | Separate panel implementations; current lean controls exercised in Firefox |
| Display zero / velocity | Shared runtime and system_config.json | Same | Saved zero subtracts from raw position; velocity requires fresh samples |
| Solenoid 4 | ESP32 route | Controllino R5 / Arduino D27 | Separate from X1 DO5 / Arduino D7 enable |

## Confirmed defects corrected in this audit

1. `supervisor_core.py`: before the first valid frame, firmware's dr=0/dd=0
   initialization values were exposed as measured zero. With the saved offset,
   this produced a misleading -137.18 mm API position. Now dq=0 yields null
   raw/displacement values. A genuinely measured zero remains 0.0. This is
   **not** the cause of zero clock edges.
2. `browser-checks-lean.js`: browser regression checks still used removed
   brushless controls. Updated the fixture and assertions for positional-servo
   On/Off/Stop signal, and added fresh/missing DRO measurement checks. These
   tests intercept browser commands and do not touch a physical controller.

## Validation

- 125 Python/native regression tests pass:
  `python3 -m unittest discover -s networked_sensors -t .` from the parent folder.
- New `test_dro_equivalence.py` extracts the actual capture/decoder routines
  from both sketches, compiles them together, and feeds 2,002 frames plus noise.
  Valid/rejected/dropped counts, signed positions and boot references match.
  This is a native functional test, not an AVR timing/electrical simulation.
- `python3 check_dashboard-lean.py` passes with local geckodriver on port 4444:
  initialization/SSE, directional and positional commands, Stop and E-STOP,
  retry/pending-state handling, positional servo, DRO display and zero,
  metadata-derived speed/travel, recording and solenoid routing. Layout checks
  pass in both modes at 1920×1080, 1440×900, 1366×768 and mobile 500×844.
  Mobile deliberately scrolls. These are isolated simulation tests.

## Outstanding concerns, not disguised as resolved regressions

- No historical known-good Controllino DRO firmware is committed: the available
  checkpoint predates DRO integration. The minimal diagnostic is application
  isolation, not an exact binary rollback of the last working DRO version.
- Correct GPIO/interrupt registers and decoder tests do not prove the input
  clock reaches PE4. Conversely, a DC multimeter voltage does not prove clock
  transitions. The working Yún demonstrates the reader/shifter can function.
- No communication-loss watchdog exists. Directional motion may continue after
  losing the dashboard. Firmware priority Stop/E-STOP bypass ownership, but
  network delivery is not guaranteed. Ordinary dashboard Stop takes the shared
  runtime lock; transport commands also serialize behind a command lock.
  Browser-level independent Stop tests do not prove bounded hardware stop latency.
- The fixed HTTP/serial buffers and nonblocking serial drain address previous
  reporting problems, but Ethernet library calls are not hard-real-time. A
  blocked main loop can drop completed DRO frames; it does not explain zero
  ISR clock edges in the minimal diagnostic.
- Lean piston endpoint labels still use legacy D6/D8 terminology even though
  Controllino limits are absent. They are display-range labels, not proof of
  connected limit hardware.

## Next decisive acceptance test

Preserve the working Yún baseline. With motion supplies disabled, reconnect the
DRO to the documented Controllino input path and compare raw port states and
edge counts against a controlled known-low/known-high input check on that same
X1 DO0 contact. Confirm contact-to-MCU mapping before changing firmware pins.
This requires the user's physical participation; the reader is currently on
the Yún. Do not flash Controllino firmware to the attached Yún USB identity.

The DRO failure is fixed only when Controllino live valid-frame counts increase,
manual displacement is reflected correctly, and capture remains healthy with
the servo both signaling and released. No such claim is made by this audit.
