# Bench experiment CSV reference

For Controllino MAXI with CubeMars CAN motor, LinACE DRO and three solenoid outputs, plus ED-593 thermocouples and DXMR90. Upload firmware **1.6.4** to make stepper halt independent of the CAN motor.

Rows run in order after **Start experiment**. Loading validates and previews the file. Use health rows to check live readiness before hardware commands.

## Format

Use comma-delimited UTF-8 CSV. Excel UTF-8 exports work. Column order is free; names and values are case-sensitive. Surrounding whitespace and blank lines are ignored.

Required headers: `action`, `source`, `max_age_ms`, `health`, `timeout_s`. Other headers are optional. Keep required headers even when a row requires blank cells. Every row must match the header's cell count. No duplicate/unknown headers or comment rows. Limits: **1–1000 action rows, 256 KiB**.

Edit or upload **Main sequence** and **Fail safe** separately in the dashboard.
Both CSVs use ordinary column names, including the five required headers; no
`fail_safe_` prefix or row padding is needed. Each sequence has its own rows.
Editing or importing one field preserves the other. **Load spreadsheet** lints
both inputs together before Start; the 1000-action and 256 KiB limits cover the
pair. A blank fail-safe field omits that response.

The text fields are drafts until **Load spreadsheet** succeeds. The table
continues to show the previously loaded procedure while you edit, or if
validation fails. Read the error below the table, correct it, and load again
before Start. For an “expected 8 cells” error after adding pressure bounds,
include `min_mbar,max_mbar` in the header and the corresponding cells on every
row. Replacing the complete CSV, including its header, avoids mismatched columns.

The default pair is [record_motor_solenoids.csv](record_motor_solenoids.csv) and
[record_motor_solenoids_fail_safe.csv](record_motor_solenoids_fail_safe.csv).
**Use example** fills both fields, and each file has its own download link.
The API accepts `csv` and `fail_safe_csv` in the same load request.

A combined file with a right-hand group of `fail_safe_` columns is also accepted
in the main field when the separate fail-safe field is blank. Loading converts
it into the two editors. Populating both representations is rejected as
ambiguous. The combined format requires contiguous main columns followed by
prefixed fail-safe columns, with the required headers in each group.

Numbers must be finite; use decimal points. **Leave unused cells blank.** Omit `pulse_us`: this bench uses CAN motor control.

```text
action,source,device,max_age_ms,health,timeout_s,set_point,min_mm,max_mm,min_c,max_c,speed_mm_s,duration_s,direction,rpm
```

## Columns

| Column | Values and meaning |
| --- | --- |
| `action` | `telemetry_health`, `requested_state`, `reading_state`, `wait`. |
| `source` | `controllino`, `ed593`, `dxmr90`, `recording`; blank for `wait`. |
| `device` | Target from the action tables below. Use semicolons for health-check or solenoid groups; targets must be unique and nonempty. |
| `max_age_ms` | Maximum response age in milliseconds, ≥ 0. Inclusive; `0` requires age zero. |
| `health` | `ok` checks that transport-error telemetry exists and reports no error. Blank omits this check where allowed. |
| `timeout_s` | Seconds > 0; blank for `wait`. See Timing. |
| `set_point` | Value from the action tables. Blank readings default to `check`. |
| `min_mm` | Inclusive lower raw-DRO bound in mm. |
| `max_mm` | Inclusive upper raw-DRO bound in mm. |
| `min_c` | Inclusive lower thermocouple bound in °C. |
| `max_c` | Inclusive upper thermocouple bound in °C. |
| `min_mbar`, `max_mbar` | Inclusive bounds for a low-pressure input. |
| `min_psi`, `max_psi` | Inclusive bounds for a high-pressure input. |
| `speed_mm_s` | Stepper speed magnitude: **0.1–10 mm/s**, inclusive. |
| `duration_s` | Seconds: wait length, nominal stepper travel duration, or CAN run duration. |
| `direction` | Stepper `forward` or `reverse`: up/positive or down/negative travel. |
| `rpm` | CAN output-shaft speed: integer **−793–793 RPM**, as advertised by the installed firmware. Negative reverses direction; zero stops/releases. |

## Health checks

`action=telemetry_health` requires `max_age_ms`, `health=ok`, and `timeout_s`. Leave `set_point` blank. A blank `device` checks only source freshness and transport health; otherwise select targets below.

| `source` | Allowed `device` values | Additional checks |
| --- | --- | --- |
| `dxmr90` | Blank only. | None. |
| `ed593` | `tc0`, `tc1`, `tc2`, `tc3` | Channel ready, enabled, fault-free. |
| `controllino` | `stepper`, `dro`, `pulse_measurement`, `can_motor`, `solenoid1`, `solenoid2`, `solenoid3`, `pressure1`, `pressure2`, `pressure5`, `pressure6`, `pressure7` | Corresponding capability flags; configured input validity for pressure; ready/fresh feedback for CAN; effective measurement age for DRO. |

`health=ok` alone does not check freshness or device readiness. Use `controllino` as the source and `stepper` as its device. Thermocouple component names are display labels; CSV targets are `tc0`–`tc3`.

