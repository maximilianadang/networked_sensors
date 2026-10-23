# Experiment CSV reference

Rows run in order after **Start experiment**. Loading only validates and previews the file; commands also undergo device checks when they run.

## Format

Use comma-delimited UTF-8 CSV. Excel UTF-8 exports work. Column order is free; names and values are case-sensitive. Surrounding whitespace and blank lines are ignored.

Required headers: `action`, `source`, `max_age_ms`, `health`, `timeout_s`. Other headers are optional. Keep required headers even when a row requires blank cells. Every row must match the header's cell count. No duplicate/unknown headers or comment rows. Limits: **1–1000 action rows, 256 KiB**.

Numbers must be finite; use decimal points. **Leave unused cells blank.**

```text
action,source,device,max_age_ms,health,timeout_s,set_point,min_mm,max_mm,min_c,max_c,speed_mm_s,duration_s,direction,pulse_us,rpm
```

## Columns

| Column | Values and meaning |
| --- | --- |
| `action` | `telemetry_health`, `requested_state`, `reading_state`, `wait`. |
| `source` | `esp32`, `dxmr90`, `controllino`, `ed593`, `recording`; blank for `wait`. |
| `device` | Target from the action tables below. Use semicolons for health-check or solenoid groups; targets must be unique and nonempty. |
| `max_age_ms` | Maximum response age in milliseconds, ≥ 0. Inclusive; `0` requires age zero. |
| `health` | `ok` checks that transport-error telemetry exists and reports no error. Blank omits this check where allowed. |
| `timeout_s` | Seconds > 0; blank for `wait`. See Timing. |
| `set_point` | Value from the action tables. Blank readings default to `check`. |
| `min_mm` | Inclusive lower raw-DRO bound in mm. |
| `max_mm` | Inclusive upper raw-DRO bound in mm. |
| `min_c` | Inclusive lower thermocouple bound in °C. |
| `max_c` | Inclusive upper thermocouple bound in °C. |
| `speed_mm_s` | Stepper speed magnitude: **0.1–10 mm/s**, inclusive. |
| `duration_s` | Seconds: wait length, nominal stepper travel duration, or CAN run duration. |
| `direction` | Stepper `forward` or `reverse`: up/positive or down/negative travel. |
| `pulse_us` | ESC pulse: integer **1000–2000 µs**, inclusive. |
| `rpm` | CAN output-shaft speed: integer **−793–793 RPM**. Negative reverses direction; zero stops/releases. Firmware may impose tighter limits. |

## Health checks

`action=telemetry_health` requires `max_age_ms`, `health=ok`, and `timeout_s`. Leave `set_point` blank. A blank `device` checks only source freshness and transport health; otherwise select targets below.

| `source` | Allowed `device` values | Additional checks |
| --- | --- | --- |
| `esp32` | `pressure_adc`, `flow_adc` | ADC ready. |
| `dxmr90` | Blank only. | None. |
| `ed593` | `tc0`, `tc1`, `tc2`, `tc3`, `tc4`, `tc5`, `tc6`, `tc7` | Channel ready, enabled, fault-free. |
| `controllino` | `stepper`, `dro`, `pulse_measurement`, `servo`, `brushless_motor`, `can_motor`, `solenoid1`, `solenoid2`, `solenoid3`, `pressure1`, `pressure2`, `pressure3`, `pressure4`, `pressure5`, `pressure6`, `pressure7` | Corresponding capability flags; pressure validity for pressure targets; ready/fresh feedback for CAN; effective measurement age for DRO. |

`health=ok` alone does not check freshness or device readiness. Use `controllino` as the source and `stepper` as its device. Thermocouple component names are display labels; CSV targets remain `tc0`–`tc7`.

## Commands

`action=requested_state` requires `set_point` and `timeout_s`. Every command except recording also requires `max_age_ms` and `health=ok`; recording requires those two cells blank.

| `source` | `device` | `set_point` | Additional parameters |
| --- | --- | --- | --- |
| `recording` | Blank | `on`, `off` | None. |
| `controllino` | `stepper` | `positional`, `directional` | None. |
| `controllino` | `stepper` | `move` | `speed_mm_s`, positive `duration_s`, `direction`. |
| `controllino` | `solenoid1`, `solenoid2`, `solenoid3`, or a group | `open`, `closed` | None. |
| `esp32` | `solenoid1`, `solenoid2`, `solenoid3`, `solenoid4`, or a group | `open`, `closed` | None. |
| `controllino` | `brushless_motor` | `pulse` | `pulse_us`. |
| `controllino` | `brushless_motor` | `on`, `off` | None; `pulse_us` blank. |
| `controllino` | `can_motor` | `speed` | `rpm`; optional `duration_s`, as below. |

