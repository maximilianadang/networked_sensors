# DEVELOPMENT-PROCEDURE - flow-management supervisor

Meta-history of building the laptop supervisor and local web dashboard. This is
the development chronicle: it records how the work is sequenced and why each
step is considered ready to move forward.

## Rules translated from dpc-flight

- **De-risk before code.** Before adding code, settle the interface by reading
  the existing sketch, Modbus reader, and handoff notes. Say what is settled
  with evidence.
- **Verify before moving forward.** A step is not complete because code exists;
  it is complete when its intended run path has been exercised.
- **Simulation first.** Hardware should not be required to validate the UI,
  recorder, schema, or merge logic.
- **One layer at a time.** Avoid mixing dashboard design, Modbus polling,
  firmware changes, and CSV export in one undifferentiated change.
- **Docs cadence.** Any change to source contracts, commands, artifacts, or
  hardware assumptions updates the matching all-caps document in the same step.
- **Protocol cadence.** Any change to how something is run, configured,
  selected, guarded, produced, or consumed checks `PROTOCOL.md`; once
  `protocol_map.py` exists, that check should be mechanical. Results from a run
  are procedure material unless they change the protocol.

## Step 0 - process layer

**Direction given:** produce all-caps markdown documents under
`networked_sensors`, using `dpc-flight` as the example and translating each
document kind to the flow-management test bench.

**Read:**

- `dpc-flight/PROTOCOL.md` for run-graph and runnable surface discipline.
- `dpc-flight/DEVELOPMENT-*` for staged, verified implementation.
- `dpc-flight/INTEGRATION-*` for drying separate arms into one protocol.
- `dpc-flight/DOCUMENTATION-*` for documenting only existing interfaces and
  harvesting verify-pass findings.
- `dpc-flight/RUNBOOK.md` and `FLIGHT_CHECKLIST.md` for separating runnable
  procedure from hardware-only checks.
- Existing `networked_sensors` files: ESP32 sketch, DXMR90 reader, README, and
  handoff notes.

**Inferred translation:**

- Autonomy method arms become sensor/source arms.
- Result-fidelity grids become simulation-first, adapter-smoke, and full-bench
  verification tiers.
- Flight bring-up checks become bench network, pressure, flow, relay, and
  recording checks.
- Generated protocol drift should begin with the first supervisor skeleton:
  source integration is already enough topology to merit `protocol_map.py`.

**Executed:** established the all-caps doc layer. No supervisor code exists yet.

**Verification:** documentation-only step; verified by source reads and by
keeping planned interfaces explicitly marked as planned rather than shipped.

## Turn - protocol-map trigger clarified

**Direction given:** build `protocol_map.py` as soon as the repo has enough
protocol topology that users or agents can get lost: more than one runnable
verb, more than one config family, variants as config axes, artifacts passed
between scripts, or the first serious integration task. This project is already
at the integration threshold. Also clarify the boundary: protocol changes cover
how things are run/configured/selected/guarded/produced/consumed; run outcomes
belong in procedure docs.

**Applied:** Step 1 now includes `protocol_map.py` as part of the supervisor
skeleton acceptance surface, and the protocol-vs-procedure boundary is recorded
in `PROTOCOL.md`, this file, and the documentation task rules.

## Step 1 - supervisor skeleton + protocol map

**Direction given:** carry out the first task: READ, INFER, DE-RISK, implement
the supervisor skeleton and protocol map, verify, and update the matching docs.

**READ:**

- `Flow_management_unit_sch1.ino`: the ESP32 samples at 10 Hz, sends SSE
  `reading` events with `p[]` and `f[]`, sends separate `sol`/`rec` events, and
  writes richer legacy CSV rows with raw volts, solenoid states, and combined
  pressure/flow.
- `read_dxmr90_modbus.py`: the DXMR90 metric vocabulary and units already exist
  as `Metric` objects; `CORE_NAMES` selects the first useful polling set.
- The Step-0 docs: the supervisor must be simulation-first, stdlib-friendly,
  source-arm based, and protocol-checked.

**INFER:**

- The smallest stable core is synchronous: source adapters expose `poll()`, the
  merger holds latest values, and the smoke CLI emits JSONL samples.
- Simulated ESP32 and DXMR90 sources belong in Step 1 because they are required
  to verify the schema and merge contract without hardware.
- Real source landing points should exist as placeholders only; no network,
  firmware, dashboard, or recording behavior belongs in this step.
- `protocol_map.py` should own the visual graph and verb/source/artifact tables
  immediately, since the project already has multiple source arms and runnable
  surfaces.

**DE-RISKED:**

- No new dependencies are needed: dataclasses, argparse, json, datetime, math,
  and local imports are enough.
- The DXMR90 simulator reuses metric names from the existing reader, avoiding a
  parallel vocabulary.
- The ESP32 simulator includes raw volts and solenoid state, but the protocol
  still records that the current real SSE stream only exposes `p[]` and `f[]`.
- `PROTOCOL.md` is generated and checked by command, so visual topology drift is
  mechanical rather than remembered.

**Executed:**