ESP32 is disabled; ESC and servo backends are absent; TC4–TC7 are disabled. Pressure inputs 3 and 4 lack voltage scales, so their health checks cannot pass. Pressure targets above check configured ADC input validity, not sensor cable detection.

## Commands

`action=requested_state` requires `set_point` and `timeout_s`. Every command except recording also requires `max_age_ms` and `health=ok`; recording requires those two cells blank.

| `source` | `device` | `set_point` | Additional parameters |
| --- | --- | --- | --- |
| `recording` | Blank | `on`, `off` | None. |
| `controllino` | `stepper` | `positional`, `directional` | None. |
| `controllino` | `stepper` | `move` | `speed_mm_s`, positive `duration_s`, `direction`. |
| `controllino` | `solenoid1`, `solenoid2`, `solenoid3`, or a group | `open`, `closed` | None. |
| `controllino` | `can_motor` | `speed` | `rpm`; optional `duration_s`, as below. |

Commands confirm the reported requested state. A stepper move waits for matching firmware completion. Solenoid groups request all targets before confirmation; confirmation covers electrical outputs only. `open` energizes a normally closed solenoid; `closed` de-energizes it. The selected source must own those outputs in the active configuration.

For stepper modes/moves, a blank `device` is also accepted. Select `positional` before `move`. `directional` selects local-velocity mode without starting motion. Travel is **speed × duration**, rounded to pulses; acceleration can extend movement time. Travel must round to at least one pulse and fit 2,147,483,647 pulses. The displayed travel span is not this command limit. Firmware mode, limits, and E-STOP still apply.

### CAN runs

| `rpm` | `duration_s` | Behavior |
| --- | --- | --- |
| Nonzero | Blank | Run through later rows using the default firmware ramp. Later speed rows update the target. An explicit `rpm=0` row is required before file end. |
| Nonzero | Number | Timed run: **0.020–5.000 s** on current firmware, in whole milliseconds. |
| `0` | Leave blank | Stop and wait for release. A maintained run ramps to zero; other runs release directly. Any supplied duration is ignored. |

Stop a maintained run before starting a timed run. A `telemetry_health` row with `source=controllino` and `device=can_motor` checks readiness. Nonzero motor commands require fresh, fault-free feedback.

A nonzero CAN row advances when the target is accepted, not when measured RPM reaches it or the run finishes. Add `wait` for a dwell.

## Reading checks

`action=reading_state` requires `timeout_s`. Bounds are inclusive, may be negative, and require minimum ≤ maximum. A failed check leaves the main sequence immediately; it does not wait for the value to enter range.

| `source` | `device` | `set_point` | Required bounds | Behavior |
| --- | --- | --- | --- | --- |
| `ed593` | One of `tc0`–`tc3` | `check` or blank | `min_c`, `max_c` | Check once. |
| `controllino` | `dro` | `check` or blank | `min_mm`, `max_mm` | Check once. |
| `controllino` | `dro` | `on` | `min_mm`, `max_mm` | Check, then monitor during later rows. |
| `controllino` | `dro` | `off` | Blank | Disable the monitor; all other check/request cells must be blank. |
| `controllino` | `AI10`, `AI11`, `AI12`, `AI13`, `AI08`, `AI09` | `check`/blank or `on` | `min_mbar`, `max_mbar` | Check once, or enable monitoring through later rows. |
| `controllino` | `AI0`, `AI1`, `AI2` | `check`/blank or `on` | `min_psi`, `max_psi` | Check once, or enable monitoring through later rows. |
| `controllino` | Any pressure input above | `off` | Blank | Disable that input's monitor. |

For `check` and `on`, `max_age_ms` and `health` are independently optional. With both blank, only a finite in-range value is required; cached values can pass. ED-593 disabled/faulted channels have no usable value. For DRO, freshness includes measurement age plus cached-source age.

DRO bounds use raw `stepper_dro_position_mm`, without the dashboard's zero offset. Only one persistent DRO monitor is allowed. Match every `on` with `off` before file end. Its age check applies **at activation only**; bounds and selected transport-health checks continue until `off`. Persistent temperature checks are unsupported.

Pressure checks use the same calibrated units as the dashboard. The calibrated
`stepper_pressureN_reading` remains available independently of dashboard
freshness; `stepper_pressureN_value` still follows dashboard freshness rules.
Blank `max_age_ms` and `health` therefore add no hidden transport conditions to
a pressure check. Missing, uncalibrated or invalid measurements fail. Selected
freshness includes pressure-sample age plus controller-sample age. Each pressure
input can have its own monitor; its freshness threshold applies at activation
only, while its value bounds and selected health check persist. `pressure1`
through `pressure9` remain accepted aliases for their configured terminals.

To halt only the stepper, use `requested_state, source=controllino,
device=stepper, set_point=halt`, with explicit `max_age_ms`, `health=ok` and
`timeout_s`. It works in either controller mode. CAN motor zero RPM remains a
separate `device=can_motor, set_point=speed, rpm=0` row.

## Waits