Commands confirm the reported requested state. A stepper move waits for matching firmware completion. Solenoid groups request all targets before confirmation; confirmation covers electrical outputs only. `open` energizes a normally closed solenoid; `closed` de-energizes it. The selected source must own those outputs in the active configuration.

For stepper modes/moves, a blank `device` is also accepted. Select `positional` before `move`. `directional` selects local-velocity mode without starting motion. Travel is **speed × duration**, rounded to pulses; acceleration can extend movement time. Travel must round to at least one pulse and fit 2,147,483,647 pulses. The displayed travel span is not this command limit. Firmware mode, limits, and E-STOP still apply.

`brushless_motor` requires ESC firmware; `can_motor` requires CubeMars firmware. `servo` supports health checks only.

### CAN runs

| `rpm` | `duration_s` | Behavior |
| --- | --- | --- |
| Nonzero | Blank | Run through later rows using the default firmware ramp. Later speed rows update the target. An explicit `rpm=0` row is required before file end. |
| Nonzero | Number | Timed run: **0.020–5.000 s** on current firmware, in whole milliseconds. |
| `0` | Leave blank | Stop and wait for release. A maintained run ramps to zero; other runs release directly. Any supplied duration is ignored. |

Stop a maintained run before starting a timed run. The CSV parser's duration ceiling is 2147483.647 s; execution enforces the connected firmware's tighter limit.

A nonzero CAN row advances when the target is accepted, not when measured RPM reaches it or the run finishes. Add `wait` for a dwell.

## Reading checks

`action=reading_state` requires `timeout_s`. Bounds are inclusive, may be negative, and require minimum ≤ maximum. A failed check stops the experiment immediately; it does not wait for the value to enter range.

| `source` | `device` | `set_point` | Required bounds | Behavior |
| --- | --- | --- | --- | --- |
| `ed593` | One of `tc0`–`tc7` | `check` or blank | `min_c`, `max_c` | Check once. |
| `controllino` | `dro` | `check` or blank | `min_mm`, `max_mm` | Check once. |
| `controllino` | `dro` | `on` | `min_mm`, `max_mm` | Check, then monitor during later rows. |
| `controllino` | `dro` | `off` | Blank | Disable the monitor; all other check/request cells must be blank. |

For `check` and `on`, `max_age_ms` and `health` are independently optional. With both blank, only a finite in-range value is required; cached values can pass. ED-593 disabled/faulted channels have no usable value. For DRO, freshness includes measurement age plus cached-source age.

DRO bounds use raw `stepper_dro_position_mm`, without the dashboard's zero offset. Only one persistent DRO monitor is allowed. Match every `on` with `off` before file end. Its age check applies **at activation only**; bounds and selected transport-health checks continue until `off`. Persistent temperature checks are unsupported.

## Waits

`action=wait` uses only positive `duration_s`; all other cells must be blank. The row advances after that interval. Existing DRO monitoring continues.

## Timing and failure

| Rows | Timeout behavior |
| --- | --- |
| Health and reading rows | Starts at row entry. Missing samples can time out; failed reading checks fail immediately. |
| Commands | Starts when sent. Waiting for source freshness/health before dispatch has no CSV timeout. |
| Started stepper move | Start-handshake timeout ends once matching motion is confirmed. No overall completion timeout. |
| Accepted maintained CAN stop | Acceptance timeout ends once accepted. No overall ramp/release timeout. |

`timeout_s` is neither a whole-experiment deadline nor a device communication setting. Ordinary health/reading rows stop checking after passing; only DRO `on` continues across rows.

Failure stops row execution. An attempted move's failure requests a stepper stop. Other failures do not undo earlier solenoid or recording changes. Maintained CAN renewal stops on failure and its firmware lease expires. Include explicit closes/stops/recording-off rows for successful runs.

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

Other CSV files in this folder cover individual actions. [record_motor_solenoids.csv](record_motor_solenoids.csv) shows a complete recording/motor/solenoid/stepper sequence. [Parser](../dashboard_app/experiment.py).