- Added `networked_sensors/__init__.py`.
- Added `supervisor_core.py`: `SourceReading`, `SourceAdapter`, simulated ESP32
  and DXMR90 sources, real-source placeholders, `SourceMerger`, and
  `iter_merged_samples`.
- Added `supervisor.py`: a Step-1 smoke CLI emitting merged JSONL samples.
- Added `protocol_map.py`: `--write` generation and `--check` drift checking for
  `PROTOCOL.md`.
- Regenerated `PROTOCOL.md` from `protocol_map.py`.

**VERIFIED:**

- `python3 -m compileall -q networked_sensors`
- `python3 networked_sensors/supervisor.py --samples 12`
- `python3 -m networked_sensors.supervisor --samples 3`
- `python3 networked_sensors/protocol_map.py --check`

The 12-sample smoke shows ESP32 updates at the 10 Hz merge cadence, DXMR90
values held between 1 Hz polls, `dxmr90_age_ms` resetting on the one-second
heartbeat update, and both source mode/connected fields present.

## Step 2 - simulation scenario hardening

**Direction given:** keep development rolling with the next task: make the
simulation source layer useful for future dashboard/recorder stale-state tests.

**READ:**

- `supervisor_core.py`: Step 1 had healthy periodic sources and a latest-value
  merger, but no way to force source dropout.
- `supervisor.py`: Step 1 CLI exposed `--samples`, `--rate-hz`, and
  `--realtime`; scenario selection was not yet part of the run contract.
- `PROTOCOL.md` / `protocol_map.py`: changing CLI selection and source behavior
  is a protocol change, so the visual graph and verb table must regenerate.

**INFER:**

- The scenario axis should configure sources, not alter the merged sample
  schema. Downstream UI/recorder code should always see the same expected keys.
- Five scenarios are enough for the next layers: `healthy`, `esp32_stale`,
  `dxmr90_stale`, `dxmr90_missing`, and `all_stale`.
- `--drop-after-s` controls when stale scenarios stop source updates;
  `--stale-after-s` controls when held latest values flip `*_connected` false.

**DE-RISKED:**

- No dashboard, recorder, real network adapter, or firmware behavior is needed.
- Missing DXMR90 values now preserve expected `dxmr90_*` keys with `null`
  values, avoiding a future UI branch on absent keys.
- Source freshness still flows only through the existing `*_age_ms` and
  `*_connected` fields.

**Executed:**

- Added expected-field declarations to source adapters.
- Added source `drop_after_s` / `start_after_s` support in `PeriodicSource`.
- Added `make_simulated_sources(scenario=..., drop_after_s=...)`.
- Added `--scenario`, `--drop-after-s`, and `--stale-after-s` to
  `supervisor.py`.
- Updated `protocol_map.py` with the scenario axis, regenerated `PROTOCOL.md`,
  and updated the runbook.

**VERIFIED:**

- `python3 -m compileall -q networked_sensors`
- `python3 networked_sensors/protocol_map.py --check`
- `python3 networked_sensors/supervisor.py --samples 12`
- `python3 networked_sensors/supervisor.py --scenario dxmr90_missing --samples 3`
- Targeted assertions:
  - `dxmr90_stale` flips `dxmr90_connected` from true to false after stale age.
  - `esp32_stale` flips `esp32_connected` from true to false after stale age.
  - `dxmr90_missing` keeps expected DXMR90 keys with `None`/JSON `null`.
  - `all_stale` flips both source connected flags false.

## Step 3 - local dashboard and API

**Direction given:** keep development rolling into the local dashboard/API now
that the supervisor stream and stale/missing scenarios are stable.

**READ:**

- `supervisor_core.py`: Step 2 provides the flat merged sample model, source
  health fields, and scenario axis that the dashboard should consume directly.
- `Flow_management_unit_sch1.ino`: the legacy UI has metadata fields, run
  start/stop, solenoid buttons, live cards, and plots; those are the operator
  affordances to preserve on the laptop side.
- `TESTBENCH_HANDOFF.md`: the clean architecture is a laptop UI with the ESP32
  kept as I/O, avoiding ESP32 RAM and CDN constraints.
- `PROTOCOL.md`: a dashboard entrypoint changes runnable verbs, endpoints,
  artifacts, and verification tiers, so the generated map must update.

**INFER:**

- A stdlib `ThreadingHTTPServer` is enough for the first dashboard slice:
  HTML/CSS/JS, JSON endpoints, and SSE samples without adding FastAPI/Flask yet.
- Step 3 should keep run state, metadata, recent history, and simulated solenoid
  state in memory. Disk recording/export is Step 4, not a hidden side effect.
- The browser should consume supervisor endpoints (`/api/*`), not talk to the
  ESP32 directly.

**DE-RISKED:**

- The UI uses inline canvas plots and no external CDN dependency.
- `dxmr90_missing` can drive the dashboard because expected DXMR90 keys remain
  present with `null` values.
- Simulated solenoid toggles now force the ESP32 simulator due for an immediate
  sample, so command responses and latest sample state agree in healthy sim mode.