`action=wait` uses only positive `duration_s`; all other cells must be blank. The row advances after that interval. Enabled reading monitors continue.

## Timing and failure

| Rows | Timeout behavior |
| --- | --- |
| Health and reading rows | Starts at row entry. Missing samples can time out; failed reading checks fail immediately. |
| Commands | Starts when sent. Waiting for source freshness/health before dispatch has no CSV timeout. |
| Started stepper move | Start-handshake timeout ends once matching motion is confirmed. No overall completion timeout. |
| Accepted maintained CAN stop | Acceptance timeout ends once accepted. No overall ramp/release timeout. |

`timeout_s` is neither a whole-experiment deadline nor a device communication setting. Ordinary health/reading rows stop checking after passing; DRO and pressure `on` checks continue across rows.

For a persistent reading, the enable row's timeout ends when that row passes.
The monitor then checks subsequent samples and timer events until its explicit
`off` row, including while a later action or wait runs. A failed measurement
enters fail safe immediately, without waiting for `timeout_s`. The `off` row
has its own row timeout; an out-of-bounds monitor is checked before disabling.

With a fail-safe section, any main-row failure or persistent monitor failure
abandons the main sequence and enters fail safe once. Main monitors are disabled
at that transition, and the original failure remains recorded. Only the
fail-safe rows define the output response: there is no implicit stepper stop
before those rows. An in-flight main command finishes before fail-safe device
commands can transmit on the shared command queue, and its eventual outcome is
attached to the original row. CAN lease renewal continues while fail safe runs,
allowing a later explicit zero-RPM row to use the normal deceleration ramp.

Fail-safe rows execute in order, continuing after errors or feedback timeouts;
every failed row is retained in the results. A command whose pre-dispatch
freshness/health requirements already fail is marked failed immediately in fail
safe, allowing later rows to proceed. Other command feedback and completion
rules remain the same. There is no recursive fail safe, main-sequence resume, or
automatic retry. At the end, the experiment result remains **failed**, even if
every fail-safe row passed. Normal completion skips fail safe. Dashboard shutdown
retains its existing immediate release/stop behavior rather than starting a CSV
sequence while transports are closing.

Without a fail-safe section, failure retains its existing behavior: stop the
queue, request a stepper stop for an attempted move, and retire a maintained CAN
lease. Include explicit closes/stops/recording-off rows in the main sequence for
successful runs as well. The default CSV pair provides fail-safe valve closing,
stepper halt, CAN zero RPM, and recording off.

## Motor-free pressure-guard example

Paste this entire CSV into **Main sequence**. It records, enables an AI10
**0–20 mbar** guard, opens valves 1–3 with waits, holds for five seconds, closes
3–1, disables the guard, and stops recording. No motor commands are included.
Exactly 20 mbar passes; values above 20, below zero, or unusable measurements
fail. Freshness and transport-health checks are omitted by leaving their cells
blank. The guard stays active through valve closing, independently of its
10-second activation-row timeout.

```csv
action,source,device,max_age_ms,health,timeout_s,set_point,duration_s,min_mbar,max_mbar
requested_state,recording,,,,10,on,,,
reading_state,controllino,AI10,,,10,on,,0,20
wait,,,,,,,1,,
requested_state,controllino,solenoid1,1000,ok,10,open,,,
wait,,,,,,,1,,
requested_state,controllino,solenoid2,1000,ok,10,open,,,
wait,,,,,,,1,,
requested_state,controllino,solenoid3,1000,ok,10,open,,,
wait,,,,,,,5,,
requested_state,controllino,solenoid3,1000,ok,10,closed,,,
requested_state,controllino,solenoid2,1000,ok,10,closed,,,
requested_state,controllino,solenoid1,1000,ok,10,closed,,,
reading_state,controllino,AI10,,,10,off,,,
requested_state,recording,,,,10,off,,,
```

Paste this into **Fail safe**:

```csv
action,source,device,max_age_ms,health,timeout_s,set_point
requested_state,controllino,solenoid1;solenoid2;solenoid3,1000,ok,10,closed
requested_state,recording,,,,10,off
```

Click **Load spreadsheet** and verify **14 main rows and 2 fail-safe rows** in
the table before Start. These checks and valve commands require no new firmware.
Save the CSV text to your own files to reuse these edits; restarting the
dashboard loads the repository's default motor-containing procedure.

## Example

Check four thermocouples for readiness and 0–100 °C readings:

```csv
action,source,device,max_age_ms,health,timeout_s,set_point,min_c,max_c
telemetry_health,ed593,tc0;tc1;tc2;tc3,3000,ok,10,,,
reading_state,ed593,tc0,3000,ok,10,check,0,100
reading_state,ed593,tc1,3000,ok,10,check,0,100
reading_state,ed593,tc2,3000,ok,10,check,0,100
reading_state,ed593,tc3,3000,ok,10,check,0,100
```

[record_motor_solenoids.csv](record_motor_solenoids.csv) uses this bench's recording, CAN motor, solenoids and stepper; it requires ready CAN feedback. Other files may target older hardware configurations. [Parser](../dashboard_app/experiment.py).
