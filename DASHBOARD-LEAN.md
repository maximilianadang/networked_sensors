# Lean dashboard

Recording starts automatically when fresh telemetry shows any solenoid 1–4
turning on (including a valve first observed on at startup). Additional activations
join the same run. Closing valves does not stop recording; press **Stop** to finish
and save. After Stop, an already-open valve or its reconnection does not restart
recording; a newly observed activation does. Manual Start remains available.

Run from this directory:

```bash
python3 dashboard-lean.py --stepper-source controllino --stepper-url http://10.77.0.10 --esp32-source real --dxmr90-source real --host 0.0.0.0
```

Defaults remain simulated. All original dashboard CLI options are supported.
The original `dashboard.py` is preserved. `./run_controllino_dashboard.sh`
launches the lean version with the existing real-device connection settings.

## Controllino DRO support

The motion firmware now reads X1 SCL (clock, gray chip pin 43) and Digital 4
(data, gray chip pin 15). The lean dashboard uses the existing position, velocity,
freshness, frame diagnostics, and saved-zero fields; no additional browser decoder
is needed. See [the wiring and code-path explanation](README.md#controllino-dro).
Firmware compilation and injected-frame tests pass; upload and physical validation
remain pending. The Ethernet diagnostic sketch does not provide DRO capture.

## Where to edit a panel

Search the exact visible heading across `dashboard_app/static/`. JavaScript
and CSS use `PANEL:` comments with those headings. `SHARED PANELS:` marks
code used by several panels; editing it affects all listed panels. Graph
series are individually labeled in `config.js`. Desktop layout overrides
are marked `SHARED RESPONSIVE LAYOUT` in the lean CSS. Keep these comments
in sync when renaming a visible heading.

| Change | File |
| --- | --- |
| Panel markup, fields, buttons and their order | `dashboard_app/static/index-lean.html` |
| Panel sizes, spacing, responsive layout | `dashboard_app/static/dashboard-lean.css` |
| Motor controls, piston, DRO and ESC behavior | `dashboard_app/static/components/stepper-lean.js` |
| Test metadata and powder-flow calculations | `dashboard_app/static/components/metadata-lean.js` |
| Live measurement cards | `dashboard_app/static/components/metrics.js` |
| Device connection indicators | `dashboard_app/static/components/sources.js` |
| Recording and toolbar | `dashboard_app/static/components/toolbar-lean.js` |
| Graph drawing | `dashboard_app/static/charts.js` |
| Browser startup and incoming samples | `dashboard_app/static/app-lean.js` |
| Browser API calls and pending/error/cleanup lifecycle | `dashboard_app/static/api-lean.js` |
| Lean asset routing / shared API routes | `dashboard_app/http-lean.py` / `dashboard_app/http.py` |
| Recording, state and motion confirmation | `dashboard_app/runtime.py` |
| Controller command/status transport | `supervisor_core.py` |
| CLI defaults, validation and process lifecycle | `dashboard-lean.py` |

The lean entry point serves separate lean HTML, CSS and changed JavaScript
modules. The original browser assets and entry point are preserved. Unchanged
chart drawing, series configuration, metric/source renderers and DOM utilities
remain shared: duplicating them would introduce another copy to maintain.
Edits to those shared files still affect both entry points.

The lean HTTP adapter overrides presentation only and explicitly allowlists
assets. All command, recording, export, configuration and SSE routes use the
same backend. No controller protocol or firmware was changed in this frontend
refactor.

## Browser organization and invariants

- `createActions` owns keyed pending state, duplicate suppression, errors and
  cleanup. Panels own validation and confirmed-state interpretation.
- Move, Stop and E-STOP use independent keys. A pending move must not block a
  stop. Failed commands release pending state and expose an error; POSTs are
  never automatically retried.
- Metadata saves preserve edits made while the request is pending. Geometry
  still comes from the system configuration, not a duplicated browser constant.
- Chart redraws coalesce per animation frame and follow panel resizes.
  Samples with the same timestamp do not accumulate twice in chart history.
- CSS shares panel frames and controls; search `SHARED RESPONSIVE LAYOUT`
  for viewport sizing. Narrow screens scroll rather than conceal controls.
  The piston artwork and stale/unavailable/limit treatments are retained.

## Verification

Run the existing Python suite from the parent directory:

```bash
python3 -m unittest discover -s networked_sensors -t .
```

For browser checks, use the installed Firefox and geckodriver (no package download):

```bash
geckodriver --port 4444
# In another terminal, from this directory:
python3 check_dashboard-lean.py --screenshots /tmp/dashboard-lean-review
```

The checker starts its own simulation-only server, verifies live startup, then
uses a scriptless fixture with mocked responses to exercise panel actions and
failure paths. It checks Local Speed/Web Position at desktop and mobile sizes.
It never contacts the physical controller. Test definitions live in
`browser-checks-lean.js`; production does not serve that file.

## Shared backend (previous refactor)

The shared runtime now uses `_confirm_stepper_locked` for all ten command
confirmation paths. Callers supply explicit success predicates; the helper
handles fresh status, firmware rejection and timeout. E-STOP still sends its
command before acquiring the runtime lock. Local run confirms direction as
well as movement.

The Controllino adapter preserves firmware ownership and reported limits.
Missing limit inputs are represented as `None`; malformed inputs are rejected.
Directional limit validation is shared with the Yún adapter. The current
firmware has no homing support, and that capability remains disabled.

These backend improvements apply to both entry points. Panel locations and
API routes are unchanged. `test_dashboard_confirmation.py` exercises stale
status, wrong-direction status, timeouts, rejections and truthful telemetry.

The options table is the single declaration of CLI defaults, types and choices
within this entry point. Runtime names differ from CLI names only where listed
in `RUNTIME_NAMES`. Server binding precedes worker startup, and cleanup runs
on startup failure, normal shutdown and keyboard interruption.

For a USB-connected Controllino, use `--stepper-source controllino-usb --stepper-port /dev/cu.usbmodem1101 --stepper-baud 9600` instead of the Ethernet source and URL. USB and Ethernet share the same Controllino decoder and dashboard behavior. The device name can change after reconnecting.
# Piston home and travel configuration

`system_config.json` → `stepper` contains two independent settings:
`dro_home` (raw DRO millimeters saved by **Set top zero**) and
`max_travel_mm` (positive top-to-bottom distance, currently 126 mm).
With the piston physically at the top, press **Set top zero** after restarting
the dashboard. Display position is raw DRO minus `dro_home`: top 0 mm,
bottom −126 mm. Calibration persists across dashboard restarts; do not zero
automatically on reconnect. A sensor reference reset requires recalibration.

Manual config edits take effect on dashboard restart. Legacy
`dro_zero_raw_mm` configs still load and migrate to `dro_home` on the next
calibration save. Existing telemetry field names are unchanged. The configured
range sets the piston scale and caps individual requested travel (also bounded
by the existing firmware-compatible command ceiling). It is not an absolute
motion envelope or a replacement for physical limits. No firmware upload is
required for this configuration change.