- The legacy JSONL smoke CLI remains separate from the long-running dashboard
  server.

**Executed:**

- Added `dashboard.py`: localhost HTML dashboard, `/api/state`, `/api/latest`,
  `/api/history`, `/api/events`, `/api/run/start`, `/api/run/stop`,
  `/api/metadata`, and `/api/solenoid/toggle`.
- Added controllable simulated solenoid methods and an `esp32_auto_sequence`
  option so dashboard controls can be manual while CLI simulation keeps its
  previous automatic sequence.
- Updated `protocol_map.py` and regenerated `PROTOCOL.md` for the new runnable
  verb, API surface, in-memory artifacts, and dashboard verification tier.
- Updated task, runbook, integration, and documentation surfaces.

**VERIFIED:**

- `python3 -m compileall -q networked_sensors`
- `python3 networked_sensors/dashboard.py --help`
- `python3 networked_sensors/supervisor.py --samples 3`
- Started `python3 networked_sensors/dashboard.py --host 127.0.0.1 --port 8765 --scenario dxmr90_missing --rate-hz 20 --history-limit 80`
- Localhost smoke checked:
  - `/` returns the dashboard HTML.
  - `/api/state`, `/api/latest`, and `/api/history?limit=5` return expected
    simulation state.
  - `dxmr90_missing` shows `dxmr90_connected=false` and
    `dxmr90_heartbeat=null`.
  - `/api/metadata` saves in-memory metadata.
  - `/api/run/start` and `/api/run/stop` flip in-memory run state.
  - `/api/solenoid/toggle?n=0` returns a matching commanded state and latest
    sample.
  - `/api/events` emits SSE `state` and `sample` events.

## Step 4 - disk-backed recorder/exporter

**Direction given:** add a disk-backed recorder/exporter and remember the task
procedure. Also answer whether the protocol evolved.

**READ:**

- `dashboard.py`: Step 3 had live dashboard state, metadata, run start/stop, and
  simulated solenoid controls, but recording was intentionally in memory only.
- `Flow_management_unit_sch1.ino`: the legacy export preserves metadata fields
  and computes first-second pressure plus first active-flow-window averages.
- `supervisor_core.py`: `SourceMerger` held latest values but did not expose
  fresh source updates, which the recorder needs for source-scoped logs.
- `PROTOCOL.md` / `protocol_map.py`: adding durable artifacts, `--record-dir`,
  and export endpoints changes run/config/produce/consume topology.

**INFER:**

- The smallest useful recorder is a run directory per test, not a database:
  `merged_samples.csv`, source CSVs, `metadata.json`, `summary.json`, and
  `export.csv`.
- `merged_samples.csv` should carry the complete merged schema, including source
  mode, connected flags, and age fields, plus `run_elapsed_s`.
- Source CSVs should log fresh source updates only. In simulation this means
  ESP32 emits at 10 Hz and DXMR90 emits at 1 Hz; later real adapters can enrich
  raw transport details.
- Preserve the legacy metadata/summary names in `export.csv`, while also writing
  structured sidecar JSON for programmatic use.

**DE-RISKED:**

- Added `recorder.py` as a separate module so file-format code is testable
  without a running web server.
- Kept the live sample schema unchanged; persistence adds files/endpoints, not
  new live fields.
- Default recordings go under `networked_sensors/recordings/`, and that
  generated directory is ignored by `networked_sensors/.gitignore`.
- SQLite remains an open future option; CSV/JSON is enough for the first
  no-hardware operator workflow.

**Executed:**

- Added `recorder.py`: `FlowRunRecorder`, run directory allocation, merged CSV,
  source CSVs, metadata JSON, summary JSON, legacy-compatible export CSV, and
  artifact listing/resolution helpers.
- Updated `SourceMerger` to expose fresh source readings after each poll.
- Updated `dashboard.py` with `--record-dir`, disk-backed start/stop lifecycle,
  `/api/recordings`, `/api/export/latest`, and
  `/api/export?run_id=...&file=...`.
- Added an Export button to the dashboard.
- Added `networked_sensors/.gitignore` for generated recordings and bytecode.
- Updated `protocol_map.py` and regenerated `PROTOCOL.md`.

**PROTOCOL EVOLUTION:**

Yes. The protocol evolved from Step 3 to Step 4 because the dashboard now
accepts a `--record-dir` config axis, creates durable artifacts, and serves
recording/export endpoints. This is a run/config/produce/consume change, so
`PROTOCOL.md` was regenerated from `protocol_map.py`.

**VERIFIED:**

- `python3 -m compileall -q networked_sensors`
- `python3 networked_sensors/dashboard.py --help`
- `python3 networked_sensors/supervisor.py --samples 2`
- Direct recorder smoke wrote 14 merged rows, 14 ESP32 source rows, 2 DXMR90
  source rows, metadata JSON, summary JSON, and export CSV in `/tmp`.
- Started `python3 networked_sensors/dashboard.py --host 127.0.0.1 --port 8766 --rate-hz 20 --record-dir /tmp/flow-dashboard-recordings --scenario healthy`
- Localhost recorder/API smoke checked:
  - `/api/metadata` saved metadata.
  - `/api/run/start` created an active recording.
  - `/api/run/stop` finalized a completed run.
  - `/api/recordings` listed the completed run.
  - `/api/export/latest` served `export.csv`.
  - `/api/export?run_id=...&file=summary.json` served structured summary.
  - `/api/export?run_id=...&file=merged_samples.csv` included `run_elapsed_s`,
    source health, and DXMR90 fields.
  - Source artifact downloads served `esp32_raw.csv` and `dxmr90_raw.csv`.

## Step 5 - SICK/DXMR90 no-quorum real-source path

**Direction given:** the live web interface should not depend on ESP32s or any
source quorum. It should run regardless and show whatever source is publishing.

**INFER:** the right contract is independent source selection. ESP32 can be
`off`, while SICK/DXMR90 can be `real`, and the dashboard must still emit stable
samples when a source is missing or unreachable.

**Executed:** implemented `DisabledSource`, implemented `RealDxmr90Source` using
`read_dxmr90_modbus.py` directly, added `make_sources(...)`, and added dashboard
flags for `--esp32-source`, `--dxmr90-source`, and SICK/DXMR90 Modbus host/port
configuration.

**PROTOCOL EVOLUTION:** yes. The dashboard run contract now includes independent
source modes and SICK/DXMR90 real-source configuration, so `PROTOCOL.md` was
regenerated from `protocol_map.py`.

**VERIFIED:** compile/help passed; source-factory smoke passed with both sources
off; real SICK/DXMR90 mode against unreachable `192.168.0.1` emitted a stable
sample with `dxmr90_connected=false` and SICK values as `None` rather than
failing the dashboard. Hardware heartbeat/register smoke is still pending
because `192.168.0.1` did not respond from the current laptop network.

## Step 5 continuation - direct 10 Hz SICK acquisition

**Direction given:** the web interface must receive actual SICK measurements at
10 Hz, not ten merged copies of the approximately 1 Hz ScriptBasic output.

**READ:** live timing showed the dashboard merge and SSE stream already emitted
at 10 Hz, while register `12001` and the `13001+` republished block changed only
about every 1.1-1.2 seconds. The exported process-data contract maps each SICK
sensor directly into 16 registers: `1002-1017` and `2002-2017`.

**INFER:** increasing reads of the republished block would only duplicate stale
values. The clean acquisition path is to read both direct process windows,
decode their eight float32 values, and perform the kg/h to g/min, m3/h to L/min,
and bar to psi conversions in the laptop adapter. The republished path remains
useful as a diagnostic fallback.

**DE-RISK:** a live hardware probe completed 20 of 20 full two-window reads in
2.000 seconds. Adjacent temperature values decoded plausibly and the direct
schema included flow, pressure in bar and psi, temperature, total flow, and the
existing heartbeat. The bench was idle, so flow and STP pressure correctly read
zero during this verification.

**Executed:** added `read_direct_sick_values`, made `RealDxmr90Source` default to
the direct path at 10 Hz, added `--dxmr90-data-path direct|republished` and
`--dxmr90-rate-hz`, exposed those settings in run/recording state, and added
SICK 1/2 pressure-in-bar traces to the pressure plot.

**PROTOCOL EVOLUTION:** yes. The real source now selects a data path and rate,
consumes direct process registers by default, emits fresh source rows at 10 Hz,
and changes the dashboard invocation contract. `protocol_map.py` and generated
`PROTOCOL.md` were updated in the same task.

**VERIFIED:** compileall, dashboard help, direct-source schema smoke, sustained
live 10 Hz hardware polling, dashboard/API cadence, and protocol drift checks
passed. A dynamic-flow run remains useful for observing ten distinct physical
values per second under changing flow, but transport cadence is no longer the
bottleneck.

## Step 5 continuation - operator measurement plots split by source

**Direction given:** show SICK pressure in bar, place a dedicated SICK pressure
plot between the ESP32 P1/P2/P3 plot and mass-flow plot, and show both individual
SICK mass-flow measurements alongside their total.

**READ/INFER:** the direct adapter already emitted all required bar pressure and
individual mass-flow fields at 10 Hz. This task therefore belongs entirely in
the browser consumer; changing acquisition or recorder schemas would add risk
without adding information.

**Executed:** changed both SICK pressure cards to bar, restored the first plot
to ESP32 P1/P2/P3 only, added a middle SICK 1/SICK 2 pressure plot in bar, and
expanded the right mass-flow plot to ESP32 combined plus SICK 1, SICK 2, and
SICK total. The wide layout now has three stable columns and collapses to one
column below 820 px.

**PROTOCOL EVOLUTION:** the source and endpoint topology did not change, but
the browser consumption contract did. `protocol_map.py` now names the three
plot surfaces and generated `PROTOCOL.md` was regenerated.

**VERIFIED:** Python compile, generated-protocol drift, live HTML labels/canvas
IDs, API field availability, and 10 Hz stream cadence passed. Headless Firefox
could not produce screenshots because the installed Snap failed to initialize
its software framebuffer; responsive CSS and live DOM contracts were checked
without claiming screenshot coverage.

## Step 5A - unified simulated Yún stepper control

**Direction given:** add the agreed Yún stepper work as a task and begin it as
part of the existing local flow-management supervisor.

**READ/INFER:** `SourceMerger` and `FlowRunRecorder` were already generic across
named sources. Extending those abstractions preserved one browser, one SSE
timeline, and one run directory; a separate stepper server would have duplicated
operator and recording state.

**Executed:** added a bounded `SimulatedStepperSource`, stepper source selection,
stale/missing scenarios, move/stop/status handlers, shared dashboard motion
controls, live D4/D6/D8 and open-loop state, recorder artifact access, and
`test_stepper_control.py`.

**PROTOCOL EVOLUTION:** yes. The source topology, scenario axis, dashboard CLI,
HTTP endpoints, merged schema, browser consumer, and recording artifacts all
changed, so `protocol_map.py` and generated `PROTOCOL.md` were updated.

**VERIFIED:** 8/8 unit tests, compileall, localhost HTML/API command smoke, exact
1.5 mm simulated completion, and shared recorder output passed. The smoke run
wrote `stepper_raw.csv` with 97 rows next to ESP32 and DXMR90 source files.
This step did not contact the Yún.

## Step 5B - read-only physical Yún diagnostics over USB

**Direction given:** support two real contexts with one webpage: laptop-local
USB testing that preserves the laptop's internet connection, and later
production status/control over the Yún's bench-LAN Wi-Fi.

**READ/INFER:** the prior plan called USB a temporary harness and did not expose
D5, limit latches, or the reason STEP output was blocked. The safe first slice
is read-only telemetry that preserves the currently working manual D4/D5 motion
logic. Motion commands remain a separate bounded-engine task.

**Executed:** added compact versioned JSON status on firmware state changes and
as a one-second heartbeat; added `UsbStepperSource` using stdlib nonblocking
TTY reads; added `--stepper-source usb`, port, and baud configuration; expanded
the existing stepper panel with owner, D4/D5, D6/D8 active/latches, effective
speed, blocked reason, sequence, and transport error; disabled Move/Stop for
read-only sources.

**PROTOCOL EVOLUTION:** yes. The stepper selection axis is now
`sim|usb|network|off`; USB is implemented read-only, network is reserved and
fails closed, and USB/network must eventually share semantics and exclusive
command ownership. The protocol map and operational docs were updated.

**VERIFIED:** Python compile and 13 unit tests passed. Tests cover the observed
Forward-blocked-by-D6/Reverse-away state, malformed messages, a real
pseudo-terminal, and a missing USB device that disconnects only the stepper
arm. Restored an official toolchain entirely under `/tmp` (Arduino CLI 1.5.1,
AVR core 1.8.8, AccelStepper 1.64.0); the final Yún build passed at 12,300 bytes/42%
flash and 419 bytes/16% RAM. The initial sandboxed check reported no USB board;
that was an execution restriction rather than a physical absence.
Host-level `lsusb`, serial symlinks, and Arduino discovery subsequently
confirmed Arduino Yún USB ID `2341:8041` at `/dev/ttyACM0`, owned by `dialout`,
which includes the operator account.

**Live continuation:** after the user explicitly confirmed the system safe, a
fresh build was uploaded to `/dev/ttyACM0` with verification. The supervisor was
started at `http://127.0.0.1:8000/` through the stable by-id symlink. Initial
telemetry exposed a reason-precedence inconsistency (`b=1` with `run_off`), so
the firmware was corrected to prioritize the directional limit whenever the
limit-block bit is true, rebuilt, and uploaded with verification again. The
corrected live feed reports D4 HIGH/disabled, D5 HIGH/Forward, D6 LOW/active and
latched, D8 HIGH/clear, `positive_limit`, zero effective speed, and advancing
heartbeat sequence. Deliberate switch transitions, unplug behavior, and
step-timing effects remain open.

## Step 5C - D4-off manual speed tuning over USB

**Direction given:** the known 1.5 mm/s manual motion is too slow to diagnose
direction and usable travel, so allow the webpage to change speed now without
prematurely granting remote start or distance control.

**DE-RISKED:** the speed command cannot create motion. Firmware accepts the
versioned `V1 S10..1000` command only while D4 reads OFF/HIGH. D4 still starts
and stops, D5 still chooses direction, and D6/D8 still block travel into their
ends. Configured speed is reported separately from effective signed speed.

**Executed:** added bounded firmware parsing, `csps` status, USB adapter writes,
`/api/stepper/speed`, and a separate Apply Manual Speed page action. Full USB
Move/Stop remains disabled.

**PROTOCOL EVOLUTION:** yes. USB changed from a status-only producer to a
guarded parameter command consumer, and the dashboard gained an endpoint and
consumer. The generated protocol, runbook, tasks, and checklist were updated.

**VERIFIED SO FAR:** Python compile and 16 tests pass, including invalid values,
D4-on rejection, compact status decoding, and exact pseudo-terminal output.
The Yún target compiles at 13,944 bytes/48% flash and 462 bytes/18% RAM. No T4B
upload or physical speed change had occurred at this verification point.

**Live continuation:** after the user confirmed the system safe, the fresh T4B
artifact was rebuilt, uploaded to `/dev/ttyACM0`, and verified. The updated
localhost process then received `stepper_speed_command_capable=true`. With D4
OFF and effective speed at zero, `/api/stepper/speed` set 3.0 mm/s and the next
firmware status echoed configured speed 3.0 mm/s. Physical staged
1.5/3.0/5.0 mm/s checks remain.

## Step 5D - guarded electrical direction mapping

**Direction given:** the motor rotates but the piston does not translate, and
the physical sign may be loading farther into a hard stop; make direction
reversible from the page as well.

**DE-RISKED:** the page changes only the logical-to-electrical DIR mapping, not
the live D5 selection. `V1 D0|1` is accepted only while D4 is OFF and does not
emit steps. Limit decisions remain based on logical Forward/Reverse before the
electrical sign is applied.

**Executed:** added firmware mapping/status, USB decode/write support, strict
boolean API, Normal/Inverted UI, and separate capability/status fields. Logical
speed decoding removes the electrical sign so inverted Reverse remains reported
as negative travel.

**VERIFIED SO FAR:** Python compile and 18 tests pass, including inverted
logical Reverse, exact direction command bytes, strict validation, and runtime
forwarding. The Yún target compiles at 14,294 bytes/49% flash and 484 bytes/18%
RAM. Upload and physical sign identification remain motor-safe gated.

**Live continuation:** under the standing safe-upload direction, the T4C
artifact was rebuilt, uploaded, and verified. The restarted localhost page
received both calibration capability flags and mapping `normal` while D4 was
OFF and effective speed was zero. No physical mapping has been selected yet.

**Browser correction:** the 10 Hz renderer could restore the live mapping after
the operator selected a different option but before the Apply click read it.
The client now holds a deliberate selection until firmware echoes it. A direct
API isolation check changed the stopped mapping to Inverted, the Yún echoed it,
and the restarted fixed page reports Inverted with D4 OFF and zero motion.

## Step 5E - dual-mode bounded USB position engine

**Direction given:** retain D5 as a useful direction control, add webpage
relative distance and an optional D8-limit action, remove operator-adjustable
acceleration, and give D5 a clear role during web control.

**READ/INFER:** the existing firmware exposed only indefinite `runSpeed()`
motion. Local Velocity preserves the working D4 run/stop and D5 direction
behavior. In Web Position the operator enters a positive relative travel
magnitude and D5 selects Forward or Reverse when the command is accepted. The
supervisor converts that pair to the signed internal delta; the USB adapter and
firmware re-check D5, and a D5 change during motion aborts rather than reversing.

**DE-RISKED:** mode changes require D4 OFF and stopped motion; boot always
returns to stopped Local Velocity; unfinished commands are discarded at reset;
and D6/D8 remain directional stops. Open-loop homed/position/target values are
not authorization inputs and no absolute software envelope is enforced. One
relative command remains capped at the measured 137.18 mm stroke. The installed
normally-open limits remain a known broken-wire limitation. The 100 steps/mm
conversion is explicitly provisional until STEP pulses are checked against a
DRO displacement.

**Executed:** replaced the single velocity loop with a dual-mode non-blocking
AccelStepper engine; added fixed 5 mm/s² acceleration, an optional fixed 1.5
mm/s Move to D8 Limit action, immediate Stop, relative move commands, and 10 Hz
moving telemetry. Each move uses a fresh relative pulse counter; it does not
consult an absolute position. Extended `UsbStepperSource`, the shared API, and
the existing dashboard with Local Velocity/Web Position mode, D5
travel-direction display, and real USB Move/Stop. Removed acceleration and
open-loop absolute position/target/remaining from the operator form.

**VERIFIED SO FAR:** the revised Yún target compiles at 18,086 bytes/63% flash
and 698 bytes/27% global RAM. Twenty-two desktop tests pass, covering simulation,
legacy status compatibility, new status validation, exact mode/Home/move/Stop
bytes, an unreferenced move, and dashboard-runtime acknowledgement. The artifact
was uploaded to the Yún with verification. The restarted USB dashboard reported
stopped Local Velocity, D4 OFF, D5 Forward, D6/D8 clear, boot armed, suppressed
position fields, and zero effective speed. No motion command was issued for that
smoke; staged D8-limit seek, D5-selected Forward/Reverse moves, D4-abort,
destination-limit stop, and mid-move D5-abort checks remain.

**Browser correction:** relative travel distance and command ID are position
commands, not Local Velocity parameters. Their rows now initialize hidden and
remain hidden and disabled outside Web Position mode. An explicit
`[hidden] { display: none !important; }` rule prevents the form's label layout
from overriding that state. Move, Move to D8 Limit, and Stop are likewise Web Position-only;
Apply Local Velocity Speed is Local Velocity-only. The shared speed input
remains editable in both modes, but its label changes with its active meaning.

**Direction correction:** the initial Web Position form redundantly required a
signed distance while also showing D5 as a direction authorization. The form
and `/api/stepper/move` now accept only a positive travel magnitude. D5 supplies
the direction; the supervisor resolves the signed internal delta immediately
before dispatch. A pseudo-terminal regression proves that a 2.5 mm request with
D5 Reverse produces `V1 G-250,200,1`, while a negative operator magnitude is
rejected before any serial write. The compact signed firmware command is
retained as an internal safety contract, so no ATmega upload was required.

**Operator-state correction:** removed Homed, Position, Target, and Remaining
from the Motion card. The legacy `stepper_homed` wire/API field may record that
D8 zeroed a diagnostic counter, but it is not consulted by Move. USB position,
target, remaining, and software-envelope fields are published as null.

## Step 6 - real ESP32 HTTP/SSE adapter software landing

**Direction given:** make the current laptop-hosted `dashboard.py` accept real
ESP32 flow-management input.

**Read/de-risk evidence:** the existing ESP32 firmware already exposes the
needed transport. `GET /events` emits 10 Hz named `reading` events containing
three pressure values in `p[]` and three flow values in `f[]`; a separate named
`sol` event carries three booleans. `POST /solenoid/toggle?n=0..2` returns `ON`
or `OFF`. The event does not contain raw ADS1115 volts, and the ESP32's legacy
recording state is separate from laptop recording.

**Executed:** replaced the `RealEsp32Source` placeholder with a no-dependency,
reconnecting background SSE client so an HTTP read cannot block the 10 Hz merge
loop. The adapter validates finite three-value arrays, derives combined
pressure/flow, joins the latest separate solenoid event, forwards toggle POSTs,
bypasses inherited HTTP proxies for local bench traffic, and reports transport
errors through the merged schema. Added `--esp32-url` and `--esp32-timeout`,
real-source solenoid buttons, an ESP32 transport readout, and run-state config.
Raw-voltage fields remain null rather than being inferred.

**Superseded by Step 7:** this was the intermediate compatibility adapter before
the user selected a strict headless firmware boundary.

**Verification so far:** five parser/adapter/CLI/dashboard-runtime tests run
against a loopback HTTP server reproducing the firmware SSE and POST contract.
Together with the existing stepper suite, 27 tests pass. This proves software acceptance,
schema projection, solenoid forwarding, and dashboard runtime selection without
contacting hardware. A physical `/events` stream smoke, a deliberately safe
solenoid toggle, and a recorded-run artifact check remain before Step 6 is
complete.

## Step 7 - headless ESP32 firmware and strict v1 correction

**Direction given:** the laptop webpage is the current UI, so it has no reason
to remain compatible with a different webpage formerly hosted by the ESP32.
Keep that old sketch locally for reference and make the deployable ESP32 code a
headless data/control device.

**Executed:** moved the 698-line self-hosted-dashboard sketch to
`legacy/Flow_management_unit_sch1/Flow_management_unit_sch1.ino` and replaced
the primary sketch with a 228-line headless service. The primary `/events`
reading is explicitly version 1 and atomically includes `sample_ms`, three
pressure values, three flow values, both three-value clamped-voltage arrays, and
all three solenoid states. It retains the immediate `sol` event and the bounded
solenoid-toggle POST, but removes HTML, metadata, in-RAM recording, and `/test/*`.

`RealEsp32Source` now requires the complete v1 contract. It rejects
unversioned, incomplete, wrong-length, non-finite, or wrongly typed samples
instead of adapting the archived partial stream. This clean failure is the
deployment-version check; it is not webpage compatibility.

**Verification:** six ESP32 tests cover primary/legacy firmware layout, strict
decoding/rejection, background SSE, solenoid POST, CLI selection, and dashboard
runtime selection. With the stepper suite, all 28 desktop tests pass. The
primary sketch compiles for
`esp32:esp32:adafruit_feather_esp32s3_nopsram` using 1,095,217 bytes (52%) flash
and 80,940 bytes (24%) global RAM. Physical flash, sustained v1 streaming, safe
relay toggle, and recorded-run verification remain pending.

## Step 7A - fourth ESP32 solenoid output

**Direction given:** add a fourth solenoid using the existing output template,
with GPIO 10 as its ESP32 control pin.

**Contract decision:** increasing `sol[]` from three booleans to four changes a
strict payload's shape, so the headless ESP32 contract advances from version 1
to version 2. Older firmware is rejected visibly rather than being mistaken for
a four-output controller. The archived three-output webpage sketch remains
unchanged as a historical reference.

**Executed:** the primary firmware now defines four active-low relay outputs on
GPIO 5, 6, 9, and 10, initializes all four OFF, accepts toggle indices 0–3, and
emits four states in both complete readings and immediate `sol` events. The
simulation, stable merged schema (`esp32_sol4`), strict adapter, fourth laptop
button, and loopback server use the same cardinality. GPIO 10 drives only a
relay input; the 24 V solenoid is not connected directly to the ESP32.

**Verification:** all six ESP32 contract/layout tests pass, including strict
rejection of a three-state payload and a fourth-channel POST. The complete
28-test desktop suite passes. The target compiles for
`esp32:esp32:adafruit_feather_esp32s3_nopsram` at 1,095,265 bytes (52%) flash
and 80,940 bytes (24%) global RAM. Physical IN4/coil/flyback wiring, flash,
OFF-at-boot observation, and a deliberately safe channel-4 toggle remain
pending.

## Step 5F - latched dashboard software E-STOP

**Direction given:** add an E-STOP button near the top of the dashboard that
stops stepper motion.

**Contract decision:** the control is labeled **SOFTWARE E-STOP** because it
depends on the browser, Python process, USB link, and powered Yún logic. It does
not remove DM542T power and cannot be treated as a hardwired safety-rated
emergency stop. Unlike the Web Position-only Stop button, it must stop Local
Velocity too and remain latched until a deliberate reset.

**Executed:** firmware accepts priority `V1 E1`, aborts any bounded motion,
suppresses `runSpeed()` pulses, publishes latch field `e` and state 9, and
continues enforcing the latch every loop. `V1 E0` is rejected until physical D4
is OFF, preventing reset from immediately restarting Local Velocity. The
simulator, USB parser/adapter, merged schema, runtime acknowledgement, two API
endpoints, and prominent red top-level page control share that contract. All
motion/setup controls are disabled while latched; stop has no confirmation and
reset does.

**Verification:** 26 stepper tests and the complete 32-test desktop suite pass.
The Yún target compiles with Arduino AVR core 1.8.8 and AccelStepper at 18,652
bytes (65%) flash and 733 bytes (28%) global RAM. Upload and deliberate physical
stops from Local Velocity and Web Position were initially pending. On
2026-07-13, all 18,652 bytes were uploaded to `/dev/ttyACM0` and verified by
flash readback. With D4 OFF/HIGH, both limits clear, and zero motion, the live
dashboard/API latched state 9 with `emergency_stop`, then confirmed `V1 E0`
returned to `manual_stopped`. Moving-stop, D4-on reset, disconnect persistence,
and latency checks initially remained. The combined dashboard then exposed a
cross-source issue: an unreachable DXMR90 held the shared poll lock, producing a
1.26-second stopped E-STOP HTTP response. E-STOP now writes to the serialized
Yún command channel before acquiring that lock and waits normally for the fresh
status acknowledgement afterward. With DXMR90 still unreachable and its timeout
set to 0.1 s, the stopped latch response measured 0.041 s. Moving-stop and
moving-latency qualification remain; no loaded stop-time claim is made yet.

## Step 5G - Yún Linux UART/HTTP bridge

**Direction given:** the Yún is already associated with the bench WLAN, so make
the stepper available to a dashboard running on another LAN laptop instead of
requiring the Yún USB cable on that host.

**Design decision:** use the T6-permitted small Linux-side service rather than
the archived blocking Bridge API in the time-sensitive ATmega loop. The ATmega
polls Serial1 in bounded chunks and drains status/ack lines only into UART bytes
reported writable. `yun_stepper_bridge.py` runs on the AR9331, owns
`/dev/ttyATH0`, validates exact V1 grammar, caches status, and exposes health,
status, and command endpoints on trusted-LAN port 8080. The service performs no
motion logic. The archived official Bridge daemon must stop because it owns the
same UART.

**Ownership/failure decision:** an accepted mutating command claims either USB
or network in firmware. A competing owner is rejected. Stop and `V1 E1` remain
accepted from either path. An idle owner releases only while motion is stopped
and physical D4 has been OFF for two seconds. The laptop network adapter does
not retry non-idempotent commands; Linux waits for an explicit ATmega ack, and
the dashboard then waits for a fresh status sequence before reporting success.

**Executed:** added the raw-UART firmware transport and ownership field `o`,
the Python 2/3 Linux service plus OpenWrt init wrapper,
`NetworkStepperSource`, `--stepper-url`, `--stepper-timeout`, background 10 Hz
polling, proxy bypass, network-aware page messaging, and physical USB/network
acknowledgement parity in dashboard runtime.

**Verification:** six focused network tests pass exact pseudo-terminal relay,
HTTP status/command, owner decode, accepted speed echo, firmware rejection,
ack timeout, source factory/CLI, and fresh dashboard E-STOP confirmation. The
complete 38-test desktop suite passes. The Yún target compiles at 20,794 bytes
(72%) flash and 1,399 bytes (54%) global RAM. The image was uploaded through
`/dev/ttyACM0`; a fresh stopped USB heartbeat showed D4/D5 HIGH, D6/D8 clear,
zero motion, E-STOP clear, and owner none. No Yún-side service installation,
physical LAN response, jitter measurement, moving stop, Linux restart, or
disconnect test is claimed yet. This host was on `AsteraMesh`, not the Yún's
configured `GL-MT3000-b3a`, so it did not guess or install against an
unidentified LAN address.

The Linux relay also latches its command channel unsynchronized after any UART
write or acknowledgement timeout and rejects subsequent commands until the
service restarts. This prevents a late, uncorrelated acknowledgement from
being accepted as proof of a later motion command.
